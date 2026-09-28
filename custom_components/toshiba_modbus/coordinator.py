"""Polling coordinator: one client, merged reads, one lock over the bus."""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from modbus_connection import ModbusError, ModbusUnit

from . import registers as reg
from .const import (
    CONF_DISCOVER_MAX,
    CONF_EXCLUDED,
    CONF_FRAMING,
    CONF_RESCAN_INTERVAL,
    CONF_SERIALS,
    CONF_SLAVE,
    CONF_UNITS,
    DEFAULT_DISCOVER_MAX,
    DEFAULT_RESCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    FRAMING_RTUOVERTCP,
    IFACE_SLOW_INTERVAL,
    OPTIMISTIC_HOLD,
    SIGNAL_NEW_UNIT,
)
from .transport import call, link_params

_LOGGER = logging.getLogger(__name__)

SPACES = ("coil", "discrete", "input", "holding")


class ToshibaModbusCoordinator(DataUpdateCoordinator[dict[str, dict[int, int]]]):
    """Reads every configured indoor unit in as few frames as the map allows.

    The link is a unit on Home Assistant's shared Modbus connection (core 2026.9,
    `async_get_unit`). The core serializes every request on it, so two entries -
    or two integrations - on one gateway queue instead of talking over each other,
    and a write waits for at most the one transaction in flight rather than for a
    whole ~30-frame cycle. A master outside Home Assistant on the same gateway is
    still a second master; the core cannot see it.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.host: str = entry.data["host"]
        self.port: int = entry.data["port"]
        self.framing: str = entry.data.get(CONF_FRAMING, FRAMING_RTUOVERTCP)
        self.slave: int = entry.data.get(CONF_SLAVE, 1)
        self.units: list[int] = list(entry.data.get(CONF_UNITS, []))
        self.names: dict[int, str] = {
            int(k): v for k, v in (entry.data.get("names") or {}).items()
        }
        self.serials: dict[int, str] = {
            int(k): v for k, v in (entry.options.get(CONF_SERIALS) or {}).items() if v
        }

        # Połączenie jest współdzielone i zwalniane przez rdzeń przy wyładowaniu wpisu.
        self._rtu = self.framing == FRAMING_RTUOVERTCP
        self._unit: ModbusUnit = async_get_unit(
            hass, entry, link_params(self.host, self.port, self.framing), self.slave
        )
        # Id urządzenia interfejsu w rejestrze HA, ustawiane w async_setup_entry.
        self.hub_device_id: str | None = None
        # (przestrzeń, adres) -> (wartość zapisana, wartość sprzed zapisu, termin).
        self._optimistic: dict[tuple[str, int], tuple[int, int | None, float]] = {}
        self._plan = self._build_plan()
        self.frames_last = 0
        # Liczniki funkcji 0x08. Odpowiada na nie sam interfejs i nigdy nie schodzą
        # na magistralę Uh, więc nie obciążają jednostek wewnętrznych.
        self.counters: dict[str, int | None] = {
            "bus_messages": None, "bus_errors": None, "device_messages": None,
        }
        # Stan interfejsu i rzadziej potrzebne liczniki. Czytane co IFACE_SLOW_INTERVAL,
        # bo każda ramka to ~0,9 s, a cykl ma już ~30 ramek przy interwale 30 s.
        self.iface: dict[str, Any] = {
            "software": None, "version": None, "status": None,
            "exceptions": None, "no_response": None, "busy": None, "overrun": None,
        }
        self._slow_due = 0.0

        self.discover_max: int = int(
            entry.options.get(CONF_DISCOVER_MAX,
                              entry.data.get(CONF_DISCOVER_MAX, DEFAULT_DISCOVER_MAX))
        )
        rescan = float(
            entry.options.get(CONF_RESCAN_INTERVAL,
                              entry.data.get(CONF_RESCAN_INTERVAL, DEFAULT_RESCAN_INTERVAL))
        )
        # 0 wyłącza skan w tle i zostawia przycisk jako jedyną drogę; wartości
        # dodatnie poniżej minuty podnosimy, żeby nie zalewać magistrali.
        self._rescan_every = 0.0 if rescan <= 0 else max(rescan, 60.0)
        self._rescan_due = 0.0
        self.last_rescan: float | None = None
        # Adresy odznaczone przy dodawaniu wpisu. Bez tej listy skan w tle dołożyłby
        # je z powrotem w ciągu kilku minut i wybór użytkownika nic by nie znaczył.
        self.excluded: list[int] = sorted(
            int(x) for x in entry.options.get(CONF_EXCLUDED, entry.data.get(CONF_EXCLUDED, []))
        )

        scan = entry.options.get("scan_interval", entry.data.get("scan_interval", DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"toshiba_modbus {self.host}:{self.port}",
            update_interval=timedelta(seconds=int(scan)),
        )

    # ----------------------------------------------------------------- plan

    def _build_plan(self) -> list[tuple[str, int, int]]:
        plan: list[tuple[str, int, int]] = []
        for unit in self.units:
            for space in SPACES:
                for start, count in reg.blocks_for_unit(space, unit):
                    plan.append((space, start, count))
        return plan

    @property
    def frames_per_cycle(self) -> int:
        """Bloki rejestrów plus trzy ramki liczników 0x08."""
        return len(self._plan) + len(self.counters)

    # ----------------------------------------------------------------- łącze

    async def _read(self, space: str, start: int, count: int) -> list[int]:
        """Jeden blok. Błąd Modbus idzie w górę jako UpdateFailed z nazwą bloku."""
        u = self._unit
        request = {
            "coil": lambda: u.read_coils(start, count),
            "discrete": lambda: u.read_discrete_inputs(start, count),
            "input": lambda: u.read_input_registers(start, count),
            "holding": lambda: u.read_holding_registers(start, count),
        }[space]
        try:
            values = await call(u, request, rtu=self._rtu)
        except ModbusError as err:
            raise UpdateFailed(f"{space} {start}+{count}: {err}") from err
        return [int(v) for v in values[:count]]

    async def _async_update_data(self) -> dict[str, dict[int, int]]:
        data: dict[str, dict[int, int]] = {s: {} for s in SPACES}
        frames = 0
        if not self._plan:
            # Wpis bez jednostek nie wysyła żadnego bloku, a liczniki i skan połykają
            # swoje błędy - bez tej pętli zwrotnej martwa bramka dawała stan "loaded".
            try:
                await call(self._unit, lambda: self._unit.diagnostics(0x00, 0xA5A5), rtu=self._rtu)
            except ModbusError as err:
                raise UpdateFailed(f"Interfejs nie odpowiada: {err}") from err
            frames += 1
        for space, start, count in list(self._plan):
            values = await self._read(space, start, count)
            frames += 1
            for i, value in enumerate(values):
                data[space][start + i] = value
        frames += await self._read_counters()
        frames += await self._read_interface_if_due()
        frames += await self._rescan_if_due(data)
        self.frames_last = frames
        self._apply_optimistic(data)
        return data

    # ----------------------------------------------------------------- stan po zapisie

    def _hold(self, space: str, address: int, value: int) -> None:
        """Pokazuje zapisaną wartość od razu, zanim wróci z magistrali."""
        if self.data is None:
            return
        before = self.data[space].get(address)
        self._optimistic[(space, address)] = (value, before, time.monotonic() + OPTIMISTIC_HOLD)
        self.data[space][address] = value

    def _apply_optimistic(self, data: dict[str, dict[int, int]]) -> None:
        """Odczyt wygrywa, gdy potwierdził zapis, gdy pokazał cokolwiek innego niż
        stan sprzed zapisu (zmiana z pilota albo tryb auto zgłoszony jako 5 lub 6),
        albo gdy minął termin. Do tego czasu stary stan nie wraca na ekran."""
        now = time.monotonic()
        for key, (value, before, until) in list(self._optimistic.items()):
            space, address = key
            polled = data[space].get(address)
            if now > until or polled is None or polled == value or polled != before:
                del self._optimistic[key]
            else:
                data[space][address] = value

    def _hold_after_write(self, space: str, address: int, value: int) -> None:
        self._hold(space, address, value)
        where = reg.locate(space, address)
        if where is not None:
            unit, key = where
            status = reg.WRITE_STATUS.get((space, key))
            if status is not None:
                self._hold(status[0], reg.addr(status[0], unit, status[1]), value)
        self.async_update_listeners()

    def _unknown_addresses(self) -> list[int]:
        return [
            n for n in range(reg.ADDR_MIN, self.discover_max + 1)
            if n not in self.units and n not in self.excluded
        ]

    async def _read_unit_into(self, unit: int, target: dict[str, dict[int, int]]) -> int:
        """Dociąga rejestry jednej jednostki od razu po jej wykryciu.

        Bez tego encja rejestruje się, zanim koordynator ma jej nazwę modelu, a wtedy
        urządzenie ląduje w rejestrze HA z modelem zastępczym i bez numeru seryjnego -
        i już tam zostaje, bo device_info czyta się przy zakładaniu encji.
        """
        frames = 0
        for space in SPACES:
            for start, count in reg.blocks_for_unit(space, unit):
                values = await self._read(space, start, count)
                frames += 1
                for i, value in enumerate(values):
                    target.setdefault(space, {})[start + i] = value
        return frames

    async def _rescan_if_due(
        self,
        data: dict[str, dict[int, int]] | None = None,
        force: bool = False,
    ) -> int:
        """Szuka jednostek, które jeszcze się nie zgłosiły.

        Adaptery RAC są wpinane po kolei, więc jednorazowe wykrycie przy zakładaniu
        wpisu opisuje tylko ten jeden moment. Skanowane są wyłącznie adresy nieznane -
        po znalezieniu kompletu ta metoda nie wysyła już nic.
        """
        pending = self._unknown_addresses()
        now = time.monotonic()
        if not pending:
            return 0
        if not force and (self._rescan_every <= 0 or now < self._rescan_due):
            return 0

        self._rescan_due = now + self._rescan_every
        frames = 0
        found: list[int] = []
        for unit in pending:
            start, count = reg.addr("input", unit, "model"), reg.width("input", "model")
            try:
                words = await call(
                    self._unit, lambda: self._unit.read_input_registers(start, count),
                    rtu=self._rtu)
                frames += 1
            except ModbusError as err:
                _LOGGER.debug("skan jednostki %s przerwany: %s", unit, err)
                break
            if reg.decode_ascii(list(words)):
                found.append(unit)

        self.last_rescan = now
        if found:
            self.units = sorted(self.units + found)
            self._plan = self._build_plan()
            target = data if data is not None else self.data
            for unit in found:
                try:
                    frames += await self._read_unit_into(unit, target)
                except UpdateFailed as err:
                    _LOGGER.warning("jednostka %s wykryta, ale nieodczytana: %s", unit, err)
            _LOGGER.info("nowe jednostki na magistrali: %s", found)
            for unit in found:
                async_dispatcher_send(self.hass, SIGNAL_NEW_UNIT.format(self.entry.entry_id), unit)
        return frames

    async def async_rescan_now(self) -> None:
        """Ręczne wymuszenie skanu - po wpięciu adaptera nie ma sensu czekać."""
        await self._rescan_if_due(force=True)
        await self.async_request_refresh()

    async def _read_counters(self) -> int:
        """Diagnostyka interfejsu. Błąd tutaj nie może wywalić całego odczytu -
        liczniki są dodatkiem, a nie powodem, dla którego encje mają zniknąć."""
        calls = (("bus_messages", 0x0B), ("bus_errors", 0x0C), ("device_messages", 0x0E))
        frames = 0
        for name, sub in calls:
            try:
                self.counters[name] = int(
                    await call(self._unit, lambda sub=sub: self._unit.diagnostics(sub), rtu=self._rtu))
                frames += 1
            except ModbusError as err:
                _LOGGER.debug("licznik %s niedostępny: %s", name, err)
                self.counters[name] = None
        return frames

    async def _read_interface_if_due(self) -> int:
        """Wersja, stan i liczniki rzadkich zdarzeń - raz na IFACE_SLOW_INTERVAL.

        Tak jak liczniki, połyka własne błędy: to diagnostyka, nie powód, żeby
        jednostki poszły w unavailable.
        """
        now = time.monotonic()
        if now < self._slow_due:
            return 0
        self._slow_due = now + IFACE_SLOW_INTERVAL
        frames = 0
        start, count = reg.IFACE_INFO_START, reg.IFACE_INFO_COUNT
        try:
            words = await call(
                self._unit, lambda: self._unit.read_input_registers(start, count), rtu=self._rtu)
            frames += 1
            software, version, status = reg.decode_iface_info([int(w) for w in words])
            self.iface.update(software=software, version=version, status=status)
        except ModbusError as err:
            _LOGGER.debug("stan interfejsu niedostępny: %s", err)
            self.iface["status"] = None
        calls = (("exceptions", 0x0D), ("no_response", 0x0F), ("busy", 0x11), ("overrun", 0x12))
        for name, sub in calls:
            try:
                self.iface[name] = int(
                    await call(self._unit, lambda sub=sub: self._unit.diagnostics(sub), rtu=self._rtu))
                frames += 1
            except ModbusError as err:
                _LOGGER.debug("licznik %s niedostępny: %s", name, err)
                self.iface[name] = None
        self._publish_version()
        return frames

    def _publish_version(self) -> None:
        """Wersja oprogramowania trafia do urządzenia interfejsu, nie do encji."""
        version = self.iface["version"]
        if not version or self.hub_device_id is None:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get(self.hub_device_id)
        if device is not None and device.sw_version != version:
            registry.async_update_device(self.hub_device_id, sw_version=version)

    # ----------------------------------------------------------------- zapis

    async def _write(self, what: str, address: int, request) -> None:
        """Zapis jako jedna ramka w kolejce współdzielonego połączenia. Błąd idzie do
        użytkownika jako HomeAssistantError - UpdateFailed w akcji usługi niczego mu nie mówi."""
        try:
            await call(self._unit, request, rtu=self._rtu)
        except ModbusError as err:
            raise HomeAssistantError(f"Zapis {what} {address}: {err}") from err

    async def async_write_register(self, address: int, value: int) -> None:
        await self._write("rejestru", address, lambda: self._unit.write_register(address, value))
        self._hold_after_write("holding", address, value)
        await self.async_request_refresh()

    async def async_write_coil(self, address: int, value: bool) -> None:
        await self._write("cewki", address, lambda: self._unit.write_coil(address, bool(value)))
        self._hold_after_write("coil", address, int(bool(value)))
        await self.async_request_refresh()


    # ----------------------------------------------------------------- odczyt pól

    def word(self, unit: int, space: str, key: str) -> int | None:
        return self.data[space].get(reg.addr(space, unit, key)) if self.data else None

    def bit(self, unit: int, space: str, key: str) -> bool | None:
        value = self.word(unit, space, key)
        return None if value is None else bool(value)

    def text(self, unit: int, key: str) -> str | None:
        if not self.data:
            return None
        start = reg.addr("input", unit, key)
        words = [self.data["input"].get(start + i) for i in range(reg.width("input", key))]
        if any(w is None for w in words):
            return None
        return reg.decode_ascii([w for w in words if w is not None])

    def present(self, unit: int) -> bool:
        """Jednostka nieobecna oddaje poprawną ramkę zer, nie wyjątek."""
        return bool(self.text(unit, "model"))

    def model(self, unit: int) -> str | None:
        """Nazwa modelu bez tekstu zastępczego adaptera. Obecność liczy się z
        present() - "RACIF Model Name" też znaczy, że adapter odpowiada."""
        text = self.text(unit, "model")
        return None if not text or text in reg.PLACEHOLDER_MODELS else text

    def serial(self, unit: int) -> str | None:
        """Numer z interfejsu, a gdy go nie ma - wpisany w opcjach.

        Odczyt wygrywa: jeśli kiedyś interfejs zacznie go podawać, to on jest źródłem.
        """
        return self.text(unit, "serial") or self.serials.get(unit)

    def unit_name(self, unit: int) -> str:
        return self.names.get(unit) or f"Unit {unit}"

    def diagnostics(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "framing": self.framing,
            "slave": self.slave,
            "units": self.units,
            "frames_per_cycle": self.frames_per_cycle,
            "discover_max": self.discover_max,
            "pending_addresses": self._unknown_addresses(),
            "excluded": self.excluded,
            "frames_last": self.frames_last,
            "counters": dict(self.counters),
            "interface": dict(self.iface),
            "optimistic": len(self._optimistic),
            "plan": [{"space": s, "start": a, "count": c} for s, a, c in self._plan],
        }
