"""PageCrawl API client wrapping an OAuth2 session."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError, ClientResponse

from homeassistant.helpers import config_entry_oauth2_flow

from .const import DEFAULT_BASE_URL

_LOGGER = logging.getLogger(__name__)


class PageCrawlError(Exception):
    """Base error for the PageCrawl client."""


class PageCrawlAuthError(PageCrawlError):
    """Raised on 401/403 responses (token invalid / insufficient scope)."""


class PageCrawlRateLimitError(PageCrawlError):
    """Raised on 429 responses; carries the Retry-After hint in seconds."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        """Store the retry-after hint."""
        super().__init__(message)
        self.retry_after = retry_after


class PageCrawlApiError(PageCrawlError):
    """Raised on any other non-success response or transport error."""


class PageCrawlClient:
    """Async client for the PageCrawl REST API.

    All requests go through HA's `OAuth2Session`, which injects the bearer
    token and refreshes it automatically. Every call is scoped to a workspace
    via the `?workspace_id=` query param when one is set.
    """

    def __init__(
        self,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
        base_url: str = DEFAULT_BASE_URL,
        workspace_id: int | str | None = None,
    ) -> None:
        """Initialize the client."""
        self._oauth_session = oauth_session
        self._base_url = base_url.rstrip("/")
        self._workspace_id = workspace_id

    @property
    def workspace_id(self) -> int | str | None:
        """Return the workspace this client is pinned to."""
        return self._workspace_id

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build query params, always including workspace_id when set."""
        params: dict[str, Any] = {}
        if self._workspace_id is not None:
            params["workspace_id"] = self._workspace_id
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        return params

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Perform an authenticated request and return decoded JSON."""
        url = f"{self._base_url}{path}"
        try:
            response: ClientResponse = await self._oauth_session.async_request(
                method,
                url,
                params=params,
                json=json,
                headers={"Accept": "application/json"},
            )
        except ClientError as err:
            raise PageCrawlApiError(f"Transport error calling {path}: {err}") from err

        return await self._handle_response(response, path)

    async def _handle_response(
        self, response: ClientResponse, path: str
    ) -> Any:
        """Map HTTP status codes to typed errors and decode the body."""
        status = response.status

        if status in (401, 403):
            await response.release()
            raise PageCrawlAuthError(
                f"Authentication/authorization failed for {path} (HTTP {status})"
            )

        if status == 429:
            retry_after_raw = response.headers.get("Retry-After")
            retry_after: int | None = None
            if retry_after_raw is not None:
                try:
                    retry_after = int(retry_after_raw)
                except (TypeError, ValueError):
                    retry_after = None
            await response.release()
            raise PageCrawlRateLimitError(
                f"Rate limited on {path} (HTTP 429)", retry_after=retry_after
            )

        if status >= 400:
            text = await response.text()
            raise PageCrawlApiError(
                f"Unexpected response from {path} (HTTP {status}): {text[:500]}"
            )

        if status == 204 or not response.content_length:
            await response.read()
            try:
                return await response.json(content_type=None)
            except (ValueError, ClientError):
                return None

        try:
            return await response.json(content_type=None)
        except (ValueError, ClientError) as err:
            raise PageCrawlApiError(
                f"Invalid JSON from {path}: {err}"
            ) from err

    # --- Endpoints --------------------------------------------------------

    async def async_get_user(self) -> dict[str, Any]:
        """GET /api/user -> the authenticated user + accessible workspaces."""
        data = await self._request("GET", "/api/user", params=self._params())
        if not isinstance(data, dict):
            raise PageCrawlApiError("Unexpected /api/user response shape")
        return data

    async def async_list_pages(
        self,
        folder: str | None = None,
        workspace_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /api/pages?simple=1&take=1 -> list of monitor dicts."""
        params = self._params({"simple": 1, "take": 1, "folder": folder})
        if workspace_id is not None:
            params["workspace_id"] = workspace_id
        data = await self._request("GET", "/api/pages", params=params)
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if not isinstance(data, list):
            raise PageCrawlApiError("Unexpected /api/pages response shape")
        return data

    async def async_list_folders(
        self,
        workspace_id: int | str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /api/folders -> list of {id, name, slug} folder dicts."""
        params = self._params()
        if workspace_id is not None:
            params["workspace_id"] = workspace_id
        data = await self._request("GET", "/api/folders", params=params)
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if not isinstance(data, list):
            raise PageCrawlApiError("Unexpected /api/folders response shape")
        return data

    async def async_check_now(self, page_id: int | str) -> Any:
        """PUT /api/pages/{id}/check -> trigger an immediate check."""
        return await self._request(
            "PUT", f"/api/pages/{page_id}/check", params=self._params()
        )

    async def async_track_page(self, payload: dict[str, Any]) -> Any:
        """POST /api/track-simple -> create a new monitor."""
        return await self._request(
            "POST", "/api/track-simple", params=self._params(), json=payload
        )

    async def async_create_hook(
        self,
        target_url: str,
        workspace_id: int | str | None = None,
    ) -> dict[str, Any]:
        """POST /api/hooks -> create a catch-all webhook for this workspace."""
        params = self._params()
        if workspace_id is not None:
            params["workspace_id"] = workspace_id
        # Subscribe to change/error events for every monitor in the workspace.
        # Price changes also surface as "change_detected", so this covers them.
        payload = {
            "target_url": target_url,
            "match_type": "all",
            "events": ["change_detected", "error"],
        }
        data = await self._request(
            "POST", "/api/hooks", params=params, json=payload
        )
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            data = data["data"]
        if not isinstance(data, dict):
            raise PageCrawlApiError("Unexpected /api/hooks response shape")
        return data

    async def async_delete_hook(self, hook_id: int | str) -> Any:
        """DELETE /api/hooks/{id} -> remove the managed webhook."""
        return await self._request(
            "DELETE", f"/api/hooks/{hook_id}", params=self._params()
        )
