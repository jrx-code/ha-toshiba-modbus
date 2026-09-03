"""Polling coordinator: one client, merged reads, one lock over the bus."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.framer import FramerType

from . import registers as reg
from .const import (
    CONF_FRAMING,
    CONF_SLAVE,
    CONF_UNITS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    FRAMING_RTUOVERTCP,
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
        return len(self._plan)

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
            self.frames_last = frames
            return data

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
            "frames_last": self.frames_last,
            "plan": [{"space": s, "start": a, "count": c} for s, a, c in self._plan],
        }
