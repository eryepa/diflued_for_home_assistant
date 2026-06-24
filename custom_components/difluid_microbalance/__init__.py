from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_DEVICE_TYPE, CONF_IS_TI, CONF_LICENSE_KEY, DEVICE_TYPE_R2, DOMAIN
from .coordinator import DifluidMicrobalanceCoordinator
from .coordinator_r2 import DifluidR2Coordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    address = entry.data[CONF_ADDRESS]

    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_R2:
        coordinator = DifluidR2Coordinator(
            hass,
            address=address,
            license_key=entry.data.get(CONF_LICENSE_KEY, ""),
        )
    else:
        coordinator = DifluidMicrobalanceCoordinator(
            hass,
            address=address,
            is_ti=entry.data.get(CONF_IS_TI, False),
        )

    try:
        await coordinator.async_start()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to Difluid device {address}: {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop()
    return unload_ok
