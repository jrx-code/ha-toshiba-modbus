"""Filter sign reset - a write with no state of its own."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers as reg
from .const import DOMAIN
from .coordinator import ToshibaModbusCoordinator
from .entity import ToshibaUnitEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ToshibaModbusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ToshibaFilterReset(coordinator, unit) for unit in coordinator.units)


class ToshibaFilterReset(ToshibaUnitEntity, ButtonEntity):
    _attr_translation_key = "filter_reset"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, unit: int) -> None:
        super().__init__(coordinator, unit, "filter_reset")

    async def async_press(self) -> None:
        await self.coordinator.async_write_coil(
            reg.addr("coil", self._unit, "filter_reset"), True
        )
