"""Web server: receives HubSpot webhooks and exposes manual trigger endpoints.

Endpoints
  GET  /health                  - is the service up and configured
  POST /webhooks/hubspot        - HubSpot calls this on company / contact creation
  POST /enrich/company/{id}     - manually enrich one company
  POST /enrich/contact/{id}     - manually enrich one contact
  POST /find-contacts           - find people at a company, filtered by job title
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.enrichment import EnrichmentService
from app.forager import ForagerClient
from app.hubspot import HubSpotClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("app")

app = FastAPI(title="Forager x HubSpot Enrichment", version="1.0.0")

SIGNATURE_MAX_AGE_SECONDS = 300
COMPANY_TYPE_ID = "0-2"
CONTACT_TYPE_ID = "0-1"

# HubSpot can deliver the same event more than once. Remember recent ones.
_seen_events: OrderedDict[str, float] = OrderedDict()
_SEEN_EVENTS_MAX = 2000


def get_service(settings: Settings = Depends(get_settings)) -> EnrichmentService:
    missing = settings.missing()
    if missing:
        raise HTTPException(503, f"Missing environment variables: {', '.join(missing)}")
    return EnrichmentService(settings, ForagerClient(settings), HubSpotClient(settings))


def require_api_key(
    x_api_key: str = Header(default=""), settings: Settings = Depends(get_settings)
) -> None:
    """Manual endpoints are private: the caller must send the service API key."""
    expected = settings.service_api_key
    if not expected or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "Invalid or missing X-API-Key header")


# ---- health ----------------------------------------------------------------

@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    missing = settings.missing()
    return {"status": "ok" if not missing else "misconfigured", "missing": missing}


# ---- HubSpot webhook -------------------------------------------------------

def _is_valid_signature(request: Request, body: bytes, settings: Settings) -> bool:
    """Check HubSpot's v3 signature so nobody else can trigger enrichment.

    HubSpot signs: HTTP method + full URL + raw body + timestamp, using the
    app's client secret (HMAC-SHA256, base64 encoded).
    """
    signature = request.headers.get("x-hubspot-signature-v3", "")
    timestamp = request.headers.get("x-hubspot-request-timestamp", "")
    if not signature or not timestamp.isdigit() or not settings.hubspot_client_secret:
        return False
    if abs(time.time() * 1000 - int(timestamp)) > SIGNATURE_MAX_AGE_SECONDS * 1000:
        return False

    # Behind Railway's proxy the app sees http://, but HubSpot signed https://.
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    query = f"?{request.url.query}" if request.url.query else ""
    url = f"{scheme}://{host}{request.url.path}{query}"

    source = f"{request.method}{url}{body.decode('utf-8')}{timestamp}".encode()
    digest = hmac.new(settings.hubspot_client_secret.encode(), source, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def _is_new_event(event: dict[str, Any]) -> bool:
    key = str(event.get("eventId") or f"{event.get('subscriptionType')}:{event.get('objectId')}")
    if key in _seen_events:
        return False
    _seen_events[key] = time.time()
    while len(_seen_events) > _SEEN_EVENTS_MAX:
        _seen_events.popitem(last=False)
    return True


def _event_kind(event: dict[str, Any]) -> str:
    """Return 'company', 'contact' or '' for events we do not handle.
    Covers both HubSpot event formats (company.creation and object.creation)."""
    subscription = str(event.get("subscriptionType", ""))
    if not subscription.endswith(".creation"):
        return ""
    if subscription == "company.creation" or event.get("objectTypeId") == COMPANY_TYPE_ID:
        return "company"
    if subscription == "contact.creation" or event.get("objectTypeId") == CONTACT_TYPE_ID:
        return "contact"
    return ""


def _process_event(service: EnrichmentService, kind: str, object_id: str) -> None:
    """Runs in the background, after HubSpot already got its 200 reply."""
    try:
        if kind == "company":
            result = service.on_company_created(object_id)
        else:
            result = service.on_contact_created(object_id)
        logger.info("Webhook %s %s done: %s", kind, object_id, result)
    except Exception:
        logger.exception("Webhook %s %s failed", kind, object_id)


@app.post("/webhooks/hubspot")
async def hubspot_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    service: EnrichmentService = Depends(get_service),
) -> dict[str, int]:
    body = await request.body()
    if not settings.allow_unsigned_webhooks and not _is_valid_signature(request, body, settings):
        raise HTTPException(401, "Invalid HubSpot signature")

    try:
        events = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Body is not valid JSON") from exc
    if isinstance(events, dict):
        events = [events]

    accepted = 0
    for event in events:
        kind = _event_kind(event)
        if not kind or not event.get("objectId") or not _is_new_event(event):
            continue
        # Enrichment takes a while, and HubSpot expects an answer within
        # 5 seconds, so the real work happens after we reply.
        background_tasks.add_task(_process_event, service, kind, str(event["objectId"]))
        accepted += 1

    logger.info("Webhook received: %d events, %d accepted", len(events), accepted)
    return {"received": len(events), "accepted": accepted}


# ---- manual endpoints ------------------------------------------------------

class FindContactsRequest(BaseModel):
    company_id: str = Field(description="HubSpot company id")
    job_titles: list[str] = Field(
        default_factory=list,
        description='Titles to look for, e.g. ["Head of Sales", "VP Sales"]',
    )
    limit: int = Field(default=5, ge=1, le=25)
    only_complete: bool = Field(
        default=True, description="Only save people with email, phone and full location"
    )


@app.post("/enrich/company/{company_id}", dependencies=[Depends(require_api_key)])
def enrich_company(
    company_id: str, service: EnrichmentService = Depends(get_service)
) -> dict[str, Any]:
    org = service.enrich_company(company_id)
    return {"company_id": company_id, "enriched": bool(org), "name": (org or {}).get("name")}


@app.post("/enrich/contact/{contact_id}", dependencies=[Depends(require_api_key)])
def enrich_contact(
    contact_id: str, service: EnrichmentService = Depends(get_service)
) -> dict[str, Any]:
    return {"contact_id": contact_id, "enriched": service.enrich_contact(contact_id)}


@app.post("/find-contacts", dependencies=[Depends(require_api_key)])
def find_contacts(
    payload: FindContactsRequest, service: EnrichmentService = Depends(get_service)
) -> dict[str, Any]:
    org = service.enrich_company(payload.company_id)
    if not org:
        raise HTTPException(404, "Company not found in Forager")
    created = service.find_contacts(
        payload.company_id, org,
        job_titles=payload.job_titles or None,
        limit=payload.limit,
        only_complete=payload.only_complete,
    )
    return {"company": org.get("name"), "created": created}
