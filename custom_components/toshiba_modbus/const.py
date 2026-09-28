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
# Limit na jedno zapytanie. Jedna transakcja przez bramkę Waveshare trwa ~800 ms
# niezależnie od długości (zmierzone 2026-09-03), więc 3 s zostawia zapas.
# Współdzielone połączenie rdzenia ma na sztywno 10 s i żadnych ponowień, a każda
# cicha ramka trzymałaby całą kolejkę - razem z zapisami - tyle, ile ten limit.
DEFAULT_TIMEOUT: Final = 3.0
# Ile trzymać wartość pokazaną zaraz po zapisie, zanim interfejs zgłosi ją w
# rejestrze statusu. Po tym czasie wygrywa to, co przyszło z magistrali.
OPTIMISTIC_HOLD: Final = 60.0
# Ile adresów centralnych przeszukać przy dodawaniu wpisu. Manual dopuszcza 1-64,
# ale skan to jedna ramka na adres, więc domyślnie tylko początek zakresu.
DEFAULT_DISCOVER_MAX: Final = 8
# Jak często szukać jednostek, które jeszcze się nie zgłosiły. Skanowane są tylko
# adresy nieznane, więc po znalezieniu kompletu ten interwał nic nie kosztuje.
DEFAULT_RESCAN_INTERVAL: Final = 300
# Stan interfejsu i liczniki rzadkich zdarzeń. Pięć ramek co tyle sekund zamiast
# w każdym cyklu - cykl trzech jednostek zajmuje już ~24 s z 30.
IFACE_SLOW_INTERVAL: Final = 300.0
# Numery seryjne jednostek wpisane w opcjach - RAC I/F odpowiada na 30015-30022
# samymi 0xFF, więc jedynym źródłem jest tabliczka albo aplikacja Toshiba.
CONF_SERIALS: Final = "serials"
# Modele jednostek wpisane w opcjach - część adapterów RAC I/F oddaje zamiast nazwy
# modelu tekst zastępczy "RACIF Model Name" (zmierzone 2026-09-28 na dwóch z trzech).
CONF_MODELS: Final = "models"

MANUFACTURER: Final = "Toshiba"
INTERFACE_MODEL: Final = "BMS-IFMB1280U-E"
ADAPTER_MODEL: Final = "TCB-SSRL011UUP-E"

# Nowa jednostka wykryta w trakcie pracy - platformy dokładają dla niej encje.
SIGNAL_NEW_UNIT: Final = "toshiba_modbus_new_unit_{}"
