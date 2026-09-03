"""Mapa rejestrów tutaj i w panelu modbus-ui musi opisywać ten sam sprzęt.

Oba projekty są wdrażane osobno i celowo nie mają wspólnej zależności — panel jest
bezzależnościowy i publiczny, integracja żyje w HA. Ceną za tę niezależność jest
ryzyko rozjazdu map, więc rozjazd ma wywalić test, a nie ujawnić się jako encja
czytająca sąsiednią jednostkę.

Test pomija się sam, gdy checkout panelu nie leży obok.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
COMPONENT = HERE.parent / "custom_components" / "toshiba_modbus"

CANDIDATES = [
    pathlib.Path.home() / "CodeHub" / "z4-server" / "serwisy" / "modbus-ui" / "app",
    HERE.parent.parent.parent.parent / "z4-server" / "serwisy" / "modbus-ui" / "app",
]


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


reg = _load("registers", COMPONENT / "registers.py")

panel_app = next((p for p in CANDIDATES if (p / "devices" / "toshiba.py").is_file()), None)
pytestmark = pytest.mark.skipif(panel_app is None, reason="brak checkoutu modbus-ui")


def _panel_registers(unit: int) -> dict[str, dict[str, int]]:
    sys.path.insert(0, str(panel_app))
    try:
        toshiba = _load("panel_toshiba", panel_app / "devices" / "toshiba.py")
    finally:
        sys.path.pop(0)
    out: dict[str, dict[str, int]] = {}
    for r in toshiba.registers_for_unit(unit, "test"):
        out.setdefault(r["space"], {})[r["key"].split(".", 1)[1]] = r["addr"]
    return out


# panelowa nazwa pola -> nazwa w integracji
ALIAS = {
    "coil": {"onoff": "onoff", "filter_reset": "filter_reset", "hipower": "hi_power",
             "eco": "eco", "quiet": "quiet", "silence": "silence"},
    "discrete": {"onoff_st": "onoff", "filter_sign": "filter_sign", "alarm": "alarm",
                 "thermo": "thermo", "st_purefilter": "st_pure_filter",
                 "st_hipower": "st_hi_power", "st_eco": "st_eco",
                 "st_quiet": "st_quiet", "st_silence": "st_silence"},
    "input": {"room_temp": "room_temp", "setpoint_st": "setpoint", "check_code": "check_code",
              "model": "model", "serial": "serial", "capacity": "capacity",
              "mode_st": "mode", "fan_st": "fan", "louver_st": "louver",
              "func_status": "func_status"},
    "holding": {"setpoint": "setpoint", "hours": "hours", "mode_set": "mode",
                "fan_set": "fan", "louver_set": "louver", "rc_lock": "rc_lock", "save": "save"},
}


@pytest.mark.parametrize("unit", [1, 2, 3, 17, 64])
def test_addresses_match_panel(unit: int) -> None:
    panel = _panel_registers(unit)
    for space, mapping in ALIAS.items():
        for panel_key, own_key in mapping.items():
            assert panel_key in panel[space], f"panel nie ma {space}.{panel_key}"
            assert reg.addr(space, unit, own_key) == panel[space][panel_key], (
                f"{space}.{own_key} jednostka {unit}: "
                f"{reg.addr(space, unit, own_key)} != {panel[space][panel_key]}"
            )


def test_every_field_is_covered() -> None:
    """Panel nie może mieć pola, którego integracja nie zna."""
    panel = _panel_registers(1)
    for space, fields in panel.items():
        unknown = set(fields) - set(ALIAS[space])
        assert not unknown, f"pola bez odpowiednika w integracji: {space}.{sorted(unknown)}"
