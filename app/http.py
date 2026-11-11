"""Shared HTTP helper: one request function with retries for both APIs."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


class ApiError(Exception):
    """An API answered with an error we cannot recover from."""

    def __init__(self, service: str, status_code: int, body: str) -> None:
        super().__init__(f"{service} API error {status_code}: {body[:500]}")
        self.service = service
        self.status_code = status_code
        self.body = body


def request_json(
    client: httpx.Client,
    service: str,
    method: str,
    url: str,
    **kwargs: Any,
) -> Any:
    """Send a request and return the parsed JSON body (None if the body is empty).

    Rate limits (429) and temporary server errors are retried with a growing
    pause, honouring the `Retry-After` header when the API sends one.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if attempt == MAX_ATTEMPTS:
                raise ApiError(service, 0, f"network error: {exc}") from exc
            time.sleep(2**attempt)
            continue

        if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
            retry_after = response.headers.get("Retry-After", "")
            pause = float(retry_after) if retry_after.isdigit() else float(2**attempt)
            logger.warning(
                "%s %s %s returned %s, retrying in %.0fs",
                service, method, url, response.status_code, pause,
            )
            time.sleep(min(pause, 30))
            continue

        if response.status_code >= 400:
            raise ApiError(service, response.status_code, response.text)

        if not response.content.strip():
            return None
        return response.json()

    raise ApiError(service, 0, "retries exhausted")
