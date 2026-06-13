"""Button platform for PageCrawl ("Check now" per monitor)."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PageCrawlConfigEntry
from .api import PageCrawlError
from .coordinator import PageCrawlDataUpdateCoordinator
from .entity import PageCrawlEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PageCrawlConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a "Check now" button per monitor, adding new ones over time."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new_entities: list[ButtonEntity] = []
        data = coordinator.data or {}
        for monitor_id in data:
            uid = f"{entry.entry_id}_{monitor_id}_check_now"
            if uid in known:
                continue
            known.add(uid)
            new_entities.append(PageCrawlCheckNowButton(coordinator, monitor_id))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class PageCrawlCheckNowButton(PageCrawlEntity, ButtonEntity):
    """Trigger an immediate check of a monitor."""

    _attr_icon = "mdi:refresh"

    def __init__(
        self,
        coordinator: PageCrawlDataUpdateCoordinator,
        monitor_id: int,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_check_now"
        self._attr_name = "Check now"

    async def async_press(self) -> None:
        """Trigger the check and refresh the coordinator."""
        try:
            await self.coordinator.client.async_check_now(self._monitor_id)
        except PageCrawlError as err:
            raise HomeAssistantError(
                f"Check now failed for monitor {self._monitor_id}: {err}"
            ) from err
        await self.coordinator.async_request_refresh()
