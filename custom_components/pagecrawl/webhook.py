"""Webhook handling for PageCrawl push updates.

Registers an HA webhook (preferring a Nabu Casa cloudhook), verifies the
HMAC-SHA256 signature on inbound deliveries, and dispatches verified payloads
to the coordinator's `apply_webhook_update`.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from hashlib import sha256
from typing import Any

from aiohttp.hdrs import METH_POST
from aiohttp.web import Request, Response

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CLOUDHOOK_URL,
    CONF_SIGNING_SECRET,
    CONF_WEBHOOK_ID,
    DOMAIN,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    WEBHOOK_SIGNATURE_MAX_AGE,
)
from .coordinator import PageCrawlDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def verify_signature(
    secret: str,
    timestamp: str | None,
    body: str,
    header: str | None,
    max_age: int = WEBHOOK_SIGNATURE_MAX_AGE,
) -> bool:
    """Verify a PageCrawl outbound webhook signature.

    The signature is `sha256=<hex>` where hex = HMAC_SHA256(secret,
    "{timestamp}.{body}"). Rejects missing inputs, stale timestamps, and
    mismatches using a constant-time comparison.
    """
    if not secret or not header or not timestamp:
        return False

    # Reject stale deliveries to prevent replay.
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if max_age and abs(time.time() - ts) > max_age:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        sha256,
    ).hexdigest()

    provided = header
    if provided.startswith("sha256="):
        provided = provided[len("sha256=") :]

    return hmac.compare_digest(expected, provided)


async def async_register_webhook(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: PageCrawlDataUpdateCoordinator,
) -> tuple[str, str | None]:
    """Register the HA webhook and return (public_url, cloudhook_url).

    Prefers a Nabu Casa cloudhook when HA Cloud is available; otherwise uses
    the local externally-reachable URL. The returned public URL is what should
    be passed to PageCrawl's `POST /api/hooks` as the target.
    """
    webhook_id = entry.data.get(CONF_WEBHOOK_ID) or webhook.async_generate_id()

    webhook.async_register(
        hass,
        DOMAIN,
        f"PageCrawl ({entry.title})",
        webhook_id,
        _build_handler(entry, coordinator),
        allowed_methods=[METH_POST],
    )

    cloudhook_url: str | None = entry.data.get(CONF_CLOUDHOOK_URL)
    public_url: str | None = cloudhook_url

    if public_url is None and _cloud_available(hass):
        try:
            from homeassistant.components.cloud import async_create_cloudhook

            cloudhook_url = await async_create_cloudhook(hass, webhook_id)
            public_url = cloudhook_url
        except Exception as err:  # noqa: BLE001 - cloud optional/may fail
            _LOGGER.debug("Cloudhook creation failed, falling back: %s", err)

    if public_url is None:
        public_url = webhook.async_generate_url(hass, webhook_id)

    # Persist the webhook id (and cloudhook url) so reloads reuse them.
    new_data = {**entry.data, CONF_WEBHOOK_ID: webhook_id}
    if cloudhook_url is not None:
        new_data[CONF_CLOUDHOOK_URL] = cloudhook_url
    if new_data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=new_data)

    return public_url, cloudhook_url


async def async_unregister_webhook(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    delete_cloudhook: bool = False,
) -> None:
    """Unregister the HA webhook; optionally delete the cloudhook too."""
    webhook_id = entry.data.get(CONF_WEBHOOK_ID)
    if webhook_id:
        try:
            webhook.async_unregister(hass, webhook_id)
        except ValueError:
            # Already unregistered.
            pass

    if delete_cloudhook and entry.data.get(CONF_CLOUDHOOK_URL) and webhook_id:
        if _cloud_available(hass):
            try:
                from homeassistant.components.cloud import async_delete_cloudhook

                await async_delete_cloudhook(hass, webhook_id)
            except Exception as err:  # noqa: BLE001 - cloud optional
                _LOGGER.debug("Cloudhook deletion failed: %s", err)


def _cloud_available(hass: HomeAssistant) -> bool:
    """Return True when the Nabu Casa cloud component is loaded."""
    return "cloud" in hass.config.components


def _build_handler(
    entry: ConfigEntry, coordinator: PageCrawlDataUpdateCoordinator
):
    """Build the webhook request handler bound to this entry/coordinator."""

    async def _handle_webhook(
        hass: HomeAssistant, webhook_id: str, request: Request
    ) -> Response:
        """Verify and apply an inbound PageCrawl delivery."""
        body = await request.text()
        signature = request.headers.get(HEADER_SIGNATURE)
        timestamp = request.headers.get(HEADER_TIMESTAMP)
        secret = entry.data.get(CONF_SIGNING_SECRET, "")

        if not verify_signature(secret, timestamp, body, signature):
            _LOGGER.warning(
                "Rejected PageCrawl webhook with invalid/stale signature"
            )
            return Response(status=401)

        try:
            payload: dict[str, Any] = json.loads(body)
        except (ValueError, TypeError):
            _LOGGER.warning("Rejected PageCrawl webhook with invalid JSON")
            return Response(status=400)

        coordinator.apply_webhook_update(payload)
        return Response(status=200)

    return _handle_webhook
