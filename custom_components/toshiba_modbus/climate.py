"""Climate entity: one per indoor unit."""

from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import registers as reg
from .const import DOMAIN, SIGNAL_NEW_UNIT
from .coordinator import ToshibaModbusCoordinator
from .entity import ToshibaUnitEntity

# Rejestr trybu (odczyt) rozróżnia auto-grzanie i auto-chłodzenie; HA ma jeden AUTO.
READ_TO_HVAC = {
    1: HVACMode.HEAT, 2: HVACMode.COOL, 3: HVACMode.DRY,
    4: HVACMode.FAN_ONLY, 5: HVACMode.AUTO, 6: HVACMode.AUTO,
}
HVAC_TO_WRITE = {
    HVACMode.HEAT: 1, HVACMode.COOL: 2, HVACMode.DRY: 3,
    HVACMode.FAN_ONLY: 4, HVACMode.AUTO: 5,
}
FAN_TO_WORD = {"auto": 2, "low": 5, "low_plus": 8, "medium": 4, "high": 3, "high_plus": 7}
WORD_TO_FAN = {v: k for k, v in FAN_TO_WORD.items()}
SWING_TO_WORD = {"off": 7, "swing": 1}
WORD_TO_SWING = {7: "off", 1: "swing"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ToshibaModbusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ToshibaClimate(coordinator, unit) for unit in coordinator.units)


    @callback
    def _new_unit(unit: int) -> None:
        async_add_entities([ToshibaClimate(coordinator, unit)])

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_UNIT.format(entry.entry_id), _new_unit)
    )


class ToshibaClimate(ToshibaUnitEntity, ClimateEntity):
    """Sterowanie klimatem jednej jednostki wewnętrznej."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = reg.TEMP_STEP
    _attr_min_temp = reg.TEMP_MIN
    _attr_max_temp = reg.TEMP_MAX
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.DRY,
                        HVACMode.FAN_ONLY, HVACMode.AUTO]
    _attr_fan_modes = list(FAN_TO_WORD)
    _attr_swing_modes = list(SWING_TO_WORD)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: ToshibaModbusCoordinator, unit: int) -> None:
        super().__init__(coordinator, unit, "climate")

    # ------------------------------------------------------------------ odczyt

    def _scaled(self, space: str, key: str) -> float | None:
        word = self.coordinator.word(self._unit, space, key)
        return None if word is None else reg.signed(word) / 10

    @property
    def current_temperature(self) -> float | None:
        return self._scaled("input", "room_temp")

    @property
    def target_temperature(self) -> float | None:
        return self._scaled("holding", "setpoint")

    @property
    def hvac_mode(self) -> HVACMode | None:
        if not self.coordinator.bit(self._unit, "discrete", "onoff"):
            return HVACMode.OFF
        word = self.coordinator.word(self._unit, "input", "mode")
        return READ_TO_HVAC.get(word) if word is not None else None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Bit sprężarki (10004) mówi, czy jednostka faktycznie grzeje lub chłodzi."""
        if not self.coordinator.bit(self._unit, "discrete", "onoff"):
            return HVACAction.OFF
        thermo = self.coordinator.bit(self._unit, "discrete", "thermo")
        if thermo is None:
            return None
        if not thermo:
            return HVACAction.IDLE
        mode = self.hvac_mode
        if mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if mode == HVACMode.COOL:
            return HVACAction.COOLING
        if mode == HVACMode.DRY:
            return HVACAction.DRYING
        if mode == HVACMode.FAN_ONLY:
            return HVACAction.FAN
        return HVACAction.IDLE

    @property
    def fan_mode(self) -> str | None:
        word = self.coordinator.word(self._unit, "input", "fan")
        return WORD_TO_FAN.get(word) if word is not None else None

    @property
    def swing_mode(self) -> str | None:
        word = self.coordinator.word(self._unit, "input", "louver")
        if word is None:
            return None
        # Pozycje F1-F5 nie występują na RAC, ale gdyby przyszły, nie udawaj swingu.
        return WORD_TO_SWING.get(word, "off")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        c = self.coordinator
        code = c.word(self._unit, "input", "check_code")
        func = c.word(self._unit, "input", "func_status")
        attrs: dict[str, object] = {}
        if code is not None:
            attrs["kod_bledu"] = f"0x{code:04X}"
            attrs["kod_bledu_opis"] = reg.CHECK_CODES.get(code, "nieznany")
        if func is not None:
            attrs["funkcje_rac"] = [n for i, n in enumerate(reg.FUNC_BITS) if func >> i & 1]
        return attrs

    # ------------------------------------------------------------------ zapis

    async def async_set_temperature(self, **kwargs) -> None:
        target = kwargs.get("temperature")
        if target is None:
            return
        value = int(round(float(target) * 10))
        await self.coordinator.async_write_register(
            reg.addr("holding", self._unit, "setpoint"), value
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
            return
        await self.coordinator.async_write_register(
            reg.addr("holding", self._unit, "mode"), HVAC_TO_WRITE[hvac_mode]
        )
        if not self.coordinator.bit(self._unit, "discrete", "onoff"):
            await self.async_turn_on()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_write_register(
            reg.addr("holding", self._unit, "fan"), FAN_TO_WORD[fan_mode]
        )

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.coordinator.async_write_register(
            reg.addr("holding", self._unit, "louver"), SWING_TO_WORD[swing_mode]
        )

    async def async_turn_on(self) -> None:
        await self.coordinator.async_write_coil(reg.addr("coil", self._unit, "onoff"), True)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_write_coil(reg.addr("coil", self._unit, "onoff"), False)
