"""Tests for the PageCrawl OAuth2 config flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.pagecrawl.const import (
    CONF_WORKSPACE_ID,
    DEFAULT_BASE_URL,
    DOMAIN,
    OAUTH_TOKEN_PATH,
)

from .conftest import CLIENT_ID, USER_ID, WORKSPACE_ID

REDIRECT_URI = "https://example.com/auth/external/callback"
TOKEN_URL = f"{DEFAULT_BASE_URL}{OAUTH_TOKEN_PATH}"


async def _start_oauth(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host: Any,
    source: str = config_entries.SOURCE_USER,
    flow_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drive the OAuth handshake up to (and including) the token exchange."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source, **(flow_context or {})}
    )
    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {
            "flow_id": result["flow_id"],
            "redirect_uri": REDIRECT_URI,
        },
    )

    assert result["type"] == FlowResultType.EXTERNAL_STEP
    assert result["url"].startswith(f"{DEFAULT_BASE_URL}/oauth/authorize")

    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200

    aioclient_mock.post(
        TOKEN_URL,
        json={
            "refresh_token": "mock-refresh-token",
            "access_token": "mock-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "integration",
        },
    )

    return result


@pytest.fixture(autouse=True)
def _bypass_setup() -> Any:
    """Don't actually set up the entry during config-flow tests."""
    with patch(
        "custom_components.pagecrawl.async_setup_entry", return_value=True
    ):
        yield


async def test_flow_imports_builtin_credential(
    hass: HomeAssistant,
    current_request_with_host,
) -> None:
    """Starting the flow imports the built-in client and goes straight to OAuth.

    No application credentials are pre-registered here, so reaching the external
    OAuth step proves the flow imported the built-in public client itself (the
    user never sees the "Add application credentials" dialog).
    """
    assert await async_setup_component(hass, "application_credentials", {})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] == FlowResultType.EXTERNAL_STEP
    assert result["url"].startswith(f"{DEFAULT_BASE_URL}/oauth/authorize")


async def test_full_flow_multi_workspace(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    user_payload,
) -> None:
    """Happy path: OAuth -> workspace chooser -> entry created."""
    result = await _start_oauth(
        hass, hass_client_no_auth, aioclient_mock, current_request_with_host
    )

    with patch(
        "custom_components.pagecrawl.config_flow.PageCrawlClient."
        "async_get_user",
        AsyncMock(return_value=user_payload),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"]
        )

    # More than one workspace -> chooser step.
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "workspace"

    with (
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_folders",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_pages",
            AsyncMock(return_value=[]),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_WORKSPACE_ID: str(WORKSPACE_ID)}
        )

        # Import-scope chooser step.
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "import_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"import_mode": "all"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Acme Workspace"
    assert result["result"].unique_id == f"{USER_ID}:{WORKSPACE_ID}"
    assert result["data"][CONF_WORKSPACE_ID] == WORKSPACE_ID
    assert "token" in result["data"]
    assert result["options"]["import_mode"] == "all"


async def test_single_workspace_auto_pick(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    user_payload_single,
) -> None:
    """A single-workspace account skips the chooser and creates the entry."""
    result = await _start_oauth(
        hass, hass_client_no_auth, aioclient_mock, current_request_with_host
    )

    with (
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_get_user",
            AsyncMock(return_value=user_payload_single),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_folders",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.pagecrawl.config_flow.PageCrawlClient."
            "async_list_pages",
            AsyncMock(return_value=[]),
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"]
        )

        # Single workspace auto-picked -> import-scope chooser step.
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "import_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"import_mode": "all"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Acme Workspace"
    assert result["result"].unique_id == f"{USER_ID}:{WORKSPACE_ID}"


async def test_reauth(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    user_payload_single,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauth re-runs OAuth and updates the existing entry."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": mock_config_entry.entry_id,
        },
        data=mock_config_entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    # Now we're at the OAuth external step; drive the callback.
    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {"flow_id": result["flow_id"], "redirect_uri": REDIRECT_URI},
    )
    assert result["type"] == FlowResultType.EXTERNAL_STEP

    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200

    aioclient_mock.post(
        TOKEN_URL,
        json={
            "refresh_token": "new-refresh-token",
            "access_token": "new-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "integration",
        },
    )

    with patch(
        "custom_components.pagecrawl.config_flow.PageCrawlClient."
        "async_get_user",
        AsyncMock(return_value=user_payload_single),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"]
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert (
        mock_config_entry.data["token"]["access_token"] == "new-access-token"
    )


async def test_authorize_url_has_pkce_and_scope(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
) -> None:
    """The PKCE implementation is used and the authorize URL carries
    code_challenge + scope=integration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.EXTERNAL_STEP
    url = result["url"]
    assert url.startswith(f"{DEFAULT_BASE_URL}/oauth/authorize")
    # PKCE markers proving PageCrawlPkceImplementation is in use.
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    # Requested scope.
    assert "scope=api%3Aread" in url or "scope=integration" in url
    assert f"client_id={CLIENT_ID}" in url


async def test_uses_pkce_implementation(
    hass: HomeAssistant,
    setup_credentials,
) -> None:
    """async_get_auth_implementation returns the PKCE implementation."""
    from homeassistant.components.application_credentials import (
        ClientCredential,
    )

    from custom_components.pagecrawl.application_credentials import (
        PageCrawlPkceImplementation,
        async_get_auth_implementation,
    )

    impl = await async_get_auth_implementation(
        hass, DOMAIN, ClientCredential(CLIENT_ID, "")
    )
    assert isinstance(impl, PageCrawlPkceImplementation)


async def test_abort_no_workspaces(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
) -> None:
    """A user with no workspaces aborts with no_workspaces."""
    result = await _start_oauth(
        hass, hass_client_no_auth, aioclient_mock, current_request_with_host
    )

    with patch(
        "custom_components.pagecrawl.config_flow.PageCrawlClient."
        "async_get_user",
        AsyncMock(return_value={"id": USER_ID, "workspaces": []}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"]
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_workspaces"


async def test_abort_cannot_connect(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
) -> None:
    """A failure fetching /api/user aborts with cannot_connect."""
    from custom_components.pagecrawl.api import PageCrawlApiError

    result = await _start_oauth(
        hass, hass_client_no_auth, aioclient_mock, current_request_with_host
    )

    with patch(
        "custom_components.pagecrawl.config_flow.PageCrawlClient."
        "async_get_user",
        AsyncMock(side_effect=PageCrawlApiError("boom")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"]
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_reauth_wrong_account_aborts(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host,
    setup_credentials,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauth that lands on a different user/workspace aborts wrong_account."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": mock_config_entry.entry_id,
        },
        data=mock_config_entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {"flow_id": result["flow_id"], "redirect_uri": REDIRECT_URI},
    )
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200
    aioclient_mock.post(
        TOKEN_URL,
        json={
            "refresh_token": "new-refresh-token",
            "access_token": "new-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "integration",
        },
    )

    # Different user id -> unique_id mismatch -> wrong_account.
    wrong = {
        "id": 9999,
        "workspaces": [{"id": WORKSPACE_ID, "name": "Acme Workspace"}],
    }
    with patch(
        "custom_components.pagecrawl.config_flow.PageCrawlClient."
        "async_get_user",
        AsyncMock(return_value=wrong),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"]
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
