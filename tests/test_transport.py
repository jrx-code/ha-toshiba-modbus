"""The per-request limit on top of the core's shared Modbus connection."""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import types

import pytest
from modbus_connection import ModbusTimeoutError

HERE = pathlib.Path(__file__).resolve().parent
COMP = HERE.parent / "custom_components" / "toshiba_modbus"

# const.py and transport.py do not import Home Assistant, so they load as a
# stand-alone package without it.
pkg = types.ModuleType("tm")
pkg.__path__ = [str(COMP)]
sys.modules["tm"] = pkg
for name in ("const", "transport"):
    spec = importlib.util.spec_from_file_location(f"tm.{name}", COMP / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"tm.{name}"] = module
    spec.loader.exec_module(module)
transport = sys.modules["tm.transport"]
const = sys.modules["tm.const"]


class FakeUnit:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.disconnects = 0

    async def disconnect(self) -> None:
        self.disconnects += 1


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    monkeypatch.setattr(transport, "DEFAULT_TIMEOUT", 0.05)


def run(coro):
    return asyncio.run(coro)


def test_framing_names_map_to_core_framers():
    assert transport.link_params("h", 502, const.FRAMING_TCP).framer == "socket"
    assert transport.link_params("h", 8899, const.FRAMING_RTUOVERTCP).framer == "rtu"


def test_answer_passes_through():
    async def answer():
        return [1, 2]

    assert run(transport.call(FakeUnit(), answer, rtu=True)) == [1, 2]


@pytest.mark.parametrize("rtu, drops", [(True, 1), (False, 0)])
def test_silence_times_out_and_only_rtu_drops_the_link(rtu, drops):
    """RTU has no transaction id, so a late answer would pass as the next one."""
    unit = FakeUnit()

    async def silence():
        await asyncio.sleep(1)

    with pytest.raises(ModbusTimeoutError):
        run(transport.call(unit, silence, rtu=rtu))
    assert unit.disconnects == drops


def test_opening_the_link_is_not_cut_short():
    """The core bounds the connect itself; cancelling it midway gets logged as an error."""
    unit = FakeUnit(connected=False)

    async def slow_connect_then_answer():
        await asyncio.sleep(0.1)  # longer than the patched limit
        return [7]

    assert run(transport.call(unit, slow_connect_then_answer, rtu=True)) == [7]
    assert unit.disconnects == 0
