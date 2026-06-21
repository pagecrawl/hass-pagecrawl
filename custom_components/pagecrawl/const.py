"""Constants for the PageCrawl integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "pagecrawl"

# --- OAuth2 ---------------------------------------------------------------
# Relative paths appended to the (per-entry configurable) base URL.
OAUTH_AUTHORIZE_PATH: Final = "/oauth/authorize"
OAUTH_TOKEN_PATH: Final = "/oauth/token"

# PageCrawl base URL.
DEFAULT_BASE_URL: Final = "https://pagecrawl.io"

# First-party public client id baked into the integration so OAuth setup is one
# click for pagecrawl.io users.
DEFAULT_CLIENT_ID: Final = "9f1d6c2e-1a2b-4c3d-8e5f-0a1b2c3d4e5f"

# OAuth scope requested by Home Assistant (read + create + check, no edit/delete).
OAUTH_SCOPE: Final = "integration"

# --- Config entry / options keys -----------------------------------------
CONF_BASE_URL: Final = "base_url"
CONF_WORKSPACE_ID: Final = "workspace_id"
CONF_UPDATE_MODE: Final = "update_mode"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_IMPORT_MODE: Final = "import_mode"
CONF_FOLDERS: Final = "folders"
CONF_MONITORS: Final = "monitors"
CONF_HOOK_ID: Final = "hook_id"
CONF_SIGNING_SECRET: Final = "signing_secret"
CONF_CLOUDHOOK_URL: Final = "cloudhook_url"
CONF_WEBHOOK_ID: Final = "webhook_id"

# --- Update modes ---------------------------------------------------------
UPDATE_MODE_AUTO: Final = "auto"
UPDATE_MODE_PUSH: Final = "push"
UPDATE_MODE_POLL: Final = "poll"

UPDATE_MODES: Final = [UPDATE_MODE_AUTO, UPDATE_MODE_PUSH, UPDATE_MODE_POLL]

# --- Import scope modes ---------------------------------------------------
# Which monitors in the workspace become Home Assistant devices.
IMPORT_MODE_ALL: Final = "all"
IMPORT_MODE_FOLDERS: Final = "folders"
IMPORT_MODE_MONITORS: Final = "monitors"

IMPORT_MODES: Final = [
    IMPORT_MODE_ALL,
    IMPORT_MODE_FOLDERS,
    IMPORT_MODE_MONITORS,
]

# --- Events ---------------------------------------------------------------
EVENT_CHANGE: Final = "pagecrawl_change"

# --- Intervals (seconds) --------------------------------------------------
# When push is active the poll is only a slow reconciliation loop.
DEFAULT_PUSH_RECONCILE_INTERVAL: Final = 1800
# Polling-only default.
DEFAULT_POLL_INTERVAL: Final = 900
# Floor to respect the PageCrawl rate limiter (60/min free).
MIN_SCAN_INTERVAL: Final = 60

# Webhook signature staleness window (seconds).
WEBHOOK_SIGNATURE_MAX_AGE: Final = 300

# --- Services -------------------------------------------------------------
SERVICE_CHECK_NOW: Final = "check_now"
SERVICE_TRACK_PAGE: Final = "track_page"

# --- HMAC header names (PageCrawl outbound webhook) -----------------------
HEADER_SIGNATURE: Final = "X-PageCrawl-Signature"
HEADER_TIMESTAMP: Final = "X-PageCrawl-Timestamp"

# --- Platforms (string values to avoid importing Platform here) -----------
PLATFORM_SENSOR: Final = "sensor"
PLATFORM_BINARY_SENSOR: Final = "binary_sensor"
PLATFORM_BUTTON: Final = "button"

# --- Element type -> platform / device_class / state_class mapping --------
# Keyed by the PageCrawl tracked-element `type`. Used by the platform setup
# code to decide which entity platform owns an element and how to type it.
#
# Each value is a dict with:
#   - "platform":     PLATFORM_SENSOR or PLATFORM_BINARY_SENSOR
#   - "device_class": HA device_class string or None
#   - "state_class":  HA state_class string or None (sensors only)
#   - "kind":         coarse value-handling hint for the platform code, one of:
#                       "numeric"   -> parse contents as float
#                       "boolean"   -> truthy contents
#                       "stock"     -> availability / in-stock parse
#                       "text"      -> string contents, truncate to 255
#                       "count"     -> list length as state, items in attribute
ELEMENT_TYPE_MAP: Final[dict[str, dict[str, str | None]]] = {
    "price": {
        "platform": PLATFORM_SENSOR,
        "device_class": "monetary",
        "state_class": "measurement",
        "kind": "numeric",
    },
    "number": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": "measurement",
        "kind": "numeric",
    },
    "rating": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": "measurement",
        "kind": "numeric",
    },
    "reviews": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": "measurement",
        "kind": "numeric",
    },
    "http_status": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "numeric",
    },
    "boolean": {
        "platform": PLATFORM_BINARY_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "boolean",
    },
    "availability": {
        "platform": PLATFORM_BINARY_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "stock",
    },
    "text": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "text",
    },
    "fullpage": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "text",
    },
    "html": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "text",
    },
    "ai_extract": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "text",
    },
    "json_path": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "text",
    },
    "seo": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "text",
    },
    "links": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "count",
    },
    "feed": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "count",
    },
    "leaderboard": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "count",
    },
    "text_multiple": {
        "platform": PLATFORM_SENSOR,
        "device_class": None,
        "state_class": None,
        "kind": "count",
    },
}

# Fallback mapping for unknown element types: a plain text sensor.
ELEMENT_TYPE_DEFAULT: Final[dict[str, str | None]] = {
    "platform": PLATFORM_SENSOR,
    "device_class": None,
    "state_class": None,
    "kind": "text",
}

# Element types whose free-form text value we may auto-upgrade to a richer
# sensor type (timestamp / numeric) when the value cleanly parses. Kept to the
# extraction-style types: their output is a single value, unlike fullpage/html
# which are large blobs that should always stay plain text.
AUTO_DETECT_TYPES: Final[frozenset[str]] = frozenset(
    {"ai_extract", "text", "json_path", "seo"}
)

# HA state string length limit; long text values are truncated to this.
MAX_STATE_LENGTH: Final = 255
