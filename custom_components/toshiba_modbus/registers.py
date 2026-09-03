"""Register map for the Toshiba BMS-IFMB1280U-E Modbus interface.

Source: Service/Specifications Manual FILE No. A10-2103-7, Revision 7 (05.2024),
chapter 7 "Address assignment table" and 3-7 "List of functions for Room Air
Conditioner TU2C-LINK Interface (RAC)".

Wire addresses are documentation numbers minus the space offset:
    coil     = number - 1
    discrete = number - 10001
    input    = number - 30001
    holding  = number - 40001

The stride between indoor units differs per space: 152 for coils and discrete
inputs, 156 for input and holding registers. Getting that wrong reads a
neighbouring unit and looks like plausible data, so it is defined once here.

This module is the counterpart of ``app/devices/toshiba.py`` in the ``modbus-ui``
repository. Both describe the same hardware; ``tests/test_map_parity.py`` fails
when they drift apart.
"""

from __future__ import annotations

from typing import Final

STRIDE_BITS: Final = 152  # coil / discrete
STRIDE_WORDS: Final = 156  # input / holding

# Central control address range with "old controller", which RAC interfaces
# require (SM p. 16 and p. 33).
ADDR_MIN: Final = 1
ADDR_MAX: Final = 64

# --------------------------------------------------------------------------- słowniki

HVAC_MODE_READ: Final = {
    0: "invalid", 1: "heat", 2: "cool", 3: "dry",
    4: "fan_only", 5: "auto_heat", 6: "auto_cool", 7: "unfix",
}
HVAC_MODE_WRITE: Final = {"unfix": 0, "heat": 1, "cool": 2, "dry": 3, "fan_only": 4, "auto": 5}

FAN_MODES: Final = {1: "stop", 2: "auto", 3: "high", 4: "medium", 5: "low", 7: "high_plus", 8: "low_plus"}
LOUVER_MODES: Final = {1: "swing", 2: "f1", 3: "f2", 4: "f3", 5: "f4", 6: "f5", 7: "stop"}

# RAC obsługuje tylko swing i stop (OM RAC I/F, uwaga przy żaluzji).
LOUVER_RAC: Final = (1, 7)

SAVE_MODES: Final = {0: "no_limit", 1: "variable", 2: "half", 3: "full_save"}
# "100% Save" jest w tabeli rejestrów, ale manual oznacza je jako niedostępne dla RAC.
SAVE_UNAVAILABLE_RAC: Final = (3,)

RC_LOCK_BITS: Final = ("onoff", "mode", "setpoint", "louver", "fan", "ventilation")
FUNC_BITS: Final = ("pure_filter", "hi_power", "eco", "quiet_fcu", "silence_cdu")

# Kody błędów: TU2C-LINK -> odpowiednik TCC-LINK (SM rozdz. "Caution at Servicing").
CHECK_CODES: Final = {
    0x00: "brak błędu", 0x04: "E04", 0x07: "H04", 0x0C: "F10", 0x0D: "F03",
    0x0E: "J29", 0x0F: "F01", 0x11: "P12", 0x12: "F29", 0x14: "P26",
}

# --------------------------------------------------------------------------- offsety

# Nazwa -> offset względem bazy jednostki. Bazy: bits = 152*(n-1), words = 156*(n-1).
COIL: Final = {
    "onoff": 0, "filter_reset": 1,
    "hi_power": 57, "eco": 58, "quiet": 59, "silence": 60,
}
DISCRETE: Final = {
    "onoff": 0, "filter_sign": 1, "alarm": 2, "thermo": 3,
    "st_pure_filter": 80, "st_hi_power": 81, "st_eco": 82, "st_quiet": 83, "st_silence": 84,
}
INPUT: Final = {
    "room_temp": 0, "setpoint": 1, "check_code": 2,
    "model": 6, "serial": 14, "capacity": 22,
    "mode": 35, "fan": 36, "louver": 37, "func_status": 58,
}
HOLDING: Final = {
    "setpoint": 0, "hours": 1, "mode": 6, "fan": 7, "louver": 8,
    "rc_lock": 9, "save": 10,
}

# Rejestry wielosłowowe (ASCII).
WIDTH: Final = {("input", "model"): 8, ("input", "serial"): 8}

TEMP_MIN: Final = 17.0
TEMP_MAX: Final = 30.0
TEMP_STEP: Final = 0.5


def base(space: str, unit: int) -> int:
    """Adres bazowy jednostki w danej przestrzeni."""
    stride = STRIDE_BITS if space in ("coil", "discrete") else STRIDE_WORDS
    return stride * (unit - 1)


def addr(space: str, unit: int, key: str) -> int:
    """Adres na drucie dla jednego pola jednej jednostki."""
    table = {"coil": COIL, "discrete": DISCRETE, "input": INPUT, "holding": HOLDING}[space]
    return base(space, unit) + table[key]


def width(space: str, key: str) -> int:
    return WIDTH.get((space, key), 1)


def blocks_for_unit(space: str, unit: int, max_len: int = 60, max_gap: int = 12) -> list[tuple[int, int]]:
    """Scala pola jednej przestrzeni w ciągłe zakresy odczytu.

    Bez tego wychodzi jedna transakcja na pole. Przy 9600 bps to jest różnica
    między 21 a 96 ramkami na cykl odpytania trzech jednostek.
    """
    table = {"coil": COIL, "discrete": DISCRETE, "input": INPUT, "holding": HOLDING}[space]
    spans = sorted(
        (base(space, unit) + off, base(space, unit) + off + width(space, key) - 1)
        for key, off in table.items()
    )
    out: list[tuple[int, int]] = []
    lo, hi = spans[0]
    for a, b in spans[1:]:
        if a - hi - 1 <= max_gap and (b - lo + 1) <= max_len:
            hi = max(hi, b)
        else:
            out.append((lo, hi - lo + 1))
            lo, hi = a, b
    out.append((lo, hi - lo + 1))
    return out


def decode_ascii(words: list[int]) -> str:
    """Rejestry tekstowe. Same zera = jednostki nie ma na magistrali.

    Manual nie zwraca wyjątku 0x07 dla nieobecnej jednostki - oddaje poprawną
    ramkę wypełnioną zerami. Nazwa modelu jest jedynym pewnym testem obecności.
    """
    raw = b"".join(int(w).to_bytes(2, "big") for w in words)
    return raw.decode("ascii", "replace").replace("\x00", "").strip()


def signed(word: int) -> int:
    return word - 0x10000 if word >= 0x8000 else word
