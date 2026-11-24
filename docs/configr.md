# ConfigR-derived integration documentation

This repository implements local Dimplex Bluetooth control using the verified
ConfigR application behavior available in the extracted official artifact.
ConfigR is the protocol authority for names, sequencing, pairing lifecycle, and
recovery behavior in this project.

## Evidence boundary

The readable ConfigR output is a Native AOT application plus extracted managed
resources. This document records only behavior directly recovered from that
artifact or verified against the running integration. Undocumented packet
values, retry rules, cloud registration writes, firmware-update commands, and
panel-heater bond deletion are not invented.

## Advanced Verification lifecycle

The ConfigR workflow separates cloud verification from local Bluetooth
verification:

1. A normal verification attempt checks for a production-line cloud record.
2. If the cloud record is unavailable, Advanced Verification switches to local
   Bluetooth verification.
3. The product must be switched off before the local verification starts.
4. ConfigR refreshes the Bluetooth connection and, on iOS, instructs the user to
   forget the phone's Bluetooth bond.
5. After local verification, ConfigR requires a mains power-off step followed by
   a mains power-on step before continuing.

The extracted artifact does not prove the exact GATT packet sequence or any
cloud-registration write. The integration therefore uses the lifecycle boundary
and authenticated BLE behavior, not ConfigR cloud services or undocumented
commands.

## Pairing prerequisites recovered from ConfigR resources

The extracted localized resources also state that:

- the panel heater's Bluetooth setting must be enabled;
- ConfigR starts the central connection attempt and then accepts the PIN shown
  by the product;
- ConfigR disconnects the current product before connecting to another product;
- other nearby Dimplex products should be switched off while selecting a product;
- a later reconnect may require waiting roughly 10 seconds for the product to
  enter standby;
- a connection error is recovered by removing the phone's Bluetooth pairing for
  the product and retrying.

The resource bundle contains a 30-second ID/PIN-display instruction for some
Quantum variants. The AM6532 behavior verified during this work is different:
enabling Bluetooth alone does not display the PIN; the PIN appears only after a
central pairing attempt starts. The integration therefore starts pairing before
asking for the PIN.

These are commissioning/recovery prerequisites, not reasons to add device
power-cycling or bond-deletion commands to the integration. The artifact does
not expose a supported panel-heater bond-reset operation.

## Connection and pairing policy

- Keep one serialized commissioning operation per proxy.
- Do not run competing pairing attempts for one panel heater.
- Forward the displayed six-digit PIN only to the address-scoped proxy relay.
- During commissioning, use only the proxy that was armed for that address;
  runtime RSSI balancing must not redirect an unencrypted PIN exchange.
- Treat a successful API call as insufficient evidence; protected GATT access is
  the readiness check.
- Reuse a successfully authenticated connection for coordinator and command
  operations.
- On a failed operation, close and release the connection before retrying.
- When a fresh connection is required, open the selected route without
  first-time pairing, then request native encryption from its existing bond;
  a valid bond does not require a new PIN.
- Runtime route selection stays with the configured ESPHome proxy. The
  integration does not implement RSSI roaming or a second load-balancer; bonds
  are proxy-local and route changes require explicit reconfiguration.
- If no eligible route can re-establish the existing bond, mark the entry
  unavailable instead of looping or silently starting a new pairing flow.
- Explicit commissioning/reconfiguration remains the operation that creates or
  verifies a bond on a selected proxy.

The custom ESPHome component is limited to the passkey callback boundary.
Connection, encryption, service discovery, retries, availability, and teardown
remain in Home Assistant and the native ESPHome Bluetooth Proxy.

## Safety boundary

Do not read or write ConfigR security-material, firmware-update, or other
undocumented characteristics. Do not claim a model capability solely because a
ConfigR class or resource names a parameter. Every Home Assistant entity and
command must have a verified device-level behavior.

## Capability boundary

The current AM6532 evidence is recorded in
[`capability-matrix.md`](capability-matrix.md). ConfigR proves that supported
storage-heater profiles use concepts such as Auto/Timer, Manual, Eco, Frost,
Boost, and Away, and that its diagnostics can display values such as charge
level, element status, and total energy. The extracted artifact does not prove
the AM6532 characteristic mapping, payload formats, or reversible commands for
those concepts.

The integration exposes the AM6532 fields that were read and decoded repeatedly:
current room temperature, selected LCD/controller temperature, target
temperature, target limits, display unit, error code, model identity, rated
output, raw operating mode, Trac heating state, and the PLX report-screen output
power percentage. The Eco operating-mode command was accepted by the panel heater and
confirmed on its display as `Eco Mode / 18C`; the Frost payload selected the
ConfigR-documented 7 °C target, but its physical display confirmation remains
phase 2.

Official Dimplex [PLX Series C support documentation](https://support.dimplex.co.uk/what-does-the-letters-numbers-report-screen-mean-on-my-panel-heater?journey=consumer) defines report-screen field C as output power and explicitly includes PLX050E. On this panel heater, the live Trac response correlated
repeatably with that field: selector 1 and selector 2 returned `010164` and
`020164` at a forced 30 °C target, then `010000` after the target was restored to
20 °C and the panel heater became idle. The integration exposes that byte as a
percentage diagnostic value and calculates estimated instantaneous watts as
`rated_power_w × output_power_percent ÷ 100`; it does not claim a metered power
or cumulative energy reading. The complete response remains available as an
attribute.

Home All Day, Away Mode, Manual, Eco Mode, Frost Protect, and User Timer are exposed as
climate presets; the climate uses the single heat-only `HVACMode.HEAT` because
the panel heater needs a target temperature in User Timer. `HVACMode.AUTO` is
not used because Home Assistant intentionally hides target-temperature control
in that mode. Away activation now requires an explicit future deadline through
the separate Away return-time datetime entity; the verified `0/0x102A` payload
still uses the device's 7 °C target unless a future device target is reported.
The integration does not expose Boost, fan, swing, humidity, a semantic
schedule/calendar, cumulative energy, charge, separate element status, or
firmware updates. The integration does not expose arbitrary raw GATT read/write
controls: unknown and protected characteristics remain outside the Home
Assistant surface.

The live GATT inventory confirmed that the panel heater exposes Dimplex service groups 0
through 3. Inventory presence is not capability proof: protected characteristics
and unknown read-only values remain excluded from unverified writes and Home
Assistant entities.

## Reliability evidence

The connection lifecycle follows the ConfigR-derived boundary: arm the proxy's
short-lived passkey relay, establish an encrypted connection through the
selected proxy, verify a complete snapshot, then clear the relay. Runtime
polling reuses one authenticated client per config entry; operation failures
close that client, and the next poll establishes a fresh connection. ESPHome's
native connector owns proxy connection-slot management; the integration does not
duplicate that capacity policy.

On 19 November 2026, the current deployment completed a bounded live soak after
a Home Assistant restart. Post-restart logs from 18:46:48 through 18:57:19 show
successful snapshots every roughly 12–13 seconds, with individual polls taking
about 2.2–3.3 seconds. Source-route interruptions were observed; each
reconnected through the captured Bluetooth device without a panel-heater power cycle.
A separate 120-second entity soak observed zero `unavailable` or `unknown`
climate/power states. This is direct evidence of recovery, not a claim that
unbounded environmental Bluetooth failures are impossible.

## Current implementation

- Home Assistant integration: `custom_components/dimplex_ble/`
- Minimal proxy relay: `components/dimplex_passkey_proxy/`
- Proxy configuration template: `esphome_proxy.yaml`
- ConfigR artifact workspace: `/tmp/configr-apk/`

The integration is a standalone custom integration. It is not an official Home
Assistant Quality Scale-rated integration.
