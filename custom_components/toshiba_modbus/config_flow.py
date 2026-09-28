"""Config flow: connect, then find the indoor units that actually answer."""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow, ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from modbus_connection import ModbusConnectionError, ModbusError

from . import registers as reg
from .transport import call, link_params
from .const import (
    CONF_DISCOVER_MAX, CONF_EXCLUDED, CONF_FRAMING, CONF_RESCAN_INTERVAL, CONF_SCAN_INTERVAL,
    CONF_SLAVE, CONF_UNITS, DEFAULT_DISCOVER_MAX, DEFAULT_PORT,
    DEFAULT_RESCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DEFAULT_SLAVE,
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


class NoReply(Exception):
    """Gniazdo się otworzyło, ale interfejs nie odpowiedział poprawną ramką."""


async def _discover(
    hass: HomeAssistant, host: str, port: int, framing: str, slave: int, limit: int
) -> dict[int, tuple[str, str]]:
    """Nazwa modelu jest jedynym pewnym testem obecności - nieobecna jednostka
    oddaje poprawną ramkę zer, nie wyjątek.

    Rozdzielamy dwie porażki, bo prowadzą do zupełnie innych rzeczy do sprawdzenia:
    nieudane połączenie TCP to zły adres albo port, a brak odpowiedzi na otwartym
    gnieździe to ramkowanie, adres slave albo drugi master na tej samej linii.
    Połączenie pożyczamy od rdzenia na czas kreatora; jeśli wpis już je trzyma,
    zostaje otwarte.
    """
    found: dict[int, tuple[str, str]] = {}
    async with async_get_temporary_unit(hass, link_params(host, port, framing), slave) as unit:

        async def text(n: int, key: str) -> str:
            start, count = reg.addr("input", n, key), reg.width("input", key)
            try:
                words = await call(unit, lambda: unit.read_input_registers(start, count),
                                   rtu=framing == FRAMING_RTUOVERTCP)
            except ModbusConnectionError:
                raise
            except ModbusError as err:
                raise NoReply(f"interfejs nie odpowiedział na odczyt jednostki {n}: {err}") from err
            return reg.decode_ascii(list(words))

        for n in range(1, limit + 1):
            model = await text(n, "model")
            if not model:
                continue
            # Numer seryjny czytamy dopiero dla jednostek obecnych - dla pustych
            # adresów byłaby to druga ramka po nic.
            found[n] = (model, await text(n, "serial"))
    return found


class ToshibaModbusConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._found: dict[int, tuple[str, str]] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._async_abort_entries_match(
                {CONF_HOST: user_input[CONF_HOST], CONF_SLAVE: user_input[CONF_SLAVE]}
            )
            try:
                self._found = await asyncio.wait_for(
                    _discover(
                        self.hass,
                        user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_FRAMING],
                        user_input[CONF_SLAVE], user_input[CONF_DISCOVER_MAX],
                    ),
                    timeout=120,
                )
            except NoReply:
                errors["base"] = "no_reply"
            except (ModbusConnectionError, asyncio.TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            except HomeAssistantError:
                # Ta bramka jest już w użyciu przez rdzeń z innym ramkowaniem.
                errors["base"] = "in_use"
            else:
                self._data = dict(user_input)
                if not self._found:
                    # Interfejs odpowiada, ale magistrala Uh jest pusta - to jest
                    # stan poprawny przed montażem adapterów RAC. Wpis powstaje
                    # z samym interfejsem, jednostki dojdą, kiedy się zgłoszą.
                    return self._create({}, [], [])
                return await self.async_step_units()
        return self.async_show_form(step_id="user", data_schema=STEP_USER, errors=errors)

    def _labels(self) -> dict[int, tuple[str, str]]:
        """Klucze pól formularza.

        Etykieta pola to jego klucz, dopóki nie ma dla niego tłumaczenia, a kluczy
        zależnych od adresu przetłumaczyć się nie da - jest ich do 64. Dlatego adres,
        model i numer seryjny wchodzą wprost do klucza; adres na początku gwarantuje
        unikalność nawet przy dwóch identycznych tabliczkach.
        """
        return {
            unit: (f"{unit} · {model} · {serial or 'no serial'}", f"Name {unit}")
            for unit, (model, serial) in sorted(self._found.items())
        }

    async def async_step_units(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Wybór i nazwanie wykrytych jednostek w jednym oknie."""
        labels = self._labels()
        if user_input is not None:
            include = [u for u, (box, _) in labels.items() if user_input.get(box)]
            names = {
                str(u): (user_input.get(labels[u][1]) or f"Unit {u}").strip()
                for u in include
            }
            return self._create(
                names, include, [u for u in sorted(self._found) if u not in include]
            )

        schema: dict[Any, Any] = {}
        for unit, (box, name) in labels.items():
            schema[vol.Required(box, default=True)] = selector.BooleanSelector()
            schema[vol.Optional(name, default=f"Unit {unit}")] = str
        return self.async_show_form(
            step_id="units",
            data_schema=vol.Schema(schema),
            description_placeholders={"count": str(len(self._found))},
        )

    def _create(
        self, names: dict[str, str], include: list[int], exclude: list[int]
    ) -> ConfigFlowResult:
        data = dict(self._data)
        data[CONF_UNITS] = sorted(include)
        data["names"] = names
        return self.async_create_entry(
            title=f"Toshiba ({self._data[CONF_HOST]})",
            data=data,
            options={CONF_EXCLUDED: sorted(exclude)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return ToshibaModbusOptionsFlow()


class ToshibaModbusOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        excluded = sorted(
            int(x) for x in self.config_entry.options.get(CONF_EXCLUDED, [])
        )
        if user_input is not None:
            # Przywrócone adresy znikają z listy wykluczeń; skan w tle albo przycisk
            # znajdzie je przy najbliższej okazji. Reszta wykluczeń musi przetrwać
            # zapis opcji, bo async_create_entry podmienia je w całości.
            restored = {int(x) for x in user_input.pop("restore", [])}
            options = dict(user_input)
            options[CONF_EXCLUDED] = sorted(a for a in excluded if a not in restored)
            return self.async_create_entry(data=options)
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
                **({
                    vol.Optional("restore", default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=str(a), label=f"adres {a}")
                                for a in excluded
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                } if excluded else {}),
            }),
        )
