"""Power limit (Save) - the register the built-in modbus integration cannot express."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers as reg
from .const import DOMAIN
from .coordinator import ToshibaModbusCoordinator
from .entity import ToshibaUnitEntity

# "100% Save" jest w mapie rejestrów, ale manual oznacza je jako niedostępne dla RAC,
# więc nie ma go na liście wyboru - zapis, który i tak nie zadziała, jest gorszy niż brak opcji.
OFFERED = {v: k for k, v in reg.SAVE_MODES.items() if k not in reg.SAVE_UNAVAILABLE_RAC}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ToshibaModbusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ToshibaSaveSelect(coordinator, unit) for unit in coordinator.units)


class ToshibaSaveSelect(ToshibaUnitEntity, SelectEntity):
    _attr_translation_key = "save"
    _attr_options = list(OFFERED)

    def __init__(self, coordinator, unit: int) -> None:
        super().__init__(coordinator, unit, "save")

    @property
    def current_option(self) -> str | None:
        word = self.coordinator.word(self._unit, "holding", "save")
        return reg.SAVE_MODES.get(word) if word is not None else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_write_register(
            reg.addr("holding", self._unit, "save"), OFFERED[option]
        )
