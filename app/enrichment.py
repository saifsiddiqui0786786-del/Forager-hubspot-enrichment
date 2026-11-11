"""The enrichment workflows. This is the brain of the service.

Three building blocks:
  1. enrich_company  - fill a HubSpot company with Forager's company data
  2. enrich_contact  - fill a HubSpot contact with Forager's person data
  3. find_contacts   - discover new people at a company, filtered by job title

And the two webhook flows that combine them:
  - on_company_created: enrich the company, then enrich its contacts if it
    has any, otherwise find new ones.
  - on_contact_created: enrich that contact.
"""

from __future__ import annotations

import logging
from typing import Any

from app import mapping
from app.config import Settings
from app.forager import ForagerClient
from app.hubspot import HubSpotClient

logger = logging.getLogger(__name__)

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com",
}


class EnrichmentService:
    def __init__(
        self, settings: Settings, forager: ForagerClient, hubspot: HubSpotClient
    ) -> None:
        self._settings = settings
        self._forager = forager
        self._hubspot = hubspot

    # ---- 1. enrich a company --------------------------------------------

    def enrich_company(self, company_id: str) -> dict[str, Any] | None:
        """Look the company up in Forager and write the details to HubSpot.
        Returns the Forager organization, or None when there is no match."""
        company = self._hubspot.get_company(company_id)["properties"]
        domain = mapping.clean_domain(company.get("domain") or company.get("website"))
        org = self._forager.find_organization(domain=domain, name=company.get("name") or "")

        if not org:
            logger.info("Company %s: no Forager match", company_id)
            self._hubspot.update_company(company_id, {
                "forager_enrichment_status": "not_found",
                "forager_enriched_at": mapping.now_iso(),
            })
            return None

        self._hubspot.update_company(company_id, mapping.company_properties(org))
        logger.info("Company %s enriched as %s", company_id, org.get("name"))
        return org

    # ---- 2. enrich a contact --------------------------------------------

    def enrich_contact(self, contact_id: str, org_id: int | None = None) -> bool:
        """Find the person in Forager and write their details to HubSpot."""
        contact = self._hubspot.get_contact(contact_id)
        existing = contact["properties"]
        person = self._identify_person(contact)

        if not person:
            logger.info("Contact %s: no Forager match", contact_id)
            self._hubspot.update_contact(contact_id, {
                "forager_enrichment_status": "not_found",
                "forager_enriched_at": mapping.now_iso(),
            })
            return False

        role = mapping.current_role(person, preferred_org_id=org_id)
        # Keep an email that is already in HubSpot; only look one up if missing.
        email = "" if existing.get("email") else self._find_email(person)
        phone = self._find_phone(person)
        properties = mapping.contact_properties(person, role, email=email, phone=phone)
        self._hubspot.update_contact(contact_id, properties)
        logger.info("Contact %s enriched as %s", contact_id, person.get("full_name"))
        return True

    def _identify_person(self, contact: dict[str, Any]) -> dict[str, Any] | None:
        """Match a HubSpot contact to a Forager person, most reliable clue first:
        LinkedIn URL, then email, then name + company domain."""
        props = contact["properties"]

        identifier = mapping.linkedin_identifier(props.get("person_linkedin_url"))
        if identifier:
            person = self._forager.person_detail(linkedin_identifier=identifier)
            if person:
                return person

        email = (props.get("email") or "").strip().lower()
        if email:
            person = self._forager.person_by_email(email)
            if person:
                return person

        name = " ".join(p for p in (props.get("firstname"), props.get("lastname")) if p)
        domain = self._contact_domain(contact, email)
        if name and domain:
            matches = self._forager.search_people(domain=domain, person_name=name)
            if matches:
                return self._forager.person_detail(person_id=matches[0]["person"]["id"])
        return None

    def _contact_domain(self, contact: dict[str, Any], email: str) -> str:
        """Work out which company a contact belongs to."""
        props = contact["properties"]
        if props.get("company_domain"):
            return mapping.clean_domain(props["company_domain"])
        if "@" in email and email.split("@")[1] not in FREE_EMAIL_DOMAINS:
            return email.split("@")[1]
        linked = ((contact.get("associations") or {}).get("companies") or {}).get("results") or []
        if linked:
            company = self._hubspot.get_company(str(linked[0]["id"]))["properties"]
            return mapping.clean_domain(company.get("domain") or company.get("website"))
        return ""

    def _find_email(self, person: dict[str, Any]) -> str:
        """Work email first, personal email as the fallback."""
        identifier = (person.get("linkedin_info") or {}).get("public_identifier") or ""
        email = self._forager.work_email(identifier)
        if not email:
            personal = self._forager.personal_emails(person["id"])
            email = personal[0] if personal else ""
        return email

    def _find_phone(self, person: dict[str, Any]) -> str:
        phones = self._forager.phone_numbers(person["id"])
        return phones[0] if phones else ""

    # ---- 3. find new contacts -------------------------------------------

    def find_contacts(
        self,
        company_id: str,
        org: dict[str, Any],
        job_titles: list[str] | None = None,
        limit: int | None = None,
        only_complete: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Discover people at a company and create them in HubSpot.

        `job_titles` controls who is surfaced, e.g. ["Head of Sales", "VP Sales"].
        With `only_complete`, a person is saved only if they have an email, a
        phone and a full location, so every record created is demo-ready.
        """
        job_titles = job_titles or self._settings.default_job_titles
        limit = limit or self._settings.default_contact_limit
        if only_complete is None:
            only_complete = self._settings.only_complete_contacts

        # People already in HubSpot for this company are skipped, so a repeat
        # search finds new people instead of paying for the same ones again.
        known_urls = self._hubspot.company_contact_linkedin_urls(company_id)
        company_domain = mapping.clean_domain(org.get("domain"))

        created: list[dict[str, Any]] = []
        seen_people: set[int] = set()
        examined = 0
        page = 1

        while len(created) < limit and examined < self._settings.max_candidates:
            results = self._forager.search_people(
                organization_id=org["id"], job_titles=job_titles, page=page
            )
            if not results:
                break
            for result in results:
                person = result["person"]
                if person["id"] in seen_people:
                    continue
                seen_people.add(person["id"])
                # Forager's title search is fuzzy; enforce the filter strictly.
                if not mapping.title_matches(result.get("role_title"), job_titles):
                    continue
                profile_url = (person.get("linkedin_info") or {}).get("public_profile_url") or ""
                if profile_url.lower().rstrip("/") in known_urls:
                    continue
                examined += 1

                properties = self._build_new_contact(result, only_complete, company_domain)
                if properties:
                    contact_id = self._hubspot.upsert_contact(properties, company_id)
                    created.append({
                        "contact_id": contact_id,
                        "name": person.get("full_name"),
                        "job_title": properties.get("jobtitle"),
                        "complete": mapping.is_complete(properties),
                    })
                    logger.info("Created contact %s (%s)", person.get("full_name"),
                                properties.get("jobtitle"))
                if len(created) >= limit or examined >= self._settings.max_candidates:
                    break
            page += 1

        logger.info(
            "Company %s: created %d contacts for titles %s (examined %d people)",
            company_id, len(created), job_titles, examined,
        )
        return created

    def _build_new_contact(
        self, result: dict[str, Any], only_complete: bool, company_domain: str = ""
    ) -> dict[str, Any] | None:
        """Turn one search result into HubSpot properties.
        Returns None when the person is skipped for being incomplete.

        The checks run cheapest first to protect credits: location is free,
        an email costs 5 credits and a phone 15, and Forager charges for
        those two only when it actually finds one.
        """
        person = result["person"]
        role = {"role_title": result.get("role_title"), "organization": result["organization"]}

        if only_complete:
            location = mapping.split_location(person.get("location"))
            if not all(location.values()) or not person.get("last_name"):
                return None

        if only_complete:
            # Anyone can list a company on their LinkedIn profile. A verified
            # work email on the company's own domain proves they work there.
            identifier = (person.get("linkedin_info") or {}).get("public_identifier") or ""
            email = self._forager.work_email(identifier)
            if not email or (company_domain and not email.lower().endswith("@" + company_domain)):
                return None
        else:
            email = self._find_email(person)

        phone = self._find_phone(person)
        if only_complete and not phone:
            return None

        return mapping.contact_properties(person, role, email=email, phone=phone)

    # ---- webhook flows ---------------------------------------------------

    def on_company_created(self, company_id: str) -> dict[str, Any]:
        org = self.enrich_company(company_id)
        org_id = org["id"] if org else None

        existing_contacts = self._hubspot.company_contact_ids(company_id)
        if existing_contacts:
            # Contacts that were enriched before (for example by the contact
            # webhook a moment ago) are left alone, so no credit is spent twice.
            enriched = 0
            for contact_id in existing_contacts:
                contact = self._hubspot.get_contact(contact_id)
                if not contact["properties"].get("forager_enriched_at"):
                    enriched += self.enrich_contact(contact_id, org_id)
            return {"company_enriched": bool(org), "contacts_enriched": enriched}

        if not org:
            return {"company_enriched": False, "contacts_created": 0}

        company = self._hubspot.get_company(company_id)["properties"]
        title_filter = company.get("forager_job_title_filter") or ""
        job_titles = [t.strip() for t in title_filter.split(",") if t.strip()]
        created = self.find_contacts(company_id, org, job_titles=job_titles or None)
        return {"company_enriched": True, "contacts_created": len(created)}

    def on_contact_created(self, contact_id: str) -> dict[str, Any]:
        contact = self._hubspot.get_contact(contact_id)
        # Contacts this service created are already enriched. Skipping them
        # stops the "we create a contact -> webhook fires -> we enrich again" loop.
        if contact["properties"].get("forager_enriched_at"):
            return {"skipped": "already enriched"}
        return {"contact_enriched": self.enrich_contact(contact_id)}
