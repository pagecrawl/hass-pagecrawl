"""Base entity for PageCrawl integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_BASE_URL, DOMAIN
from .coordinator import PageCrawlDataUpdateCoordinator


class PageCrawlEntity(CoordinatorEntity[PageCrawlDataUpdateCoordinator]):
    """Base class for PageCrawl entities, one device per monitor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PageCrawlDataUpdateCoordinator,
        monitor_id: int,
    ) -> None:
        """Initialize the entity bound to a monitor id."""
        super().__init__(coordinator)
        self._monitor_id = monitor_id
        self._entry_id = coordinator.entry.entry_id

    @property
    def monitor(self) -> dict[str, Any]:
        """Return the current monitor dict (empty if missing)."""
        data = self.coordinator.data or {}
        return data.get(self._monitor_id, {})

    @property
    def available(self) -> bool:
        """Entity is available when the coordinator succeeded and has data."""
        return (
            super().available
            and self.coordinator.data is not None
            and self._monitor_id in self.coordinator.data
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return one device per monitor; all entities share it."""
        monitor = self.monitor
        slug = monitor.get("slug")
        base = (self.coordinator.client._base_url or DEFAULT_BASE_URL).rstrip("/")
        config_url = f"{base}/changes/{slug}" if slug else base
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}:{self._monitor_id}")},
            name=monitor.get("name") or monitor.get("url") or f"Monitor {self._monitor_id}",
            manufacturer="PageCrawl.io",
            model="Monitor",
            configuration_url=config_url,
        )

    def resolve_element_value(self, element_id: int | str | None) -> Any:
        """Resolve the current value for an element.

        Order: `checks[0].elements[element_id].contents`, then for the default
        element fall back to `latest.contents`.
        """
        monitor = self.monitor

        if element_id is not None:
            checks = monitor.get("checks") or []
            if checks:
                elements = (checks[0] or {}).get("elements") or {}
                # element ids may be int or str keys in the JSON object.
                element = elements.get(element_id)
                if element is None:
                    element = elements.get(str(element_id))
                if isinstance(element, dict) and "contents" in element:
                    return element.get("contents")

        # Default-element fallback.
        latest = monitor.get("latest") or {}
        return latest.get("contents")
