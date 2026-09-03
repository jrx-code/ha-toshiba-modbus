"""Read-only values that do not belong on the climate card."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers as reg
from .const import DOMAIN
from .coordinator import ToshibaModbusCoordinator
from .entity import ToshibaUnitEntity


@dataclass(frozen=True, kw_only=True)
class ToshibaSensorDescription(SensorEntityDescription):
    space: str
    field: str
    convert: Callable[[ToshibaModbusCoordinator, int], object]


def _tenths(c: ToshibaModbusCoordinator, u: int, space: str, key: str):
    w = c.word(u, space, key)
    return None if w is None else reg.signed(w) / 10


SENSORS: tuple[ToshibaSensorDescription, ...] = (
    ToshibaSensorDescription(
        key="setpoint_status", space="input", field="setpoint",
        translation_key="setpoint_status",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        convert=lambda c, u: _tenths(c, u, "input", "setpoint"),
    ),
    ToshibaSensorDescription(
        key="capacity", space="input", field="capacity",
        translation_key="capacity",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        convert=lambda c, u: _tenths(c, u, "input", "capacity"),
    ),
    ToshibaSensorDescription(
        key="hours", space="holding", field="hours",
        translation_key="hours",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        convert=lambda c, u: c.word(u, "holding", "hours"),
    ),
    ToshibaSensorDescription(
        key="check_code", space="input", field="check_code",
        translation_key="check_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        convert=lambda c, u: (
            None if c.word(u, "input", "check_code") is None
            else reg.CHECK_CODES.get(c.word(u, "input", "check_code"), f"0x{c.word(u, 'input', 'check_code'):04X}")
        ),
    ),
    ToshibaSensorDescription(
        key="mode_status", space="input", field="mode",
        translation_key="mode_status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(reg.HVAC_MODE_READ.values())),
        entity_registry_enabled_default=False,
        convert=lambda c, u: reg.HVAC_MODE_READ.get(c.word(u, "input", "mode")),
    ),
    ToshibaSensorDescription(
        key="fan_status", space="input", field="fan",
        translation_key="fan_status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(reg.FAN_MODES.values())),
        entity_registry_enabled_default=False,
        convert=lambda c, u: reg.FAN_MODES.get(c.word(u, "input", "fan")),
    ),
    ToshibaSensorDescription(
        key="louver_status", space="input", field="louver",
        translation_key="louver_status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(reg.LOUVER_MODES.values())),
        entity_registry_enabled_default=False,
        convert=lambda c, u: reg.LOUVER_MODES.get(c.word(u, "input", "louver")),
    ),
    ToshibaSensorDescription(
        key="model", space="input", field="model",
        translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        convert=lambda c, u: c.text(u, "model"),
    ),
    ToshibaSensorDescription(
        key="serial", space="input", field="serial",
        translation_key="serial",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        convert=lambda c, u: c.text(u, "serial"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ToshibaModbusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ToshibaSensor(coordinator, unit, desc)
        for unit in coordinator.units
        for desc in SENSORS
    )


class ToshibaSensor(ToshibaUnitEntity, SensorEntity):
    entity_description: ToshibaSensorDescription

    def __init__(self, coordinator, unit: int, description: ToshibaSensorDescription) -> None:
        super().__init__(coordinator, unit, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.convert(self.coordinator, self._unit)
