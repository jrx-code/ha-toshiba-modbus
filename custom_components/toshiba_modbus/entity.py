"""Shared entity base: one device per indoor unit, availability from presence."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ADAPTER_MODEL, DOMAIN, MANUFACTURER
from .coordinator import ToshibaModbusCoordinator


class ToshibaUnitEntity(CoordinatorEntity[ToshibaModbusCoordinator]):
    """Every entity belongs to one indoor unit, which is one device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ToshibaModbusCoordinator, unit: int, key: str) -> None:
        super().__init__(coordinator)
        self._unit = unit
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{unit}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        c = self.coordinator
        return DeviceInfo(
            identifiers={(DOMAIN, f"{c.entry.entry_id}_{self._unit}")},
            name=c.unit_name(self._unit),
            manufacturer=MANUFACTURER,
            model=c.text(self._unit, "model") or ADAPTER_MODEL,
            serial_number=c.text(self._unit, "serial") or None,
            via_device=(DOMAIN, c.entry.entry_id),
        )

    @property
    def available(self) -> bool:
        # Nieobecna jednostka zwraca poprawną ramkę zer, nie błąd - bez tego
        # testu encje pokazywałyby 0 °C i tryb "invalid" jako prawdziwe dane.
        return super().available and self.coordinator.present(self._unit)
