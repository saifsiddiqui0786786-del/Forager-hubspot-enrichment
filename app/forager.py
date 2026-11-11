"""Forager API client.

Docs: https://docs.forager.ai  (auth is the `X-API-KEY` header, and the
account id is part of every data URL).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.http import ApiError, request_json

logger = logging.getLogger(__name__)

WORK_EMAIL_TIMEOUT_SECONDS = 120
USABLE_EMAIL_STATUSES = {"valid", "risky", "unknown"}


def build_title_query(job_titles: list[str]) -> str:
    """Turn a list of job titles into Forager's boolean text query.

    ["VP Sales", "Head of Sales"]  ->  '"VP Sales" OR "Head of Sales"'
    """
    cleaned = [title.strip().replace('"', "") for title in job_titles if title.strip()]
    return " OR ".join(f'"{title}"' for title in cleaned)


class ForagerClient:
    def __init__(self, settings: Settings) -> None:
        self._account_id = settings.forager_account_id
        self._phone_quality_tier = settings.forager_phone_quality_tier
        self._client = httpx.Client(
            base_url=settings.forager_base_url,
            headers={"X-API-KEY": settings.forager_api_key},
            timeout=30,
        )

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        return request_json(self._client, "Forager", "POST", path, json=body)

    def _data_path(self, endpoint: str) -> str:
        return f"/api/{self._account_id}/datastorage/{endpoint}/"

    # ---- companies -------------------------------------------------------

    def find_organization(self, domain: str = "", name: str = "") -> dict[str, Any] | None:
        """Return the full organization record for a domain (preferred) or name.

        Many records can share one domain (a search for openai.com also returns
        "ChatGPT", "DALL-E" and dozens of look-alikes). So we first resolve the
        right record with the free autocomplete lookup, scoring each option on
        domain and name, and only then pay 1 credit for the full record.
        """
        options: list[dict[str, Any]] = []
        for query in (domain, name):
            if query:
                lookup = request_json(
                    self._client, "Forager", "GET",
                    self._data_path("autocomplete/organizations"), params={"q": query},
                )
                options.extend((lookup or {}).get("results") or [])
        if not options:
            return None

        best = max(options, key=lambda option: self._match_score(option, domain, name))
        if self._match_score(best, domain, name) == 0:
            return None
        results = self._post(
            self._data_path("organization_search"), {"organization_ids": [int(best["id"])]}
        )
        matches = (results or {}).get("search_results") or []
        return matches[0] if matches else None

    @staticmethod
    def _match_score(option: dict[str, Any], domain: str, name: str) -> int:
        """Score an autocomplete option shaped like 'OpenAI - openai.com'."""
        def normalise(value: str) -> str:
            return "".join(ch for ch in value.lower() if ch.isalnum())

        option_name, _, option_domain = (option.get("text") or "").rpartition(" - ")
        domain_root = domain.split(".")[0] if domain else ""
        score = 0
        if domain and option_domain.strip().lower() == domain.lower():
            score += 2
        if normalise(option_name) in {normalise(name), normalise(domain_root)} - {""}:
            score += 2
        return score

    # ---- people ----------------------------------------------------------

    def search_people(
        self,
        organization_id: int | None = None,
        domain: str = "",
        job_titles: list[str] | None = None,
        person_name: str = "",
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """Search current roles at a company, optionally filtered by job title."""
        body: dict[str, Any] = {"role_is_current": True, "page": page}
        if organization_id:
            body["organizations"] = [organization_id]
        elif domain:
            body["organization_domains"] = [domain]
        if job_titles:
            body["role_title"] = build_title_query(job_titles)
        if person_name:
            body["person_name"] = f'"{person_name}"'
        results = self._post(self._data_path("person_role_search"), body)
        return (results or {}).get("search_results") or []

    def person_detail(
        self, person_id: int | None = None, linkedin_identifier: str = ""
    ) -> dict[str, Any] | None:
        body: dict[str, Any] = {}
        if person_id:
            body["person_id"] = person_id
        elif linkedin_identifier:
            body["linkedin_public_identifier"] = linkedin_identifier
        else:
            return None
        return self._lookup_or_none("person_detail_lookup", body)

    def person_by_email(self, email: str) -> dict[str, Any] | None:
        return self._lookup_or_none("person_detail_reverse_lookup/by_email", {"email": email})

    def _lookup_or_none(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """Lookups answer 404 when the person is unknown. Treat that as 'no match'."""
        try:
            return self._post(self._data_path(endpoint), body) or None
        except ApiError as exc:
            if exc.status_code == 404:
                return None
            raise

    # ---- contact details -------------------------------------------------

    def phone_numbers(self, person_id: int) -> list[str]:
        """Phone numbers for a person. Credits are charged only when one is found."""
        body = {"person_id": person_id, "quality_tier": self._phone_quality_tier}
        rows = self._lookup_or_none("person_contacts_lookup/phone_numbers", body) or []
        return [row["phone_number"] for row in rows if row.get("phone_number")]

    def personal_emails(self, person_id: int) -> list[str]:
        rows = self._lookup_or_none(
            "person_contacts_lookup/personal_emails", {"person_id": person_id}
        ) or []
        return [
            row["email"] for row in rows
            if row.get("email") and row.get("validation_status") in USABLE_EMAIL_STATUSES
        ]

    def work_email(self, linkedin_identifier: str) -> str:
        """Find a verified work email. Forager runs this as a background job, so
        we submit it and then poll until it finishes. Polling is free."""
        if not linkedin_identifier:
            return ""
        path = f"/api/v2/{self._account_id}/datastorage/person_contacts_lookup/work_emails/"
        try:
            job = self._post(path, {"linkedin_public_identifier": linkedin_identifier})
        except ApiError as exc:
            logger.warning("Work email lookup refused for %s: %s", linkedin_identifier, exc)
            return ""

        deadline = time.monotonic() + WORK_EMAIL_TIMEOUT_SECONDS
        while job and job.get("status") not in {"completed", "failed"}:
            if time.monotonic() > deadline:
                logger.warning("Work email lookup timed out for %s", linkedin_identifier)
                return ""
            time.sleep(3)
            job = request_json(self._client, "Forager", "GET", f"{path}{job['job_id']}/")

        return self._first_email((job or {}).get("emails") or [])

    @staticmethod
    def _first_email(emails: list[Any]) -> str:
        """Entries arrive as {"email", "validation_status"} or as [email, status]."""
        for entry in emails:
            if isinstance(entry, dict):
                address, status = entry.get("email", ""), entry.get("validation_status", "")
            elif isinstance(entry, (list, tuple)) and entry:
                address, status = entry[0], entry[1] if len(entry) > 1 else ""
            else:
                address, status = str(entry), ""
            if address and (not status or status in USABLE_EMAIL_STATUSES):
                return address
        return ""

    def close(self) -> None:
        self._client.close()
