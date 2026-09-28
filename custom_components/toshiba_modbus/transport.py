"""The link to the gateway, borrowed from Home Assistant's shared Modbus connections.

Since 2026.9 the core `modbus` integration hands out units over connections it
shares between integrations (`async_get_unit`), and serializes every request on a
connection behind one pacer. This module keeps the two things that are specific to
this installation on top of it: the framing names used in the config entry, and a
per-request time limit.
"""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TypeVar

from modbus_connection import (
    ModbusError,
    ModbusTcpParams,
    ModbusTimeoutError,
    ModbusUnit,
)

from .const import DEFAULT_TIMEOUT, FRAMING_RTUOVERTCP

T = TypeVar("T")


def link_params(host: str, port: int, framing: str) -> ModbusTcpParams:
    """Connection details as the core wants them: MBAP is "socket", raw RTU is "rtu"."""
    return ModbusTcpParams(
        host=host, port=port, framer="rtu" if framing == FRAMING_RTUOVERTCP else "socket"
    )


async def call(unit: ModbusUnit, request: Callable[[], Awaitable[T]], *, rtu: bool) -> T:
    """One request, bounded by DEFAULT_TIMEOUT once the link is up.

    The core opens the connection with a fixed 10 s timeout. Over this gateway a
    read that is too long, or a frame lost on a weak WiFi link, gets no answer at
    all, so every such frame would hold the shared queue for 10 s - and a write
    queued behind it would wait as long. One transaction takes ~800 ms, so 3 s is
    plenty.

    On our timeout an RTU link is dropped: a late answer would otherwise be read as
    the answer to the next request, and RTU framing has no transaction id to catch
    that. MBAP keeps the link - the late answer carries an old transaction id and
    is discarded; measured on the Waveshare gateway, three reads after each of two
    abandoned requests all answered in ~0.8 s. Dropping it anyway would cost more:
    the first request on a fresh link runs under the core's 10 s limit.

    Opening the link is left alone. The core does it inside the first request,
    behind a shielded task bounded at 10 s; cancelling that midway leaves the task
    to fail on its own, and asyncio logs it as an error on every unreachable cycle.
    A refused or unreachable host then surfaces as the core's ModbusConnectionError,
    which the config flow reports as a connection failure rather than a silent
    interface.
    """
    if not unit.connected:
        return await request()
    try:
        async with asyncio.timeout(DEFAULT_TIMEOUT):
            return await request()
    except TimeoutError as err:
        if rtu:
            with contextlib.suppress(ModbusError, OSError):
                await unit.disconnect()
        raise ModbusTimeoutError(f"no answer within {DEFAULT_TIMEOUT:g} s") from err
