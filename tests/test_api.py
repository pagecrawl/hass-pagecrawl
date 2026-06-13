"""Tests for the PageCrawl API client error mapping + request shaping."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError

from custom_components.pagecrawl.api import (
    PageCrawlApiError,
    PageCrawlAuthError,
    PageCrawlClient,
    PageCrawlRateLimitError,
)


def _response(
    status: int,
    *,
    json_body: Any = None,
    text_body: str = "",
    headers: dict[str, str] | None = None,
    content_length: int | None = 1,
) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.headers = headers or {}
    resp.content_length = content_length
    resp.json = AsyncMock(return_value=json_body)
    resp.text = AsyncMock(return_value=text_body)
    resp.read = AsyncMock(return_value=b"")
    resp.release = AsyncMock(return_value=None)
    return resp


def _client(response: MagicMock, workspace_id: int | None = 7) -> tuple[
    PageCrawlClient, MagicMock
]:
    session = MagicMock()
    session.async_request = AsyncMock(return_value=response)
    client = PageCrawlClient(session, "https://pagecrawl.io/", workspace_id)
    return client, session


async def test_get_user_ok() -> None:
    client, session = _client(_response(200, json_body={"id": 42}))
    assert (await client.async_get_user())["id"] == 42
    # workspace_id always sent.
    assert session.async_request.await_args.kwargs["params"]["workspace_id"] == 7


async def test_auth_error_maps_401() -> None:
    client, _ = _client(_response(401))
    with pytest.raises(PageCrawlAuthError):
        await client.async_get_user()


async def test_auth_error_maps_403() -> None:
    client, _ = _client(_response(403))
    with pytest.raises(PageCrawlAuthError):
        await client.async_list_pages()


async def test_rate_limit_maps_429_with_retry_after() -> None:
    client, _ = _client(_response(429, headers={"Retry-After": "42"}))
    with pytest.raises(PageCrawlRateLimitError) as exc:
        await client.async_list_pages()
    assert exc.value.retry_after == 42


async def test_rate_limit_maps_429_without_retry_after() -> None:
    client, _ = _client(_response(429))
    with pytest.raises(PageCrawlRateLimitError) as exc:
        await client.async_list_pages()
    assert exc.value.retry_after is None


async def test_other_error_maps_to_api_error() -> None:
    client, _ = _client(_response(500, text_body="boom"))
    with pytest.raises(PageCrawlApiError):
        await client.async_list_pages()


async def test_transport_error_maps_to_api_error() -> None:
    session = MagicMock()
    session.async_request = AsyncMock(side_effect=ClientError("dead"))
    client = PageCrawlClient(session, "https://pagecrawl.io")
    with pytest.raises(PageCrawlApiError):
        await client.async_get_user()


async def test_list_pages_unwraps_data_envelope() -> None:
    client, _ = _client(
        _response(200, json_body={"data": [{"id": 1}, {"id": 2}]})
    )
    pages = await client.async_list_pages()
    assert [p["id"] for p in pages] == [1, 2]


async def test_list_pages_bad_shape_raises() -> None:
    client, _ = _client(_response(200, json_body={"nope": True}))
    with pytest.raises(PageCrawlApiError):
        await client.async_list_pages()


async def test_create_hook_payload_and_unwrap() -> None:
    client, session = _client(
        _response(200, json_body={"data": {"id": 9, "signing_secret": "s"}})
    )
    hook = await client.async_create_hook("https://hook", workspace_id=7)
    assert hook == {"id": 9, "signing_secret": "s"}
    payload = session.async_request.await_args.kwargs["json"]
    assert payload["target_url"] == "https://hook"
    assert payload["match_type"] == "all"
    assert "change_detected" in payload["events"]
    # price_change_detected is intentionally omitted (422 on the backend).
    assert "price_change_detected" not in payload["events"]


async def test_check_now_uses_put() -> None:
    client, session = _client(_response(200, json_body={"ok": True}))
    await client.async_check_now(1001)
    assert session.async_request.await_args.args[0] == "PUT"
    assert "/api/pages/1001/check" in session.async_request.await_args.args[1]


async def test_track_page_posts_payload() -> None:
    client, session = _client(_response(200, json_body={"id": 5}))
    await client.async_track_page({"url": "https://x"})
    assert session.async_request.await_args.args[0] == "POST"
    assert session.async_request.await_args.kwargs["json"]["url"] == "https://x"


async def test_no_workspace_id_omitted() -> None:
    client, session = _client(
        _response(200, json_body={"id": 1}), workspace_id=None
    )
    await client.async_get_user()
    assert "workspace_id" not in session.async_request.await_args.kwargs["params"]
