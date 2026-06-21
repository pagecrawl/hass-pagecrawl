"""Tests for PageCrawl entity platforms (sensor / binary_sensor / button)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.pagecrawl.const import DOMAIN, SERVICE_TRACK_PAGE


def _build_client(pages: list[dict[str, Any]]) -> MagicMock:
    client = MagicMock()
    client.workspace_id = 7
    client._base_url = "https://pagecrawl.io"
    client.async_list_pages = AsyncMock(return_value=pages)
    client.async_get_user = AsyncMock(return_value={"id": 42})
    client.async_check_now = AsyncMock(return_value=None)
    client.async_track_page = AsyncMock(return_value={"id": 1})
    client.async_create_hook = AsyncMock(
        return_value={"id": 1, "signing_secret": "s"}
    )
    client.async_delete_hook = AsyncMock(return_value=None)
    return client


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, mock_config_entry, sample_pages, mock_no_push
):
    """Set up the integration with a mocked API client."""
    client = _build_client(sample_pages)
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.pagecrawl.PageCrawlClient", return_value=client
    ), patch(
        "custom_components.pagecrawl.config_entry_oauth2_flow."
        "async_get_config_entry_implementation",
        AsyncMock(),
    ), patch(
        "custom_components.pagecrawl.config_entry_oauth2_flow.OAuth2Session",
        MagicMock(),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    return client


def _state(hass: HomeAssistant, ent_reg, unique_id: str):
    entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
    if entity_id is None:
        entity_id = ent_reg.async_get_entity_id(
            "binary_sensor", DOMAIN, unique_id
        )
    assert entity_id is not None, f"entity for {unique_id} not created"
    return entity_id, hass.states.get(entity_id)


async def test_price_sensor(hass, setup_integration, mock_config_entry):
    """Price element -> monetary sensor with numeric state and currency unit."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_11"
    )
    state = hass.states.get(eid)
    assert state.state == "100.0"
    assert state.attributes["device_class"] == "monetary"
    assert state.attributes["state_class"] == "measurement"
    # Element currency (EUR) takes precedence over latest.currency.
    assert state.attributes["unit_of_measurement"] == "EUR"


async def test_number_sensor(hass, setup_integration, mock_config_entry):
    """Number element -> measurement sensor with numeric state."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_12"
    )
    state = hass.states.get(eid)
    assert state.state == "3.0"
    assert state.attributes["state_class"] == "measurement"


async def test_boolean_binary_sensor(hass, setup_integration, mock_config_entry):
    """Boolean element -> binary_sensor that is on for 'true'."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_13"
    )
    state = hass.states.get(eid)
    assert state.state == "on"


async def test_availability_binary_sensor(
    hass, setup_integration, mock_config_entry
):
    """Availability element -> binary_sensor, on = in stock."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_14"
    )
    state = hass.states.get(eid)
    assert state.state == "on"


async def test_fullpage_text_truncated(
    hass, setup_integration, mock_config_entry
):
    """Long text is truncated to 255 chars; full text in full_value attr."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_15"
    )
    state = hass.states.get(eid)
    assert len(state.state) == 255
    assert state.attributes["full_value"] == "x" * 400
    assert state.attributes["truncated"] is True


async def test_links_count_sensor(hass, setup_integration, mock_config_entry):
    """Links element -> count state with items list in attribute."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_16"
    )
    state = hass.states.get(eid)
    assert state.state == "3"
    assert state.attributes["items"] == [
        "https://a.example",
        "https://b.example",
        "https://c.example",
    ]
    assert state.attributes["item_count"] == 3


async def test_diagnostic_sensors(hass, setup_integration, mock_config_entry):
    """Status / last_checked / change_percent diagnostic sensors exist."""
    ent_reg = er.async_get(hass)
    for key in ("status", "last_checked", "last_changed", "change_percent"):
        eid = ent_reg.async_get_entity_id(
            "sensor",
            DOMAIN,
            f"{mock_config_entry.entry_id}_1001_diag_{key}",
        )
        assert eid is not None, f"diagnostic {key} missing"
        entry = ent_reg.async_get(eid)
        assert entry.entity_category == EntityCategory.DIAGNOSTIC

    status_eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_diag_status"
    )
    assert hass.states.get(status_eid).state == "ok"

    pct_eid = ent_reg.async_get_entity_id(
        "sensor",
        DOMAIN,
        f"{mock_config_entry.entry_id}_1001_diag_change_percent",
    )
    assert hass.states.get(pct_eid).state == "-5.0"


async def test_check_now_button(hass, setup_integration, mock_config_entry):
    """A 'Check now' button exists and calls the API client on press."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "button", DOMAIN, f"{mock_config_entry.entry_id}_1001_check_now"
    )
    assert eid is not None
    await hass.services.async_call(
        "button", "press", {"entity_id": eid}, blocking=True
    )
    setup_integration.async_check_now.assert_awaited_with(1001)


async def test_track_page_service_posts(
    hass, setup_integration, mock_config_entry
):
    """pagecrawl.track_page posts the payload to track-simple."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_TRACK_PAGE,
        {
            "entry_id": mock_config_entry.entry_id,
            "url": "https://example.com/new",
            "name": "New monitor",
            "tracking_mode": "price",
        },
        blocking=True,
    )
    setup_integration.async_track_page.assert_awaited_once()
    payload = setup_integration.async_track_page.await_args.args[0]
    assert payload["url"] == "https://example.com/new"
    assert payload["name"] == "New monitor"
    assert payload["tracking_mode"] == "price"


async def test_device_per_monitor(hass, setup_integration, mock_config_entry):
    """All of a monitor's entities share one device."""
    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(
        identifiers={(DOMAIN, f"{mock_config_entry.entry_id}:1001")}
    )
    assert device is not None
    assert device.name == "Acme Widget"


def _sensor_state(hass, ent_reg, mock_config_entry, element_id):
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_{element_id}"
    )
    assert eid is not None, f"sensor for element {element_id} missing"
    return hass.states.get(eid)


async def test_rating_sensor(hass, setup_integration, mock_config_entry):
    """Rating -> measurement numeric sensor."""
    ent_reg = er.async_get(hass)
    state = _sensor_state(hass, ent_reg, mock_config_entry, 17)
    assert state.state == "4.5"
    assert state.attributes["state_class"] == "measurement"
    assert "device_class" not in state.attributes


async def test_reviews_sensor(hass, setup_integration, mock_config_entry):
    """Reviews -> measurement numeric sensor; commas stripped."""
    ent_reg = er.async_get(hass)
    state = _sensor_state(hass, ent_reg, mock_config_entry, 18)
    assert state.state == "1234.0"
    assert state.attributes["state_class"] == "measurement"


async def test_http_status_sensor(hass, setup_integration, mock_config_entry):
    """http_status -> numeric sensor, no device/state class."""
    ent_reg = er.async_get(hass)
    state = _sensor_state(hass, ent_reg, mock_config_entry, 19)
    assert state.state == "200.0"
    assert "state_class" not in state.attributes


async def test_text_sensors(hass, setup_integration, mock_config_entry):
    """text / html / ai_extract / seo stay plain text sensors."""
    ent_reg = er.async_get(hass)
    expected = {
        20: "A short description",
        21: "<p>hi</p>",
        22: "AI extracted value",
        24: "Title | Meta",
    }
    for element_id, value in expected.items():
        state = _sensor_state(hass, ent_reg, mock_config_entry, element_id)
        assert state.state == value
        assert state.attributes["full_value"] == value
        assert state.attributes["truncated"] is False


async def test_auto_detect_numeric_text_element(
    hass, setup_integration, mock_config_entry
):
    """A json_path element whose value is a pure number is auto-upgraded to a
    numeric (measurement) sensor, with the raw string kept in full_value."""
    from homeassistant.components.sensor import SensorStateClass

    ent_reg = er.async_get(hass)
    # Element 23 is a json_path returning "42".
    state = _sensor_state(hass, ent_reg, mock_config_entry, 23)
    assert state.state == "42.0"
    assert state.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert state.attributes["full_value"] == "42"


async def test_count_sensors(hass, setup_integration, mock_config_entry):
    """feed / leaderboard / text_multiple expose count + items."""
    ent_reg = er.async_get(hass)
    # feed: newline string -> 2 items.
    feed = _sensor_state(hass, ent_reg, mock_config_entry, 25)
    assert feed.state == "2"
    assert feed.attributes["items"] == ["item one", "item two"]
    # leaderboard: list -> 4 items.
    lb = _sensor_state(hass, ent_reg, mock_config_entry, 26)
    assert lb.state == "4"
    assert lb.attributes["item_count"] == 4
    # text_multiple: {items:[...]} dict -> 2 items.
    tm = _sensor_state(hass, ent_reg, mock_config_entry, 27)
    assert tm.state == "2"
    assert tm.attributes["items"] == ["one", "two"]


async def test_unknown_type_falls_back_to_text(
    hass, setup_integration, mock_config_entry
):
    """An unknown element type becomes a plain text sensor."""
    ent_reg = er.async_get(hass)
    state = _sensor_state(hass, ent_reg, mock_config_entry, 28)
    assert state.state == "unknown-kind value"
    assert state.attributes["element_type"] == "mystery_future_type"


async def test_element_without_label_uses_type(
    hass, setup_integration, mock_config_entry
):
    """An element with no label names itself after its type."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_29"
    )
    entry = ent_reg.async_get(eid)
    assert entry.original_name == "text"


async def test_availability_out_of_stock(
    hass, setup_integration, mock_config_entry
):
    """An out-of-stock availability element reads off."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_30"
    )
    assert hass.states.get(eid).state == "off"


async def test_primary_sensor_attributes(
    hass, setup_integration, mock_config_entry
):
    """The primary sensor carries the common monitor attributes."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_primary"
    )
    state = hass.states.get(eid)
    assert state.state == "Price dropped 5%"
    assert state.attributes["url"] == "https://example.com/widget"
    assert state.attributes["status"] == "ok"
    assert state.attributes["diff_url"] == (
        "https://pagecrawl.io/app/pages/acme-widget"
    )


async def test_last_checked_is_timestamp(
    hass, setup_integration, mock_config_entry
):
    """The last_checked diagnostic is a parsed timestamp."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_diag_last_checked"
    )
    entry = ent_reg.async_get(eid)
    from homeassistant.components.sensor import SensorDeviceClass

    assert entry.device_class is None  # set on entity, not registry override
    state = hass.states.get(eid)
    assert state.attributes["device_class"] == SensorDeviceClass.TIMESTAMP
    # Value parses as an ISO datetime.
    assert state.state.startswith("2026-06-13T10:00:00")


async def test_last_changed_is_timestamp(
    hass, setup_integration, mock_config_entry
):
    """The last_changed diagnostic reflects latest.changed_at as a timestamp."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_diag_last_changed"
    )
    assert eid is not None
    from homeassistant.components.sensor import SensorDeviceClass

    state = hass.states.get(eid)
    assert state.attributes["device_class"] == SensorDeviceClass.TIMESTAMP
    assert state.state.startswith("2026-06-13T10:00:00")


async def test_dynamic_entity_creation_on_new_monitor(
    hass, setup_integration, mock_config_entry, sample_pages
):
    """A new monitor appearing via async_set_updated_data creates entities."""
    ent_reg = er.async_get(hass)
    coordinator = mock_config_entry.runtime_data.coordinator

    new_monitor = {
        "id": 2002,
        "slug": "new-mon",
        "name": "New Monitor",
        "url": "https://example.com/new",
        "status": "ok",
        "last_checked_at": "2026-06-13T13:00:00.000000Z",
        "latest": {"contents": "9.99", "changed_at": "2026-06-13T13:00:00Z"},
        "elements": [
            {"id": 50, "type": "price", "selector": ".p", "label": "Price"}
        ],
        "checks": [
            {"id": 1, "elements": {"50": {"element_id": 50, "contents": "9.99"}}}
        ],
        "history": [],
    }
    data = dict(coordinator.data)
    data[2002] = new_monitor
    coordinator.async_set_updated_data(data)
    await hass.async_block_till_done()

    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_2002_50"
    )
    assert eid is not None
    assert hass.states.get(eid).state == "9.99"


async def test_zero_element_monitor_still_has_device(
    hass, mock_config_entry, empty_monitor_pages, mock_no_push
):
    """A monitor with no elements still yields a non-empty device."""
    from homeassistant.helpers import device_registry as dr

    client = _build_client(empty_monitor_pages)
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.pagecrawl.PageCrawlClient", return_value=client
    ), patch(
        "custom_components.pagecrawl.config_entry_oauth2_flow."
        "async_get_config_entry_implementation",
        AsyncMock(),
    ), patch(
        "custom_components.pagecrawl.config_entry_oauth2_flow.OAuth2Session",
        MagicMock(),
    ):
        assert await hass.config_entries.async_setup(
            mock_config_entry.entry_id
        )
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    # Primary + 3 diagnostic sensors exist even with zero elements.
    assert (
        ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{mock_config_entry.entry_id}_3003_primary"
        )
        is not None
    )
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(
        identifiers={(DOMAIN, f"{mock_config_entry.entry_id}:3003")}
    )
    assert device is not None


def test_detect_text_kind_classifies_values():
    """Auto-detect upgrades dates and pure numbers, leaves prose as text."""
    from custom_components.pagecrawl.sensor import _detect_text_kind

    assert _detect_text_kind("2026-06-23") == "timestamp"
    assert _detect_text_kind("2026-06-23T14:30:00Z") == "timestamp"
    assert _detect_text_kind("19.99") == "numeric"
    assert _detect_text_kind("1,234") == "numeric"
    # Prose that merely contains a number stays text.
    assert _detect_text_kind("Price: $19.99") == "text"
    assert _detect_text_kind("Starfall Demo Mission") == "text"
    assert _detect_text_kind("") == "text"
    assert _detect_text_kind(None) == "text"


def test_parse_timestamp_returns_aware_datetime():
    """Dates and datetimes become tz-aware; non-dates return None."""
    from custom_components.pagecrawl.sensor import _parse_timestamp

    iso_date = _parse_timestamp("2026-06-23")
    assert iso_date is not None
    assert iso_date.tzinfo is not None
    assert (iso_date.year, iso_date.month, iso_date.day) == (2026, 6, 23)

    iso_dt = _parse_timestamp("2026-06-23T14:30:00+00:00")
    assert iso_dt is not None
    assert iso_dt.tzinfo is not None

    # A later check that no longer parses degrades to None (sensor
    # unavailable), never raising on a timestamp-typed entity.
    assert _parse_timestamp("TBD") is None
    assert _parse_timestamp(None) is None


def test_strict_float_only_parses_whole_numeric_strings():
    """Strict parse accepts clean numbers, rejects prose and booleans."""
    from custom_components.pagecrawl.sensor import _strict_float

    assert _strict_float("19.99") == 19.99
    assert _strict_float("1,234") == 1234.0
    assert _strict_float("2026") == 2026.0
    assert _strict_float("Price: $19.99") is None
    assert _strict_float("") is None
    assert _strict_float(True) is None


async def test_ai_extract_iso_date_becomes_timestamp_sensor(
    hass, setup_integration, mock_config_entry
):
    """An ai_extract element whose value is an ISO date is auto-typed as a
    TIMESTAMP sensor, with the raw string still in full_value."""
    from homeassistant.components.sensor import SensorDeviceClass

    ent_reg = er.async_get(hass)
    coordinator = mock_config_entry.runtime_data.coordinator

    monitor = {
        "id": 4004,
        "slug": "next-launch",
        "name": "Next launch",
        "url": "https://example.com/launches",
        "status": "ok",
        "last_checked_at": "2026-06-13T13:00:00.000000Z",
        "latest": {
            "contents": "2026-06-23",
            "changed_at": "2026-06-13T13:00:00Z",
        },
        "elements": [
            {"id": 70, "type": "ai_extract", "selector": "content",
             "label": "Launch date"}
        ],
        "checks": [
            {"id": 1, "elements": {
                "70": {"element_id": 70, "contents": "2026-06-23"}}}
        ],
        "history": [],
    }
    data = dict(coordinator.data)
    data[4004] = monitor
    coordinator.async_set_updated_data(data)
    await hass.async_block_till_done()

    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_4004_70"
    )
    assert eid is not None
    state = hass.states.get(eid)
    assert state.attributes["device_class"] == SensorDeviceClass.TIMESTAMP
    # Date-only is read as midnight in HA's configured time zone, so the exact
    # UTC offset depends on the test tz; assert the calendar date and that the
    # value is a tz-aware ISO timestamp.
    assert "2026-06-23" in state.state
    assert state.state.endswith("+00:00")
    # Raw extracted string preserved for templating / display.
    assert state.attributes["full_value"] == "2026-06-23"


async def test_check_now_service_targets_device(
    hass, setup_integration, mock_config_entry
):
    """pagecrawl.check_now targeting a device calls the client + refreshes."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.pagecrawl.const import SERVICE_CHECK_NOW

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(
        identifiers={(DOMAIN, f"{mock_config_entry.entry_id}:1001")}
    )
    await hass.services.async_call(
        DOMAIN,
        SERVICE_CHECK_NOW,
        {"device_id": [device.id]},
        blocking=True,
    )
    setup_integration.async_check_now.assert_awaited_with(1001)


async def test_check_now_service_targets_slug(
    hass, setup_integration, mock_config_entry
):
    """pagecrawl.check_now targeting a slug resolves the right monitor."""
    from custom_components.pagecrawl.const import SERVICE_CHECK_NOW

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CHECK_NOW,
        {"slug": ["acme-widget"]},
        blocking=True,
    )
    setup_integration.async_check_now.assert_awaited_with(1001)


async def test_check_now_service_targets_monitor_id(
    hass, setup_integration, mock_config_entry
):
    """pagecrawl.check_now targeting a monitor_id resolves the right monitor."""
    from custom_components.pagecrawl.const import SERVICE_CHECK_NOW

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CHECK_NOW,
        {"monitor_id": ["1001"]},
        blocking=True,
    )
    setup_integration.async_check_now.assert_awaited_with(1001)


async def test_check_now_service_unknown_slug_no_call(
    hass, setup_integration, mock_config_entry
):
    """An unknown slug resolves no targets, so the client is not called."""
    from custom_components.pagecrawl.const import SERVICE_CHECK_NOW

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CHECK_NOW,
        {"slug": ["does-not-exist"]},
        blocking=True,
    )
    setup_integration.async_check_now.assert_not_awaited()


async def test_track_page_error_raises(
    hass, setup_integration, mock_config_entry
):
    """A failing track_page surfaces as a HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.pagecrawl.api import PageCrawlApiError

    setup_integration.async_track_page = AsyncMock(
        side_effect=PageCrawlApiError("bad url")
    )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TRACK_PAGE,
            {"entry_id": mock_config_entry.entry_id, "url": "https://x"},
            blocking=True,
        )


async def test_ai_summary_sensor_poll_shape(
    hass, setup_integration, mock_config_entry
):
    """AI summary sensor reads checks[0].ai_summary (poll path)."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_ai_summary"
    )
    assert eid is not None
    state = hass.states.get(eid)
    assert state.state == "The widget price dropped by 5 percent."
    assert (
        state.attributes["full_value"]
        == "The widget price dropped by 5 percent."
    )
    assert state.attributes["truncated"] is False
    assert state.attributes["ai_importance_tag"] == "price_drop"
    assert state.attributes["is_noise"] is False


async def test_ai_summary_sensor_truncates(
    hass, setup_integration, mock_config_entry
):
    """A long AI summary is truncated to the state limit; full text in attr."""
    from custom_components.pagecrawl.const import MAX_STATE_LENGTH

    long_summary = "z" * 400
    coordinator = mock_config_entry.runtime_data.coordinator
    data = dict(coordinator.data)
    monitor = dict(data[1001])
    checks = [dict(monitor["checks"][0])]
    checks[0]["ai_summary"] = long_summary
    monitor["checks"] = checks
    data[1001] = monitor
    coordinator.async_set_updated_data(data)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_ai_summary"
    )
    state = hass.states.get(eid)
    assert len(state.state) == MAX_STATE_LENGTH
    assert state.state.endswith("…")
    assert state.attributes["full_value"] == long_summary
    assert state.attributes["truncated"] is True


async def test_ai_summary_sensor_push_shape(
    hass, setup_integration, mock_config_entry
):
    """AI summary sensor reads latest.ai_summary (push path) preferentially."""
    coordinator = mock_config_entry.runtime_data.coordinator
    data = dict(coordinator.data)
    monitor = dict(data[1001])
    latest = dict(monitor["latest"])
    latest["ai_summary"] = "Pushed AI summary"
    monitor["latest"] = latest
    data[1001] = monitor
    coordinator.async_set_updated_data(data)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_ai_summary"
    )
    state = hass.states.get(eid)
    assert state.state == "Pushed AI summary"


async def test_ai_priority_sensor_poll_shape(
    hass, setup_integration, mock_config_entry
):
    """AI priority sensor reads checks[0].priority_score and is diagnostic."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_ai_priority"
    )
    assert eid is not None
    entry = ent_reg.async_get(eid)
    assert entry.entity_category == EntityCategory.DIAGNOSTIC
    state = hass.states.get(eid)
    assert state.state == "72.0"
    assert state.attributes["state_class"] == "measurement"


async def test_ai_priority_sensor_push_shape(
    hass, setup_integration, mock_config_entry
):
    """AI priority sensor prefers latest.ai_priority_score (push path)."""
    coordinator = mock_config_entry.runtime_data.coordinator
    data = dict(coordinator.data)
    monitor = dict(data[1001])
    latest = dict(monitor["latest"])
    latest["ai_priority_score"] = 91
    monitor["latest"] = latest
    data[1001] = monitor
    coordinator.async_set_updated_data(data)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_ai_priority"
    )
    state = hass.states.get(eid)
    assert state.state == "91.0"


async def test_ai_sensors_absent_without_ai_data(
    hass, mock_config_entry, no_ai_pages, mock_no_push
):
    """AI sensors are NOT created for a monitor with no AI data."""
    client = _build_client(no_ai_pages)
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.pagecrawl.PageCrawlClient", return_value=client
    ), patch(
        "custom_components.pagecrawl.config_entry_oauth2_flow."
        "async_get_config_entry_implementation",
        AsyncMock(),
    ), patch(
        "custom_components.pagecrawl.config_entry_oauth2_flow.OAuth2Session",
        MagicMock(),
    ):
        assert await hass.config_entries.async_setup(
            mock_config_entry.entry_id
        )
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    assert (
        ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{mock_config_entry.entry_id}_4004_ai_summary"
        )
        is None
    )
    assert (
        ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{mock_config_entry.entry_id}_4004_ai_priority"
        )
        is None
    )
    # The last_change sensor is created for every monitor, AI or not.
    assert (
        ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{mock_config_entry.entry_id}_4004_last_change"
        )
        is not None
    )


async def test_ai_sensors_appear_dynamically(
    hass, mock_config_entry, no_ai_pages, mock_no_push
):
    """AI sensors materialize once AI data shows up via async_set_updated_data."""
    client = _build_client(no_ai_pages)
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.pagecrawl.PageCrawlClient", return_value=client
    ), patch(
        "custom_components.pagecrawl.config_entry_oauth2_flow."
        "async_get_config_entry_implementation",
        AsyncMock(),
    ), patch(
        "custom_components.pagecrawl.config_entry_oauth2_flow.OAuth2Session",
        MagicMock(),
    ):
        assert await hass.config_entries.async_setup(
            mock_config_entry.entry_id
        )
        await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    assert (
        ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{mock_config_entry.entry_id}_4004_ai_summary"
        )
        is None
    )

    coordinator = mock_config_entry.runtime_data.coordinator
    data = dict(coordinator.data)
    monitor = dict(data[4004])
    latest = dict(monitor["latest"])
    latest["ai_summary"] = "Now there is AI data"
    latest["ai_priority_score"] = 60
    monitor["latest"] = latest
    data[4004] = monitor
    coordinator.async_set_updated_data(data)
    await hass.async_block_till_done()

    summary_eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_4004_ai_summary"
    )
    assert summary_eid is not None
    assert hass.states.get(summary_eid).state == "Now there is AI data"
    priority_eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_4004_ai_priority"
    )
    assert priority_eid is not None
    assert hass.states.get(priority_eid).state == "60.0"


async def test_last_change_sensor(hass, setup_integration, mock_config_entry):
    """Last change sensor exposes latest.human_difference + metadata."""
    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_last_change"
    )
    assert eid is not None
    state = hass.states.get(eid)
    assert state.state == "Price dropped 5%"
    assert state.attributes["full_value"] == "Price dropped 5%"
    assert state.attributes["truncated"] is False
    assert state.attributes["difference"] == -5.0
    assert state.attributes["changed_at"] == "2026-06-13T10:00:00.000000Z"


async def test_last_change_sensor_truncates(
    hass, setup_integration, mock_config_entry
):
    """A long human_difference is truncated; full text retained in attr."""
    from custom_components.pagecrawl.const import MAX_STATE_LENGTH

    long_diff = "d" * 400
    coordinator = mock_config_entry.runtime_data.coordinator
    data = dict(coordinator.data)
    monitor = dict(data[1001])
    latest = dict(monitor["latest"])
    latest["human_difference"] = long_diff
    monitor["latest"] = latest
    data[1001] = monitor
    coordinator.async_set_updated_data(data)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry.entry_id}_1001_last_change"
    )
    state = hass.states.get(eid)
    assert len(state.state) == MAX_STATE_LENGTH
    assert state.attributes["full_value"] == long_diff
    assert state.attributes["truncated"] is True


async def test_diagnostics_redacts_secrets(hass, mock_config_entry):
    """Config-entry diagnostics redact tokens, secrets, hook + webhook ids.

    Called directly on the (not-yet-set-up) entry so the push artifacts in
    `data` are still present and we can assert they are redacted.
    """
    from custom_components.pagecrawl.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    data = diag["entry"]["data"]
    # The whole token object is redacted (it holds access + refresh tokens).
    assert data["token"] == "**REDACTED**"
    assert data["signing_secret"] == "**REDACTED**"
    assert data["hook_id"] == "**REDACTED**"
    assert data["webhook_id"] == "**REDACTED**"
    # Non-secret data is preserved.
    assert data["workspace_id"] == 7
