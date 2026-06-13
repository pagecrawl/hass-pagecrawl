"""Tests for the PageCrawl data update coordinator and webhook verify."""

from __future__ import annotations

import hmac
import json
import time
from hashlib import sha256
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import HomeAssistant

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.pagecrawl.api import (
    PageCrawlApiError,
    PageCrawlAuthError,
    PageCrawlRateLimitError,
)
from custom_components.pagecrawl.const import EVENT_CHANGE, MIN_SCAN_INTERVAL
from custom_components.pagecrawl.coordinator import (
    PageCrawlDataUpdateCoordinator,
)
from custom_components.pagecrawl.webhook import verify_signature

from .conftest import SIGNING_SECRET


def _make_coordinator(
    hass: HomeAssistant,
    mock_config_entry,
    pages: list[dict[str, Any]],
) -> PageCrawlDataUpdateCoordinator:
    """Build a coordinator with a stubbed client returning `pages`."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.workspace_id = 7
    client.async_list_pages = AsyncMock(return_value=pages)
    client._base_url = "https://pagecrawl.io"
    return PageCrawlDataUpdateCoordinator(
        hass, mock_config_entry, client, update_interval=900
    )


async def test_parses_sample_payload(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """The sample payload is keyed by monitor id in coordinator.data."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert set(coordinator.data) == {1001}
    monitor = coordinator.data[1001]
    assert monitor["name"] == "Acme Widget"
    assert monitor["latest"]["contents"] == "100.00"
    # Element-level value is reachable via checks[0].elements.
    assert monitor["checks"][0]["elements"]["11"]["contents"] == "100.00"


async def test_fires_change_event_on_changed_at_advance(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """pagecrawl_change fires when latest.changed_at advances, not on seed."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)

    events: list[Any] = []
    hass.bus.async_listen(EVENT_CHANGE, lambda e: events.append(e))

    # First refresh seeds the state; no event.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert events == []

    # Advance changed_at and refresh again.
    advanced = [dict(sample_pages[0])]
    advanced[0]["latest"] = {
        **sample_pages[0]["latest"],
        "changed_at": "2026-06-13T11:30:00.000000Z",
        "contents": "95.00",
    }
    coordinator.client.async_list_pages = AsyncMock(return_value=advanced)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data["monitor_id"] == 1001
    assert data["changed_at"] == "2026-06-13T11:30:00.000000Z"
    assert data["contents"] == "95.00"
    assert data["slug"] == "acme-widget"


async def test_change_event_enriches_ai_data_poll_shape(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """The event resolves ai_summary/score from the poll shape (checks[0])."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)

    events: list[Any] = []
    hass.bus.async_listen(EVENT_CHANGE, lambda e: events.append(e))

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    advanced = [dict(sample_pages[0])]
    advanced[0]["latest"] = {
        **sample_pages[0]["latest"],
        "changed_at": "2026-06-13T11:30:00.000000Z",
    }
    coordinator.client.async_list_pages = AsyncMock(return_value=advanced)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data["status"] == sample_pages[0].get("status")
    assert data["ai_summary"] == "The widget price dropped by 5 percent."
    assert data["ai_priority_score"] == 72.0


async def test_change_event_enriches_ai_data_push_shape(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """A pushed update resolves ai_summary/score from latest (push shape)."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    events: list[Any] = []
    hass.bus.async_listen(EVENT_CHANGE, lambda e: events.append(e))

    coordinator.apply_webhook_update(
        {
            "id": 1001,
            "status": "ok",
            "changed_at": "2026-06-13T12:00:00.000000Z",
            "ai_summary": "Pushed AI summary from latest.",
            "ai_priority_score": 91,
        }
    )
    await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data["status"] == "ok"
    assert data["ai_summary"] == "Pushed AI summary from latest."
    assert data["ai_priority_score"] == 91.0


async def test_change_event_ai_data_none_when_absent(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """ai_summary/ai_priority_score are None when no AI data is present."""
    page = {
        "id": 1001,
        "slug": "acme-widget",
        "status": "ok",
        "latest": {"changed_at": "2026-06-13T10:00:00.000000Z"},
    }
    coordinator = _make_coordinator(hass, mock_config_entry, [page])

    events: list[Any] = []
    hass.bus.async_listen(EVENT_CHANGE, lambda e: events.append(e))

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    advanced = [
        {
            "id": 1001,
            "slug": "acme-widget",
            "status": "ok",
            "latest": {"changed_at": "2026-06-13T11:00:00.000000Z"},
        }
    ]
    coordinator.client.async_list_pages = AsyncMock(return_value=advanced)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    assert data["ai_summary"] is None
    assert data["ai_priority_score"] is None


async def test_no_event_when_changed_at_stable(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """No event fires when changed_at does not advance."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    events: list[Any] = []
    hass.bus.async_listen(EVENT_CHANGE, lambda e: events.append(e))

    await coordinator.async_refresh()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert events == []


async def test_apply_webhook_update_updates_state_and_fires(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """A pushed update merges into data and fires the change event."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    events: list[Any] = []
    hass.bus.async_listen(EVENT_CHANGE, lambda e: events.append(e))

    coordinator.apply_webhook_update(
        {
            "id": 1001,
            "status": "ok",
            "contents": "88.00",
            "difference": -12.0,
            "human_difference": "Price dropped 12%",
            "changed_at": "2026-06-13T12:00:00.000000Z",
        }
    )
    await hass.async_block_till_done()

    assert coordinator.data[1001]["latest"]["contents"] == "88.00"
    assert coordinator.data[1001]["latest"]["changed_at"] == (
        "2026-06-13T12:00:00.000000Z"
    )
    assert len(events) == 1
    assert events[0].data["contents"] == "88.00"


async def test_apply_webhook_update_merges_elements_by_tracked_id(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """Pushed page_elements update checks[0].elements keyed by TrackedElement id."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    monitor_id = next(iter(coordinator.data))
    elements = coordinator.data[monitor_id]["elements"]
    # Pick a non-default tracked element to prove multi-element push works.
    tracked_id = elements[-1]["id"]

    coordinator.apply_webhook_update(
        {
            "id": monitor_id,
            "changed_at": "2026-06-13T12:30:00.000000Z",
            "page_elements": [
                {
                    "id": 999999,  # ChangeHistory id (legacy, ignored for keying)
                    "element_id": tracked_id,
                    "contents": "PUSHED-VALUE",
                    "difference": 7.0,
                    "changed": True,
                }
            ],
        }
    )
    await hass.async_block_till_done()

    merged = coordinator.data[monitor_id]["checks"][0]["elements"][str(tracked_id)]
    assert merged["contents"] == "PUSHED-VALUE"
    assert merged["difference"] == 7.0
    assert merged["changed"] is True


async def test_apply_webhook_update_unknown_monitor_refreshes(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """A push for an unknown monitor triggers a coordinator refresh."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    coordinator.client.async_list_pages.reset_mock()
    coordinator.apply_webhook_update({"id": 2002, "contents": "x"})
    await hass.async_block_till_done()

    assert coordinator.client.async_list_pages.called


async def test_auth_error_triggers_reauth(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """A 401/403 surfaces as ConfigEntryAuthFailed (triggers reauth)."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    coordinator.client.async_list_pages = AsyncMock(
        side_effect=PageCrawlAuthError("nope")
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_generic_api_error_is_update_failed(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """A generic API error becomes UpdateFailed."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    coordinator.client.async_list_pages = AsyncMock(
        side_effect=PageCrawlApiError("boom")
    )
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_rate_limit_backoff_and_restore(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """A 429 backs off the interval; a later success restores it."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    base = coordinator.update_interval

    coordinator.client.async_list_pages = AsyncMock(
        side_effect=PageCrawlRateLimitError("slow down", retry_after=1234)
    )
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.update_interval.total_seconds() == 1234
    assert coordinator.update_interval != base

    # Recover.
    coordinator.client.async_list_pages = AsyncMock(return_value=sample_pages)
    await coordinator._async_update_data()
    assert coordinator.update_interval == base


async def test_rate_limit_backoff_without_retry_after_doubles(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """Without Retry-After, the interval doubles the base."""
    coordinator = _make_coordinator(hass, mock_config_entry, sample_pages)
    base_seconds = coordinator.update_interval.total_seconds()
    coordinator.client.async_list_pages = AsyncMock(
        side_effect=PageCrawlRateLimitError("slow down", retry_after=None)
    )
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.update_interval.total_seconds() == base_seconds * 2


async def test_min_scan_interval_enforced(
    hass: HomeAssistant, mock_config_entry, sample_pages
) -> None:
    """The coordinator never polls faster than the floor."""
    mock_config_entry.add_to_hass(hass)
    client = MagicMock()
    client.workspace_id = 7
    client.async_list_pages = AsyncMock(return_value=sample_pages)
    client._base_url = "https://pagecrawl.io"
    coordinator = PageCrawlDataUpdateCoordinator(
        hass, mock_config_entry, client, update_interval=1
    )
    assert coordinator.update_interval.total_seconds() == MIN_SCAN_INTERVAL


async def test_skips_pages_without_id(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Pages missing an id are skipped, not crashed on."""
    coordinator = _make_coordinator(
        hass,
        mock_config_entry,
        [{"name": "no id"}, {"id": 5, "name": "ok"}],
    )
    await coordinator.async_refresh()
    assert set(coordinator.data) == {5}


# --- HMAC signature verification -----------------------------------------


def _sign(secret: str, timestamp: str, body: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.{body}".encode("utf-8"),
        sha256,
    ).hexdigest()
    return f"sha256={digest}"


def test_verify_signature_accepts_valid() -> None:
    """A correctly signed, fresh delivery verifies."""
    body = json.dumps({"id": 1001})
    ts = str(int(time.time()))
    header = _sign(SIGNING_SECRET, ts, body)
    assert verify_signature(SIGNING_SECRET, ts, body, header) is True


def test_verify_signature_accepts_without_prefix() -> None:
    """The bare hex (no sha256= prefix) is also accepted."""
    body = json.dumps({"id": 1001})
    ts = str(int(time.time()))
    header = _sign(SIGNING_SECRET, ts, body).removeprefix("sha256=")
    assert verify_signature(SIGNING_SECRET, ts, body, header) is True


def test_verify_signature_rejects_bad_signature() -> None:
    """A tampered signature is rejected."""
    body = json.dumps({"id": 1001})
    ts = str(int(time.time()))
    assert verify_signature(SIGNING_SECRET, ts, body, "sha256=deadbeef") is False


def test_verify_signature_rejects_tampered_body() -> None:
    """Signing one body but sending another is rejected."""
    ts = str(int(time.time()))
    header = _sign(SIGNING_SECRET, ts, json.dumps({"id": 1001}))
    assert (
        verify_signature(SIGNING_SECRET, ts, json.dumps({"id": 9999}), header)
        is False
    )


def test_verify_signature_rejects_stale_timestamp() -> None:
    """A delivery older than the max age window is rejected."""
    body = json.dumps({"id": 1001})
    ts = str(int(time.time()) - 10_000)
    header = _sign(SIGNING_SECRET, ts, body)
    assert verify_signature(SIGNING_SECRET, ts, body, header) is False


def test_verify_signature_rejects_missing_inputs() -> None:
    """Missing secret/header/timestamp all reject."""
    body = "{}"
    ts = str(int(time.time()))
    assert verify_signature("", ts, body, "sha256=x") is False
    assert verify_signature(SIGNING_SECRET, ts, body, None) is False
    assert verify_signature(SIGNING_SECRET, None, body, "sha256=x") is False
