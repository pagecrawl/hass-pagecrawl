"""Shared fixtures for the PageCrawl integration tests."""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pagecrawl.const import (
    CONF_HOOK_ID,
    CONF_SIGNING_SECRET,
    CONF_WEBHOOK_ID,
    CONF_WORKSPACE_ID,
    DOMAIN,
)

CLIENT_ID = "9f1d6c2e-1a2b-4c3d-8e5f-0a1b2c3d4e5f"
CLIENT_SECRET = ""  # public PKCE client
SIGNING_SECRET = "a" * 64
WORKSPACE_ID = 7
USER_ID = 42


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Enable loading of the pagecrawl custom integration in all tests."""
    return


@pytest.fixture
def expires_at() -> float:
    """Token expiry far in the future."""
    return time.time() + 3600


@pytest.fixture
def token_entry_data(expires_at: float) -> dict[str, Any]:
    """OAuth token payload as stored on a config entry."""
    return {
        "auth_implementation": DOMAIN,
        "token": {
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "expires_in": 3600,
            "expires_at": expires_at,
            "token_type": "Bearer",
            "scope": "integration",
        },
        CONF_WORKSPACE_ID: WORKSPACE_ID,
    }


@pytest.fixture
def mock_config_entry(token_entry_data: dict[str, Any]) -> MockConfigEntry:
    """A configured PageCrawl config entry (push enabled, hook stored)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Acme Workspace",
        unique_id=f"{USER_ID}:{WORKSPACE_ID}",
        data={
            **token_entry_data,
            CONF_HOOK_ID: 123,
            CONF_SIGNING_SECRET: SIGNING_SECRET,
            CONF_WEBHOOK_ID: "test-webhook-id",
        },
        options={"update_mode": "poll", "scan_interval": 900},
    )


@pytest.fixture
async def setup_credentials(hass: HomeAssistant) -> None:
    """Register application credentials so the OAuth flow can run."""
    from homeassistant.components.application_credentials import (
        ClientCredential,
        async_import_client_credential,
    )

    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass,
        DOMAIN,
        ClientCredential(CLIENT_ID, CLIENT_SECRET),
    )


@pytest.fixture
def sample_pages() -> list[dict[str, Any]]:
    """A realistic ?simple=1 /api/pages payload.

    Shaped after Change::toSimpleArray(): each monitor has elements[],
    latest{}, and checks[] whose elements map is keyed by element_id.
    Covers price, number, boolean, availability, fullpage text, and links.
    """
    now = "2026-06-13T10:00:00.000000Z"
    return [
        {
            "id": 1001,
            "slug": "acme-widget",
            "name": "Acme Widget",
            "url": "https://example.com/widget",
            "status": "ok",
            "last_checked_at": now,
            "latest": {
                "contents": "100.00",
                "difference": -5.0,
                "changed_at": now,
                "human_difference": "Price dropped 5%",
                "three_month_difference": -12.5,
                "numeric": 100.0,
                "currency": "USD",
            },
            "elements": [
                {
                    "id": 11,
                    "type": "price",
                    "selector": ".price",
                    "label": "Widget price",
                    "currency": "EUR",
                },
                {
                    "id": 12,
                    "type": "number",
                    "selector": ".qty",
                    "label": "In stock count",
                },
                {
                    "id": 13,
                    "type": "boolean",
                    "selector": ".sale",
                    "label": "On sale",
                },
                {
                    "id": 14,
                    "type": "availability",
                    "selector": ".stock",
                    "label": "Availability",
                },
                {
                    "id": 15,
                    "type": "fullpage",
                    "selector": None,
                    "label": "Full page text",
                },
                {
                    "id": 16,
                    "type": "links",
                    "selector": "a",
                    "label": "Links",
                },
                {
                    "id": 17,
                    "type": "rating",
                    "selector": ".rating",
                    "label": "Rating",
                },
                {
                    "id": 18,
                    "type": "reviews",
                    "selector": ".reviews",
                    "label": "Review count",
                },
                {
                    "id": 19,
                    "type": "http_status",
                    "selector": None,
                    "label": "HTTP status",
                },
                {
                    "id": 20,
                    "type": "text",
                    "selector": ".desc",
                    "label": "Description",
                },
                {
                    "id": 21,
                    "type": "html",
                    "selector": ".html",
                    "label": "HTML block",
                },
                {
                    "id": 22,
                    "type": "ai_extract",
                    "selector": None,
                    "label": "AI extract",
                },
                {
                    "id": 23,
                    "type": "json_path",
                    "selector": "$.price",
                    "label": "JSON value",
                },
                {
                    "id": 24,
                    "type": "seo",
                    "selector": None,
                    "label": "SEO",
                },
                {
                    "id": 25,
                    "type": "feed",
                    "selector": None,
                    "label": "Feed items",
                },
                {
                    "id": 26,
                    "type": "leaderboard",
                    "selector": None,
                    "label": "Leaderboard",
                },
                {
                    "id": 27,
                    "type": "text_multiple",
                    "selector": ".items",
                    "label": "Text multiple",
                },
                {
                    "id": 28,
                    "type": "mystery_future_type",
                    "selector": None,
                    "label": "Unknown type",
                },
                {
                    # Element with no label -> name falls back to type.
                    "id": 29,
                    "type": "text",
                    "selector": None,
                    "label": None,
                },
                {
                    # Availability element reporting out of stock.
                    "id": 30,
                    "type": "availability",
                    "selector": ".oos",
                    "label": "Backorder availability",
                },
            ],
            "checks": [
                {
                    "id": 5001,
                    "status": "ok",
                    "created_at": now,
                    # Poll-path AI data lives on the most recent check.
                    "ai_summary": "The widget price dropped by 5 percent.",
                    "priority_score": 72,
                    "ai_importance_tag": "price_drop",
                    "is_noise": False,
                    "elements": {
                        "11": {
                            "element_id": 11,
                            "contents": "100.00",
                            "difference": -5.0,
                            "changed": True,
                            "original": "$100.00",
                        },
                        "12": {
                            "element_id": 12,
                            "contents": "3",
                            "difference": 0,
                            "changed": False,
                            "original": "3 left",
                        },
                        "13": {
                            "element_id": 13,
                            "contents": "true",
                            "difference": 0,
                            "changed": False,
                            "original": None,
                        },
                        "14": {
                            "element_id": 14,
                            "contents": "In stock",
                            "difference": 0,
                            "changed": False,
                            "original": None,
                        },
                        "15": {
                            "element_id": 15,
                            "contents": "x" * 400,
                            "difference": 0,
                            "changed": False,
                            "original": None,
                        },
                        "16": {
                            "element_id": 16,
                            "contents": (
                                "https://a.example\n"
                                "https://b.example\n"
                                "https://c.example"
                            ),
                            "difference": 0,
                            "changed": False,
                            "original": None,
                        },
                        "17": {
                            "element_id": 17,
                            "contents": "4.5",
                            "original": "4.5 stars",
                        },
                        "18": {
                            "element_id": 18,
                            "contents": "1234",
                            "original": "1,234 reviews",
                        },
                        "19": {
                            "element_id": 19,
                            "contents": "200",
                        },
                        "20": {
                            "element_id": 20,
                            "contents": "A short description",
                        },
                        "21": {
                            "element_id": 21,
                            "contents": "<p>hi</p>",
                        },
                        "22": {
                            "element_id": 22,
                            "contents": "AI extracted value",
                        },
                        "23": {
                            "element_id": 23,
                            "contents": "42",
                        },
                        "24": {
                            "element_id": 24,
                            "contents": "Title | Meta",
                        },
                        "25": {
                            "element_id": 25,
                            "contents": "item one\nitem two",
                        },
                        "26": {
                            "element_id": 26,
                            "contents": ["alpha", "bravo", "charlie", "delta"],
                        },
                        "27": {
                            "element_id": 27,
                            "contents": {"items": ["one", "two"]},
                        },
                        "28": {
                            "element_id": 28,
                            "contents": "unknown-kind value",
                        },
                        "29": {
                            "element_id": 29,
                            "contents": "no-label text",
                        },
                        "30": {
                            "element_id": 30,
                            "contents": "Out of stock",
                        },
                    },
                }
            ],
            "history": [],
        }
    ]


@pytest.fixture
def empty_monitor_pages() -> list[dict[str, Any]]:
    """A monitor with zero elements still yields a (diagnostic) device."""
    now = "2026-06-13T10:00:00.000000Z"
    return [
        {
            "id": 3003,
            "slug": "empty-mon",
            "name": "Empty Monitor",
            "url": "https://example.com/empty",
            "status": "ok",
            "last_checked_at": now,
            "latest": {"contents": None, "changed_at": now},
            "elements": [],
            "checks": [],
            "history": [],
        }
    ]


@pytest.fixture
def no_ai_pages() -> list[dict[str, Any]]:
    """A normal monitor whose check carries no AI summary / priority."""
    now = "2026-06-13T10:00:00.000000Z"
    return [
        {
            "id": 4004,
            "slug": "no-ai-mon",
            "name": "No AI Monitor",
            "url": "https://example.com/no-ai",
            "status": "ok",
            "last_checked_at": now,
            "latest": {
                "contents": "5",
                "difference": 0,
                "changed_at": now,
                "human_difference": "No notable change",
            },
            "elements": [
                {"id": 60, "type": "text", "selector": ".x", "label": "X"}
            ],
            "checks": [
                {
                    "id": 6001,
                    "status": "ok",
                    "created_at": now,
                    "elements": {
                        "60": {"element_id": 60, "contents": "value"}
                    },
                }
            ],
            "history": [],
        }
    ]


@pytest.fixture
def sample_folders() -> list[dict[str, Any]]:
    """A flat folder tree as returned by GET /api/folders."""
    return [
        {"id": 1, "name": "Electronics", "slug": "electronics", "count": 2},
        {"id": 2, "name": "Groceries", "slug": "groceries", "count": 1},
        {"id": 3, "name": "Empty", "slug": "empty", "count": 0},
    ]


@pytest.fixture
def multi_folder_pages() -> dict[str, list[dict[str, Any]]]:
    """Monitors keyed by folder slug, plus the full-workspace list.

    Change::toSimpleArray does NOT carry a folder id, so folder membership is
    only knowable via the per-folder GET /api/pages?folder=<slug> call. This
    fixture maps each folder slug to the monitors that fetch returns, and a
    special "*"/all key for the full workspace list (every monitor).
    """
    now = "2026-06-13T10:00:00.000000Z"

    def _mon(monitor_id: int, slug: str, name: str) -> dict[str, Any]:
        return {
            "id": monitor_id,
            "slug": slug,
            "name": name,
            "url": f"https://example.com/{slug}",
            "status": "ok",
            "last_checked_at": now,
            "latest": {"contents": "1", "changed_at": now},
            "elements": [
                {"id": monitor_id * 10 + 1, "type": "text", "selector": ".x", "label": "X"}
            ],
            "checks": [
                {
                    "id": monitor_id * 100,
                    "status": "ok",
                    "created_at": now,
                    "elements": {
                        str(monitor_id * 10 + 1): {
                            "element_id": monitor_id * 10 + 1,
                            "contents": "value",
                        }
                    },
                }
            ],
            "history": [],
        }

    tv = _mon(2001, "tv", "Smart TV")
    laptop = _mon(2002, "laptop", "Laptop")
    milk = _mon(2003, "milk", "Milk")

    return {
        "electronics": [tv, laptop],
        "groceries": [milk],
        "empty": [],
        "all": [tv, laptop, milk],
    }


@pytest.fixture
def user_payload() -> dict[str, Any]:
    """A /api/user response with multiple workspaces."""
    return {
        "id": USER_ID,
        "name": "Test User",
        "email": "test@example.com",
        "workspaces": [
            {"id": WORKSPACE_ID, "name": "Acme Workspace", "changes_count": 3},
            {"id": 99, "name": "Second Workspace", "changes_count": 0},
        ],
    }


@pytest.fixture
def user_payload_single() -> dict[str, Any]:
    """A /api/user response with a single workspace."""
    return {
        "id": USER_ID,
        "name": "Test User",
        "email": "test@example.com",
        "workspaces": [
            {"id": WORKSPACE_ID, "name": "Acme Workspace", "changes_count": 3},
        ],
    }


@pytest.fixture
def mock_no_push() -> Generator[None, None, None]:
    """Disable push setup so entry setup doesn't try to register webhooks."""
    with patch(
        "custom_components.pagecrawl._async_setup_push",
        return_value=None,
    ):
        yield
