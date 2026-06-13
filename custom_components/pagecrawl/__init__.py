"""The PageCrawl integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import (
    config_entry_oauth2_flow,
    config_validation as cv,
)
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er

from .api import (
    PageCrawlAuthError,
    PageCrawlClient,
    PageCrawlError,
)
from .const import (
    CONF_BASE_URL,
    CONF_CLOUDHOOK_URL,
    CONF_HOOK_ID,
    CONF_SIGNING_SECRET,
    CONF_SCAN_INTERVAL,
    CONF_FOLDERS,
    CONF_IMPORT_MODE,
    CONF_MONITORS,
    CONF_UPDATE_MODE,
    CONF_WEBHOOK_ID,
    CONF_WORKSPACE_ID,
    DEFAULT_BASE_URL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PUSH_RECONCILE_INTERVAL,
    DOMAIN,
    IMPORT_MODE_ALL,
    SERVICE_CHECK_NOW,
    SERVICE_TRACK_PAGE,
    UPDATE_MODE_AUTO,
    UPDATE_MODE_POLL,
    UPDATE_MODE_PUSH,
)
from .coordinator import PageCrawlDataUpdateCoordinator
from .webhook import async_register_webhook, async_unregister_webhook

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_TRACK_PAGE_SCHEMA = vol.Schema(
    {
        vol.Required("url"): cv.string,
        vol.Optional("entry_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("tracking_mode"): cv.string,
        vol.Optional("selector"): cv.string,
        vol.Optional("prompt"): cv.string,
        vol.Optional("frequency"): cv.string,
    }
)

SERVICE_CHECK_NOW_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): cv.entity_ids,
        vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
    }
)


@dataclass
class PageCrawlRuntimeData:
    """Runtime data stored on the config entry."""

    client: PageCrawlClient
    coordinator: PageCrawlDataUpdateCoordinator


PageCrawlConfigEntry = ConfigEntry  # ConfigEntry[PageCrawlRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration (register global services).

    The built-in public OAuth client is imported by the config flow
    (``async_step_user``) so it exists before the OAuth implementation is
    resolved, and is persisted by Home Assistant across restarts.
    """
    _async_register_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: PageCrawlConfigEntry
) -> bool:
    """Set up PageCrawl from a config entry."""
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(
        hass, entry, implementation
    )

    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    workspace_id = entry.data.get(CONF_WORKSPACE_ID)
    client = PageCrawlClient(oauth_session, base_url, workspace_id)

    update_mode = entry.options.get(CONF_UPDATE_MODE, UPDATE_MODE_AUTO)
    scan_interval = _resolve_interval(entry, update_mode)
    import_mode = entry.options.get(CONF_IMPORT_MODE, IMPORT_MODE_ALL)
    folders = entry.options.get(CONF_FOLDERS, []) or []
    monitors = entry.options.get(CONF_MONITORS, []) or []

    coordinator = PageCrawlDataUpdateCoordinator(
        hass,
        entry,
        client,
        scan_interval,
        import_mode=import_mode,
        folders=folders,
        monitors=monitors,
    )
    await coordinator.async_config_entry_first_refresh()

    # Remove devices for monitors that are no longer in scope (e.g. a folder
    # was deselected or a monitor removed from the selection).
    _async_prune_stale_devices(hass, entry, coordinator.in_scope_ids)

    # Keep pruning whenever the in-scope set changes on subsequent updates.
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: _async_prune_stale_devices(
                hass, entry, coordinator.in_scope_ids
            )
        )
    )

    # Set up push (webhook + PageCrawl hook) when the mode wants it.
    if update_mode in (UPDATE_MODE_AUTO, UPDATE_MODE_PUSH):
        try:
            await _async_setup_push(hass, entry, client, coordinator)
        except PageCrawlError as err:
            _LOGGER.warning(
                "Could not enable PageCrawl push for %s, falling back to polling: %s",
                entry.title,
                err,
            )
    else:
        # Polling-only: tear down any push artifacts left over from a previous
        # mode so we don't leak a server-side hook or cloudhook.
        await _async_teardown_push(hass, entry, client)

    entry.runtime_data = PageCrawlRuntimeData(
        client=client, coordinator=coordinator
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: PageCrawlConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    if unload_ok:
        await async_unregister_webhook(hass, entry)
    return unload_ok


async def async_remove_entry(
    hass: HomeAssistant, entry: PageCrawlConfigEntry
) -> None:
    """Clean up the PageCrawl hook and cloudhook when an entry is removed."""
    await async_unregister_webhook(hass, entry, delete_cloudhook=True)

    hook_id = entry.data.get(CONF_HOOK_ID)
    if hook_id:
        try:
            implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
                hass, entry
            )
            oauth_session = config_entry_oauth2_flow.OAuth2Session(
                hass, entry, implementation
            )
            client = PageCrawlClient(
                oauth_session,
                entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                entry.data.get(CONF_WORKSPACE_ID),
            )
            await client.async_delete_hook(hook_id)
        except PageCrawlError as err:
            _LOGGER.debug("Failed to delete PageCrawl hook on removal: %s", err)


async def _async_update_listener(
    hass: HomeAssistant, entry: PageCrawlConfigEntry
) -> None:
    """Reload the entry when options change (re-evaluates push mode)."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_prune_stale_devices(
    hass: HomeAssistant,
    entry: PageCrawlConfigEntry,
    in_scope_ids: set[int],
) -> None:
    """Remove devices for monitors no longer in the import scope.

    Devices are identified by ``(DOMAIN, f"{entry_id}:{monitor_id}")``. Any such
    device for this entry whose monitor id is not in ``in_scope_ids`` is removed
    from the device registry (which cascades to its entities).
    """
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            _entry_id, _, monitor_raw = identifier.partition(":")
            try:
                monitor_id = int(monitor_raw)
            except ValueError:
                continue
            if monitor_id not in in_scope_ids:
                dev_reg.async_remove_device(device.id)
            break


def _resolve_interval(entry: ConfigEntry, update_mode: str) -> int:
    """Resolve the poll interval based on the update mode."""
    if update_mode == UPDATE_MODE_POLL:
        return int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_POLL_INTERVAL)
        )
    # Push (or auto with push enabled): slow reconcile loop.
    return int(
        entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_PUSH_RECONCILE_INTERVAL
        )
    )


async def _async_setup_push(
    hass: HomeAssistant,
    entry: PageCrawlConfigEntry,
    client: PageCrawlClient,
    coordinator: PageCrawlDataUpdateCoordinator,
) -> None:
    """Register the HA webhook and create/reuse the PageCrawl hook."""
    public_url, _cloudhook = await async_register_webhook(
        hass, entry, coordinator
    )

    # Reuse an existing PageCrawl hook if we already have one.
    if entry.data.get(CONF_HOOK_ID) and entry.data.get(CONF_SIGNING_SECRET):
        return

    hook = await client.async_create_hook(
        public_url, workspace_id=client.workspace_id
    )
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_HOOK_ID: hook.get("id"),
            CONF_SIGNING_SECRET: hook.get("signing_secret"),
        },
    )


async def _async_teardown_push(
    hass: HomeAssistant,
    entry: PageCrawlConfigEntry,
    client: PageCrawlClient,
) -> None:
    """Remove the HA webhook + cloudhook and the server-side PageCrawl hook.

    Used when an entry is (re)loaded in polling-only mode so we never leave a
    dangling hook delivering to a webhook we no longer serve.
    """
    await async_unregister_webhook(hass, entry, delete_cloudhook=True)

    hook_id = entry.data.get(CONF_HOOK_ID)
    if hook_id:
        try:
            await client.async_delete_hook(hook_id)
        except PageCrawlError as err:
            _LOGGER.debug(
                "Failed to delete PageCrawl hook on switch to polling: %s", err
            )

    # Drop the stored push artifacts from the entry.
    new_data = {
        k: v
        for k, v in entry.data.items()
        if k not in (CONF_HOOK_ID, CONF_SIGNING_SECRET, CONF_CLOUDHOOK_URL, CONF_WEBHOOK_ID)
    }
    if new_data != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=new_data)


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services exactly once."""
    if hass.services.has_service(DOMAIN, SERVICE_CHECK_NOW):
        return

    async def _handle_check_now(call: ServiceCall) -> None:
        """Trigger an immediate check for the targeted monitors."""
        for client, monitor_id in _resolve_targets(hass, call):
            try:
                await client.async_check_now(monitor_id)
            except PageCrawlError as err:
                raise HomeAssistantError(
                    f"Check now failed for monitor {monitor_id}: {err}"
                ) from err
        # Refresh affected coordinators.
        for entry in _entries_with_runtime(hass):
            await entry.runtime_data.coordinator.async_request_refresh()

    async def _handle_track_page(call: ServiceCall) -> None:
        """Create a new monitor via track-simple."""
        entry = _resolve_entry(hass, call.data.get("entry_id"))
        if entry is None:
            raise HomeAssistantError("No PageCrawl config entry available")

        payload: dict[str, Any] = {"url": call.data["url"]}
        for key in ("name", "tracking_mode", "selector", "prompt", "frequency"):
            if key in call.data:
                payload[key] = call.data[key]

        try:
            await entry.runtime_data.client.async_track_page(payload)
        except PageCrawlError as err:
            raise HomeAssistantError(f"Track page failed: {err}") from err

        await entry.runtime_data.coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_CHECK_NOW,
        _handle_check_now,
        schema=SERVICE_CHECK_NOW_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TRACK_PAGE,
        _handle_track_page,
        schema=SERVICE_TRACK_PAGE_SCHEMA,
    )


def _entries_with_runtime(hass: HomeAssistant) -> list[PageCrawlConfigEntry]:
    """Return loaded entries that have runtime data."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def _resolve_entry(
    hass: HomeAssistant, entry_id: str | None
) -> PageCrawlConfigEntry | None:
    """Resolve a config entry by id, or the only loaded one."""
    entries = _entries_with_runtime(hass)
    if entry_id:
        return next((e for e in entries if e.entry_id == entry_id), None)
    if len(entries) == 1:
        return entries[0]
    return None


def _resolve_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[tuple[PageCrawlClient, int]]:
    """Resolve (client, monitor_id) pairs from entity/device targets."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    targets: list[tuple[PageCrawlClient, int]] = []
    seen: set[tuple[str, int]] = set()

    device_ids: set[str] = set(call.data.get("device_id", []) or [])

    for entity_id in call.data.get("entity_id", []) or []:
        entity = ent_reg.async_get(entity_id)
        if entity and entity.device_id:
            device_ids.add(entity.device_id)

    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device is None:
            continue
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            # identifier is "{entry_id}:{monitor_id}".
            entry_id, _, monitor_raw = identifier.partition(":")
            entry = _resolve_entry(hass, entry_id)
            if entry is None:
                continue
            try:
                monitor_id = int(monitor_raw)
            except ValueError:
                continue
            key = (entry_id, monitor_id)
            if key in seen:
                continue
            seen.add(key)
            targets.append((entry.runtime_data.client, monitor_id))

    return targets
