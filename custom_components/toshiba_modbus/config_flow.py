"""Config flow: connect, then find the indoor units that actually answer."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow, ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.framer import FramerType

from . import registers as reg
from .const import (
    CONF_DISCOVER_MAX, CONF_FRAMING, CONF_RESCAN_INTERVAL, CONF_SCAN_INTERVAL,
    CONF_SLAVE, CONF_UNITS, DEFAULT_DISCOVER_MAX, DEFAULT_PORT,
    DEFAULT_RESCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DEFAULT_SLAVE, DEFAULT_TIMEOUT,
    DOMAIN, FRAMING_RTUOVERTCP, FRAMINGS,
)

def number(minimum: int, maximum: int, unit: str | None = None):
    """Pole liczbowe, nie suwak.

    Zwykły `vol.Range` na liczbie całkowitej frontend HA renderuje jako suwak, co przy
    adresie slave czy porcie jest bezużyteczne - wartość wpisuje się z płytki albo
    z instrukcji, nie dobiera przeciąganiem.
    """
    config: dict[str, Any] = {
        "min": minimum, "max": maximum, "step": 1,
        "mode": selector.NumberSelectorMode.BOX,
    }
    # Klucz jednostki musi zniknąć, gdy jej nie ma - None nie przechodzi walidacji
    # selektora ("expected str for dictionary value").
    if unit:
        config["unit_of_measurement"] = unit
    return vol.All(
        selector.NumberSelector(selector.NumberSelectorConfig(**config)),
        vol.Coerce(int),
    )


STEP_USER = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_PORT, default=DEFAULT_PORT): number(1, 65535),
    vol.Required(CONF_FRAMING, default=FRAMING_RTUOVERTCP): vol.In(FRAMINGS),
    vol.Required(CONF_SLAVE, default=DEFAULT_SLAVE): number(1, 247),
    vol.Required(CONF_DISCOVER_MAX, default=DEFAULT_DISCOVER_MAX): number(1, reg.ADDR_MAX),
    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): number(10, 600, "s"),
    vol.Required(CONF_RESCAN_INTERVAL, default=DEFAULT_RESCAN_INTERVAL): number(0, 3600, "s"),
})


async def _discover(host: str, port: int, framing: str, slave: int, limit: int) -> dict[int, str]:
    """Nazwa modelu jest jedynym pewnym testem obecności - nieobecna jednostka
    oddaje poprawną ramkę zer, nie wyjątek."""
    framer = FramerType.RTU if framing == FRAMING_RTUOVERTCP else FramerType.SOCKET
    client = AsyncModbusTcpClient(host=host, port=port, framer=framer, timeout=DEFAULT_TIMEOUT)
    if not await client.connect():
        raise ConnectionError("nie można połączyć się z bramką")
    found: dict[int, str] = {}
    try:
        for unit in range(1, limit + 1):
            result = await client.read_input_registers(
                address=reg.addr("input", unit, "model"),
                count=reg.width("input", "model"),
                device_id=slave,
            )
            if result.isError():
                raise ConnectionError(f"interfejs odrzucił odczyt jednostki {unit}: {result}")
            name = reg.decode_ascii(list(result.registers))
            if name:
                found[unit] = name
    finally:
        client.close()
    return found


class ToshibaModbusConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._found: dict[int, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._async_abort_entries_match(
                {CONF_HOST: user_input[CONF_HOST], CONF_SLAVE: user_input[CONF_SLAVE]}
            )
            try:
                self._found = await asyncio.wait_for(
                    _discover(
                        user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_FRAMING],
                        user_input[CONF_SLAVE], user_input[CONF_DISCOVER_MAX],
                    ),
                    timeout=120,
                )
            except (ConnectionError, asyncio.TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            else:
                self._data = dict(user_input)
                if not self._found:
                    # Interfejs odpowiada, ale magistrala Uh jest pusta - to jest
                    # stan poprawny przed montażem adapterów RAC. Wpis powstaje
                    # z samym interfejsem, jednostki dojdą, kiedy się zgłoszą.
                    return self._create({})
                return await self.async_step_names()
        return self.async_show_form(step_id="user", data_schema=STEP_USER, errors=errors)

    async def async_step_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Nazwy pomieszczeń - w rejestrach jest tylko model, nie ma gdzie ich wziąć."""
        if user_input is not None:
            return self._create({str(u): user_input[f"unit_{u}"] for u in sorted(self._found)})
        schema = vol.Schema({
            vol.Required(f"unit_{u}", default=f"Jednostka {u}"): str
            for u in sorted(self._found)
        })
        return self.async_show_form(
            step_id="names",
            data_schema=schema,
            description_placeholders={
                "found": ", ".join(f"{u}: {m}" for u, m in sorted(self._found.items()))
            },
        )

    def _create(self, names: dict[str, str]) -> ConfigFlowResult:
        data = dict(self._data)
        data[CONF_UNITS] = sorted(self._found)
        data["names"] = names
        return self.async_create_entry(title=f"Toshiba ({self._data[CONF_HOST]})", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return ToshibaModbusOptionsFlow()


class ToshibaModbusOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        def now(key, fallback):
            return self.config_entry.options.get(
                key, self.config_entry.data.get(key, fallback)
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_SCAN_INTERVAL,
                             default=now(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): number(10, 600, "s"),
                vol.Required(CONF_RESCAN_INTERVAL,
                             default=now(CONF_RESCAN_INTERVAL, DEFAULT_RESCAN_INTERVAL)): number(0, 3600, "s"),
                vol.Required(CONF_DISCOVER_MAX,
                             default=now(CONF_DISCOVER_MAX, DEFAULT_DISCOVER_MAX)): number(1, reg.ADDR_MAX),
            }),
        )
