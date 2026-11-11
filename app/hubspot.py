"""HubSpot CRM API client: read, create and update companies and contacts."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.http import ApiError, request_json

logger = logging.getLogger(__name__)

COMPANY_READ_PROPERTIES = [
    "name", "domain", "website", "forager_job_title_filter", "forager_enriched_at",
]
CONTACT_READ_PROPERTIES = [
    "firstname", "lastname", "email", "company", "jobtitle",
    "person_linkedin_url", "company_domain", "forager_enriched_at",
]


class HubSpotClient:
    def __init__(self, settings: Settings) -> None:
        self._client = httpx.Client(
            base_url=settings.hubspot_base_url,
            headers={"Authorization": f"Bearer {settings.hubspot_access_token}"},
            timeout=30,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return request_json(self._client, "HubSpot", method, path, **kwargs)

    # ---- companies -------------------------------------------------------

    def get_company(self, company_id: str) -> dict[str, Any]:
        params = {"properties": ",".join(COMPANY_READ_PROPERTIES)}
        return self._request("GET", f"/crm/v3/objects/companies/{company_id}", params=params)

    def update_company(self, company_id: str, properties: dict[str, Any]) -> None:
        self._request(
            "PATCH", f"/crm/v3/objects/companies/{company_id}", json={"properties": properties}
        )

    def company_contact_ids(self, company_id: str) -> list[str]:
        """Ids of the contacts already associated with a company."""
        data = self._request(
            "GET", f"/crm/v4/objects/companies/{company_id}/associations/contacts",
            params={"limit": 100},
        )
        return [str(row["toObjectId"]) for row in (data or {}).get("results", [])]

    def company_contact_linkedin_urls(self, company_id: str) -> set[str]:
        """LinkedIn URLs of the contacts a company already has, lower-cased."""
        contact_ids = self.company_contact_ids(company_id)
        if not contact_ids:
            return set()
        body = {
            "properties": ["person_linkedin_url"],
            "inputs": [{"id": contact_id} for contact_id in contact_ids],
        }
        data = self._request("POST", "/crm/v3/objects/contacts/batch/read", json=body)
        urls = (row["properties"].get("person_linkedin_url") for row in (data or {}).get("results", []))
        return {url.lower().rstrip("/") for url in urls if url}

    # ---- contacts --------------------------------------------------------

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        params = {
            "properties": ",".join(CONTACT_READ_PROPERTIES),
            "associations": "companies",
        }
        return self._request("GET", f"/crm/v3/objects/contacts/{contact_id}", params=params)

    def update_contact(self, contact_id: str, properties: dict[str, Any]) -> None:
        try:
            self._request(
                "PATCH", f"/crm/v3/objects/contacts/{contact_id}",
                json={"properties": properties},
            )
        except ApiError as exc:
            # The email belongs to another contact already: save everything else.
            if exc.status_code != 409 or "email" not in properties:
                raise
            logger.warning("Email already used by another contact, keeping the old one")
            remaining = {k: v for k, v in properties.items() if k != "email"}
            self._request(
                "PATCH", f"/crm/v3/objects/contacts/{contact_id}",
                json={"properties": remaining},
            )

    def find_contact_id_by_email(self, email: str) -> str | None:
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
            ],
            "limit": 1,
        }
        data = self._request("POST", "/crm/v3/objects/contacts/search", json=body)
        results = (data or {}).get("results", [])
        return str(results[0]["id"]) if results else None

    def upsert_contact(self, properties: dict[str, Any], company_id: str = "") -> str:
        """Create a contact, or update the existing one with the same email.
        Then link it to the company. Returns the contact id."""
        email = properties.get("email", "")
        contact_id = self.find_contact_id_by_email(email) if email else None
        if contact_id:
            self.update_contact(contact_id, properties)
        else:
            created = self._request(
                "POST", "/crm/v3/objects/contacts", json={"properties": properties}
            )
            contact_id = str(created["id"])
        if company_id:
            self._request(
                "PUT",
                f"/crm/v4/objects/contacts/{contact_id}/associations/default/companies/{company_id}",
            )
        return contact_id

    # ---- custom properties ----------------------------------------------

    def ensure_property_group(self, object_type: str, name: str, label: str) -> None:
        try:
            self._request(
                "POST", f"/crm/v3/properties/{object_type}/groups",
                json={"name": name, "label": label},
            )
        except ApiError as exc:
            if exc.status_code != 409:
                raise

    def ensure_property(self, object_type: str, definition: dict[str, Any]) -> bool:
        """Create a custom property. Returns False if it already existed."""
        try:
            self._request("POST", f"/crm/v3/properties/{object_type}", json=definition)
            return True
        except ApiError as exc:
            if exc.status_code == 409:
                return False
            raise

    def close(self) -> None:
        self._client.close()
