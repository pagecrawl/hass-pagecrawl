"""Diagnostics support for PageCrawl."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import PageCrawlConfigEntry
from .const import (
    CONF_CLOUDHOOK_URL,
    CONF_HOOK_ID,
    CONF_SIGNING_SECRET,
    CONF_WEBHOOK_ID,
)

TO_REDACT = {
    "access_token",
    "refresh_token",
    "token",
    CONF_SIGNING_SECRET,
    CONF_HOOK_ID,
    CONF_WEBHOOK_ID,
    CONF_CLOUDHOOK_URL,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PageCrawlConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    runtime = getattr(entry, "runtime_data", None)

    monitors: list[dict[str, Any]] = []
    coordinator = getattr(runtime, "coordinator", None)
    if coordinator is not None and coordinator.data:
        for monitor_id, monitor in coordinator.data.items():
            elements = monitor.get("elements") or []
            monitors.append(
                {
                    "id": monitor_id,
                    "status": monitor.get("status"),
                    "last_checked_at": monitor.get("last_checked_at"),
                    "element_count": len(elements),
                    "element_types": sorted(
                        {
                            str(element.get("type"))
                            for element in elements
                            if element.get("type")
                        }
                    ),
                }
            )

    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": getattr(
                coordinator, "last_update_success", None
            )
            if coordinator
            else None,
            "update_interval": str(getattr(coordinator, "update_interval", None))
            if coordinator
            else None,
            "monitor_count": len(monitors),
        },
        "monitors": monitors,
    }
