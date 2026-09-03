# toshiba_modbus

Custom integration for HA. Register map documented in the parent project
(`../docs/08-modbus-interface.md`); manuals in `../docs/manuals/`.

## Where this lives and why

The working copy sits inside `~/CodeHub/home/klimatyzacja/integracja/`, but it is an
**independent git repo**, ignored by the parent — the same pattern as the 32 repos
under `z4-server/serwisy/`. Two constraints forced it:

- the `klimatyzacja` repo holds prices, offers, negotiations and invoice data in
  six `docs/*.md` files, so it can never be published;
- HACS resolves integrations at `custom_components/<domain>` **relative to the repo
  root**, so nesting the component inside another repo's subdirectory breaks install.

Remotes: `origin` = Forgejo, `github` = public mirror.

## Gotchas

- **The map is duplicated on purpose.** `registers.py` here and
  `app/devices/toshiba.py` in `modbus-ui` describe the same hardware. The panel is
  dependency-free and public; coupling them would drag HA into that repo. The cost
  is drift, so `tests/test_map_parity.py` fails when the addresses stop matching.
  Fix the map, never the test.
- **Address stride is per space**: 152 for coil/discrete, 156 for input/holding.
- **Presence is the model-name string.** A missing unit returns a valid frame of
  zeros, so every entity hangs its `available` on `coordinator.present()`. Without
  it the UI shows 0 °C and mode `invalid` as if they were readings.
- **One lock over the bus.** The coordinator serialises reads and writes through
  `asyncio.Lock` and keeps a single client. Gateways accept several TCP clients but
  deliver replies to the wrong one — measured on an EW11, both sockets received the
  same frame. Never open a second client "just for writes".
- **Writes echo the request.** Functions `0x05`/`0x06` return the request frame,
  which pymodbus accepts; the coordinator then forces a refresh, because the
  interface needs a poll cycle before the readback registers change.
- **`hvac_action` comes from the compressor bit** (`10004`), not from the mode
  register. Mode says what was asked for, the bit says what the unit is doing.
- **Zero indoor units is a valid config entry.** The interface answers while the Uh bus
  is empty, which is exactly the state before the adapters are fitted. Refusing the entry
  there means the interface cannot be added until an installer has been on site.
- **Read a newly discovered unit before dispatching it.** `device_info` is read when the
  entity registers, so a unit added to `self.units` without its registers already in
  `self.data` lands in the device registry with the fallback model and no serial, and
  stays that way. `_read_unit_into` fills the data first.
- **A skipped unit has to be remembered, not just skipped.** Addresses cleared in the
  select step go to `options["excluded"]`, and `_unknown_addresses()` filters them out.
  Without that the background rescan would put them back within minutes and the choice
  would mean nothing.
- **The options flow replaces options wholesale.** `async_create_entry(data=...)` in an
  OptionsFlow overwrites everything, so `excluded` has to be rebuilt into the payload on
  every save or it silently disappears.
- **`ModbusIOException` is not an `OSError`.** pymodbus raises it when nothing answers
  after its retries, and it escapes `except (ConnectionError, OSError)` entirely — the
  config flow then returns HTTP 500 with the traceback only in the container log. Catch
  `ModbusException` from `pymodbus.exceptions`.
- **Two failures, two messages.** A refused TCP connect means the address or port is
  wrong; an open socket with no valid reply means framing, slave address, or a second
  master on the line. Telling the user to check framing when nothing is listening on the
  port sends them looking in the wrong place.
- **Integer fields with a range render as sliders.** A plain `vol.Range` on an int gives
  the user a slider, which is useless for a port or a slave address copied off a board.
  `number()` wraps `NumberSelector` in `BOX` mode; keep new numeric fields going through it.
- **`unit_of_measurement=None` breaks `NumberSelectorConfig`.** The validator wants a
  string, so the key has to be absent, not None, or the whole `config_flow` module fails
  to import and the integration disappears from the add-integration list with only
  "Invalid handler specified" visible from the API.
- **`vol.Any` cannot be serialised into a config-flow form.** HA raises
  `ValueError: Unable to convert schema` and the flow returns HTTP 500 with the traceback
  only in the container log. Use a plain `vol.Range` and handle special values in code.
- **`/api/error_log` is 404 on this HAOS build.** The body of the 404 contains no
  traceback, so grepping it for errors always looks clean. Read
  `docker logs homeassistant` instead.
- **Changing `entity_registry_enabled_default` does not re-enable existing entities.**
  The registry keeps `disabled_by: integration` from the install that created them, so
  the new default only reaches fresh installs. Re-enable the old ones over the
  websocket with `config/entity_registry/update` and `disabled_by: None`.
- **Counter reads must not fail the update.** `_read_counters` swallows its own errors
  and stores `None`. They are diagnostics; letting them raise would take every climate
  entity down with them when an interface answers registers but not `0x08`.
- **The connectivity entity overrides `available` to `True`.** An entity that reports
  connectivity has to survive the loss of it, otherwise it goes unavailable exactly
  when it has something to say.
- **Brand icons are local.** `brand/icon.png` + `@2x` (and dark variants) inside the
  component; no PR to `home-assistant/brands`, no manifest key. Changing them needs
  a full HA restart, not a config-entry reload.
- **Test on the spare HA instance first, never on the production one.** The deploy path
  there is the SSH add-on with a password, which needs `-o PubkeyAuthentication=no`
  because `Host *` in the local ssh config disables password auth. Addresses are in the
  session memory, not here — this file is public.

## Emulator

`tools/emulator.py` imports `registers.py` by path, so the emulator and the
integration cannot disagree about addresses. It reproduces the three slave
addresses, exception `0x02` on `N+2`, the zero-filled frame for an absent unit,
and function `0x08`.
