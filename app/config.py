"""Application settings, loaded from environment variables.

Nothing sensitive is hard-coded. Locally the values come from a `.env` file,
on Railway they come from the service's Variables tab.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _get_list(name: str, default: str = "") -> list[str]:
    """Read a comma separated variable into a clean list."""
    return [item.strip() for item in _get(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Forager
    forager_api_key: str = field(default_factory=lambda: _get("FORAGER_API_KEY"))
    forager_account_id: str = field(default_factory=lambda: _get("FORAGER_ACCOUNT_ID"))
    forager_base_url: str = field(
        default_factory=lambda: _get("FORAGER_BASE_URL", "https://api-v2.forager.ai")
    )
    forager_phone_quality_tier: str = field(
        default_factory=lambda: _get("FORAGER_PHONE_QUALITY_TIER", "most_coverage")
    )

    # HubSpot
    hubspot_access_token: str = field(default_factory=lambda: _get("HUBSPOT_ACCESS_TOKEN"))
    hubspot_client_secret: str = field(default_factory=lambda: _get("HUBSPOT_CLIENT_SECRET"))
    hubspot_base_url: str = field(
        default_factory=lambda: _get("HUBSPOT_BASE_URL", "https://api.hubapi.com")
    )
    # Only for local testing. In production every webhook must be signed.
    allow_unsigned_webhooks: bool = field(
        default_factory=lambda: _get_bool("ALLOW_UNSIGNED_WEBHOOKS", False)
    )

    # Protects the manual /enrich and /find-contacts endpoints.
    service_api_key: str = field(default_factory=lambda: _get("SERVICE_API_KEY"))

    # Contact finding defaults, used when a company has no filter of its own.
    default_job_titles: list[str] = field(
        default_factory=lambda: _get_list(
            "DEFAULT_JOB_TITLES", "Chief,VP,Vice President,Head of,Director"
        )
    )
    default_contact_limit: int = field(
        default_factory=lambda: int(_get("DEFAULT_CONTACT_LIMIT", "5"))
    )
    # Upper bound on how many people we look at while hunting for complete records.
    max_candidates: int = field(default_factory=lambda: int(_get("MAX_CANDIDATES", "25")))
    # When true, only people with email + phone + city/state/country are saved.
    only_complete_contacts: bool = field(
        default_factory=lambda: _get_bool("ONLY_COMPLETE_CONTACTS", True)
    )

    def missing(self) -> list[str]:
        """Names of required variables that are not set."""
        required = {
            "FORAGER_API_KEY": self.forager_api_key,
            "FORAGER_ACCOUNT_ID": self.forager_account_id,
            "HUBSPOT_ACCESS_TOKEN": self.hubspot_access_token,
        }
        return [name for name, value in required.items() if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
