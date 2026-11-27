# Firmware update

This page describes how the integration updates a heater's display firmware. It covers the Dimplex cloud sign-in and lookup, the file checks, and the over-the-air (OTA) transfer over Bluetooth Low Energy (BLE). It also records the failure handling, the safety reasoning, the emulator rehearsal and the first update on a real heater. Generic Attribute Profile (GATT) encodings outside the update are in [Bluetooth GATT protocol](protocol.md).

## Components

Five modules share the update. The cloud is used only to find and download firmware; the heater is always controlled locally.

- **[cloud.py](../../custom_components/dimplex_ble/cloud.py)**: Dimplex sign-in, firmware lookup and download
- **[firmware.py](../../custom_components/dimplex_ble/firmware.py)**: the shared Dimplex account, its HTTP session and token refresh
- **[ota.py](../../custom_components/dimplex_ble/ota.py)**: release checks, update frames, the cyclic redundancy check (CRC) and the transfer sequence
- **[link.py](../../custom_components/dimplex_ble/link.py)**: `HeaterLink.async_flash`, the fresh Bluetooth link for the transfer
- **[update.py](../../custom_components/dimplex_ble/update.py)**: the Home Assistant update entity, install and post-flash confirmation

## Dimplex sign-in

The integration signs in to the Dimplex account once and keeps a refresh token. The sign-in uses Azure Active Directory B2C (business to consumer), the identity service behind Dimplex's ConfigR app.

| Item | Value |
|---|---|
| Host | `https://gdhvb2c.b2clogin.com` |
| Tenant path | `/gdhvb2c.onmicrosoft.com/B2C_1A_DimplexToolkitSignupSignin` |
| Client | ConfigR's public client ID (`CLIENT_ID` in cloud.py); public clients have no secret |
| Redirect | `msal<client ID>://auth`, ConfigR's custom scheme |
| Scope | `openid offline_access https://gdhvb2c.onmicrosoft.com/Mobile/read` |

### Why the integration fills in the form

B2C returns the sign-in result only to ConfigR's `msal…://auth` address. No browser can hand that address to Home Assistant, so a redirect or callback flow cannot finish. The integration therefore fills in Dimplex's own sign-in form as a browser would.

1. Build a Proof Key for Code Exchange (PKCE) pair with method `S256` and a random `state`.
2. `GET {tenant}/oauth2/v2.0/authorize` with the client ID, redirect, `response_type=code`, `response_mode=query`, the scope, `state`, the PKCE challenge, `ui_locales=en` and `prompt=login`. Without `ui_locales` the policy fails with `AADB2C90037`.
3. Read `csrf`, `transId` and `api` from the page's `var SETTINGS = {…}` object.
4. `POST {tenant}/SelfAsserted?tx={transId}&p={policy}` with form fields `request_type=RESPONSE`, `email` and `password`, and headers `X-CSRF-TOKEN` and `X-Requested-With: XMLHttpRequest`. A JSON reply with `status` `200` means the credentials were accepted.
5. `GET {tenant}/api/{api}/confirmed` with `rememberMe=false`, `csrf_token`, `tx` and `p`, without following redirects.
6. Check that the `Location` header starts with the redirect address and carries the same `state`. Take the `code` from it.
7. `POST {tenant}/oauth2/v2.0/token` with `grant_type=authorization_code`, the code, the redirect and the PKCE verifier. Keep the refresh token.

The page keeps its state in cookies. The aiohttp cookie jar does not send the `x-ms-cpim-cache|…` cookie back correctly, and the form then answers HTTP 400. The integration uses `aiohttp.DummyCookieJar` and sends the cookies by hand.

### Token storage and lifetime

The integration-level **Dimplex** config entry holds the refresh token. Every heater shares it.

- **Password**: used for the sign-in, then dropped. It is never stored.
- **Lifetime**: 90 days, renewed on every use, as noted in cloud.py and firmware.py. The integration stores the renewed token whenever it changes.
- **Refresh**: `POST …/token` with `grant_type=refresh_token`. HTTP 400 means the token is refused.
- **Refused token**: the integration clears it and starts Home Assistant's re-authentication of the Dimplex entry.
- **Session**: one HTTP session for all heaters. It outlives heater reloads and closes when Home Assistant stops.

[OTA research](research/ota-research.md) section A.4 recommended the OAuth device-code grant. The code does not use it; it uses the form flow above.

## Firmware lookup

The integration asks Dimplex for the latest firmware of one heater every 12 hours. The lookup makes no change to the Dimplex account.

`POST https://mobileapi.gdhv-iot.com/api/ToolkitV2/VerifyApplianceIdAndCodeV2` with this JSON body:

| Field | Value |
|---|---|
| `ApplianceId` | The appliance ID from `3/0x2002`, byte-reversed into 12 uppercase hexadecimal digits ([Identity](protocol.md#identity)) |
| `SecurityCode` | The heater's 6-digit pairing passkey, zero padded. Whether it equals a code on the label isn't verified. |
| `CheckIfAlreadyHasWallController` | `false` |

### Required headers

The application programming interface (API) answers HTTP 500 without ConfigR's default headers.

| Header | Value |
|---|---|
| `app_name` | `DimplexToolkit` |
| `api_version` | `1.0` |
| `app_version` | `3.12.2` |
| `app_device_os` | `Android` |
| `device_version` | `14` |
| `device_manufacturer` | `Google` |
| `Accept` | `application/json` |
| `Authorization` | `Bearer` and a fresh access token |

### Lookup result

The integration reads three values from the reply. Everything else in it is ignored.

- **HTTP 401**: the access token was refused. The check fails and runs again at the next interval with a newly refreshed token.
- **`ResultCode`**: must be `1` (`Valid`). Any other value means Dimplex does not know this ID and code pair.
- **`ApplianceDetails.LatestBluetoothFirmware.Version`**: the offered version, shown as the entity's latest version.
- **`ApplianceDetails.LatestBluetoothFirmware.Location`**: the pre-signed download link.
- **`ApplianceDetails.FirmwareVersion`**: Dimplex's record of the installed version. The integration ignores it: the heater reports its own version.
- **No `Version` or `Location`**: the check fails with "No firmware offered".

The heater does not need to be registered to the account (verified live on 2026-11-25, [OTA research](research/ota-research.md)). The integration never calls the registration endpoints or ConfigR's `UpdateFirmwareVersion` and `AddBluetoothFirmwareHistory` reports. Dimplex's record of the installed version therefore stays unchanged after an update.

### Entity availability

The Firmware update entity is available when three conditions hold:

- The Dimplex entry holds a refresh token
- The heater's appliance ID has been read from `3/0x2002`
- The heater's config entry holds the passkey

The installed version is the user interface (UI) version read from `3/0x2001` on every poll. Home Assistant compares it with the offered version.

## Download

The firmware is a ZIP archive behind a pre-signed link. The download sends no sign-in.

An install repeats the lookup first, so the link is fresh.

- **Request**: `GET` on the `Location` link, 120-second timeout, HTTP 200 required.
- **Integrity**: when the reply carries `Content-MD5`, the MD5 digest of the body must match it.
- **Link handling**: the link is a credential, valid for hours (inference: likely an Azure shared access signature (SAS) URL). Network errors are reported by type only, so the link never reaches a log.

For the PLX050E the offer on 2026-11-25 was `EP4213_IUI_5_0_PRD_240514.zip`, 552,647 bytes ([OTA research](research/ota-research.md#live-findings-2026-11-25-latest-plx050e-firmware)).

## File checks

The integration refuses any file ConfigR would refuse, plus a broken ESP32 image. `ota.load_release` runs the checks in this order:

1. The ZIP opens and contains `release_config.json`.
2. `ota_file_structure` holds exactly one item. Multi-image releases are refused.
3. The item's `module` is `UI`, and its `filename` matches `EP` + 4 digits + `_` + name + `.bin`.
4. The heater's EP number (ConfigR `EpIdentifier`, the product code) from `3/0x200A` is the filename prefix or one of the item's `productcodes`.
5. The named image file exists in the ZIP.
6. The image is an ESP32 app image: at least 64 bytes, first byte `0xE9`, byte 23 equal to `1` (hash appended).
7. The SHA-256 digest of the image without its last 32 bytes equals those 32 bytes.
8. The item's `version` equals the version Dimplex offered in the lookup.

An install also fails before the download when the heater's EP number has not been read. The `verification` field of `release_config.json` is not checked; ConfigR never reads it either. For `EP4213_IUI_5_0_PRD_240514` it is `0xB8AB`, which matches no common CRC-16 variant.

## Link and MTU

The transfer runs on a fresh link that bypasses the service cache. The rows need a large maximum transmission unit (MTU).

- **Cached connect**: the proxy keeps the default MTU of 23 bytes. Over 800 connects on 2026-11-25 showed this.
- **Uncached connect**: the proxy exchanges the MTU, and the link runs at 517 bytes.
- **Requirement**: a row frame holds 236 image bytes plus 6 frame bytes, and ConfigR sizes rows as MTU minus 11. The integration refuses to start below MTU 247.
- **Lock**: the update holds the heater's link lock for the whole transfer. Polls and commands wait.
- **Connection interval**: ConfigR requests a low-latency connection interval. The integration does not.

## Update mode switch

`1/0x300E` (ConfigR `Esp32OtaEnableParameter`) switches the heater between normal operation and update mode. It is one byte.

| Value | Meaning |
|---|---|
| `01` | Update mode for the UI image |
| `00` | Normal operation |

The heater reads `00` at rest (AM6532 2026-11-19, AM2086 2026-11-25).

## Frame format

The update frames go to characteristic `00060001-f8ce-11e4-abf4-0002a5d5c51d` in service `00060000-f8ce-11e4-abf4-0002a5d5c51d`. The heater answers with a notification on the same characteristic.

```text
command: 01 | cmd | len (2 bytes, little-endian) | payload | cksum | 17
reply:   01 | status | cksum | 17
```

- **Checksum**: one byte chosen so that `0x17` plus the sum of all bytes before the checksum plus the checksum is 0 modulo 256. In code: `(-(0x17 + sum(head))) & 0xFF`.
- **Reply check**: a reply must be 4 bytes, start with `01`, end with `17` and pass the checksum. Status `00` is success.
- **Write mode**: write with response.

| Step | `cmd` | Payload |
|---|---|---|
| EnterOTA | `0x38` | 13 bytes: the 4 ASCII digits after `EP` in the filename, `00 00 FF 00 00`, then the image size as 4 bytes little-endian |
| Row | `0x39` | Up to 236 image bytes; the last row holds the remainder |
| Verify | `0x35` | The image CRC as 2 bytes little-endian |
| ExitOTA | `0x3B` | None |

ConfigR uses row command `0x37` for Zigbee products only. Panel heaters use `0x39`.

### Captured frames

These frames come from the read-only handshake on AM2086 at 2026-11-25 12:31 UTC, with no image data sent ([OTA research](research/ota-research.md#live-handshake-2026-11-25-1231-utc-am2086-no-image-data)).

```text
EnterOTA (size 906,848): 01 38 0d 00 34 32 31 33 00 00 ff 00 00 60 d6 0d 00 97 17
Reply after 0.1 s:       01 00 e8 17
ExitOTA:                 01 3b 00 00 ad 17
```

ExitOTA without rows and verify got no reply in 30 seconds. Writing `300E = 00` then returned the heater to normal operation.

### Image checksum

The verify frame carries a CRC-16/AUG-CCITT over the whole image. ConfigR computes the same value in `OtaHelper.CalculateFileChecksum`.

| Parameter | Value |
|---|---|
| Polynomial | `0x1021` |
| Initial value (direct form) | `0x1D0F` |
| Input and output reflection | None |
| Final XOR | None |
| Check value for `123456789` | `0xE5CC` |

The 5.0 image for `EP4213` gives `0x37E5`.

## BLE update sequence

The integration follows ConfigR's ESP32 sequence. Each numbered step after the subscription waits for a reply with status `00` before the next.

1. Connect without the service cache and pair. Check that the update characteristic exists and the MTU is at least 247.
2. Subscribe to notifications on the update characteristic. Wait 2 seconds, as ConfigR does.
3. Write `1/0x300E` = `01`.
4. Send EnterOTA and wait for its reply.
5. Send each row of 236 bytes and wait for its reply. Progress updates after every row.
6. Send Verify with the CRC and wait for its reply.
7. Send ExitOTA. No reply is awaited.
8. Wait 1 second.
9. Write `1/0x300E` = `00`.
10. Disconnect.

- **Reply timeout**: 30 seconds per step, ConfigR's inactivity timer.
- **Steps 7 and 9**: best effort. The heater may reboot into the new image meanwhile, so a failure of either is ignored.
- **No resume**: a failed transfer starts again from the first row. ConfigR's ESP32 path has no resume either.

## Failure handling

On any failure the integration tries to take the heater out of update mode before it reports the error. The original cause is always the reported error. Over a dropped link the cleanup writes can't arrive; the next successful poll shows whether the heater runs normally.

- **Cleanup**: on any exception between step 3 and step 6, the integration sends ExitOTA, then writes `1/0x300E` = `00`, then re-raises. A failed `300E = 01` write is cleaned up too, because the heater may apply the write and lose the acknowledgement.
- **Cleanup errors**: suppressed, so a dead link never replaces the reason the update stopped.
- **Dropped link**: the reply wait checks the connection once a second. A drop fails the step within 1 second, not after the 30-second timeout.
- **GATT errors**: a Bleak or OS error, or a GATT operation timeout, during the transfer is reported as "The heater dropped the link".
- **Reply timeout**: no reply within 30 seconds is reported as "No reply to" and the step name. A rebooting heater fails the write in flight with GATT status 133 after the supervision timeout.
- **Refusal**: a reply with a nonzero status fails the step and names the status.
- **Malformed reply**: fails the step.

## Post-flash confirmation

After a successful transfer the integration checks that the heater runs the new version. ConfigR does not re-read the version over BLE.

1. Wait 10 seconds.
2. Run a full poll, which reads `3/0x2001`.
3. Stop when the UI version equals the installed image's version.
4. Repeat up to 6 times, about one minute in total.

When the version never matches, the install fails with "firmware not confirmed" and names the version the heater reports. The device page shows the new version after the next poll.

## Safety reasoning

An interrupted transfer should leave the old firmware running. This is an inference from the image and the ESP32 OTA model, not a test on real hardware.

- **Spare slot**: the running app performs the update. Its image contains `esp_ota_begin`, `esp_ota_end` and `esp_ota_set_boot_partition`. An ESP32 app cannot overwrite the partition it runs from, so the image goes to the other slot.
- **Boot switch**: the boot partition changes only after `esp_ota_end` validates the whole image.
- **Image hash**: the image carries an appended SHA-256 that the bootloader checks. The integration checks the same hash before it sends anything.
- **Not settled**: whether the heater rolls back after a valid image that fails at runtime. No interrupted transfer was tried on a real heater.

Evidence: [OTA research](research/ota-research.md#brick-risk-evidence-from-the-image-static).

## Emulator rehearsal

The update path was rehearsed against an emulated heater before it touched a real one. The emulator is [firmware/test/heater_emulator.yaml](../../firmware/test/heater_emulator.yaml), added on 2026-11-25.

- **Hardware**: a test ESP32 on USB. It must never be flashed to a proxy.
- **GATT table**: the layout the integration polls, with AM2086's captured values.
- **Pairing**: a static passkey from the ESPHome secrets file.
- **MTU**: 517.
- **Checks**: every frame and the CRC-16/AUG-CCITT of the received image.

Each install attempt plays the next case. The attempt counter and the reported version survive reboots.

| Attempt | Behaviour at row 50 | What it tests |
|---|---|---|
| 0 | Replies with a nonzero status | Refusal and cleanup |
| 1 | Stops replying | The 30-second reply timeout |
| 2 | Reboots, dropping the link | Dropped-link detection and cleanup on a dead link |
| 3 and later | Completes, then reports UI 5.0 | The full sequence and the version confirmation |

The reboot case exposed two faults, both fixed on 2026-11-25. Cleanup errors hid the reason the update stopped, and the dropped link surfaced only after the 30-second timeout. The rebooting emulator also produced the GATT 133 failure that the integration now reports as a dropped link.

## First update on a real heater

AM2086 was the first heater updated through the integration, on 2026-11-25.

| Item | Value |
|---|---|
| Heater | AM2086, product code `EP4213` |
| Version | UI 1.7 to 5.0 |
| Image | `EP4213_IUI_5_0_PRD_240514` |
| MTU | 517 |
| Duration | About 14 minutes |

The EnterOTA frame captured for this image on AM2086 carries a size of 906,848 bytes. At 236 bytes per row that is 3,843 rows, about 0.22 seconds per row over 14 minutes. After the update the heater reports UI 5.0 on `3/0x2001`. The image's own `esp_app_desc` version string reads 4.0; the heater's reported version follows the release, not that string.

## Where the archive differs from the code

The research archive records the state at the time of writing. The code is the current reference where the two disagree.

- **Sign-in flow**: [OTA research](research/ota-research.md) section A.4 recommends the device-code grant. The code uses the form flow, because B2C returns only to ConfigR's custom scheme.
- **Firmware source**: OTA research section A.2 reads the download link from `GetAppliancesAndGroups`, which needs a registered heater. The code uses `VerifyApplianceIdAndCodeV2`, which section A.3 later found returns the link for an unregistered heater.
- **Label code**: OTA research section A.3 says the BLE pairing personal identification number (PIN) is not the label's security code. The code sends the passkey as `SecurityCode`, and Dimplex accepts it.
- **Firmware update scope**: the [capability matrix](research/capability-matrix.md) and the [ConfigR entity gap](research/configr-entity-gap.md) list `1/0x300E` as blocked and the update as out of scope. The integration now writes it during an update.
- **Version re-read**: OTA research section B.5 notes that ConfigR does not re-read the version after an update. The integration does, for about a minute.
