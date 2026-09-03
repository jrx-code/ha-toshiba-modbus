#!/usr/bin/env python3
"""Emulator interfejsu Toshiba BMS-IFMB1280U-E.

Serwer Modbus RTU-over-TCP, stdlib, bez zależności. Odpowiada tak, jak zachowuje
się prawdziwy interfejs, łącznie z rzeczami, które łatwo przeoczyć przy pisaniu
integracji:

* jeden interfejs zajmuje **trzy** adresy slave — N (jednostki 1-64),
  N+1 (65-128) i N+2 (linie jednostek zewnętrznych, tu zwraca wyjątek 0x02);
* **nieobecna jednostka nie daje wyjątku** — oddaje poprawną ramkę wypełnioną
  zerami, więc jedynym testem obecności jest nazwa modelu;
* funkcja 0x08 (pętla zwrotna i liczniki) odpowiada sam interfejs i nie schodzi
  na magistralę Uh.

    python3 emulator.py --port 5502 --units 1,2,3 --absent 3

Domyślnie jednostka 3 jest nieobecna, żeby było na czym sprawdzić, że encje
schodzą w `unavailable` zamiast pokazywać zera jako prawdziwe dane.
"""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import importlib.util
import logging
import pathlib
import socket
import socketserver
import struct
import threading
import time

_SPEC = importlib.util.spec_from_file_location(
    "registers",
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components" / "toshiba_modbus" / "registers.py",
)
reg = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(reg)

_LOG = logging.getLogger("emulator")

MODEL = "RAS-B10N4KVRG-E"
SERIAL_PREFIX = "SN00000"


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def ascii_words(text: str, words: int) -> list[int]:
    raw = text.encode("ascii")[: words * 2].ljust(words * 2, b"\x00")
    return [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]


class Unit:
    """Stan jednej jednostki wewnętrznej."""

    def __init__(self, number: int, present: bool) -> None:
        self.n = number
        self.present = present
        self.on = number == 1
        self.setpoint = 220 + number * 5      # dziesiąte części stopnia
        self.room = 231 - number * 3
        self.mode = 2                          # chłodzenie
        self.fan = 2                           # auto
        self.louver = 7                        # stop
        self.save = 0
        self.rc_lock = 0
        self.hours = 1200 + number * 37
        self.check_code = 0
        self.filter_sign = number == 2
        self.alarm = False
        self.functions = {"hi_power": False, "eco": False, "quiet": False, "silence": False}

    # ---- odczyt ---------------------------------------------------------
    def input_word(self, offset: int) -> int:
        if not self.present:
            return 0
        table = reg.INPUT
        if offset == table["room_temp"]:
            return self.room & 0xFFFF
        if offset == table["setpoint"]:
            return self.setpoint & 0xFFFF
        if offset == table["check_code"]:
            return self.check_code
        if table["model"] <= offset < table["model"] + 8:
            return ascii_words(MODEL, 8)[offset - table["model"]]
        if table["serial"] <= offset < table["serial"] + 8:
            return ascii_words(f"{SERIAL_PREFIX}{self.n}", 8)[offset - table["serial"]]
        if offset == table["capacity"]:
            return 25                          # 2,5 kW
        if offset == table["mode"]:
            return self.mode if self.on else 0
        if offset == table["fan"]:
            return self.fan if self.on else 1
        if offset == table["louver"]:
            return self.louver
        if offset == table["func_status"]:
            return 0b11111                     # wszystkie funkcje RAC obsługiwane
        return 0

    def holding_word(self, offset: int) -> int:
        if not self.present:
            return 0
        table = reg.HOLDING
        return {
            table["setpoint"]: self.setpoint & 0xFFFF,
            table["hours"]: self.hours,
            table["mode"]: self.mode,
            table["fan"]: self.fan,
            table["louver"]: self.louver,
            table["rc_lock"]: self.rc_lock,
            table["save"]: self.save,
        }.get(offset, 0)

    def discrete_bit(self, offset: int) -> bool:
        if not self.present:
            return False
        table = reg.DISCRETE
        return {
            table["onoff"]: self.on,
            table["filter_sign"]: self.filter_sign,
            table["alarm"]: self.alarm,
            table["thermo"]: self.on and self.mode in (1, 2),
            table["st_pure_filter"]: False,
            table["st_hi_power"]: self.functions["hi_power"],
            table["st_eco"]: self.functions["eco"],
            table["st_quiet"]: self.functions["quiet"],
            table["st_silence"]: self.functions["silence"],
        }.get(offset, False)

    def coil_bit(self, offset: int) -> bool:
        if not self.present:
            return False
        table = reg.COIL
        return {
            table["onoff"]: self.on,
            table["filter_reset"]: False,
            table["hi_power"]: self.functions["hi_power"],
            table["eco"]: self.functions["eco"],
            table["quiet"]: self.functions["quiet"],
            table["silence"]: self.functions["silence"],
        }.get(offset, False)

    # ---- zapis ----------------------------------------------------------
    def write_holding(self, offset: int, value: int) -> None:
        table = reg.HOLDING
        if offset == table["setpoint"]:
            self.setpoint = value if value < 0x8000 else value - 0x10000
        elif offset == table["mode"]:
            self.mode = value
        elif offset == table["fan"]:
            self.fan = value
        elif offset == table["louver"]:
            self.louver = value
        elif offset == table["rc_lock"]:
            self.rc_lock = value
        elif offset == table["save"]:
            self.save = value

    def write_coil(self, offset: int, value: bool) -> None:
        table = reg.COIL
        if offset == table["onoff"]:
            self.on = value
        elif offset == table["filter_reset"] and value:
            self.filter_sign = False
        else:
            for name, off in (("hi_power", table["hi_power"]), ("eco", table["eco"]),
                              ("quiet", table["quiet"]), ("silence", table["silence"])):
                if offset == off:
                    self.functions[name] = value


class Interface:
    def __init__(self, slave: int, present: list[int], absent: list[int]) -> None:
        self.slave = slave
        self.units = {n: Unit(n, n not in absent) for n in sorted(set(present) | set(absent))}
        self.lock = threading.Lock()
        self.messages = 0
        self.crc_errors = 0

    def _locate(self, space: str, address: int) -> tuple[Unit | None, int]:
        stride = reg.STRIDE_BITS if space in ("coil", "discrete") else reg.STRIDE_WORDS
        number = address // stride + 1
        return self.units.get(number), address % stride

    def read(self, space: str, address: int, count: int) -> list[int]:
        out = []
        for i in range(count):
            unit, offset = self._locate(space, address + i)
            if unit is None:
                out.append(0)           # jednostka spoza konfiguracji: zera, nie wyjątek
            elif space == "input":
                out.append(unit.input_word(offset))
            elif space == "holding":
                out.append(unit.holding_word(offset))
            elif space == "discrete":
                out.append(int(unit.discrete_bit(offset)))
            else:
                out.append(int(unit.coil_bit(offset)))
        return out

    def write(self, space: str, address: int, value: int) -> None:
        unit, offset = self._locate(space, address)
        if unit is None or not unit.present:
            return
        if space == "holding":
            unit.write_holding(offset, value)
        else:
            unit.write_coil(offset, bool(value))


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        iface: Interface = self.server.iface  # type: ignore[attr-defined]
        self.request.settimeout(300)
        buffer = b""
        while True:
            try:
                chunk = self.request.recv(512)
            except (TimeoutError, OSError):
                return
            if not chunk:
                return
            buffer += chunk
            while len(buffer) >= 4:
                consumed, reply = self._one(iface, buffer)
                if consumed == 0:
                    break
                buffer = buffer[consumed:]
                if reply:
                    self.request.sendall(reply)

    def _one(self, iface: Interface, buf: bytes) -> tuple[int, bytes]:
        # Wszystkie obsługiwane funkcje mają ramkę zapytania długości 8.
        if len(buf) < 8:
            return 0, b""
        frame, rest = buf[:8], buf[8:]
        if crc16(frame[:6]) != frame[6:8]:
            with iface.lock:
                iface.crc_errors += 1
            return 8, b""

        slave, func = frame[0], frame[1]
        addr, qty = struct.unpack(">HH", frame[2:6])

        base = iface.slave
        if slave not in (base, base + 1, base + 2):
            return 8, b""                       # cudzy adres: cisza, jak na magistrali

        with iface.lock:
            iface.messages += 1

            if func == 0x08:
                # Pętla zwrotna i liczniki - odpowiada sam interfejs.
                data = {0x0B: iface.messages, 0x0C: iface.crc_errors,
                        0x0E: iface.messages}.get(addr, qty)
                body = bytes([slave, 0x08]) + struct.pack(">HH", addr, data)
                return 8, body + crc16(body)

            if slave == base + 2:
                return 8, self._exception(slave, func, 0x02)   # linie zewnętrzne: brak mapy
            if slave == base + 1:
                values = [0] * qty                              # jednostki 65-128: nieobecne
            else:
                space = {0x01: "coil", 0x02: "discrete", 0x03: "holding", 0x04: "input"}.get(func)
                if space is None and func not in (0x05, 0x06):
                    return 8, self._exception(slave, func, 0x01)
                if func in (0x05, 0x06):
                    iface.write("holding" if func == 0x06 else "coil", addr,
                                qty if func == 0x06 else int(qty == 0xFF00))
                    return 8, frame                              # echo zapytania
                if qty < 1 or qty > 125:
                    return 8, self._exception(slave, func, 0x03)
                values = iface.read(space, addr, qty)

        if func in (0x01, 0x02):
            packed = bytearray((len(values) + 7) // 8)
            for i, bit in enumerate(values):
                if bit:
                    packed[i // 8] |= 1 << (i % 8)
            body = bytes([slave, func, len(packed)]) + bytes(packed)
        else:
            body = bytes([slave, func, len(values) * 2]) + b"".join(
                struct.pack(">H", v & 0xFFFF) for v in values
            )
        return 8, body + crc16(body)

    @staticmethod
    def _exception(slave: int, func: int, code: int) -> bytes:
        body = bytes([slave, func | 0x80, code])
        return body + crc16(body)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5502)
    ap.add_argument("--slave", type=int, default=1)
    ap.add_argument("--units", default="1,2,3", help="adresy centralne jednostek")
    ap.add_argument("--absent", default="3", help="które z nich udają brak jednostki")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    present = [int(x) for x in args.units.split(",") if x.strip()]
    absent = [int(x) for x in args.absent.split(",") if x.strip()]

    iface = Interface(args.slave, present, absent)
    server = Server((args.host, args.port), Handler)
    server.iface = iface  # type: ignore[attr-defined]
    _LOG.info(
        "emulator BMS-IFMB1280U-E na %s:%s | slave %s (+%s, +%s) | jednostki %s | nieobecne %s",
        args.host, args.port, args.slave, args.slave + 1, args.slave + 2,
        present, absent or "brak",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
