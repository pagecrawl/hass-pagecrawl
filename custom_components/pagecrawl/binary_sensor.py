"""Binary sensor platform for PageCrawl (boolean / availability)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PageCrawlConfigEntry
from .const import ELEMENT_TYPE_MAP, PLATFORM_BINARY_SENSOR
from .coordinator import PageCrawlDataUpdateCoordinator
from .entity import PageCrawlEntity

_LOGGER = logging.getLogger(__name__)

# Strings that signal a product is in stock / available.
_IN_STOCK_TOKENS = (
    "in stock",
    "instock",
    "in_stock",
    "available",
    "availablenow",
    "available now",
    "add to cart",
    "add to basket",
    "buy now",
    "ready to ship",
    "yes",
    "true",
)
# Strings that signal a product is out of stock / unavailable.
_OUT_OF_STOCK_TOKENS = (
    "out of stock",
    "outofstock",
    "out_of_stock",
    "sold out",
    "soldout",
    "unavailable",
    "not available",
    "no",
    "false",
    "discontinued",
    "backorder",
    "back order",
    "pre-order",
    "preorder",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PageCrawlConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PageCrawl binary sensors, adding new ones as they appear."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new_entities: list[BinarySensorEntity] = []
        data = coordinator.data or {}
        for monitor_id, monitor in data.items():
            for element in monitor.get("elements") or []:
                element_id = element.get("id")
                if element_id is None:
                    continue
                mapping = ELEMENT_TYPE_MAP.get(element.get("type"))
                if mapping is None or mapping["platform"] != PLATFORM_BINARY_SENSOR:
                    continue
                uid = f"{entry.entry_id}_{monitor_id}_{element_id}"
                if uid in known:
                    continue
                known.add(uid)
                new_entities.append(
                    PageCrawlBinarySensor(
                        coordinator, monitor_id, element, mapping
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


def _parse_truthy(value: Any) -> bool | None:
    """Parse a boolean element's contents into True/False/None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return None
    if text in ("0", "false", "no", "off", "n"):
        return False
    if text in ("1", "true", "yes", "on", "y"):
        return True
    # Any other non-empty string is considered truthy.
    return True


def _parse_in_stock(value: Any) -> bool | None:
    """Parse an availability element into in-stock (True) / out (False)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    normalized = "".join(c for c in text if c.isalnum() or c == " ")
    for token in _OUT_OF_STOCK_TOKENS:
        if token in normalized:
            return False
    for token in _IN_STOCK_TOKENS:
        if token in normalized:
            return True
    return None


class PageCrawlBinarySensor(PageCrawlEntity, BinarySensorEntity):
    """Binary sensor for a boolean or availability tracked element."""

    def __init__(
        self,
        coordinator: PageCrawlDataUpdateCoordinator,
        monitor_id: int,
        element: dict[str, Any],
        mapping: dict[str, str | None],
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, monitor_id)
        self._element_id = element.get("id")
        self._element_type = element.get("type")
        self._kind = mapping["kind"]
        self._attr_unique_id = (
            f"{self._entry_id}_{monitor_id}_{self._element_id}"
        )
        self._attr_name = element.get("label") or (
            self._element_type or "value"
        )
        if self._kind == "stock":
            # on = in stock; presence/availability semantics fit no built-in
            # device_class cleanly, so leave plain and rely on the icon.
            self._attr_icon = "mdi:package-variant-closed"
        elif mapping.get("device_class"):
            self._attr_device_class = mapping["device_class"]

    @property
    def is_on(self) -> bool | None:
        """Return the parsed boolean state."""
        raw = self.resolve_element_value(self._element_id)
        if self._kind == "stock":
            return _parse_in_stock(raw)
        return _parse_truthy(raw)

    @property
    def icon(self) -> str | None:
        """Reflect stock state in the icon."""
        if self._kind == "stock":
            state = self.is_on
            if state is True:
                return "mdi:package-variant-closed-check"
            if state is False:
                return "mdi:package-variant-closed-remove"
            return "mdi:package-variant-closed"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw value and element metadata."""
        raw = self.resolve_element_value(self._element_id)
        attrs: dict[str, Any] = {
            "element_id": self._element_id,
            "element_type": self._element_type,
            "raw_value": None if raw is None else str(raw)[:255],
        }
        for element in self.monitor.get("elements") or []:
            if element.get("id") == self._element_id and element.get("selector"):
                attrs["selector"] = element.get("selector")
                break
        return attrs
