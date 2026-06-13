"""Tests for the PageCrawl integration setup/unload lifecycle + webhook + push."""

from __future__ import annotations

import hmac
import json
import time
from hashlib import sha256
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pagecrawl.const import (
    CONF_HOOK_ID,
    CONF_SIGNING_SECRET,
    CONF_WEBHOOK_ID,
    CONF_WORKSPACE_ID,
    DEFAULT_CLIENT_ID,
    DOMAIN,
    EVENT_CHANGE,
)

from .conftest import SIGNING_SECRET, USER_ID, WORKSPACE_ID


def _build_client(pages: list[dict[str, Any]]) -> MagicMock:
    client = MagicMock()
    client.workspace_id = WORKSPACE_ID
    client._base_url = "https://pagecrawl.io"
    client.async_list_pages = AsyncMock(return_value=pages)
    client.async_get_user = AsyncMock(return_value={"id": USER_ID})
    client.async_check_now = AsyncMock(return_value=None)
    client.async_track_page = AsyncMock(return_value={"id": 1})
    client.async_create_hook = AsyncMock(
        return_value={"id": 555, "signing_secret": SIGNING_SECRET}
    )
    client.async_delete_hook = AsyncMock(return_value=None)
    return client


def _patches(client: MagicMock):
    return (
        patch(
            "custom_components.pagecrawl.PageCrawlClient", return_value=client
        ),
        patch(
            "custom_components.pagecrawl.config_entry_oauth2_flow."
            "async_get_config_entry_implementation",
            AsyncMock(),
        ),
        patch(
            "custom_components.pagecrawl.config_entry_oauth2_flow.OAuth2Session",
            MagicMock(),
        ),
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock):
    entry.add_to_hass(hass)
    p1, p2, p3 = _patches(client)
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _entry(update_mode: str) -> MockConfigEntry:
    import time as _t

    return MockConfigEntry(
        domain=DOMAIN,
        title="Acme Workspace",
        unique_id=f"{USER_ID}:{WORKSPACE_ID}",
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "a",
                "refresh_token": "r",
                "expires_at": _t.time() + 3600,
                "token_type": "Bearer",
            },
            CONF_WORKSPACE_ID: WORKSPACE_ID,
        },
        options={"update_mode": update_mode, "scan_interval": 900},
    )


# --- async_setup: services registration ----------------------------------


async def test_async_setup_registers_services(hass: HomeAssistant) -> None:
    """async_setup registers the global services.

    The built-in OAuth client is imported by the config flow, not here, so
    async_setup only needs to register services.
    """
    from custom_components.pagecrawl import async_setup

    assert await async_setup(hass, {})
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "check_now")
    assert hass.services.has_service(DOMAIN, "track_page")
    # The default client id is the baked-in UUID.
    assert DEFAULT_CLIENT_ID == "9f1d6c2e-1a2b-4c3d-8e5f-0a1b2c3d4e5f"


# --- update modes ---------------------------------------------------------


async def test_setup_poll_mode_no_push(
    hass: HomeAssistant, sample_pages
) -> None:
    """Polling-only mode does not create a PageCrawl hook."""
    entry = _entry("poll")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)

    assert entry.state is ConfigEntryState.LOADED
    client.async_create_hook.assert_not_called()
    assert CONF_HOOK_ID not in entry.data


async def test_setup_push_mode_creates_hook(
    hass: HomeAssistant, sample_pages
) -> None:
    """Push mode registers an HA webhook and creates the PageCrawl hook."""
    assert await async_setup_component(hass, "webhook", {})
    entry = _entry("push")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)

    assert entry.state is ConfigEntryState.LOADED
    client.async_create_hook.assert_awaited_once()
    assert entry.data[CONF_HOOK_ID] == 555
    assert entry.data[CONF_SIGNING_SECRET] == SIGNING_SECRET
    assert entry.data.get(CONF_WEBHOOK_ID)


async def test_setup_auto_mode_creates_hook(
    hass: HomeAssistant, sample_pages
) -> None:
    """Auto mode also enables push when possible."""
    assert await async_setup_component(hass, "webhook", {})
    entry = _entry("auto")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)

    assert entry.state is ConfigEntryState.LOADED
    client.async_create_hook.assert_awaited_once()


async def test_switch_push_to_poll_tears_down_hook(
    hass: HomeAssistant, sample_pages
) -> None:
    """Reloading in poll mode tears down a previously-created hook."""
    assert await async_setup_component(hass, "webhook", {})
    entry = _entry("push")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)
    assert entry.data[CONF_HOOK_ID] == 555

    # Switch to polling-only; the update listener reloads the entry.
    p1, p2, p3 = _patches(client)
    with p1, p2, p3:
        hass.config_entries.async_update_entry(
            entry, options={"update_mode": "poll", "scan_interval": 900}
        )
        await hass.async_block_till_done()

    client.async_delete_hook.assert_awaited_with(555)
    assert CONF_HOOK_ID not in entry.data
    assert CONF_SIGNING_SECRET not in entry.data


async def test_unload_entry(hass: HomeAssistant, sample_pages) -> None:
    """Unloading an entry succeeds and unloads platforms."""
    entry = _entry("poll")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_remove_entry_deletes_hook(
    hass: HomeAssistant, sample_pages
) -> None:
    """Removing a push entry deletes the server-side hook."""
    assert await async_setup_component(hass, "webhook", {})
    entry = _entry("push")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)
    assert entry.data[CONF_HOOK_ID] == 555

    p1, p2, p3 = _patches(client)
    with p1, p2, p3:
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

    client.async_delete_hook.assert_awaited()


async def test_diagnostics_includes_monitors_after_setup(
    hass: HomeAssistant, sample_pages
) -> None:
    """Diagnostics summarise the coordinator's monitors when loaded."""
    from custom_components.pagecrawl.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = _entry("poll")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["coordinator"]["monitor_count"] == 1
    monitor = diag["monitors"][0]
    assert monitor["id"] == 1001
    assert monitor["element_count"] == len(sample_pages[0]["elements"])
    assert "price" in monitor["element_types"]


async def test_setup_first_refresh_failure_not_ready(
    hass: HomeAssistant,
) -> None:
    """A failing first refresh leaves the entry in SETUP_RETRY."""
    from custom_components.pagecrawl.api import PageCrawlApiError

    entry = _entry("poll")
    client = _build_client([])
    client.async_list_pages = AsyncMock(side_effect=PageCrawlApiError("down"))
    entry.add_to_hass(hass)
    p1, p2, p3 = _patches(client)
    with p1, p2, p3:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


# --- webhook handler end-to-end (HMAC accept / reject) --------------------


def _sign(secret: str, ts: str, body: str) -> str:
    digest = hmac.new(
        secret.encode(), f"{ts}.{body}".encode(), sha256
    ).hexdigest()
    return f"sha256={digest}"


async def _post_webhook(
    hass_client_no_auth, webhook_id: str, body: str, headers: dict[str, str]
):
    client = await hass_client_no_auth()
    return await client.post(
        f"/api/webhook/{webhook_id}", data=body, headers=headers
    )


async def test_webhook_valid_signature_updates_and_fires(
    hass: HomeAssistant, hass_client_no_auth, sample_pages
) -> None:
    """A valid signed delivery updates state and fires pagecrawl_change."""
    assert await async_setup_component(hass, "webhook", {})
    entry = _entry("push")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)
    webhook_id = entry.data[CONF_WEBHOOK_ID]

    events: list[Any] = []
    hass.bus.async_listen(EVENT_CHANGE, lambda e: events.append(e))

    body = json.dumps(
        {
            "id": 1001,
            "contents": "77.00",
            "changed_at": "2026-06-13T15:00:00.000000Z",
        }
    )
    ts = str(int(time.time()))
    resp = await _post_webhook(
        hass_client_no_auth,
        webhook_id,
        body,
        {
            "X-PageCrawl-Signature": _sign(SIGNING_SECRET, ts, body),
            "X-PageCrawl-Timestamp": ts,
        },
    )
    assert resp.status == 200
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    assert coordinator.data[1001]["latest"]["contents"] == "77.00"
    assert len(events) == 1


async def test_webhook_bad_signature_rejected(
    hass: HomeAssistant, hass_client_no_auth, sample_pages
) -> None:
    """A tampered signature is rejected with 401 and state is unchanged."""
    assert await async_setup_component(hass, "webhook", {})
    entry = _entry("push")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)
    webhook_id = entry.data[CONF_WEBHOOK_ID]

    body = json.dumps({"id": 1001, "contents": "0.00"})
    ts = str(int(time.time()))
    resp = await _post_webhook(
        hass_client_no_auth,
        webhook_id,
        body,
        {
            "X-PageCrawl-Signature": "sha256=deadbeef",
            "X-PageCrawl-Timestamp": ts,
        },
    )
    assert resp.status == 401
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    assert coordinator.data[1001]["latest"]["contents"] == "100.00"


async def test_push_prefers_cloudhook_when_cloud_present(
    hass: HomeAssistant, sample_pages
) -> None:
    """When HA Cloud is loaded, the webhook registration creates a cloudhook."""
    assert await async_setup_component(hass, "webhook", {})
    # Pretend the cloud component is loaded.
    hass.config.components.add("cloud")

    entry = _entry("push")
    client = _build_client(sample_pages)

    cloud_url = "https://hooks.nabu.casa/ABCD"
    # The real homeassistant.components.cloud module is not importable in this
    # test env (transitive turbojpeg import), so inject a stub the webhook code
    # imports `async_create_cloudhook` from.
    import sys
    import types

    create_ch = AsyncMock(return_value=cloud_url)
    stub = types.ModuleType("homeassistant.components.cloud")
    stub.async_create_cloudhook = create_ch
    stub.async_delete_cloudhook = AsyncMock(return_value=None)
    with patch.dict(
        sys.modules, {"homeassistant.components.cloud": stub}
    ):
        await _setup(hass, entry, client)

    create_ch.assert_awaited_once()
    # The PageCrawl hook target is the cloudhook URL.
    target = client.async_create_hook.await_args.args[0]
    assert target == cloud_url
    from custom_components.pagecrawl.const import CONF_CLOUDHOOK_URL

    assert entry.data[CONF_CLOUDHOOK_URL] == cloud_url


async def test_webhook_missing_signature_rejected(
    hass: HomeAssistant, hass_client_no_auth, sample_pages
) -> None:
    """A delivery with no signature header is rejected."""
    assert await async_setup_component(hass, "webhook", {})
    entry = _entry("push")
    client = _build_client(sample_pages)
    await _setup(hass, entry, client)
    webhook_id = entry.data[CONF_WEBHOOK_ID]

    body = json.dumps({"id": 1001})
    resp = await _post_webhook(hass_client_no_auth, webhook_id, body, {})
    assert resp.status == 401
