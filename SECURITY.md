# Security

## Operational risk

This integration writes Modbus coils and holding registers on a live Toshiba RAC
bus. Misconfiguration (wrong framing, wrong slave, a second master on the same
gateway) can leave indoor units unavailable or apply unexpected setpoints.

Recommended practice:

1. Commission against `tools/emulator.py` before pointing at hardware.
2. Keep a single Modbus master on the RS-485 gateway.
3. Prefer the companion [`modbus-ui`](https://github.com/jrx-code/modbus-ui) panel
   with `write_enabled: false` for read-only diagnostics during bring-up.

## Reporting a vulnerability

Please open a private security advisory on this GitHub repository, or email
`engineering@iwanus.eu`. Do not file a public issue for exploitable flaws.
