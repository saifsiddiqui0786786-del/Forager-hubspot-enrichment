"""One-time setup: create the custom HubSpot properties this service writes to.

Run once per HubSpot account:   python -m app.setup_hubspot
It is safe to run again; properties that already exist are skipped.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.hubspot import HubSpotClient

GROUP_NAME = "forager_enrichment"
GROUP_LABEL = "Forager Enrichment"


def _text(name: str, label: str, long: bool = False) -> dict[str, str]:
    return {
        "name": name, "label": label, "groupName": GROUP_NAME,
        "type": "string", "fieldType": "textarea" if long else "text",
    }


def _number(name: str, label: str) -> dict[str, str]:
    return {
        "name": name, "label": label, "groupName": GROUP_NAME,
        "type": "number", "fieldType": "number",
    }


def _datetime(name: str, label: str) -> dict[str, str]:
    return {
        "name": name, "label": label, "groupName": GROUP_NAME,
        "type": "datetime", "fieldType": "date",
    }


COMPANY_PROPERTIES = [
    _text("forager_job_title_filter", "Job Title Filter (comma separated)"),
    _text("forager_industry", "Industry (Forager)"),
    _text("forager_employees_range", "Employee Range (Forager)"),
    _text("forager_keywords", "Keywords (Forager)", long=True),
    _text("forager_legal_name", "Legal Name (Forager)"),
    _text("forager_operating_status", "Operating Status"),
    _text("forager_logo_url", "Logo URL (Forager)"),
    _number("forager_org_id", "Forager Organization ID"),
    _text("forager_enrichment_status", "Forager Enrichment Status"),
    _datetime("forager_enriched_at", "Forager Enriched At"),
]

CONTACT_PROPERTIES = [
    _text("person_linkedin_url", "Person LinkedIn URL"),
    _text("company_domain", "Company Domain"),
    _text("company_linkedin_url", "Company LinkedIn URL"),
    _text("forager_headline", "LinkedIn Headline"),
    _text("forager_about", "LinkedIn About", long=True),
    _text("forager_skills", "Skills (Forager)", long=True),
    _number("forager_person_id", "Forager Person ID"),
    _text("forager_enrichment_status", "Forager Enrichment Status"),
    _datetime("forager_enriched_at", "Forager Enriched At"),
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    hubspot = HubSpotClient(get_settings())
    for object_type, definitions in (
        ("companies", COMPANY_PROPERTIES),
        ("contacts", CONTACT_PROPERTIES),
    ):
        hubspot.ensure_property_group(object_type, GROUP_NAME, GROUP_LABEL)
        for definition in definitions:
            created = hubspot.ensure_property(object_type, definition)
            state = "created" if created else "already exists"
            logging.info("%s.%s: %s", object_type, definition["name"], state)
    hubspot.close()


if __name__ == "__main__":
    main()
