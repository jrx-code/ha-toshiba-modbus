"""Discrete inputs: what the unit reports about itself."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ToshibaModbusCoordinator
from .entity import ToshibaInterfaceEntity, ToshibaUnitEntity


@dataclass(frozen=True, kw_only=True)
class ToshibaBinaryDescription(BinarySensorEntityDescription):
    field: str


BINARY_SENSORS: tuple[ToshibaBinaryDescription, ...] = (
    ToshibaBinaryDescription(key="power", field="onoff", translation_key="power",
                             device_class=BinarySensorDeviceClass.POWER),
    ToshibaBinaryDescription(key="filter_sign", field="filter_sign", translation_key="filter_sign",
                             device_class=BinarySensorDeviceClass.PROBLEM,
                             entity_category=EntityCategory.DIAGNOSTIC),
    ToshibaBinaryDescription(key="alarm", field="alarm", translation_key="alarm",
                             device_class=BinarySensorDeviceClass.PROBLEM),
    ToshibaBinaryDescription(key="thermo", field="thermo", translation_key="thermo",
                             device_class=BinarySensorDeviceClass.RUNNING),
    ToshibaBinaryDescription(key="st_pure_filter", field="st_pure_filter",
                             translation_key="st_pure_filter",
                             entity_category=EntityCategory.DIAGNOSTIC),
    ToshibaBinaryDescription(key="st_hi_power", field="st_hi_power", translation_key="st_hi_power",
                             entity_category=EntityCategory.DIAGNOSTIC),
    ToshibaBinaryDescription(key="st_eco", field="st_eco", translation_key="st_eco",
                             entity_category=EntityCategory.DIAGNOSTIC),
    ToshibaBinaryDescription(key="st_quiet", field="st_quiet", translation_key="st_quiet",
                             entity_category=EntityCategory.DIAGNOSTIC),
    ToshibaBinaryDescription(key="st_silence", field="st_silence", translation_key="st_silence",
                             entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ToshibaModbusCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        ToshibaBinarySensor(coordinator, unit, desc)
        for unit in coordinator.units
        for desc in BINARY_SENSORS
    ]
    entities.append(ToshibaInterfaceConnectivity(coordinator))
    async_add_entities(entities)


class ToshibaBinarySensor(ToshibaUnitEntity, BinarySensorEntity):
    entity_description: ToshibaBinaryDescription

    def __init__(self, coordinator, unit: int, description: ToshibaBinaryDescription) -> None:
        super().__init__(coordinator, unit, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.bit(self._unit, "discrete", self.entity_description.field)


class ToshibaInterfaceConnectivity(ToshibaInterfaceEntity, BinarySensorEntity):
    """Czy ostatni cykl odpytania się udał."""

    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "connectivity")

    @property
    def available(self) -> bool:
        # Encja o łączności musi żyć właśnie wtedy, gdy łączności nie ma.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success
