"""Translate Forager records into HubSpot properties.

These are plain functions with no network calls, so they are easy to test.
Empty values are dropped, which means enrichment never wipes data that is
already in HubSpot.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

CITY_TYPES = {"city", "town", "village", "municipality", "locality", "suburb", "hamlet"}
STATE_TYPES = {"state", "province", "region", "county", "administrative"}
COUNTRY_TYPES = {"country"}
MAX_TEXT = 5000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_domain(value: str | None) -> str:
    """'https://www.OpenAI.com/about' -> 'openai.com'"""
    if not value:
        return ""
    value = value.strip().lower()
    host = urlparse(value if "//" in value else f"//{value}").netloc or value
    host = host.split("/")[0].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def linkedin_identifier(url: str | None) -> str:
    """'https://www.linkedin.com/in/sam-altman/?x=1' -> 'sam-altman'"""
    if not url or "linkedin.com/in/" not in url:
        return ""
    return url.split("linkedin.com/in/")[1].split("?")[0].strip("/").split("/")[0]


def split_location(location: dict[str, Any] | None) -> dict[str, str]:
    """Break a Forager location into city, state and country.

    Forager gives a display name ("San Francisco, California, United States")
    plus typed parts. The typed parts win; the display name is the fallback.
    """
    result = {"city": "", "state": "", "country": ""}
    if not location:
        return result

    for part in location.get("osm_locations") or []:
        place_type = (part.get("place_type") or "").lower()
        # Typed names repeat their parents ("California, United States"),
        # so only the first piece is the value we want.
        value = (part.get("name") or "").split(",")[0].strip()
        if place_type in COUNTRY_TYPES and not result["country"]:
            result["country"] = value
        elif place_type in STATE_TYPES and not result["state"]:
            result["state"] = value
        elif place_type in CITY_TYPES and not result["city"]:
            result["city"] = value

    # Only trust the display name when there are no typed parts at all. Names
    # can be messy ("united states, bangladesh") and would give wrong cities.
    if any(result.values()):
        return result
    pieces = [p.strip().title() for p in (location.get("name") or "").split(",") if p.strip()]
    if len(pieces) >= 3:
        fallback = {"city": pieces[0], "state": pieces[1], "country": pieces[-1]}
    elif len(pieces) == 2:
        fallback = {"city": pieces[0], "state": "", "country": pieces[1]}
    elif len(pieces) == 1:
        fallback = {"city": "", "state": "", "country": pieces[0]}
    else:
        fallback = {}
    for key, value in fallback.items():
        result[key] = result[key] or value
    return result


def title_matches(role_title: str | None, job_titles: list[str]) -> bool:
    """True when a role title contains one of the wanted titles as whole words.

    "VP" matches "VP Finance" but not "MVP Engineer". Forager's text search is
    fuzzy, so this second check keeps the job-title filter strict.
    """
    if not job_titles:
        return True
    title = f" {(role_title or '').lower()} "
    for wanted in job_titles:
        pattern = r"(?<![a-z0-9])" + re.escape(wanted.strip().lower()) + r"(?![a-z0-9])"
        if wanted.strip() and re.search(pattern, title):
            return True
    return False


def _compact(properties: dict[str, Any]) -> dict[str, Any]:
    """Drop empty values so we never overwrite HubSpot data with blanks."""
    return {k: v for k, v in properties.items() if v not in (None, "", [])}


def _names(items: list[dict[str, Any]] | None, limit: int = 25) -> str:
    return ", ".join(item["name"] for item in (items or [])[:limit] if item.get("name"))


# ---- company ---------------------------------------------------------------

def company_properties(org: dict[str, Any]) -> dict[str, Any]:
    linkedin = org.get("linkedin_info") or {}
    # A company can have several addresses; use the most detailed one.
    address = max(
        org.get("addresses") or [{}],
        key=lambda a: sum(1 for value in a.values() if value),
    )
    location = split_location(org.get("location"))
    street = " ".join(
        part for part in (address.get("street_number"), address.get("street_name")) if part
    )
    founded = org.get("founded_date") or ""

    return _compact({
        "name": org.get("name"),
        "domain": clean_domain(org.get("domain") or org.get("website")),
        "website": org.get("website"),
        "description": (org.get("description") or "")[:MAX_TEXT],
        "annualrevenue": (org.get("finance_info") or {}).get("revenue"),
        "numberofemployees": org.get("employees_amount"),
        "founded_year": founded[:4],
        "linkedin_company_page": linkedin.get("public_profile_url"),
        "address": street,
        # The typed location has full names ("California", "United States");
        # addresses only carry codes ("CA", "US"), so they are the fallback.
        "city": location["city"] or address.get("city"),
        "state": location["state"] or address.get("state"),
        "country": location["country"] or address.get("country"),
        "zip": address.get("postcode"),
        "forager_industry": (linkedin.get("industry") or {}).get("name"),
        "forager_employees_range": org.get("employees_range"),
        "forager_keywords": _names(org.get("keywords")),
        "forager_legal_name": org.get("legal_name"),
        "forager_operating_status": org.get("operating_status"),
        "forager_logo_url": org.get("logo"),
        "forager_org_id": org.get("id"),
        "forager_enrichment_status": "enriched",
        "forager_enriched_at": now_iso(),
    })


# ---- contact ---------------------------------------------------------------

def current_role(person: dict[str, Any], preferred_org_id: int | None = None) -> dict[str, Any]:
    """Pick the person's current job, preferring the company we searched for."""
    roles = [role for role in person.get("roles") or [] if role.get("is_current")]
    if not roles:
        return {}
    for role in roles:
        if preferred_org_id and (role.get("organization") or {}).get("id") == preferred_org_id:
            return role
    return max(roles, key=lambda role: role.get("start_date") or "")


def contact_properties(
    person: dict[str, Any],
    role: dict[str, Any],
    email: str = "",
    phone: str = "",
) -> dict[str, Any]:
    """Build the HubSpot contact from a Forager person and their current role."""
    employer = role.get("organization") or {}
    location = split_location(person.get("location"))
    employer_linkedin = (employer.get("linkedin_info") or {}).get("public_profile_url")
    employer_name = employer.get("name") or role.get("organization_name") or ""
    # Tidy titles like "Head of Product at OpenAI" down to "Head of Product".
    title = role.get("role_title") or ""
    suffix = f" at {employer_name}".lower()
    if employer_name and title.lower().endswith(suffix):
        title = title[: -len(suffix)].strip()

    return _compact({
        "firstname": person.get("first_name"),
        "lastname": person.get("last_name"),
        "jobtitle": title,
        "email": email,
        "phone": phone,
        "city": location["city"],
        "state": location["state"],
        "country": location["country"],
        "company": employer_name,
        "company_domain": clean_domain(employer.get("domain")),
        "company_linkedin_url": employer_linkedin,
        "person_linkedin_url": (person.get("linkedin_info") or {}).get("public_profile_url"),
        # Nice-to-have extras, flagged for discussion in NOTES.md
        "forager_headline": person.get("headline"),
        "forager_about": (person.get("description") or "")[:MAX_TEXT],
        "forager_skills": _names(person.get("skills")),
        "forager_person_id": person.get("id"),
        "forager_enrichment_status": "enriched",
        "forager_enriched_at": now_iso(),
    })


def is_complete(properties: dict[str, Any]) -> bool:
    """True when a contact has every field the brief asks for."""
    required = (
        "firstname", "lastname", "jobtitle", "email", "phone",
        "city", "state", "country", "person_linkedin_url",
    )
    return all(properties.get(name) for name in required)
