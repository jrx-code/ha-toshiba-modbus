"""Config flow: connect, then find the indoor units that actually answer."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow, ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.framer import FramerType

from . import registers as reg
from .const import (
    CONF_DISCOVER_MAX, CONF_FRAMING, CONF_RESCAN_INTERVAL, CONF_SCAN_INTERVAL,
    CONF_SLAVE, CONF_UNITS, DEFAULT_DISCOVER_MAX, DEFAULT_PORT,
    DEFAULT_RESCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DEFAULT_SLAVE, DEFAULT_TIMEOUT,
    DOMAIN, FRAMING_RTUOVERTCP, FRAMINGS,
)

STEP_USER = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(int, vol.Range(min=1, max=65535)),
    vol.Required(CONF_FRAMING, default=FRAMING_RTUOVERTCP): vol.In(FRAMINGS),
    vol.Required(CONF_SLAVE, default=DEFAULT_SLAVE): vol.All(int, vol.Range(min=1, max=247)),
    vol.Required(CONF_DISCOVER_MAX, default=DEFAULT_DISCOVER_MAX):
        vol.All(int, vol.Range(min=1, max=reg.ADDR_MAX)),
    vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL):
        vol.All(int, vol.Range(min=10, max=600)),
    vol.Required(CONF_RESCAN_INTERVAL, default=DEFAULT_RESCAN_INTERVAL):
        vol.All(int, vol.Range(min=0, max=3600)),
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
                vol.Required(CONF_SCAN_INTERVAL, default=now(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
                    vol.All(int, vol.Range(min=10, max=600)),
                vol.Required(CONF_RESCAN_INTERVAL,
                             default=now(CONF_RESCAN_INTERVAL, DEFAULT_RESCAN_INTERVAL)):
                    vol.All(int, vol.Range(min=0, max=3600)),
                vol.Required(CONF_DISCOVER_MAX, default=now(CONF_DISCOVER_MAX, DEFAULT_DISCOVER_MAX)):
                    vol.All(int, vol.Range(min=1, max=reg.ADDR_MAX)),
            }),
        )
