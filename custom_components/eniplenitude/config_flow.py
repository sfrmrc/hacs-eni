"""Config flow for Eni Plenitude."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import PlenitudeApi, PlenitudeAuthError, PlenitudeCaptchaError, PlenitudeError
from .const import CONF_POD, CONF_PRICE, DEFAULT_PRICE, DOMAIN

_LOGGER = logging.getLogger(__name__)

PRICE_FIELD = vol.All(vol.Coerce(float), vol.Range(min=0))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_PRICE, default=DEFAULT_PRICE): PRICE_FIELD,
    }
)


class PlenitudeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Email/password login, then pick the POD if the account has more than one."""

    VERSION = 1

    def __init__(self) -> None:
        self._input: dict[str, Any] = {}
        self._pods: list[str] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, self._pods = await self._async_validate(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if not errors:
                self._input = user_input
                if len(self._pods) == 1:
                    return await self._async_create(self._pods[0])
                return await self.async_step_pod()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_pod(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._async_create(user_input[CONF_POD])
        return self.async_show_form(
            step_id="pod", data_schema=vol.Schema({vol.Required(CONF_POD): vol.In(self._pods)})
        )

    async def _async_create(self, pod: str) -> ConfigFlowResult:
        await self.async_set_unique_id(pod)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=f"Plenitude {pod}", data={**self._input, CONF_POD: pod})

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, _ = await self._async_validate(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def _async_validate(self, username: str, password: str) -> tuple[dict[str, str], list[str]]:
        """Log in and list the electricity PODs. Returns (errors, pods)."""
        api = PlenitudeApi(async_create_clientsession(self.hass), username, password)
        try:
            await api.async_login()
            supplies = await api.async_get_power_supplies()
        except PlenitudeCaptchaError:
            return {"base": "captcha_required"}, []
        except PlenitudeAuthError:
            return {"base": "invalid_auth"}, []
        except PlenitudeError:
            _LOGGER.exception("Cannot reach Eni Plenitude")
            return {"base": "cannot_connect"}, []
        if not supplies:
            return {"base": "no_supplies"}, []
        return {}, [s["pod"] for s in supplies]

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PlenitudeOptionsFlow()


class PlenitudeOptionsFlow(OptionsFlow):
    """Change the €/kWh price."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        price = self.config_entry.options.get(CONF_PRICE, self.config_entry.data[CONF_PRICE])
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required(CONF_PRICE, default=price): PRICE_FIELD}),
        )
