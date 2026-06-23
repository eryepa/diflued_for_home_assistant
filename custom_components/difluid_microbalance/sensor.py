from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfMass, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_IS_TI, DOMAIN
from .coordinator import DifluidMicrobalanceCoordinator, MicrobalanceData


@dataclass(frozen=True)
class DifluidSensorDescription(SensorEntityDescription):
    value_fn: Callable[[MicrobalanceData], float | int | str | None] = lambda _: None


SENSORS: tuple[DifluidSensorDescription, ...] = (
    DifluidSensorDescription(
        key="weight",
        translation_key="weight",
        name="Weight",
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        suggested_display_precision=1,
        value_fn=lambda d: d.weight,
    ),
    DifluidSensorDescription(
        key="flow_rate",
        translation_key="flow_rate",
        name="Flow Rate",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="g/s",
        suggested_display_precision=1,
        icon="mdi:water-flow",
        value_fn=lambda d: d.flow_rate,
    ),
    DifluidSensorDescription(
        key="timer",
        translation_key="timer",
        name="Timer",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        icon="mdi:timer-outline",
        value_fn=lambda d: d.timer,
    ),
    DifluidSensorDescription(
        key="battery",
        translation_key="battery",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda d: d.battery,
    ),
    DifluidSensorDescription(
        key="charging",
        translation_key="charging",
        name="Charging",
        icon="mdi:battery-charging",
        value_fn=lambda d: "charging" if d.charging else "idle",
    ),
    DifluidSensorDescription(
        key="device_status",
        translation_key="device_status",
        name="Device Status",
        icon="mdi:information-outline",
        value_fn=lambda d: d.device_status,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DifluidMicrobalanceCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DifluidMicrobalanceSensor(coordinator, description, entry)
        for description in SENSORS
    )


class DifluidMicrobalanceSensor(
    CoordinatorEntity[DifluidMicrobalanceCoordinator], SensorEntity
):
    entity_description: DifluidSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DifluidMicrobalanceCoordinator,
        description: DifluidSensorDescription,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Difluid",
            model="Microbalance Ti" if entry.data.get(CONF_IS_TI) else "Microbalance",
        )

    @property
    def native_value(self) -> float | int | str | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self._client_connected
        )

    @property
    def _client_connected(self) -> bool:
        client = self.coordinator._client
        return client is not None and client.is_connected
