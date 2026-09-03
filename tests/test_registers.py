"""Reguły mapy, które łatwo złamać przy dopisywaniu pól."""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "registers", HERE.parent / "custom_components" / "toshiba_modbus" / "registers.py")
reg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)


def test_stride_differs_per_space():
    """152 dla bitów, 156 dla słów — pomylenie ich czyta sąsiednią jednostkę."""
    assert reg.addr("coil", 2, "onoff") - reg.addr("coil", 1, "onoff") == 152
    assert reg.addr("input", 2, "room_temp") - reg.addr("input", 1, "room_temp") == 156


def test_documented_numbers():
    """Adresy z manuala: coil = numer-1, input = numer-30001, holding = numer-40001."""
    assert reg.addr("coil", 1, "onoff") == 0            # 00001
    assert reg.addr("input", 1, "model") == 6           # 30007
    assert reg.addr("holding", 1, "save") == 10         # 40011
    assert reg.addr("discrete", 1, "thermo") == 3       # 10004


def test_blocks_are_fewer_than_fields():
    """Sens scalania: mniej ramek niż pól."""
    for space, table in (("coil", reg.COIL), ("discrete", reg.DISCRETE),
                         ("input", reg.INPUT), ("holding", reg.HOLDING)):
        blocks = reg.blocks_for_unit(space, 1)
        assert len(blocks) < len(table), f"{space}: {len(blocks)} bloków na {len(table)} pól"


def test_blocks_cover_every_field():
    for space, table in (("coil", reg.COIL), ("discrete", reg.DISCRETE),
                         ("input", reg.INPUT), ("holding", reg.HOLDING)):
        for unit in (1, 3, 64):
            covered = set()
            for start, count in reg.blocks_for_unit(space, unit):
                covered.update(range(start, start + count))
            for key in table:
                a = reg.addr(space, unit, key)
                for i in range(reg.width(space, key)):
                    assert a + i in covered, f"{space}.{key} poza blokami"


def test_absent_unit_reads_as_empty_string():
    """Nieobecna jednostka oddaje zera, nie wyjątek — to jedyny test obecności."""
    assert reg.decode_ascii([0] * 8) == ""
    assert reg.decode_ascii([0x5241, 0x532D]) == "RAS-"


def test_signed_conversion():
    assert reg.signed(0x0017) == 23
    assert reg.signed(0xFFFB) == -5


def test_no_block_exceeds_the_measured_read_limit():
    """Bramka nie odpowiada na dłuższe odczyty i nie mówi dlaczego.

    Odczyt powyżej ~16 rejestrów wraca ciszą, nie wyjątkiem, więc przekroczenie
    progu nie wygląda na błąd konfiguracji - wygląda na martwą magistralę.
    """
    for space in ("coil", "discrete", "input", "holding"):
        for unit in (1, 3, 64):
            for start, count in reg.blocks_for_unit(space, unit):
                if space in ("input", "holding"):
                    assert count <= reg.MAX_READ_LEN, f"{space} {start}+{count}"
