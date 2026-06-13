"""Config and options flow for PageCrawl."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import PageCrawlClient, PageCrawlError
from .const import (
    CONF_BASE_URL,
    CONF_FOLDERS,
    CONF_IMPORT_MODE,
    CONF_MONITORS,
    CONF_SCAN_INTERVAL,
    CONF_UPDATE_MODE,
    CONF_WORKSPACE_ID,
    DEFAULT_BASE_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    IMPORT_MODE_ALL,
    IMPORT_MODE_FOLDERS,
    IMPORT_MODE_MONITORS,
    IMPORT_MODES,
    MIN_SCAN_INTERVAL,
    OAUTH_SCOPE,
    UPDATE_MODE_AUTO,
    UPDATE_MODES,
)

_LOGGER = logging.getLogger(__name__)


def _import_options_schema(
    folders: list[dict[str, Any]],
    monitors: list[dict[str, Any]],
    defaults: Mapping[str, Any],
) -> vol.Schema:
    """Build the import-scope schema (mode + folder/monitor multi-selects)."""
    folder_options = [
        SelectOptionDict(
            value=str(f.get("slug")),
            label=f.get("name") or str(f.get("slug")),
        )
        for f in folders
        if f.get("slug")
    ]
    monitor_options = [
        SelectOptionDict(
            value=str(m.get("id")),
            label=m.get("name") or f"Monitor {m.get('id')}",
        )
        for m in monitors
        if m.get("id") is not None
    ]

    return vol.Schema(
        {
            vol.Required(
                CONF_IMPORT_MODE,
                default=defaults.get(CONF_IMPORT_MODE, IMPORT_MODE_ALL),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(IMPORT_MODES),
                    translation_key=CONF_IMPORT_MODE,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_FOLDERS,
                default=list(defaults.get(CONF_FOLDERS, []) or []),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=folder_options,
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_MONITORS,
                default=list(defaults.get(CONF_MONITORS, []) or []),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=monitor_options,
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


class PageCrawlOAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the OAuth2 config flow for PageCrawl."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        super().__init__()
        self._token_data: dict[str, Any] | None = None
        self._user: dict[str, Any] | None = None
        self._workspaces: list[dict[str, Any]] = []
        self._workspace: dict[str, Any] | None = None
        self._folders: list[dict[str, Any]] = []
        self._monitors: list[dict[str, Any]] = []

    @property
    def logger(self) -> logging.Logger:
        """Return the flow logger."""
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Request the read scope."""
        return {"scope": OAUTH_SCOPE}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Register the built-in public client, then start OAuth.

        Importing the credential here (rather than in async_setup) ensures it
        exists before the OAuth implementation is resolved, so users never see
        the "Add application credentials" dialog.
        """
        await async_import_client_credential(
            self.hass,
            DOMAIN,
            ClientCredential(DEFAULT_CLIENT_ID, "", "PageCrawl"),
        )
        return await super().async_step_user(user_input)

    async def async_oauth_create_entry(
        self, data: dict[str, Any]
    ) -> ConfigFlowResult:
        """After token exchange, fetch the user and pick a workspace."""
        self._token_data = data

        client = self._build_client(data)
        try:
            self._user = await client.async_get_user()
        except PageCrawlError as err:
            _LOGGER.error("Failed to fetch PageCrawl user: %s", err)
            return self.async_abort(reason="cannot_connect")

        self._workspaces = self._extract_workspaces(self._user)

        if not self._workspaces:
            return self.async_abort(reason="no_workspaces")

        if len(self._workspaces) == 1:
            return await self._async_create_for_workspace(self._workspaces[0])

        return await self.async_step_workspace()

    async def async_step_workspace(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose a workspace when there is more than one."""
        if user_input is not None:
            workspace_id = user_input[CONF_WORKSPACE_ID]
            workspace = next(
                (
                    w
                    for w in self._workspaces
                    if str(w.get("id")) == str(workspace_id)
                ),
                None,
            )
            if workspace is None:
                return self.async_abort(reason="unknown_workspace")
            return await self._async_create_for_workspace(workspace)

        options = {
            str(w.get("id")): w.get("name") or f"Workspace {w.get('id')}"
            for w in self._workspaces
        }
        return self.async_show_form(
            step_id="workspace",
            data_schema=vol.Schema(
                {vol.Required(CONF_WORKSPACE_ID): vol.In(options)}
            ),
        )

    async def _async_create_for_workspace(
        self, workspace: dict[str, Any]
    ) -> ConfigFlowResult:
        """Create (or update on reauth) the config entry for a workspace."""
        assert self._token_data is not None
        assert self._user is not None

        user_id = self._user.get("id") or (self._user.get("user") or {}).get("id")
        workspace_id = workspace.get("id")

        unique_id = f"{user_id}:{workspace_id}"
        await self.async_set_unique_id(unique_id)

        entry_data = {
            **self._token_data,
            CONF_WORKSPACE_ID: workspace_id,
        }

        if self.source == "reauth":
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            reauth_entry = self._get_reauth_entry()
            return self.async_update_reload_and_abort(
                reauth_entry, data=entry_data
            )

        self._abort_if_unique_id_configured()

        # Remember the workspace and let the user choose what to import.
        self._workspace = workspace
        return await self.async_step_import_options()

    async def async_step_import_options(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose which monitors become HA devices."""
        assert self._token_data is not None
        assert self._workspace is not None

        workspace_id = self._workspace.get("id")
        workspace_name = (
            self._workspace.get("name") or f"Workspace {workspace_id}"
        )

        errors: dict[str, str] = {}

        if user_input is not None:
            mode = user_input.get(CONF_IMPORT_MODE, IMPORT_MODE_ALL)
            folders = user_input.get(CONF_FOLDERS, []) or []
            monitors = user_input.get(CONF_MONITORS, []) or []
            if mode == IMPORT_MODE_FOLDERS and not folders:
                errors[CONF_FOLDERS] = "import_no_folders"
            elif mode == IMPORT_MODE_MONITORS and not monitors:
                errors[CONF_MONITORS] = "import_no_monitors"
            else:
                options = {
                    CONF_IMPORT_MODE: mode,
                    CONF_FOLDERS: folders if mode == IMPORT_MODE_FOLDERS else [],
                    CONF_MONITORS: (
                        monitors if mode == IMPORT_MODE_MONITORS else []
                    ),
                }
                return self.async_create_entry(
                    title=workspace_name,
                    data={
                        **self._token_data,
                        CONF_WORKSPACE_ID: workspace_id,
                    },
                    options=options,
                )

        # Fetch folders + monitors for the selectors.
        client = self._build_client(self._token_data)
        try:
            self._folders = await client.async_list_folders(
                workspace_id=workspace_id
            )
            self._monitors = await client.async_list_pages(
                workspace_id=workspace_id
            )
        except PageCrawlError as err:
            _LOGGER.error("Failed to fetch folders/monitors: %s", err)
            return self.async_abort(reason="cannot_connect")

        return self.async_show_form(
            step_id="import_options",
            data_schema=_import_options_schema(
                self._folders,
                self._monitors,
                user_input or {},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth on token expiry/revocation."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth and restart the OAuth flow."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    def _build_client(self, token_data: dict[str, Any]) -> PageCrawlClient:
        """Build a transient client to query /api/user during the flow."""
        session = config_entry_oauth2_flow.OAuth2Session(
            self.hass,
            _TransientConfigEntry(token_data),
            self.flow_impl,
        )
        return PageCrawlClient(session, DEFAULT_BASE_URL)

    @staticmethod
    def _extract_workspaces(user: dict[str, Any]) -> list[dict[str, Any]]:
        """Pull the workspace list out of the /api/user response."""
        for key in ("workspaces", "teams"):
            value = user.get(key)
            if isinstance(value, list) and value:
                return [w for w in value if isinstance(w, dict) and "id" in w]
        # Some shapes nest under `user`.
        nested = user.get("user")
        if isinstance(nested, dict):
            for key in ("workspaces", "teams"):
                value = nested.get(key)
                if isinstance(value, list) and value:
                    return [
                        w for w in value if isinstance(w, dict) and "id" in w
                    ]
        return []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> PageCrawlOptionsFlow:
        """Return the options flow."""
        return PageCrawlOptionsFlow()


class _TransientConfigEntry:
    """Minimal config-entry stand-in to drive OAuth2Session during the flow.

    OAuth2Session only reads `.data["token"]` and writes it back via an update
    hook; during the flow the token is freshly minted so no refresh occurs.
    """

    def __init__(self, token_data: dict[str, Any]) -> None:
        self.data = token_data
        self.entry_id = "config_flow_transient"

    def async_start_reauth(self, *args: Any, **kwargs: Any) -> None:
        """No-op during the flow."""


class PageCrawlOptionsFlow(OptionsFlow):
    """Handle PageCrawl options: update mode, interval, and import scope."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        options = self.config_entry.options
        errors: dict[str, str] = {}

        if user_input is not None:
            # Normalize scan interval to the floor.
            scan_interval = max(
                int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_POLL_INTERVAL)),
                MIN_SCAN_INTERVAL,
            )
            mode = user_input.get(CONF_IMPORT_MODE, IMPORT_MODE_ALL)
            folders = user_input.get(CONF_FOLDERS, []) or []
            monitors = user_input.get(CONF_MONITORS, []) or []
            if mode == IMPORT_MODE_FOLDERS and not folders:
                errors[CONF_FOLDERS] = "import_no_folders"
            elif mode == IMPORT_MODE_MONITORS and not monitors:
                errors[CONF_MONITORS] = "import_no_monitors"
            else:
                # The (de)registration of webhook + PageCrawl hook and the
                # reload are handled by the entry's update listener in
                # __init__.py. Persisting the options triggers it.
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_UPDATE_MODE: user_input.get(
                            CONF_UPDATE_MODE, UPDATE_MODE_AUTO
                        ),
                        CONF_SCAN_INTERVAL: scan_interval,
                        CONF_IMPORT_MODE: mode,
                        CONF_FOLDERS: (
                            folders if mode == IMPORT_MODE_FOLDERS else []
                        ),
                        CONF_MONITORS: (
                            monitors if mode == IMPORT_MODE_MONITORS else []
                        ),
                    },
                )

        # Re-fetch current folders/monitors so the selectors stay in sync.
        folder_list, monitor_list = await self._async_fetch_scope_choices()

        defaults: dict[str, Any] = {
            CONF_IMPORT_MODE: options.get(CONF_IMPORT_MODE, IMPORT_MODE_ALL),
            CONF_FOLDERS: options.get(CONF_FOLDERS, []),
            CONF_MONITORS: options.get(CONF_MONITORS, []),
        }
        if user_input is not None:
            defaults.update(user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_MODE,
                    default=options.get(CONF_UPDATE_MODE, UPDATE_MODE_AUTO),
                ): vol.In(UPDATE_MODES),
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_POLL_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
            }
        ).extend(
            _import_options_schema(
                folder_list, monitor_list, defaults
            ).schema
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )

    async def _async_fetch_scope_choices(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch folders and monitors for the import-scope selectors."""
        entry = self.config_entry
        try:
            implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
                self.hass, entry
            )
            session = config_entry_oauth2_flow.OAuth2Session(
                self.hass, entry, implementation
            )
            client = PageCrawlClient(
                session,
                entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                entry.data.get(CONF_WORKSPACE_ID),
            )
            folders = await client.async_list_folders(
                workspace_id=client.workspace_id
            )
            monitors = await client.async_list_pages(
                workspace_id=client.workspace_id
            )
            return folders, monitors
        except PageCrawlError as err:
            _LOGGER.warning(
                "Could not fetch folders/monitors for options: %s", err
            )
            return [], []
