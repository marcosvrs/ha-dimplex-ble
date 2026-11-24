# Design addendum (2026-11-24) — overrides target-architecture.md where they differ

Inputs: target-architecture.md (scratchpad artifacts), codex-architecture-review.md, runtime-pin-entry.md, ledger.md.

## A. PIN handling (requirement R-PIN)
- The heater displays its PIN only once pairing starts. PIN is static per heater (evidence: ledger R-PIN).
- HA stores the PIN in the config entry after the first successful commissioning (needed for automatic re-pair after bond loss). The PIN is never logged and is redacted in diagnostics.
- Firmware: stock ESPHome YAML (no external component, no C++ files). Shared package `dimplex_pin_responder` on every proxy:
  - RAM-only globals: `dimplex_pin` (int, 1000000 = disarmed), `dimplex_pin_set_ms` (uint32).
  - One dormant `ble_client` (id `dimplex_pin_client`, placeholder MAC, `auto_connect: false`), retargeted at runtime with `set_address()`.
  - `on_passkey_request`: `wait_until` PIN armed (timeout 25 s) → `if` armed and armed < 180 s ago → `ble_client.passkey_reply` with the stored PIN. No PIN in log text.
  - API actions: `dimplex_pin_arm(address: string, passkey: int)` (set address + PIN + time; passkey may be 1000000 = "target only, wait for PIN") and `dimplex_pin_clear()`.
  - `esp32_ble.max_connections: 4` (3 proxy slots + 1 responder).
- HA calls these actions through the proxy's native API client (ESPHome config entry runtime data → `APIClient.execute_service`), NOT via `hass.services.async_call`, so the PIN never enters the event bus / recorder. Verify the exact runtime_data API in HA 2026.11.3 source.

## B. Commissioning flow (config flow + reauth/reconfigure share one path)
1. User selects heater (bluetooth discovery or user step) and proxy (default: best RSSI with free slot among proxies that expose the responder actions).
2. HA: `dimplex_pin_arm(address, 1000000)`; open temporary link pinned to the selected proxy; start `pair()` as a background task that is never cancelled mid-flight (shielded; awaited to completion).
3. Flow shows the PIN form (description: "Enter the PIN shown on the heater display").
4. On submit: `dimplex_pin_arm(address, pin)`. If the first `pair()` is still pending, it completes. If it already failed (timeout 99 or other), run `pair()` once more with PIN pre-armed.
5. On success: one authenticated snapshot read; store PIN + proxy_source; `dimplex_pin_clear()`; close temp link only after pair() resolved.
6. Wrong PIN (expected reason 81 — verify on hardware) → show form error, allow retry. Always `dimplex_pin_clear()` when the flow ends.

## C. Runtime link (decisions from Codex review)
- Persistent link per heater; on new link: connect(pair=False) → per-proxy security lock → `dimplex_pin_arm(address, stored_pin)` → shielded `pair()` → release lock → GATT. (Arming before every pair means a lost bond is re-created automatically; with a valid bond pair() just re-encrypts and the responder is not asked.) Clear the responder after pair() resolves.
- Never disconnect while pair() is in flight (RC-2). Cancellation/unload waits for pair() completion.
- ATT 0x05/0x0F after successful pair() → transient (UpdateFailed), close link.
- Reauth (ConfigEntryAuthFailed) only when pairing fails with the stored PIN rejected (code confirmed on hardware), never for timeouts.
- Connect only when the pinned proxy currently sees the heater (habluetooth scanner devices filtered by source); register a bluetooth callback for the heater address to request a refresh when its advertisement reappears (event-driven reconnect).
- Invalidate the cached client when the proxy API disconnects / scanner object changes.
- Unload/stop: stop coordinator, await in-flight work, single serialized close.
- SOUND keeps its slow schedule.
- No: route ranking beyond default proxy choice, cached-route block, synthetic BLEDevice, backoff ladder, auth ladder, repair automation, no-wait private release, MITM actions, string matching except the forced pairing-code parse.

## D. Observability (production level DEBUG lines, address in every line)
CONNECT_REQUEST/RESULT, PAIR_REQUEST/RESULT(code, elapsed), GATT op result(code), DISCONNECT(reason/initiator), ROUTE_NOT_SEEN, RESPONDER_ARM/CLEAR (no PIN).
