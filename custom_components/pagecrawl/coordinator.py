"""Data update coordinator for PageCrawl."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PageCrawlAuthError,
    PageCrawlClient,
    PageCrawlError,
    PageCrawlRateLimitError,
)
from .const import (
    DOMAIN,
    EVENT_CHANGE,
    IMPORT_MODE_ALL,
    IMPORT_MODE_FOLDERS,
    IMPORT_MODE_MONITORS,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class PageCrawlDataUpdateCoordinator(DataUpdateCoordinator[dict[int, dict[str, Any]]]):
    """Coordinate updates of monitor data, keyed by monitor id."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PageCrawlClient,
        update_interval: int,
        import_mode: str = IMPORT_MODE_ALL,
        folders: list[str] | None = None,
        monitors: list[str] | None = None,
    ) -> None:
        """Initialize the coordinator.

        Import-scope filtering contract:
          - ``import_mode == "all"``: every monitor in the workspace is in scope.
          - ``import_mode == "folders"``: only monitors in ``folders`` (a list of
            folder slugs); fetched via one ``async_list_pages(folder=slug)`` per
            slug and merged/deduped by id.
          - ``import_mode == "monitors"``: only monitors whose id (as a string)
            is in ``monitors``.
        The in-scope id set is exposed via ``in_scope_ids`` after each refresh so
        __init__ can prune stale devices; ``apply_webhook_update`` ignores pushes
        for out-of-scope monitors.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=max(update_interval, MIN_SCAN_INTERVAL)),
        )
        self.entry = entry
        self.client = client
        self.import_mode = import_mode
        self.folders = list(folders or [])
        self.monitors = list(monitors or [])
        self._base_interval = max(update_interval, MIN_SCAN_INTERVAL)
        # Tracks last seen latest.changed_at per monitor id, for change events.
        self._last_changed_at: dict[int, str | None] = {}
        # Monitor ids currently in scope (populated after each refresh).
        self.in_scope_ids: set[int] = set()

    async def _async_fetch_pages(self) -> list[dict[str, Any]]:
        """Fetch the in-scope pages according to the import mode."""
        if self.import_mode == IMPORT_MODE_FOLDERS and self.folders:
            # One call per folder slug, merged/deduped by monitor id.
            merged: dict[int, dict[str, Any]] = {}
            for slug in self.folders:
                pages = await self.client.async_list_pages(
                    folder=slug,
                    workspace_id=self.client.workspace_id,
                )
                for page in pages:
                    monitor_id = page.get("id")
                    if monitor_id is None:
                        continue
                    merged[int(monitor_id)] = page
            return list(merged.values())

        # ALL and MONITORS both fetch the full workspace list; MONITORS filters
        # by id below.
        return await self.client.async_list_pages(
            workspace_id=self.client.workspace_id,
        )

    def _in_scope(self, monitor_id: int) -> bool:
        """Return whether a monitor id is within the configured import scope."""
        if self.import_mode == IMPORT_MODE_MONITORS:
            return str(monitor_id) in self.monitors
        # ALL is always in scope; FOLDERS scope is already enforced by the
        # per-folder fetch in _async_fetch_pages.
        return True

    async def _async_update_data(self) -> dict[int, dict[str, Any]]:
        """Fetch the monitor list and key it by monitor id."""
        try:
            pages = await self._async_fetch_pages()
        except PageCrawlAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PageCrawlRateLimitError as err:
            self._back_off(err.retry_after)
            raise UpdateFailed(f"Rate limited: {err}") from err
        except PageCrawlError as err:
            raise UpdateFailed(f"Error communicating with PageCrawl: {err}") from err

        # Successful poll: restore the base interval if we had backed off.
        self._restore_interval()

        result: dict[int, dict[str, Any]] = {}
        for page in pages:
            monitor_id = page.get("id")
            if monitor_id is None:
                continue
            monitor_id = int(monitor_id)
            if not self._in_scope(monitor_id):
                continue
            result[monitor_id] = page
            self._maybe_fire_change(monitor_id, page)

        self.in_scope_ids = set(result)
        return result

    def _maybe_fire_change(self, monitor_id: int, page: dict[str, Any]) -> None:
        """Fire the change event when a monitor's changed_at advances."""
        latest = page.get("latest") or {}
        changed_at = latest.get("changed_at")
        previous = self._last_changed_at.get(monitor_id)
        # Only fire when we have a prior value and it advanced (skip first seed).
        if (
            monitor_id in self._last_changed_at
            and changed_at is not None
            and changed_at != previous
        ):
            self._fire_change_event(monitor_id, page)
        self._last_changed_at[monitor_id] = changed_at

    def _fire_change_event(self, monitor_id: int, page: dict[str, Any]) -> None:
        """Emit the pagecrawl_change bus event for automations."""
        latest = page.get("latest") or {}
        slug = page.get("slug")
        self.hass.bus.async_fire(
            EVENT_CHANGE,
            {
                "entry_id": self.entry.entry_id,
                "monitor_id": monitor_id,
                "name": page.get("name"),
                "url": page.get("url"),
                "slug": slug,
                "status": page.get("status"),
                "contents": latest.get("contents"),
                "difference": latest.get("difference"),
                "human_difference": latest.get("human_difference"),
                "ai_summary": _event_ai_summary(page),
                "ai_priority_score": _event_ai_priority(page),
                "diff_url": f"https://pagecrawl.io/app/pages/{slug}" if slug else None,
                "changed_at": latest.get("changed_at"),
            },
        )

    def apply_webhook_update(self, payload: dict[str, Any]) -> None:
        """Merge a pushed change into the data and notify listeners.

        Called by the webhook handler after signature verification. The payload
        is a PageCrawl outbound webhook body. If the monitor is unknown (new),
        we trigger a refresh so its device/entities get created.
        """
        monitor_id_raw = payload.get("id")
        if monitor_id_raw is None:
            return
        monitor_id = int(monitor_id_raw)

        current = dict(self.data or {})

        if monitor_id not in current:
            # For monitor-scoped imports, only refresh for explicitly selected
            # monitors; folder/all scopes resolve membership on the refresh.
            if (
                self.import_mode == IMPORT_MODE_MONITORS
                and str(monitor_id) not in self.monitors
            ):
                return
            # New (in-scope) monitor delivered via webhook: refresh to build
            # entities. Folder scope is reconciled by the poll, which only keeps
            # monitors returned by the per-folder fetch.
            self.hass.async_create_task(self.async_request_refresh())
            return

        monitor = dict(current[monitor_id])
        latest = dict(monitor.get("latest") or {})
        # Merge the pushed fields into the monitor's latest snapshot.
        for key in (
            "contents",
            "difference",
            "human_difference",
            "changed_at",
            "short_summary",
            "ai_summary",
            "ai_priority_score",
        ):
            if key in payload:
                latest[key] = payload[key]
        monitor["latest"] = latest

        if "status" in payload:
            monitor["status"] = payload["status"]
        if "changed_at" in payload:
            monitor["last_checked_at"] = payload.get(
                "last_checked_at", payload.get("changed_at")
            )
        # Merge element-level values so per-element sensors update instantly.
        if isinstance(payload.get("page_elements"), list):
            monitor["page_elements"] = payload["page_elements"]
            self._merge_pushed_elements(monitor, payload["page_elements"])

        current[monitor_id] = monitor
        self.async_set_updated_data(current)

        # Update change tracking and fire the event for the push delivery.
        self._last_changed_at[monitor_id] = latest.get("changed_at")
        self._fire_change_event(monitor_id, monitor)

    @staticmethod
    def _merge_pushed_elements(
        monitor: dict[str, Any], page_elements: list[dict[str, Any]]
    ) -> None:
        """Apply pushed element values into checks[0].elements by element id.

        Per-element sensors resolve their value from
        ``checks[0].elements[<element_id>].contents``. The webhook payload carries
        ``page_elements[].element_id`` (the stable tracked-element id) alongside a
        per-reading ``id``, so we key the merge on ``element_id`` to update every
        element sensor without waiting for a poll.
        """
        # Build a fresh checks[0] snapshot we can mutate without aliasing.
        checks = [dict(c) for c in (monitor.get("checks") or [])]
        if checks:
            primary_check = dict(checks[0])
        else:
            primary_check = {"elements": {}}
            checks = [primary_check]
        elements = dict(primary_check.get("elements") or {})

        for element in page_elements:
            tracked_id = element.get("element_id")
            if tracked_id is None:
                continue
            key = str(tracked_id)
            merged = dict(elements.get(key) or {})
            for field in ("contents", "difference", "changed", "original", "hash"):
                if field in element:
                    merged[field] = element[field]
            merged.setdefault("element_id", tracked_id)
            elements[key] = merged

        primary_check["elements"] = elements
        checks[0] = primary_check
        monitor["checks"] = checks

    def _back_off(self, retry_after: int | None) -> None:
        """Temporarily lengthen the update interval after a 429."""
        if retry_after and retry_after > 0:
            new_interval = max(retry_after, self._base_interval)
        else:
            new_interval = self._base_interval * 2
        self.update_interval = timedelta(seconds=new_interval)
        _LOGGER.debug("Backing off PageCrawl poll to %ss", new_interval)

    def _restore_interval(self) -> None:
        """Reset the update interval to the configured base value."""
        target = timedelta(seconds=self._base_interval)
        if self.update_interval != target:
            self.update_interval = target


def _event_ai_summary(page: dict[str, Any]) -> str | None:
    """Resolve the AI summary from push (latest) or poll (checks[0]) shape."""
    latest = page.get("latest") or {}
    for value in (latest.get("ai_summary"), latest.get("short_summary")):
        if value:
            return value
    checks = page.get("checks") or []
    if checks:
        first = checks[0] or {}
        for value in (first.get("ai_summary"), first.get("short_summary")):
            if value:
                return value
    return None


def _event_ai_priority(page: dict[str, Any]) -> float | None:
    """Resolve the AI priority score from push (latest) or poll (checks[0])."""
    latest = page.get("latest") or {}
    raw = latest.get("ai_priority_score")
    if raw is None:
        checks = page.get("checks") or []
        raw = (checks[0] or {}).get("priority_score") if checks else None
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
