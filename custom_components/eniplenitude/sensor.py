"""Sensors for Eni Plenitude. Energy Dashboard data lives in the external statistics, not here."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PlenitudeConfigEntry
from .const import DOMAIN
from .coordinator import PlenitudeCoordinator


@dataclass(frozen=True, kw_only=True)
class PlenitudeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PlenitudeCoordinator], Any]


SENSORS = (
    PlenitudeSensorDescription(
        key="month_energy",
        translation_key="month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda c: c.data["month_kwh"],
    ),
    PlenitudeSensorDescription(
        key="month_cost",
        translation_key="month_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement="EUR",
        value_fn=lambda c: round(c.data["month_kwh"] * c.price, 2),
    ),
    PlenitudeSensorDescription(
        key="last_data",
        translation_key="last_data",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda c: c.data["last_data"],
    ),
    PlenitudeSensorDescription(
        key="price",
        translation_key="price",
        native_unit_of_measurement="EUR/kWh",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.price,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlenitudeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(PlenitudeSensor(coordinator, description) for description in SENSORS)


class PlenitudeSensor(CoordinatorEntity[PlenitudeCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: PlenitudeSensorDescription

    def __init__(self, coordinator: PlenitudeCoordinator, description: PlenitudeSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.pod}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.pod)},
            name=f"Plenitude {coordinator.pod}",
            manufacturer="Eni Plenitude",
            model=coordinator.data.get("product"),
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator)
