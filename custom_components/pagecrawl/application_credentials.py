"""Application Credentials platform for PageCrawl.

Provides the OAuth2 authorization server endpoints. The integration imports a
built-in public PKCE client automatically, so users do not add credentials by
hand.
"""

from __future__ import annotations

from homeassistant.components.application_credentials import (
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import (
    DEFAULT_BASE_URL,
    OAUTH_AUTHORIZE_PATH,
    OAUTH_TOKEN_PATH,
)


async def async_get_authorization_server(
    hass: HomeAssistant,
) -> AuthorizationServer:
    """Return the PageCrawl authorization server (pagecrawl.io)."""
    return AuthorizationServer(
        authorize_url=f"{DEFAULT_BASE_URL}{OAUTH_AUTHORIZE_PATH}",
        token_url=f"{DEFAULT_BASE_URL}{OAUTH_TOKEN_PATH}",
    )


class PageCrawlPkceImplementation(
    config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce
):
    """PageCrawl OAuth2 implementation using PKCE for a public (secret-less) client.

    The PageCrawl Home Assistant OAuth client is a public PKCE client (no secret),
    so we cannot use the default Application Credentials implementation (which
    assumes a confidential client). PKCE supplies the proof instead of a secret.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        auth_domain: str,
        credential: ClientCredential,
        authorization_server: AuthorizationServer,
    ) -> None:
        """Initialize from the imported client credential."""
        super().__init__(
            hass,
            auth_domain,
            credential.client_id,
            authorization_server.authorize_url,
            authorization_server.token_url,
            client_secret=credential.client_secret or "",
        )
        self._name = credential.name

    @property
    def name(self) -> str:
        """Return the friendly name shown in the UI."""
        return self._name or "PageCrawl"


async def async_get_auth_implementation(
    hass: HomeAssistant,
    auth_domain: str,
    credential: ClientCredential,
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    """Return a PKCE-capable auth implementation for the public client."""
    return PageCrawlPkceImplementation(
        hass,
        auth_domain,
        credential,
        await async_get_authorization_server(hass),
    )
