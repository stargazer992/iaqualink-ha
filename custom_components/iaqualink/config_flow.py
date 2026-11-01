"""Config flow for iAquaLink."""

from collections.abc import Mapping
from typing import Any, override

import httpx
from iaqualink.client import AqualinkClient
from iaqualink.exception import (
    AqualinkServiceException,
    AqualinkServiceUnauthorizedException,
)
import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.util.ssl import SSL_ALPN_HTTP11_HTTP2

from .const import (
    CONF_AQUALINKD_URL,
    CONF_CONNECTION,
    CONNECTION_CLOUD,
    CONNECTION_LOCAL,
    DOMAIN,
)
from .local import AqualinkdClient, AqualinkdError

DEFAULT_AQUALINKD_URL = "http://aqualinkd.local"

CREDENTIALS_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class AqualinkFlowHandler(ConfigFlow, domain=DOMAIN):
    """iAquaLink config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AqualinkOptionsFlow:
        """Return the Cloud / Local options flow."""
        return AqualinkOptionsFlow()

    async def _async_test_credentials(
        self, user_input: dict[str, Any]
    ) -> dict[str, str]:
        """Validate credentials against iAquaLink."""
        try:
            async with AqualinkClient(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                httpx_client=get_async_client(
                    self.hass, alpn_protocols=SSL_ALPN_HTTP11_HTTP2
                ),
            ):
                pass
        except AqualinkServiceUnauthorizedException:
            return {"base": "invalid_auth"}
        except AqualinkServiceException, TimeoutError, httpx.HTTPError:
            return {"base": "cannot_connect"}

        return {}

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow start."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            errors = await self._async_test_credentials(user_input)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=CREDENTIALS_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle flow triggered by an authentication failure."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle confirmation of reauthentication."""
        errors = {}

        config_entry = (
            self._get_reconfigure_entry()
            if self.source == SOURCE_RECONFIGURE
            else self._get_reauth_entry()
        )
        if user_input is not None:
            if config_entry.unique_id is not None:
                # Credentials for a different account would orphan every entity.
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_mismatch(reason="wrong_account")
            errors = await self._async_test_credentials(user_input)
            if not errors:
                return self.async_update_reload_and_abort(
                    config_entry,
                    title=user_input[CONF_USERNAME],
                    data_updates={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id=(
                "reconfigure" if self.source == SOURCE_RECONFIGURE else "reauth_confirm"
            ),
            data_schema=CREDENTIALS_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        return await self.async_step_reauth_confirm(user_input)


class AqualinkOptionsFlow(OptionsFlowWithReload):
    """Choose whether equipment is controlled via the cloud or AqualinkD."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the connection and, for Local, where AqualinkD runs."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = str(user_input.get(CONF_AQUALINKD_URL) or "").strip().rstrip("/")
            if user_input[CONF_CONNECTION] == CONNECTION_LOCAL:
                if not url.startswith(("http://", "https://")):
                    errors[CONF_AQUALINKD_URL] = "invalid_url"
                else:
                    try:
                        await AqualinkdClient(url, get_async_client(self.hass)).status()
                    except AqualinkdError, ValueError:
                        errors["base"] = "cannot_connect_aqualinkd"
            if not errors:
                return self.async_create_entry(
                    data={
                        CONF_CONNECTION: user_input[CONF_CONNECTION],
                        CONF_AQUALINKD_URL: url or DEFAULT_AQUALINKD_URL,
                    }
                )
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_CONNECTION,
                    default=options.get(CONF_CONNECTION, CONNECTION_CLOUD),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[CONNECTION_CLOUD, CONNECTION_LOCAL],
                        mode=SelectSelectorMode.LIST,
                        translation_key=CONF_CONNECTION,
                    )
                ),
                vol.Optional(
                    CONF_AQUALINKD_URL,
                    default=options.get(CONF_AQUALINKD_URL, DEFAULT_AQUALINKD_URL),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={"example_url": DEFAULT_AQUALINKD_URL},
        )
