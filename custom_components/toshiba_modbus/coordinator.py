"""Polling coordinator: one client, merged reads, one lock over the bus."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.framer import FramerType

from . import registers as reg
from .const import (
    CONF_DISCOVER_MAX,
    CONF_EXCLUDED,
    CONF_FRAMING,
    CONF_RESCAN_INTERVAL,
    CONF_SLAVE,
    CONF_UNITS,
    DEFAULT_DISCOVER_MAX,
    DEFAULT_RESCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    FRAMING_RTUOVERTCP,
    SIGNAL_NEW_UNIT,
)

_LOGGER = logging.getLogger(__name__)

SPACES = ("coil", "discrete", "input", "holding")


class ToshibaModbusCoordinator(DataUpdateCoordinator[dict[str, dict[int, int]]]):
    """Reads every configured indoor unit in as few frames as the map allows.

    The gateway does not correlate replies to TCP clients - a second master on
    the same serial line receives frames that answer someone else's request.
    One client, one lock, one master.
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

        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()
        self._plan = self._build_plan()
        self.frames_last = 0
        # Liczniki funkcji 0x08. Odpowiada na nie sam interfejs i nigdy nie schodzą
        # na magistralę Uh, więc nie obciążają jednostek wewnętrznych.
        self.counters: dict[str, int | None] = {
            "bus_messages": None, "bus_errors": None, "device_messages": None,
        }

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

    # ----------------------------------------------------------------- klient

    def _make_client(self) -> AsyncModbusTcpClient:
        framer = FramerType.RTU if self.framing == FRAMING_RTUOVERTCP else FramerType.SOCKET
        return AsyncModbusTcpClient(
            host=self.host, port=self.port, framer=framer, timeout=DEFAULT_TIMEOUT
        )

    async def _get_client(self) -> AsyncModbusTcpClient:
        if self._client is None or not self._client.connected:
            self._client = self._make_client()
            if not await self._client.connect():
                self._client = None
                raise UpdateFailed(f"Brak połączenia z bramką {self.host}:{self.port}")
        return self._client

    async def _read(self, client: AsyncModbusTcpClient, space: str, start: int, count: int) -> list[int]:
        call = {
            "coil": client.read_coils,
            "discrete": client.read_discrete_inputs,
            "input": client.read_input_registers,
            "holding": client.read_holding_registers,
        }[space]
        result = await call(address=start, count=count, device_id=self.slave)
        if result.isError():
            raise UpdateFailed(f"{space} {start}+{count}: {result}")
        return [int(b) for b in result.bits[:count]] if space in ("coil", "discrete") else list(result.registers)

    async def _async_update_data(self) -> dict[str, dict[int, int]]:
        async with self._lock:
            client = await self._get_client()
            data: dict[str, dict[int, int]] = {s: {} for s in SPACES}
            frames = 0
            try:
                for space, start, count in self._plan:
                    values = await self._read(client, space, start, count)
                    frames += 1
                    for i, value in enumerate(values):
                        data[space][start + i] = value
            except UpdateFailed:
                self._client = None
                raise
            except Exception as err:  # noqa: BLE001
                self._client = None
                raise UpdateFailed(f"Błąd odczytu: {err}") from err
            frames += await self._read_counters(client)
            frames += await self._rescan_if_due(client, data)
            self.frames_last = frames
            return data

    def _unknown_addresses(self) -> list[int]:
        return [
            n for n in range(reg.ADDR_MIN, self.discover_max + 1)
            if n not in self.units and n not in self.excluded
        ]

    async def _read_unit_into(
        self, client: AsyncModbusTcpClient, unit: int, target: dict[str, dict[int, int]]
    ) -> int:
        """Dociąga rejestry jednej jednostki od razu po jej wykryciu.

        Bez tego encja rejestruje się, zanim koordynator ma jej nazwę modelu, a wtedy
        urządzenie ląduje w rejestrze HA z modelem zastępczym i bez numeru seryjnego -
        i już tam zostaje, bo device_info czyta się przy zakładaniu encji.
        """
        frames = 0
        for space in SPACES:
            for start, count in reg.blocks_for_unit(space, unit):
                values = await self._read(client, space, start, count)
                frames += 1
                for i, value in enumerate(values):
                    target.setdefault(space, {})[start + i] = value
        return frames

    async def _rescan_if_due(
        self,
        client: AsyncModbusTcpClient,
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
            try:
                result = await client.read_input_registers(
                    address=reg.addr("input", unit, "model"),
                    count=reg.width("input", "model"),
                    device_id=self.slave,
                )
                frames += 1
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("skan jednostki %s przerwany: %s", unit, err)
                break
            if result.isError():
                continue
            if reg.decode_ascii(list(result.registers)):
                found.append(unit)

        self.last_rescan = now
        if found:
            self.units = sorted(self.units + found)
            self._plan = self._build_plan()
            target = data if data is not None else self.data
            for unit in found:
                try:
                    frames += await self._read_unit_into(client, unit, target)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("jednostka %s wykryta, ale nieodczytana: %s", unit, err)
            _LOGGER.info("nowe jednostki na magistrali: %s", found)
            for unit in found:
                async_dispatcher_send(self.hass, SIGNAL_NEW_UNIT.format(self.entry.entry_id), unit)
        return frames

    async def async_rescan_now(self) -> None:
        """Ręczne wymuszenie skanu - po wpięciu adaptera nie ma sensu czekać."""
        async with self._lock:
            client = await self._get_client()
            await self._rescan_if_due(client, force=True)
        await self.async_request_refresh()

    async def _read_counters(self, client: AsyncModbusTcpClient) -> int:
        """Diagnostyka interfejsu. Błąd tutaj nie może wywalić całego odczytu -
        liczniki są dodatkiem, a nie powodem, dla którego encje mają zniknąć."""
        calls = (
            ("bus_messages", client.diag_read_bus_message_count),
            ("bus_errors", client.diag_read_bus_comm_error_count),
            ("device_messages", client.diag_read_device_message_count),
        )
        frames = 0
        for name, call in calls:
            try:
                result = await call(device_id=self.slave)
                frames += 1
                self.counters[name] = None if result.isError() else int(result.message)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("licznik %s niedostępny: %s", name, err)
                self.counters[name] = None
        return frames

    # ----------------------------------------------------------------- zapis

    async def async_write_register(self, address: int, value: int) -> None:
        async with self._lock:
            client = await self._get_client()
            result = await client.write_register(address=address, value=value, device_id=self.slave)
            if result.isError():
                self._client = None
                raise UpdateFailed(f"Zapis rejestru {address}: {result}")
        await self.async_request_refresh()

    async def async_write_coil(self, address: int, value: bool) -> None:
        async with self._lock:
            client = await self._get_client()
            result = await client.write_coil(address=address, value=value, device_id=self.slave)
            if result.isError():
                self._client = None
                raise UpdateFailed(f"Zapis cewki {address}: {result}")
        await self.async_request_refresh()

    async def async_close(self) -> None:
        async with self._lock:
            if self._client is not None and self._client.connected:
                self._client.close()
            self._client = None

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

    def unit_name(self, unit: int) -> str:
        return self.names.get(unit) or f"Jednostka {unit}"

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
            "plan": [{"space": s, "start": a, "count": c} for s, a, c in self._plan],
        }
