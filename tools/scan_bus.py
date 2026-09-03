#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 JI ENGINEERING <engineering@iwanus.eu>
"""Sprawdza, czy adaptery RAC I/F odzywają się na magistrali Uh.

Trzy niezależne odpowiedzi, od najtańszej do najdroższej:

1. Pętla zwrotna 0x08 na każdym adresie slave. Obsługuje ją sam interfejs
   BMS-IFMB i nie schodzi ona na Uh, więc echo dowodzi tylko tego, że stoi
   łączność do interfejsu.
2. Liczniki diagnostyczne 0x08/0x0B, 0x0C i 0x0E. **Liczą ruch Modbus, nie
   magistralę Uh** - zmierzone 2026-09-03: przyrost równa się dokładnie liczbie
   ramek wysłanych przez pytającego, a w 20 s ciszy nie przybywa nic ponad
   własne odczyty licznika. Zero błędów CRC mówi więc tylko tyle, że łącze
   RS-485 do interfejsu jest czyste, i nie mówi nic o adapterach.
3. Skan nazw modeli. To jedyny test obecności adaptera. Nieobecna jednostka nie
   zwraca wyjątku, tylko poprawną ramkę zer, więc zero nie odróżnia „wyłączona"
   od „nie ma jej" - rozstrzyga nazwa modelu.

Uruchamianie bez argumentów skanuje pełny zakres 1-64. Skrypt nie zapisuje
niczego - wyłącznie odczyty.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "toshiba_modbus"))
import registers as reg  # noqa: E402


class Bus:
    """Jeden klient na całe badanie.

    Bramki przyjmują kilka połączeń TCP, ale potrafią oddać odpowiedź nie temu
    gniazdu, które pytało. Drugi klient „tylko do odczytu" nie istnieje.
    """

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.tid = 0

    def close(self) -> None:
        self.sock.close()

    def ask(self, slave: int, pdu: bytes) -> bytes | None:
        self.tid = (self.tid + 1) & 0xFFFF
        body = bytes([slave]) + pdu
        head = self.tid.to_bytes(2, "big") + b"\x00\x00" + len(body).to_bytes(2, "big")
        self.sock.sendall(head + body)
        out = b""
        try:
            while True:
                chunk = self.sock.recv(1024)
                if not chunk:
                    return None
                out += chunk
                if len(out) >= 6 and len(out) >= 6 + int.from_bytes(out[4:6], "big"):
                    break
        except socket.timeout:
            return None
        if int.from_bytes(out[0:2], "big") != self.tid:
            return None
        return out[6:]

    def read(self, slave: int, fn: int, addr: int, count: int) -> list[int] | str:
        pdu = bytes([fn]) + addr.to_bytes(2, "big") + count.to_bytes(2, "big")
        reply = self.ask(slave, pdu)
        if reply is None:
            return "cisza"
        if reply[1] & 0x80:
            return f"wyjątek {reply[2]:#04x}"
        data = reply[3:]
        return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def loopback(bus: Bus, slaves: range) -> list[int]:
    print("1. Pętla zwrotna 0x08 - czy stoi łączność do interfejsu")
    alive = []
    for slave in slaves:
        reply = bus.ask(slave, bytes.fromhex("080000bbbb"))
        if reply is None:
            print(f"   slave {slave:3d}  cisza")
        elif reply[1] & 0x80:
            print(f"   slave {slave:3d}  wyjątek {reply[2]:#04x}  (adres zarezerwowany)")
            alive.append(slave)
        else:
            print(f"   slave {slave:3d}  echo")
            alive.append(slave)
    return alive


def counters(bus: Bus, slave: int) -> None:
    print(f"\n2. Liczniki Modbus na slave {slave} (łącze do interfejsu, NIE magistrala Uh)")
    for sub, name in ((0x0B, "komunikatów"), (0x0C, "błędów CRC"), (0x0E, "do tego urządzenia")):
        pdu = bytes([0x08]) + sub.to_bytes(2, "big") + b"\x00\x00"
        reply = bus.ask(slave, pdu)
        if reply is None:
            print(f"   {name:22} cisza")
        elif reply[1] & 0x80:
            print(f"   {name:22} wyjątek {reply[2]:#04x}")
        else:
            print(f"   {name:22} {int.from_bytes(reply[4:6], 'big')}")


def scan(bus: Bus, slave: int, first: int, last: int) -> list[tuple[int, str, str]]:
    print(f"\n3. Skan nazw modeli, adresy {first}-{last} na slave {slave}")
    found = []
    for unit in range(first, last + 1):
        words = bus.read(slave, 0x04, reg.addr("input", unit, "model"), reg.width("input", "model"))
        if isinstance(words, str):
            print(f"   {unit:3d}  {words}")
            continue
        model = reg.decode_ascii(words)
        if not model:
            continue
        serial = bus.read(slave, 0x04, reg.addr("input", unit, "serial"), reg.width("input", "serial"))
        serial = reg.decode_ascii(serial) if isinstance(serial, list) else "?"
        print(f"   {unit:3d}  {model}  SN {serial or 'brak'}")
        found.append((unit, model, serial))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--slave", type=int, default=1, help="adres slave interfejsu (SW1)")
    ap.add_argument("--first", type=int, default=reg.ADDR_MIN)
    ap.add_argument("--last", type=int, default=reg.ADDR_MAX)
    ap.add_argument("--timeout", type=float, default=4.0)
    args = ap.parse_args()

    started = time.time()
    bus = Bus(args.host, args.port, args.timeout)
    try:
        alive = loopback(bus, range(args.slave, args.slave + 3))
        if not alive:
            print("\nInterfejs nie odpowiada na żadnym adresie. Skan nie ma sensu.")
            print("Do sprawdzenia: ramkowanie (MBAP czy RTU), SW1, SW3, zasilanie interfejsu.")
            return 1
        counters(bus, args.slave)
        print("   Liczniki rosną o ramki pytającego, więc świadczą o łączu RS-485,")
        print("   a nie o tym, czy na Uh jest jakikolwiek adapter.")
        found = scan(bus, args.slave, args.first, args.last)
    finally:
        bus.close()

    print(f"\nZnalezione jednostki: {len(found)}   czas {time.time() - started:.0f} s")
    if not found:
        print("Zero jednostek przy działającym interfejsie znaczy, że magistrala Uh jest pusta:")
        print("adaptery RAC I/F niezamontowane, bez zasilania, albo z rozłączoną parą Uh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
