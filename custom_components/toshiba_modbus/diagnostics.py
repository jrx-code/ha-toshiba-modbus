"""Diagnostics: the read plan and the raw registers behind every entity."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ToshibaModbusCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: ToshibaModbusCoordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "connection": coordinator.diagnostics(),
        "units": {
            str(unit): {
                "name": coordinator.unit_name(unit),
                "present": coordinator.present(unit),
                "model": coordinator.text(unit, "model"),
            }
            for unit in coordinator.units
        },
        "raw": {
            space: dict(sorted(values.items()))
            for space, values in (coordinator.data or {}).items()
        },
    }
