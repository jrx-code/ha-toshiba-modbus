# Toshiba RAC (Modbus) for Home Assistant

Local control of Toshiba residential air conditioners through the
**BMS-IFMB1280U-E** Modbus interface and one **TCB-SSRL011UUP-E** RAC adapter per
indoor unit. No cloud, no vendor account, no polling of a remote API.

```
indoor unit --UART(CN50)--> RAC I/F --Uh line--> BMS-IFMB1280U-E --RS-485--> gateway --TCP--> HA
```

## Why not the built-in `modbus` integration

It works, and for a first light it is the faster route. It also has three limits
this map runs straight into:

| | Built-in `modbus` | This integration |
|---|---|---|
| Devices in the registry | none — `entity.py` sets no `DeviceInfo` | one device per indoor unit, model and serial read from registers `30007` / `30015` |
| Frames per poll (3 units) | 96 — one transaction per entity | 21 — reads merged into contiguous blocks |
| Power limit (`40011`), remote-controller lock (`40010`) | no `select`, no `number` platform; scripts only | `select` and six `switch` entities |
| Missing indoor unit | valid frame of zeros, shown as real data | `unavailable`, detected from the model name |
| Mode / fan / louver names, check codes | numbers, mapped in templates | mapped in the integration |

## Install

**HACS** — add this repository as a custom repository of type *Integration*,
install, restart Home Assistant.

**Manually** — copy `custom_components/toshiba_modbus` into your `config/custom_components`
and restart.

Then *Settings → Devices & services → Add integration → Toshiba RAC (Modbus)*.

## Configuration

| Field | Meaning |
|---|---|
| Gateway address / port | The RS-485 gateway in front of the interface, not the interface itself |
| Framing | `rtuovertcp` for transparent bridges (Elfin EW11), `tcp` for gateways doing MBAP ⇄ RTU conversion (Waveshare mode 5) |
| Slave address | Must equal switch **SW1** on the interface board |
| Scan central addresses up to | One frame per address; units that do not answer are skipped |

Setup reads the model-name registers of each candidate address and keeps the ones
that answer with a non-empty string. You then name the rooms — the registers carry
the model, never a location.

**An interface with no indoor units is a valid setup.** Before the RAC adapters are
fitted the Uh bus is empty and every address answers with zeros; the entry is created
anyway, with the interface as its only device. Units join later, as they appear.

## Units that appear later

Adapters get fitted one at a time, so discovery is not a one-off event:

- **Find indoor units** — a button on the interface device. Press it after fitting an
  adapter instead of waiting.
- **Background rescan** — every `rescan_interval` seconds (default 300) the integration
  scans only the addresses it does not know yet, so once the full set is found it costs
  nothing. Set the interval to `0` to leave the button as the only route.

A unit found this way is read once immediately, then its device and entities are created
— without a restart and without reloading the entry.

## Entities per indoor unit

- `climate` — power, setpoint (17–30 °C, 0.5 °C), mode, fan, swing, plus `hvac_action`
  derived from the compressor bit, and the check code as an attribute
- `select` — power limit (Save)
- `switch` — Hi-Power, ECO, Quiet FCU, Silence CDU, and six remote-controller lock bits
- `button` — reset the filter sign
- `sensor` — setpoint readback, capacity, operating hours, check code, mode/fan/louver
  readback, model, serial number
- `binary_sensor` — power, filter, alarm, compressor, and the five RAC function statuses

Special-function switches disable themselves when register `30059` says the unit
does not support them. Every entity is enabled by default; the ones that mainly
matter while commissioning carry the diagnostic category, so they sit in their own
section of the device page instead of on the dashboard.

## Entities for the interface itself

The Modbus interface is its own device, with the indoor units nested under it:

- `binary_sensor` — connectivity, which stays available precisely when the bus is not
- `sensor` — bus messages, communication errors, messages to the interface, frames in
  the last cycle, units present, slave address

The three counters come from function `0x08`, which the interface answers itself and
which never reaches the Uh bus, so they cost three frames per cycle and nothing on the
appliances. The error counter is the one worth watching: rising while the connection
looks fine means interference on RS-485 — or a second master on the same line.

## Hardware notes that cost time to find

- **The interface occupies three slave addresses.** `N` covers central addresses
  1–64, `N+1` covers 65–128, `N+2` answers loopback but returns exception `0x02`
  for register reads.
- **`Central controller ID` must be `old controller`** when RAC adapters are used
  (Service Manual A10-2103-7 rev. 7, p. 16). The factory value is ID20 and it
  silently does not work. That also caps the site at 64 indoor units.
- **A missing indoor unit does not raise an exception.** It returns a valid frame
  of zeros, so zero cannot distinguish "off" from "not there". The model-name
  string is the only reliable presence test.
- **The address stride differs per space**: 152 for coils and discrete inputs,
  156 for input and holding registers.
- **One master per bus.** Serial gateways tested here accept several TCP clients
  but do not correlate replies to them — a second poller receives frames answering
  someone else's request. Point exactly one thing at the gateway.

## Testing without hardware

`tools/emulator.py` is a dependency-free Modbus RTU-over-TCP server that answers
like the real interface, including the three slave addresses, the zero-filled
frame for a missing unit, and function `0x08`.

```bash
python3 tools/emulator.py --port 5502 --units 1,2,3 --absent 3
```

Then add the integration against `<host>:5502` with framing `rtuovertcp`. Unit 3
is absent by default, so there is something to check `unavailable` against.

```bash
python3 -m pytest tests/ -q
```

`tests/test_map_parity.py` compares the register map against the `modbus-ui`
panel's map when that checkout is present, so the two cannot drift apart unnoticed.

## License

MIT.
