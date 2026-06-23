from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import (
    CONF_DEVICE_TYPE,
    CONF_IS_TI,
    CONF_LICENSE_KEY,
    DEVICE_TYPE_MICROBALANCE,
    DEVICE_TYPE_R2,
    DOMAIN,
    SERVICE_UUID_MICROBALANCE,
    SERVICE_UUID_MICROBALANCE_TI,
    SERVICE_UUID_R2,
)

_ALL_SERVICE_UUIDS = {
    SERVICE_UUID_MICROBALANCE,
    SERVICE_UUID_MICROBALANCE_TI,
    SERVICE_UUID_R2,
}


def _device_type(service_uuids: list[str]) -> str | None:
    lower = {u.lower() for u in service_uuids}
    if SERVICE_UUID_R2 in lower:
        return DEVICE_TYPE_R2
    if SERVICE_UUID_MICROBALANCE in lower or SERVICE_UUID_MICROBALANCE_TI in lower:
        return DEVICE_TYPE_MICROBALANCE
    return None


class DifluidMicrobalanceConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        dtype = _device_type(discovery_info.service_uuids)
        if dtype is None:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        if dtype == DEVICE_TYPE_R2:
            return await self.async_step_r2_license()
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery_info is not None
        info = self._discovery_info

        if user_input is not None:
            lower = {u.lower() for u in info.service_uuids}
            is_ti = SERVICE_UUID_MICROBALANCE_TI in lower
            return self.async_create_entry(
                title=info.name or f"Difluid Microbalance ({info.address})",
                data={
                    CONF_ADDRESS: info.address,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_MICROBALANCE,
                    CONF_IS_TI: is_ti,
                },
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": info.name or info.address},
        )

    async def async_step_r2_license(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery_info is not None
        info = self._discovery_info
        errors: dict[str, str] = {}

        if user_input is not None:
            license_key = user_input[CONF_LICENSE_KEY].strip()
            if len(license_key) < 8:
                errors[CONF_LICENSE_KEY] = "invalid_license"
            else:
                return self.async_create_entry(
                    title=info.name or f"Difluid R2 ({info.address})",
                    data={
                        CONF_ADDRESS: info.address,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_R2,
                        CONF_LICENSE_KEY: license_key,
                    },
                )

        return self.async_show_form(
            step_id="r2_license",
            data_schema=vol.Schema({vol.Required(CONF_LICENSE_KEY): str}),
            description_placeholders={"name": info.name or info.address},
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            info = self._discovered_devices.get(address)
            dtype = _device_type(info.service_uuids) if info else DEVICE_TYPE_MICROBALANCE
            self._discovery_info = info

            if dtype == DEVICE_TYPE_R2:
                return await self.async_step_r2_license()

            is_ti = (
                SERVICE_UUID_MICROBALANCE_TI in {u.lower() for u in info.service_uuids}
                if info
                else False
            )
            return self.async_create_entry(
                title=f"Difluid Microbalance ({address})",
                data={
                    CONF_ADDRESS: address,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_MICROBALANCE,
                    CONF_IS_TI: is_ti,
                },
            )

        current = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address not in current and _device_type(info.service_uuids):
                self._discovered_devices[info.address] = info

        if not self._discovered_devices:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
                errors={"base": "no_devices_found"},
            )

        choices = {
            addr: f"{d.name or 'Difluid Device'} ({addr})"
            for addr, d in self._discovered_devices.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(choices)}),
        )
