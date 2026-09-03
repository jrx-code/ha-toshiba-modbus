"""Filter sign reset - a write with no state of its own."""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers as reg
from .const import DOMAIN, SIGNAL_NEW_UNIT
from .coordinator import ToshibaModbusCoordinator
from .entity import ToshibaInterfaceEntity, ToshibaUnitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ToshibaModbusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [ToshibaDiscoverUnits(coordinator)]
        + [ToshibaFilterReset(coordinator, unit) for unit in coordinator.units]
    )

    @callback
    def _new_unit(unit: int) -> None:
        async_add_entities([ToshibaFilterReset(coordinator, unit)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_UNIT.format(entry.entry_id), _new_unit)
    )


class ToshibaFilterReset(ToshibaUnitEntity, ButtonEntity):
    _attr_translation_key = "filter_reset"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, unit: int) -> None:
        super().__init__(coordinator, unit, "filter_reset")

    async def async_press(self) -> None:
        await self.coordinator.async_write_coil(
            reg.addr("coil", self._unit, "filter_reset"), True
        )


class ToshibaDiscoverUnits(ToshibaInterfaceEntity, ButtonEntity):
    """Szuka jednostek, które jeszcze się nie zgłosiły na magistrali Uh.

    Adaptery RAC wpina się po kolei, więc po każdym wpięciu jest moment, w którym
    warto sprawdzić od razu, zamiast czekać na skan w tle.
    """

    _attr_translation_key = "discover_units"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "discover_units")

    @property
    def available(self) -> bool:
        # Ten przycisk jest potrzebny właśnie wtedy, gdy nic jeszcze nie odpowiada.
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_rescan_now()
