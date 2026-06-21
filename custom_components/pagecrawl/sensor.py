"""Sensor platform for PageCrawl."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import PageCrawlConfigEntry
from .const import (
    AUTO_DETECT_TYPES,
    ELEMENT_TYPE_DEFAULT,
    ELEMENT_TYPE_MAP,
    MAX_STATE_LENGTH,
    PLATFORM_SENSOR,
)
from .coordinator import PageCrawlDataUpdateCoordinator
from .entity import PageCrawlEntity

_LOGGER = logging.getLogger(__name__)

# Cap full_value / items attribute payloads so we never bloat the state machine.
MAX_ATTR_LENGTH = 16384
MAX_ITEMS = 100


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PageCrawlConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PageCrawl sensors, adding new ones as monitors appear."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new_entities: list[SensorEntity] = []
        data = coordinator.data or {}
        for monitor_id, monitor in data.items():
            # One primary + diagnostic sensors per monitor.
            primary_uid = f"{entry.entry_id}_{monitor_id}_primary"
            if primary_uid not in known:
                known.add(primary_uid)
                new_entities.append(
                    PageCrawlPrimarySensor(coordinator, monitor_id)
                )

            # Last change sensor: human_difference is broadly available.
            last_change_uid = f"{entry.entry_id}_{monitor_id}_last_change"
            if last_change_uid not in known:
                known.add(last_change_uid)
                new_entities.append(
                    PageCrawlLastChangeSensor(coordinator, monitor_id)
                )

            # AI sensors: only create once AI data is actually present so we
            # don't clutter monitors without AI. They appear later via the
            # coordinator listener if AI gets enabled.
            if _has_ai_data(monitor):
                ai_summary_uid = f"{entry.entry_id}_{monitor_id}_ai_summary"
                if ai_summary_uid not in known:
                    known.add(ai_summary_uid)
                    new_entities.append(
                        PageCrawlAiSummarySensor(coordinator, monitor_id)
                    )
                ai_priority_uid = f"{entry.entry_id}_{monitor_id}_ai_priority"
                if ai_priority_uid not in known:
                    known.add(ai_priority_uid)
                    new_entities.append(
                        PageCrawlAiPrioritySensor(coordinator, monitor_id)
                    )

            for diag in _DIAGNOSTIC_DESCRIPTIONS:
                diag_uid = f"{entry.entry_id}_{monitor_id}_diag_{diag['key']}"
                if diag_uid not in known:
                    known.add(diag_uid)
                    new_entities.append(
                        PageCrawlDiagnosticSensor(coordinator, monitor_id, diag)
                    )

            for element in monitor.get("elements") or []:
                element_id = element.get("id")
                if element_id is None:
                    continue
                mapping = ELEMENT_TYPE_MAP.get(
                    element.get("type"), ELEMENT_TYPE_DEFAULT
                )
                if mapping["platform"] != PLATFORM_SENSOR:
                    continue
                uid = f"{entry.entry_id}_{monitor_id}_{element_id}"
                if uid in known:
                    continue
                known.add(uid)
                new_entities.append(
                    PageCrawlElementSensor(
                        coordinator, monitor_id, element, mapping
                    )
                )

        if new_entities:
            async_add_entities(new_entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


def _truncate_state(value: Any) -> str | None:
    """Return a string state truncated to the HA state length limit."""
    if value is None:
        return None
    text = str(value)
    if len(text) > MAX_STATE_LENGTH:
        return text[: MAX_STATE_LENGTH - 1] + "…"
    return text


def _parse_float(value: Any) -> float | None:
    """Best-effort parse of a numeric value out of contents."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    # Strip everything except digits, sign, decimal point, comma.
    cleaned = "".join(c for c in text if c.isdigit() or c in ".,-")
    cleaned = cleaned.replace(",", "")
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _strict_float(value: Any) -> float | None:
    """Parse a value as a number only when the whole string is numeric.

    Unlike `_parse_float`, this does not strip surrounding letters/symbols, so
    "19.99" parses but "Price: $19.99" does not. Used for auto-detection where
    we must avoid mistaking a sentence that merely contains a number for a
    numeric value.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse a value into a timezone-aware datetime, or None if it isn't one.

    Accepts full ISO datetimes and date-only strings (interpreted as midnight
    in Home Assistant's configured time zone). Returns None for anything that
    does not cleanly parse, so a TIMESTAMP-typed sensor degrades to unavailable
    rather than raising when a later check returns non-date text.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        parsed = dt_util.parse_datetime(text)
        if parsed is None:
            date_only = dt_util.parse_date(text)
            if date_only is None:
                return None
            parsed = datetime(date_only.year, date_only.month, date_only.day)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return parsed


def _detect_text_kind(value: Any) -> str:
    """Classify a free-form text value as 'timestamp', 'numeric', or 'text'.

    Timestamps are checked first because a date is more specific than a number.
    Conservative by design: only a value that parses cleanly in full is
    upgraded; anything else stays plain text.
    """
    if value is None:
        return "text"
    text = str(value).strip()
    if not text:
        return "text"
    if _parse_timestamp(text) is not None:
        return "timestamp"
    if _strict_float(text) is not None:
        return "numeric"
    return "text"


def get_ai_summary(monitor: dict[str, Any]) -> str | None:
    """Resolve the AI summary of the latest change.

    Prefers the push path (`latest.ai_summary`), falling back to the poll path
    (`checks[0].ai_summary`). Returns None when neither is present.
    """
    latest = monitor.get("latest") or {}
    summary = latest.get("ai_summary") or latest.get("short_summary")
    if summary:
        return str(summary)
    checks = monitor.get("checks") or []
    if checks:
        check = checks[0] or {}
        summary = check.get("ai_summary") or check.get("short_summary")
        if summary:
            return str(summary)
    return None


def get_ai_priority(monitor: dict[str, Any]) -> float | None:
    """Resolve the AI priority score (0-100) for the latest change.

    Prefers the push path (`latest.ai_priority_score`), falling back to the
    poll path (`checks[0].priority_score`). Returns None when neither is a
    usable number.
    """
    latest = monitor.get("latest") or {}
    score = latest.get("ai_priority_score")
    if score is None:
        checks = monitor.get("checks") or []
        if checks:
            score = (checks[0] or {}).get("priority_score")
    if score is None:
        return None
    if isinstance(score, bool):
        return None
    if isinstance(score, (int, float)):
        return float(score)
    return _parse_float(score)


def _ai_meta(monitor: dict[str, Any]) -> dict[str, Any]:
    """Return ai_importance_tag / is_noise from latest or checks[0] if present."""
    latest = monitor.get("latest") or {}
    checks = monitor.get("checks") or []
    check = (checks[0] if checks else {}) or {}
    meta: dict[str, Any] = {}
    tag = latest.get("ai_importance_tag") or check.get("ai_importance_tag")
    if tag is not None:
        meta["ai_importance_tag"] = tag
    is_noise = latest.get("is_noise")
    if is_noise is None:
        is_noise = check.get("is_noise")
    if is_noise is not None:
        meta["is_noise"] = is_noise
    return meta


def _has_ai_data(monitor: dict[str, Any]) -> bool:
    """True when AI summary or priority is present for this monitor."""
    return get_ai_summary(monitor) is not None or get_ai_priority(monitor) is not None


def _coerce_list(value: Any) -> list[Any]:
    """Coerce element contents into a list for count-type elements."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # Some aggregate types deliver {items: [...]}.
        items = value.get("items")
        if isinstance(items, list):
            return items
        return list(value.values())
    # Newline-delimited string fallback (links/text_multiple).
    text = str(value).strip()
    if not text:
        return []
    return [line for line in text.splitlines() if line.strip()]


class PageCrawlElementSensor(PageCrawlEntity, SensorEntity):
    """A sensor representing a single tracked element on a monitor."""

    def __init__(
        self,
        coordinator: PageCrawlDataUpdateCoordinator,
        monitor_id: int,
        element: dict[str, Any],
        mapping: dict[str, str | None],
    ) -> None:
        """Initialize the element sensor."""
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

        device_class = mapping["device_class"]
        if device_class == "monetary":
            self._attr_device_class = SensorDeviceClass.MONETARY
        elif device_class is not None:
            self._attr_device_class = device_class

        state_class = mapping["state_class"]
        if state_class == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif state_class is not None:
            self._attr_state_class = state_class

        # Conservative auto-detect: for extraction-style text elements, upgrade
        # the sensor to a timestamp or numeric type when the current value
        # cleanly parses as one. device_class is fixed for the entity's life, so
        # we decide once here; native_value degrades to None if a later check no
        # longer parses, keeping the typed sensor valid instead of raising.
        self._auto_kind: str | None = None
        if self._kind == "text" and self._element_type in AUTO_DETECT_TYPES:
            detected = _detect_text_kind(
                self.resolve_element_value(self._element_id)
            )
            if detected == "timestamp":
                self._auto_kind = "timestamp"
                self._attr_device_class = SensorDeviceClass.TIMESTAMP
            elif detected == "numeric":
                self._auto_kind = "numeric"
                self._attr_state_class = SensorStateClass.MEASUREMENT

    def _element_dict(self) -> dict[str, Any]:
        """Return the element definition from the monitor (for currency etc.)."""
        for element in self.monitor.get("elements") or []:
            if element.get("id") == self._element_id:
                return element
        return {}

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Currency unit for price elements, else None."""
        if self._element_type == "price":
            element = self._element_dict()
            currency = (
                element.get("currency")
                or (self.monitor.get("latest") or {}).get("currency")
            )
            if currency:
                return str(currency).upper()
            # Monetary device_class requires a unit; default to USD.
            return "USD"
        return None

    @property
    def native_value(self) -> Any:
        """Resolve the element's current value per its kind."""
        raw = self.resolve_element_value(self._element_id)
        if self._kind == "numeric":
            return _parse_float(raw)
        if self._kind == "count":
            return len(_coerce_list(raw))
        if self._auto_kind == "timestamp":
            return _parse_timestamp(raw)
        if self._auto_kind == "numeric":
            return _strict_float(raw)
        return _truncate_state(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose full value / items, plus element metadata."""
        raw = self.resolve_element_value(self._element_id)
        element = self._element_dict()
        attrs: dict[str, Any] = {
            "element_id": self._element_id,
            "element_type": self._element_type,
        }
        if element.get("selector"):
            attrs["selector"] = element.get("selector")

        if self._kind == "text":
            if raw is not None:
                text = str(raw)
                attrs["full_value"] = text[:MAX_ATTR_LENGTH]
                attrs["truncated"] = len(text) > MAX_STATE_LENGTH
        elif self._kind == "count":
            items = _coerce_list(raw)
            attrs["items"] = items[:MAX_ITEMS]
            attrs["item_count"] = len(items)
        return attrs


class PageCrawlPrimarySensor(PageCrawlEntity, SensorEntity):
    """Primary monitor sensor carrying the common monitor attributes."""

    def __init__(
        self,
        coordinator: PageCrawlDataUpdateCoordinator,
        monitor_id: int,
    ) -> None:
        """Initialize the primary sensor."""
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_primary"
        self._attr_name = "Latest change"
        self._attr_icon = "mdi:text-box-search"

    @property
    def native_value(self) -> str | None:
        """The latest change summary / contents, truncated to the state limit."""
        latest = self.monitor.get("latest") or {}
        value = (
            latest.get("human_difference")
            or latest.get("contents")
            or self.monitor.get("status")
        )
        return _truncate_state(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Common monitor attributes shared across the device."""
        monitor = self.monitor
        latest = monitor.get("latest") or {}
        slug = monitor.get("slug")
        base = (self.coordinator.client._base_url or "").rstrip("/")
        diff_url = f"{base}/app/pages/{slug}" if slug and base else None
        return {
            "url": monitor.get("url"),
            "status": monitor.get("status"),
            "last_checked_at": monitor.get("last_checked_at"),
            "change_percent": latest.get("difference"),
            "three_month_difference": latest.get("three_month_difference"),
            "human_difference": latest.get("human_difference"),
            "diff_url": diff_url,
            "screenshot_url": monitor.get("screenshot_url")
            or latest.get("screenshot_url"),
        }


class PageCrawlAiSummarySensor(PageCrawlEntity, SensorEntity):
    """AI-generated summary of the latest change."""

    _attr_translation_key = "ai_summary"
    _attr_icon = "mdi:robot"

    def __init__(
        self,
        coordinator: PageCrawlDataUpdateCoordinator,
        monitor_id: int,
    ) -> None:
        """Initialize the AI summary sensor."""
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_ai_summary"

    @property
    def native_value(self) -> str | None:
        """The AI summary, truncated to the state length limit."""
        return _truncate_state(get_ai_summary(self.monitor))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Full summary text plus AI importance metadata."""
        attrs: dict[str, Any] = {}
        summary = get_ai_summary(self.monitor)
        if summary is not None:
            attrs["full_value"] = summary[:MAX_ATTR_LENGTH]
            attrs["truncated"] = len(summary) > MAX_STATE_LENGTH
        attrs.update(_ai_meta(self.monitor))
        return attrs


class PageCrawlAiPrioritySensor(PageCrawlEntity, SensorEntity):
    """AI priority score (0-100) for the latest change."""

    _attr_translation_key = "ai_priority"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:flag"

    def __init__(
        self,
        coordinator: PageCrawlDataUpdateCoordinator,
        monitor_id: int,
    ) -> None:
        """Initialize the AI priority sensor."""
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_ai_priority"

    @property
    def native_value(self) -> float | None:
        """The AI priority score, or None when unavailable."""
        return get_ai_priority(self.monitor)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """AI importance metadata alongside the score."""
        return _ai_meta(self.monitor)


class PageCrawlLastChangeSensor(PageCrawlEntity, SensorEntity):
    """Human-readable description of the latest change."""

    _attr_translation_key = "last_change"
    _attr_icon = "mdi:history"

    def __init__(
        self,
        coordinator: PageCrawlDataUpdateCoordinator,
        monitor_id: int,
    ) -> None:
        """Initialize the last change sensor."""
        super().__init__(coordinator, monitor_id)
        self._attr_unique_id = f"{self._entry_id}_{monitor_id}_last_change"

    @property
    def native_value(self) -> str | None:
        """The latest human_difference line, truncated to the state limit."""
        latest = self.monitor.get("latest") or {}
        return _truncate_state(latest.get("human_difference"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Full change text plus changed_at and difference."""
        latest = self.monitor.get("latest") or {}
        attrs: dict[str, Any] = {
            "changed_at": latest.get("changed_at"),
            "difference": latest.get("difference"),
        }
        human = latest.get("human_difference")
        if human is not None:
            text = str(human)
            attrs["full_value"] = text[:MAX_ATTR_LENGTH]
            attrs["truncated"] = len(text) > MAX_STATE_LENGTH
        return attrs


class PageCrawlDiagnosticSensor(PageCrawlEntity, SensorEntity):
    """Per-monitor diagnostic sensor (status / last checked / change %)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: PageCrawlDataUpdateCoordinator,
        monitor_id: int,
        description: dict[str, Any],
    ) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator, monitor_id)
        self._key = description["key"]
        self._value_fn: Callable[[dict[str, Any]], Any] = description["value_fn"]
        self._attr_unique_id = (
            f"{self._entry_id}_{monitor_id}_diag_{self._key}"
        )
        self._attr_name = description["name"]
        if description.get("icon"):
            self._attr_icon = description["icon"]
        if description.get("device_class"):
            self._attr_device_class = description["device_class"]
        if description.get("state_class"):
            self._attr_state_class = description["state_class"]
        if description.get("unit"):
            self._attr_native_unit_of_measurement = description["unit"]

    @property
    def native_value(self) -> Any:
        """Return the diagnostic value."""
        return self._value_fn(self.monitor)


def _status_value(monitor: dict[str, Any]) -> str | None:
    return monitor.get("status")


def _last_checked_value(monitor: dict[str, Any]) -> datetime | None:
    raw = monitor.get("last_checked_at") or (
        monitor.get("latest") or {}
    ).get("changed_at")
    if not raw:
        return None
    parsed = dt_util.parse_datetime(str(raw))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = dt_util.as_utc(parsed)
    return parsed


def _last_changed_value(monitor: dict[str, Any]) -> datetime | None:
    raw = (monitor.get("latest") or {}).get("changed_at")
    if not raw:
        return None
    parsed = dt_util.parse_datetime(str(raw))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = dt_util.as_utc(parsed)
    return parsed


def _change_percent_value(monitor: dict[str, Any]) -> float | None:
    return _parse_float((monitor.get("latest") or {}).get("difference"))


_DIAGNOSTIC_DESCRIPTIONS: list[dict[str, Any]] = [
    {
        "key": "status",
        "name": "Status",
        "icon": "mdi:check-network-outline",
        "value_fn": _status_value,
    },
    {
        "key": "last_checked",
        "name": "Last checked",
        "device_class": SensorDeviceClass.TIMESTAMP,
        "value_fn": _last_checked_value,
    },
    {
        "key": "last_changed",
        "name": "Last change date",
        "icon": "mdi:calendar-clock",
        "device_class": SensorDeviceClass.TIMESTAMP,
        "value_fn": _last_changed_value,
    },
    {
        "key": "change_percent",
        "name": "Change percent",
        "icon": "mdi:percent-outline",
        "unit": "%",
        "state_class": SensorStateClass.MEASUREMENT,
        "value_fn": _change_percent_value,
    },
]
