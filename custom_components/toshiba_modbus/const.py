"""Constants for the Toshiba Modbus integration."""

# Copyright 2026 JI ENGINEERING
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Final

DOMAIN: Final = "toshiba_modbus"

CONF_FRAMING: Final = "framing"
CONF_SLAVE: Final = "slave"
CONF_UNITS: Final = "units"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_DISCOVER_MAX: Final = "discover_max"
CONF_RESCAN_INTERVAL: Final = "rescan_interval"
CONF_EXCLUDED: Final = "excluded"

FRAMING_RTUOVERTCP: Final = "rtuovertcp"
FRAMING_TCP: Final = "tcp"
FRAMINGS: Final = (FRAMING_RTUOVERTCP, FRAMING_TCP)

DEFAULT_PORT: Final = 8899
DEFAULT_SLAVE: Final = 1
DEFAULT_SCAN_INTERVAL: Final = 30
DEFAULT_TIMEOUT: Final = 5.0
# Ile adresów centralnych przeszukać przy dodawaniu wpisu. Manual dopuszcza 1-64,
# ale skan to jedna ramka na adres, więc domyślnie tylko początek zakresu.
DEFAULT_DISCOVER_MAX: Final = 8
# Jak często szukać jednostek, które jeszcze się nie zgłosiły. Skanowane są tylko
# adresy nieznane, więc po znalezieniu kompletu ten interwał nic nie kosztuje.
DEFAULT_RESCAN_INTERVAL: Final = 300

MANUFACTURER: Final = "Toshiba"
INTERFACE_MODEL: Final = "BMS-IFMB1280U-E"
ADAPTER_MODEL: Final = "TCB-SSRL011UUP-E"

# Nowa jednostka wykryta w trakcie pracy - platformy dokładają dla niej encje.
SIGNAL_NEW_UNIT: Final = "toshiba_modbus_new_unit_{}"
