from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_IS_TI, DOMAIN
from .coordinator import DifluidMicrobalanceCoordinator

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = DifluidMicrobalanceCoordinator(
        hass,
        address=entry.data[CONF_ADDRESS],
        is_ti=entry.data.get(CONF_IS_TI, False),
    )

    try:
        await coordinator.async_start()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to Difluid Microbalance {entry.data[CONF_ADDRESS]}: {err}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: DifluidMicrobalanceCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop()
    return unload_ok
