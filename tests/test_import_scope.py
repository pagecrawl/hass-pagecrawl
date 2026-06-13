"""Tests for the import-scope selection feature.

Covers the config flow import step, the options flow, the coordinator's
per-mode filtering, and device pruning when the in-scope set shrinks.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import homeassistant.helpers.device_registry as dr
from homeassistant.helpers import config_entry_oauth2_flow

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.pagecrawl.const import (
    CONF_FOLDERS,
    CONF_IMPORT_MODE,
    CONF_MONITORS,
    CONF_SCAN_INTERVAL,
    CONF_UPDATE_MODE,
    CONF_WORKSPACE_ID,
    DEFAULT_BASE_URL,
    DOMAIN,
    EVENT_CHANGE,
    IMPORT_MODE_ALL,
    IMPORT_MODE_FOLDERS,
    IMPORT_MODE_MONITORS,
    OAUTH_TOKEN_PATH,
)
from custom_components.pagecrawl.coordinator import (
    PageCrawlDataUpdateCoordinator,
)

from .conftest import USER_ID, WORKSPACE_ID

REDIRECT_URI = "https://example.com/auth/external/callback"
TOKEN_URL = f"{DEFAULT_BASE_URL}{OAUTH_TOKEN_PATH}"


# ---------------------------------------------------------------------------
# Config flow: import step
# ---------------------------------------------------------------------------


@pytest.fixture
def _bypass_setup() -> Any:
    """Don't actually set up the entry during config-flow tests.

    NOT autouse: the options-flow tests need the real async_setup_entry so the
    coordinator (and runtime_data) is built and the reload actually re-scopes.
    """
    with patch(
        "custom_components.pagecrawl.async_setup_entry", return_value=True
    ):
        yield


async def _start_oauth(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
) -> dict[str, Any]:
    """Drive the OAuth handshake up to (and including) the token exchange."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {"flow_id": result["flow_id"], "redirect_uri": REDIRECT_URI},
    )
    assert result["type"] == FlowResultType.EXTERNAL_STEP

    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200

    aioclient_mock.post(
        TOKEN_URL,
        json={
            "refresh_token": "mock-refresh-token",
            "access_token": "mock-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "integration",
        },
    )
    return result


async def _advance_to_import_step(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    user_payload_single,
    sample_folders,
    multi_folder_pages,
) -> dict[str, Any]:
    """OAuth -> single workspace auto-pick -> import_options form."""
    result = await _start_oauth(hass, hass_client_no_auth, aioclient_mock)

    with (
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_get_user",
            AsyncMock(return_value=user_payload_single),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_folders",
            AsyncMock(return_value=sample_folders),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_pages",
            AsyncMock(return_value=multi_folder_pages["all"]),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"]
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "import_options"
    return result


async def test_import_step_appears_and_selectors_populated(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    _bypass_setup,
    user_payload_single,
    sample_folders,
    multi_folder_pages,
) -> None:
    """The import step appears after the workspace pick.

    The folder/monitor selectors are populated from async_list_folders +
    async_list_pages (we assert by completing the flow with valid values
    drawn from those mocked sources).
    """
    list_folders = AsyncMock(return_value=sample_folders)
    list_pages = AsyncMock(return_value=multi_folder_pages["all"])

    result = await _start_oauth(hass, hass_client_no_auth, aioclient_mock)
    with (
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_get_user",
            AsyncMock(return_value=user_payload_single),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_folders",
            list_folders,
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_pages",
            list_pages,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"]
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "import_options"
    # The selectors were populated from the API.
    assert list_folders.await_count == 1
    assert list_pages.await_count == 1


async def test_import_mode_all(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    _bypass_setup,
    user_payload_single,
    sample_folders,
    multi_folder_pages,
) -> None:
    """Choosing ALL stores mode=all with empty folder/monitor lists."""
    result = await _advance_to_import_step(
        hass,
        hass_client_no_auth,
        aioclient_mock,
        user_payload_single,
        sample_folders,
        multi_folder_pages,
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IMPORT_MODE: IMPORT_MODE_ALL}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    options = result["options"]
    assert options[CONF_IMPORT_MODE] == IMPORT_MODE_ALL
    assert options[CONF_FOLDERS] == []
    assert options[CONF_MONITORS] == []


async def test_import_mode_folders(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    _bypass_setup,
    user_payload_single,
    sample_folders,
    multi_folder_pages,
) -> None:
    """Choosing FOLDERS persists the selected slugs and clears monitors."""
    result = await _advance_to_import_step(
        hass,
        hass_client_no_auth,
        aioclient_mock,
        user_payload_single,
        sample_folders,
        multi_folder_pages,
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_IMPORT_MODE: IMPORT_MODE_FOLDERS,
            CONF_FOLDERS: ["electronics"],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    options = result["options"]
    assert options[CONF_IMPORT_MODE] == IMPORT_MODE_FOLDERS
    assert options[CONF_FOLDERS] == ["electronics"]
    assert options[CONF_MONITORS] == []


async def test_import_mode_monitors(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    _bypass_setup,
    user_payload_single,
    sample_folders,
    multi_folder_pages,
) -> None:
    """Choosing MONITORS persists the selected ids and clears folders."""
    result = await _advance_to_import_step(
        hass,
        hass_client_no_auth,
        aioclient_mock,
        user_payload_single,
        sample_folders,
        multi_folder_pages,
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_IMPORT_MODE: IMPORT_MODE_MONITORS,
            CONF_MONITORS: ["2001"],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    options = result["options"]
    assert options[CONF_IMPORT_MODE] == IMPORT_MODE_MONITORS
    assert options[CONF_MONITORS] == ["2001"]
    assert options[CONF_FOLDERS] == []


async def test_import_folders_mode_requires_selection(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    _bypass_setup,
    user_payload_single,
    sample_folders,
    multi_folder_pages,
) -> None:
    """FOLDERS mode with no folders selected shows the validation error."""
    result = await _advance_to_import_step(
        hass,
        hass_client_no_auth,
        aioclient_mock,
        user_payload_single,
        sample_folders,
        multi_folder_pages,
    )

    with (
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_folders",
            AsyncMock(return_value=sample_folders),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_pages",
            AsyncMock(return_value=multi_folder_pages["all"]),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_IMPORT_MODE: IMPORT_MODE_FOLDERS, CONF_FOLDERS: []},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_FOLDERS: "import_no_folders"}


async def test_import_monitors_mode_requires_selection(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    _bypass_setup,
    user_payload_single,
    sample_folders,
    multi_folder_pages,
) -> None:
    """MONITORS mode with nothing selected shows the validation error."""
    result = await _advance_to_import_step(
        hass,
        hass_client_no_auth,
        aioclient_mock,
        user_payload_single,
        sample_folders,
        multi_folder_pages,
    )

    with (
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_folders",
            AsyncMock(return_value=sample_folders),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_pages",
            AsyncMock(return_value=multi_folder_pages["all"]),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_IMPORT_MODE: IMPORT_MODE_MONITORS, CONF_MONITORS: []},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_MONITORS: "import_no_monitors"}


# ---------------------------------------------------------------------------
# Coordinator: per-mode filtering + webhook scoping
# ---------------------------------------------------------------------------


def _folder_aware_client(multi_folder_pages: dict[str, list[dict[str, Any]]]):
    """Build a client whose async_list_pages honours the folder param."""
    client = MagicMock()
    client.workspace_id = WORKSPACE_ID
    client._base_url = "https://pagecrawl.io"

    async def _list_pages(folder=None, workspace_id=None):
        if folder is None:
            return multi_folder_pages["all"]
        return multi_folder_pages.get(folder, [])

    client.async_list_pages = AsyncMock(side_effect=_list_pages)
    return client


async def test_coordinator_all_mode_keeps_everything(
    hass: HomeAssistant, mock_config_entry, multi_folder_pages
) -> None:
    """ALL mode keeps every monitor in scope."""
    mock_config_entry.add_to_hass(hass)
    client = _folder_aware_client(multi_folder_pages)
    coordinator = PageCrawlDataUpdateCoordinator(
        hass,
        mock_config_entry,
        client,
        update_interval=900,
        import_mode=IMPORT_MODE_ALL,
    )
    await coordinator.async_refresh()

    assert set(coordinator.data) == {2001, 2002, 2003}
    assert coordinator.in_scope_ids == {2001, 2002, 2003}


async def test_coordinator_folders_mode_filters_per_folder(
    hass: HomeAssistant, mock_config_entry, multi_folder_pages
) -> None:
    """FOLDERS mode fetches per slug and merges; out-of-folder monitors drop."""
    mock_config_entry.add_to_hass(hass)
    client = _folder_aware_client(multi_folder_pages)
    coordinator = PageCrawlDataUpdateCoordinator(
        hass,
        mock_config_entry,
        client,
        update_interval=900,
        import_mode=IMPORT_MODE_FOLDERS,
        folders=["electronics"],
    )
    await coordinator.async_refresh()

    # Only the electronics folder's monitors are in scope.
    assert set(coordinator.data) == {2001, 2002}
    assert coordinator.in_scope_ids == {2001, 2002}
    # The fetch was scoped to the slug, not the full workspace.
    client.async_list_pages.assert_awaited_with(
        folder="electronics", workspace_id=WORKSPACE_ID
    )


async def test_coordinator_monitors_mode_filters_by_id(
    hass: HomeAssistant, mock_config_entry, multi_folder_pages
) -> None:
    """MONITORS mode keeps only the explicitly selected ids."""
    mock_config_entry.add_to_hass(hass)
    client = _folder_aware_client(multi_folder_pages)
    coordinator = PageCrawlDataUpdateCoordinator(
        hass,
        mock_config_entry,
        client,
        update_interval=900,
        import_mode=IMPORT_MODE_MONITORS,
        monitors=["2003"],
    )
    await coordinator.async_refresh()

    assert set(coordinator.data) == {2003}
    assert coordinator.in_scope_ids == {2003}


async def test_coordinator_monitors_mode_ignores_out_of_scope_push(
    hass: HomeAssistant, mock_config_entry, multi_folder_pages
) -> None:
    """A webhook push for a monitor outside the selection is ignored."""
    mock_config_entry.add_to_hass(hass)
    client = _folder_aware_client(multi_folder_pages)
    coordinator = PageCrawlDataUpdateCoordinator(
        hass,
        mock_config_entry,
        client,
        update_interval=900,
        import_mode=IMPORT_MODE_MONITORS,
        monitors=["2003"],
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    events: list[Any] = []
    hass.bus.async_listen(EVENT_CHANGE, lambda e: events.append(e))
    client.async_list_pages.reset_mock()

    # 2001 is not in the selection -> dropped, no refresh, no event.
    coordinator.apply_webhook_update({"id": 2001, "contents": "x"})
    await hass.async_block_till_done()

    assert client.async_list_pages.called is False
    assert events == []
    assert 2001 not in coordinator.data


async def test_coordinator_monitors_mode_in_scope_push_updates(
    hass: HomeAssistant, mock_config_entry, multi_folder_pages
) -> None:
    """An in-scope push updates the monitor and fires the change event."""
    mock_config_entry.add_to_hass(hass)
    client = _folder_aware_client(multi_folder_pages)
    coordinator = PageCrawlDataUpdateCoordinator(
        hass,
        mock_config_entry,
        client,
        update_interval=900,
        import_mode=IMPORT_MODE_MONITORS,
        monitors=["2003"],
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    events: list[Any] = []
    hass.bus.async_listen(EVENT_CHANGE, lambda e: events.append(e))

    coordinator.apply_webhook_update(
        {
            "id": 2003,
            "contents": "99.00",
            "changed_at": "2026-06-13T13:00:00.000000Z",
        }
    )
    await hass.async_block_till_done()

    assert coordinator.data[2003]["latest"]["contents"] == "99.00"
    assert len(events) == 1
    assert events[0].data["monitor_id"] == 2003


# ---------------------------------------------------------------------------
# Device pruning
# ---------------------------------------------------------------------------


def _register_device(
    hass: HomeAssistant, entry: MockConfigEntry, monitor_id: int
) -> Any:
    """Register a device for a monitor under this entry."""
    dev_reg = dr.async_get(hass)
    return dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}:{monitor_id}")},
        name=f"Monitor {monitor_id}",
    )


async def test_prune_removes_out_of_scope_devices(
    hass: HomeAssistant, mock_config_entry
) -> None:
    """Pruning removes devices not in the in-scope set, keeps the rest."""
    from custom_components.pagecrawl import _async_prune_stale_devices

    mock_config_entry.add_to_hass(hass)
    _register_device(hass, mock_config_entry, 2001)
    _register_device(hass, mock_config_entry, 2002)
    _register_device(hass, mock_config_entry, 2003)

    dev_reg = dr.async_get(hass)
    assert (
        len(dr.async_entries_for_config_entry(dev_reg, mock_config_entry.entry_id))
        == 3
    )

    # Selection shrinks to just 2001.
    _async_prune_stale_devices(hass, mock_config_entry, {2001})

    remaining = dr.async_entries_for_config_entry(
        dev_reg, mock_config_entry.entry_id
    )
    remaining_ids = {
        int(identifier.split(":")[1])
        for device in remaining
        for domain, identifier in device.identifiers
        if domain == DOMAIN
    }
    assert remaining_ids == {2001}


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


def _options_client(sample_folders, multi_folder_pages) -> MagicMock:
    client = MagicMock()
    client.workspace_id = WORKSPACE_ID
    client._base_url = "https://pagecrawl.io"
    client.async_list_folders = AsyncMock(return_value=sample_folders)
    client.async_list_pages = AsyncMock(return_value=multi_folder_pages["all"])
    client.async_get_user = AsyncMock(return_value={"id": USER_ID})
    client.async_check_now = AsyncMock(return_value=None)
    client.async_create_hook = AsyncMock(
        return_value={"id": 555, "signing_secret": "s"}
    )
    client.async_delete_hook = AsyncMock(return_value=None)

    async def _list_pages(folder=None, workspace_id=None):
        if folder is None:
            return multi_folder_pages["all"]
        return multi_folder_pages.get(folder, [])

    client.async_list_pages.side_effect = _list_pages
    return client


async def test_options_flow_persists_and_reloads(
    hass: HomeAssistant,
    sample_folders,
    multi_folder_pages,
) -> None:
    """Editing import_mode/folders persists to options and reloads the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Acme Workspace",
        unique_id=f"{USER_ID}:{WORKSPACE_ID}",
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "a",
                "refresh_token": "r",
                "expires_at": __import__("time").time() + 3600,
                "token_type": "Bearer",
            },
            CONF_WORKSPACE_ID: WORKSPACE_ID,
        },
        options={
            CONF_UPDATE_MODE: "poll",
            CONF_SCAN_INTERVAL: 900,
            CONF_IMPORT_MODE: IMPORT_MODE_ALL,
        },
    )
    entry.add_to_hass(hass)
    client = _options_client(sample_folders, multi_folder_pages)

    patches = (
        patch(
            "custom_components.pagecrawl.PageCrawlClient", return_value=client
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient",
            return_value=client,
        ),
        patch(
            "custom_components.pagecrawl.config_entry_oauth2_flow."
            "async_get_config_entry_implementation",
            AsyncMock(),
        ),
        patch(
            "custom_components.pagecrawl.config_entry_oauth2_flow."
            "OAuth2Session",
            MagicMock(),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.config_entry_oauth2_flow."
            "async_get_config_entry_implementation",
            AsyncMock(),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.config_entry_oauth2_flow."
            "OAuth2Session",
            MagicMock(),
        ),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_UPDATE_MODE: "poll",
                CONF_SCAN_INTERVAL: 900,
                CONF_IMPORT_MODE: IMPORT_MODE_FOLDERS,
                CONF_FOLDERS: ["groceries"],
            },
        )
        await hass.async_block_till_done()

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert entry.options[CONF_IMPORT_MODE] == IMPORT_MODE_FOLDERS
        assert entry.options[CONF_FOLDERS] == ["groceries"]
        assert entry.options[CONF_MONITORS] == []

        # The options change triggers a reload (via the entry update listener).
        # Force the reload to completion and confirm the entry comes back
        # LOADED with the new scope applied to its coordinator.
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        reloaded = hass.config_entries.async_get_entry(entry.entry_id)
        assert reloaded.state is ConfigEntryState.LOADED
        assert set(reloaded.runtime_data.coordinator.data) == {2003}


async def test_options_flow_validation_error(
    hass: HomeAssistant,
    sample_folders,
    multi_folder_pages,
) -> None:
    """Options flow rejects FOLDERS mode with no folders selected."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Acme Workspace",
        unique_id=f"{USER_ID}:{WORKSPACE_ID}",
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "a",
                "refresh_token": "r",
                "expires_at": __import__("time").time() + 3600,
                "token_type": "Bearer",
            },
            CONF_WORKSPACE_ID: WORKSPACE_ID,
        },
        options={
            CONF_UPDATE_MODE: "poll",
            CONF_SCAN_INTERVAL: 900,
            CONF_IMPORT_MODE: IMPORT_MODE_ALL,
        },
    )
    entry.add_to_hass(hass)
    client = _options_client(sample_folders, multi_folder_pages)

    with (
        patch(
            "custom_components.pagecrawl.PageCrawlClient", return_value=client
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient",
            return_value=client,
        ),
        patch(
            "custom_components.pagecrawl.config_entry_oauth2_flow."
            "async_get_config_entry_implementation",
            AsyncMock(),
        ),
        patch(
            "custom_components.pagecrawl.config_entry_oauth2_flow."
            "OAuth2Session",
            MagicMock(),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.config_entry_oauth2_flow."
            "async_get_config_entry_implementation",
            AsyncMock(),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.config_entry_oauth2_flow."
            "OAuth2Session",
            MagicMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_UPDATE_MODE: "poll",
                CONF_SCAN_INTERVAL: 900,
                CONF_IMPORT_MODE: IMPORT_MODE_FOLDERS,
                CONF_FOLDERS: [],
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_FOLDERS: "import_no_folders"}
