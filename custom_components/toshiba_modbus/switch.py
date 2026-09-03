"""Special RAC functions and the remote-controller lock bits."""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers as reg
from .const import DOMAIN, SIGNAL_NEW_UNIT
from .coordinator import ToshibaModbusCoordinator
from .entity import ToshibaUnitEntity


@dataclass(frozen=True, kw_only=True)
class ToshibaSwitchDescription(SwitchEntityDescription):
    coil: str
    status: str
    # Bit funkcji w rejestrze 30059; None = brak informacji o wsparciu.
    func_bit: str | None = None


FUNCTION_SWITCHES: tuple[ToshibaSwitchDescription, ...] = (
    ToshibaSwitchDescription(key="hi_power", coil="hi_power", status="st_hi_power",
                             func_bit="hi_power", translation_key="hi_power"),
    ToshibaSwitchDescription(key="eco", coil="eco", status="st_eco",
                             func_bit="eco", translation_key="eco"),
    ToshibaSwitchDescription(key="quiet", coil="quiet", status="st_quiet",
                             func_bit="quiet_fcu", translation_key="quiet"),
    ToshibaSwitchDescription(key="silence", coil="silence", status="st_silence",
                             func_bit="silence_cdu", translation_key="silence"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ToshibaModbusCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []
    for unit in coordinator.units:
        entities.extend(ToshibaFunctionSwitch(coordinator, unit, d) for d in FUNCTION_SWITCHES)
        entities.extend(
            ToshibaLockSwitch(coordinator, unit, index, name)
            for index, name in enumerate(reg.RC_LOCK_BITS)
        )
    async_add_entities(entities)


    @callback
    def _new_unit(unit: int) -> None:
        new = [ToshibaFunctionSwitch(coordinator, unit, d) for d in FUNCTION_SWITCHES]
        new += [ToshibaLockSwitch(coordinator, unit, i, n)
                for i, n in enumerate(reg.RC_LOCK_BITS)]
        async_add_entities(new)

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_UNIT.format(entry.entry_id), _new_unit)
    )


class ToshibaFunctionSwitch(ToshibaUnitEntity, SwitchEntity):
    entity_description: ToshibaSwitchDescription

    def __init__(self, coordinator, unit: int, description: ToshibaSwitchDescription) -> None:
        super().__init__(coordinator, unit, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        bit = self.entity_description.func_bit
        supported = self.coordinator.word(self._unit, "input", "func_status")
        if bit is None or supported is None:
            return True
        # Rejestr 30059 mówi, które funkcje ta jednostka w ogóle obsługuje.
        return bool(supported >> reg.FUNC_BITS.index(bit) & 1)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.bit(self._unit, "discrete", self.entity_description.status)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_write_coil(
            reg.addr("coil", self._unit, self.entity_description.coil), True
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write_coil(
            reg.addr("coil", self._unit, self.entity_description.coil), False
        )


class ToshibaLockSwitch(ToshibaUnitEntity, SwitchEntity):
    """Jeden bit blokady pilota. Zapis to read-modify-write całego słowa."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, unit: int, index: int, name: str) -> None:
        super().__init__(coordinator, unit, f"rc_lock_{name}")
        self._index = index
        self._attr_translation_key = f"rc_lock_{name}"

    @property
    def is_on(self) -> bool | None:
        word = self.coordinator.word(self._unit, "holding", "rc_lock")
        return None if word is None else bool(word >> self._index & 1)

    async def _write(self, on: bool) -> None:
        word = self.coordinator.word(self._unit, "holding", "rc_lock")
        if word is None:
            return
        value = word | (1 << self._index) if on else word & ~(1 << self._index)
        await self.coordinator.async_write_register(
            reg.addr("holding", self._unit, "rc_lock"), value & 0xFFFF
        )

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(False)
