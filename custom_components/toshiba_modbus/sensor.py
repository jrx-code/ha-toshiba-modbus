"""Read-only values that do not belong on the climate card."""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers as reg
from .const import DOMAIN, SIGNAL_NEW_UNIT
from .coordinator import ToshibaModbusCoordinator
from .entity import ToshibaInterfaceEntity, ToshibaUnitEntity


@dataclass(frozen=True, kw_only=True)
class ToshibaSensorDescription(SensorEntityDescription):
    space: str
    field: str
    convert: Callable[[ToshibaModbusCoordinator, int], object]


def _tenths(c: ToshibaModbusCoordinator, u: int, space: str, key: str):
    return reg.tenths(c.word(u, space, key))


def _check_code(c: ToshibaModbusCoordinator, u: int):
    code = reg.word_or_none(c.word(u, "input", "check_code"))
    return None if code is None else reg.CHECK_CODES.get(code, f"0x{code:04X}")


SENSORS: tuple[ToshibaSensorDescription, ...] = (
    ToshibaSensorDescription(
        key="setpoint_status", space="input", field="setpoint",
        translation_key="setpoint_status",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
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
        # 0xFFFF zapisane w TOTAL_INCREASING zostałoby w statystykach na zawsze.
        convert=lambda c, u: reg.word_or_none(c.word(u, "holding", "hours")),
    ),
    ToshibaSensorDescription(
        key="check_code", space="input", field="check_code",
        translation_key="check_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        convert=_check_code,
    ),
    ToshibaSensorDescription(
        key="mode_status", space="input", field="mode",
        translation_key="mode_status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(reg.HVAC_MODE_READ.values())),
        convert=lambda c, u: reg.HVAC_MODE_READ.get(c.word(u, "input", "mode")),
    ),
    ToshibaSensorDescription(
        key="fan_status", space="input", field="fan",
        translation_key="fan_status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(reg.FAN_MODES.values())),
        convert=lambda c, u: reg.FAN_MODES.get(c.word(u, "input", "fan")),
    ),
    ToshibaSensorDescription(
        key="louver_status", space="input", field="louver",
        translation_key="louver_status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(reg.LOUVER_MODES.values())),
        convert=lambda c, u: reg.LOUVER_MODES.get(c.word(u, "input", "louver")),
    ),
    ToshibaSensorDescription(
        key="model", space="input", field="model",
        translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        convert=lambda c, u: c.model(u),
    ),
    ToshibaSensorDescription(
        key="serial", space="input", field="serial",
        translation_key="serial",
        entity_category=EntityCategory.DIAGNOSTIC,
        convert=lambda c, u: c.serial(u),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ToshibaModbusCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        ToshibaSensor(coordinator, unit, desc)
        for unit in coordinator.units
        for desc in SENSORS
    ]
    entities.extend(ToshibaInterfaceSensor(coordinator, desc) for desc in INTERFACE_SENSORS)
    async_add_entities(entities)


    @callback
    def _new_unit(unit: int) -> None:
        async_add_entities(ToshibaSensor(coordinator, unit, d) for d in SENSORS)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_UNIT.format(entry.entry_id), _new_unit)
    )


class ToshibaSensor(ToshibaUnitEntity, SensorEntity):
    entity_description: ToshibaSensorDescription

    def __init__(self, coordinator, unit: int, description: ToshibaSensorDescription) -> None:
        super().__init__(coordinator, unit, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.convert(self.coordinator, self._unit)


@dataclass(frozen=True, kw_only=True)
class ToshibaInterfaceSensorDescription(SensorEntityDescription):
    read: Callable[[ToshibaModbusCoordinator], object]


# Liczniki funkcji 0x08 odpowiada sam interfejs - nie schodzą na magistralę Uh.
# Licznik błędów jest tu najważniejszy: rosnący przy stabilnym połączeniu oznacza
# zakłócenia na RS-485 albo drugiego mastera na tej samej linii.
INTERFACE_SENSORS: tuple[ToshibaInterfaceSensorDescription, ...] = (
    ToshibaInterfaceSensorDescription(
        key="bus_messages", translation_key="bus_messages",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.counters["bus_messages"],
    ),
    ToshibaInterfaceSensorDescription(
        key="bus_errors", translation_key="bus_errors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.counters["bus_errors"],
    ),
    ToshibaInterfaceSensorDescription(
        key="device_messages", translation_key="device_messages",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.counters["device_messages"],
    ),
    ToshibaInterfaceSensorDescription(
        key="frames_last", translation_key="frames_last",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.frames_last,
    ),
    ToshibaInterfaceSensorDescription(
        key="units_present", translation_key="units_present",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: sum(1 for u in c.units if c.present(u)),
    ),
    ToshibaInterfaceSensorDescription(
        key="slave_address", translation_key="slave_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.slave,
    ),
    # Stan z rejestru 39993. "suspended" i "address_duplicated" to awarie konfiguracji:
    # interfejs żyje, odpowiada na 0x08, ale jednostek nie obsługuje.
    ToshibaInterfaceSensorDescription(
        key="iface_status", translation_key="iface_status",
        device_class=SensorDeviceClass.ENUM,
        options=sorted(set(reg.IFACE_STATUS.values())),
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.iface["status"],
    ),
    # Liczniki rzadkich zdarzeń, czytane co kilka minut. Rosnący "bez odpowiedzi" przy
    # stałych błędach CRC wskazuje raczej na drugiego mastera niż na zakłócenia.
    ToshibaInterfaceSensorDescription(
        key="exceptions", translation_key="exceptions",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.iface["exceptions"],
    ),
    ToshibaInterfaceSensorDescription(
        key="no_response", translation_key="no_response",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.iface["no_response"],
    ),
    ToshibaInterfaceSensorDescription(
        key="busy", translation_key="busy",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.iface["busy"],
    ),
    ToshibaInterfaceSensorDescription(
        key="overrun", translation_key="overrun",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        read=lambda c: c.iface["overrun"],
    ),
)


class ToshibaInterfaceSensor(ToshibaInterfaceEntity, SensorEntity):
    entity_description: ToshibaInterfaceSensorDescription

    def __init__(self, coordinator, description: ToshibaInterfaceSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.read(self.coordinator)
