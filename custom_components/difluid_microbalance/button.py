from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_TYPE, CONF_IS_TI, DEVICE_TYPE_R2, DOMAIN
from .coordinator import DifluidMicrobalanceCoordinator

# DF DF 03 02 01 01  checksum=C5 (Power Button single click = tare)
_CMD_TARE = bytes.fromhex("dfdf03020101c5")
# DF DF 03 04 01 01  checksum=C7 (Power Button long press = power off)
_CMD_POWER_OFF = bytes.fromhex("dfdf03040101c7")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_R2:
        return  # R2 does not expose physical button control
    coordinator: DifluidMicrobalanceCoordinator = hass.data[DOMAIN][entry.entry_id]
    is_ti = entry.data.get(CONF_IS_TI, False)
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Difluid",
        model="Microbalance Ti" if is_ti else "Microbalance",
    )
    async_add_entities(
        [
            DifluidButton(coordinator, entry, device_info, "tare", "Tare", "mdi:scale-balance", _CMD_TARE),
            DifluidButton(coordinator, entry, device_info, "power_off", "Power Off", "mdi:power", _CMD_POWER_OFF),
        ]
    )


class DifluidButton(CoordinatorEntity[DifluidMicrobalanceCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DifluidMicrobalanceCoordinator,
        entry: ConfigEntry,
        device_info: DeviceInfo,
        key: str,
        name: str,
        icon: str,
        cmd: bytes,
    ) -> None:
        super().__init__(coordinator)
        self._cmd = cmd
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        client = self.coordinator._client
        return client is not None and client.is_connected

    async def async_press(self) -> None:
        await self.coordinator.async_send_command(self._cmd)
