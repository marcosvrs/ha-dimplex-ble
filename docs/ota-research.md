# Dimplex heater OTA over BLE + cloud firmware: feasibility and spec (static RE)

Static reverse engineering of the official ConfigR Android app
(`com.Dimplex.DimplexToolkit`, versionName 3.12.2, versionCode 86011), to decide
whether a Home Assistant custom integration can implement heater firmware
updates (ESP32 OTA over BLE) with firmware fetched from Dimplex's cloud after a
user signs in with their Dimplex account, and to specify exactly how.

No hardware, no network calls to Dimplex/Microsoft, no app install. Builds on
[`configr-firmware-analysis.md`](configr-firmware-analysis.md) (acquisition
record, assembly-store extraction, BLE connection lifecycle) and
[`configr-clock-schedule-boost.md`](configr-clock-schedule-boost.md). This
document adds the exact cloud sign-in flow, the firmware/registration API, and
the byte-exact ESP32 OTA frame sequence, and **corrects** one inference in the
earlier doc (see "Correction", below).

Evidence labels: **VERIFIED** = read directly from decompiled IL/metadata,
strings or resources at the cited location; **INFERENCE** = reasoned from
verified facts. Citations are `Type::Method` in `ConfigR.dll` unless prefixed
`[common]` (`GlenDimplex.Mobile.Common.dll`). Line numbers refer to the
scratchpad IL dumps `configr-ilfull.txt` / `common-il.txt`.

No secret value (OAuth client secret, API/subscription key, signing key, SAS
token, instrumentation key) is reproduced. Client IDs, tenant/authority hosts,
B2C policy names, scopes and endpoint paths are not secrets and are recorded.

---

## 0. Correction to the earlier firmware analysis

[`configr-firmware-analysis.md`](configr-firmware-analysis.md) §4.2 inferred
(from call order, with branch structure lost) that "normal products use command
`0x37`" and the Zigbee variant uses `0x39`. The full-IL re-read with branch
targets shows the **opposite** mapping, and that the row-frame checksum is a
**single byte**, not 16-bit:

- `Esp32OtaReleaseConfigItem::ProcessFile` (1359091) branches on
  `get_IsZigbeeProduct`: `brfalse IL_016e`. The **non-Zigbee** branch (panel
  heaters) uses row command byte **`0x39`** (IL_01aa `ldc.i4 57`) and computes
  the image CRC once over the whole file with `OtaHelper::CalculateFileChecksum`
  (IL_028e). The **Zigbee** branch uses row command **`0x37`** (IL_0046
  `ldc.i4 55`) and an incremental CRC-16/CCITT via
  `Esp32OtaReleaseConfigItem::Crc16` (poly `0x1021`, init `0xFFFF`, IL_00bb).
- `OtaHelper::CalcChecksum` (1358702) returns **one byte** = two's complement of
  `(0x17 + Σ frame bytes) mod 256`.

The PLX Series C panel heaters are non-Zigbee (service/char `…C51D`, `.bin`
images), so **the integration's path is row command `0x39` + whole-file
CRC-16/AUG-CCITT** (see §B.3, corrected 2026-11-25). This is VERIFIED, not
inferred.

---

## A. Cloud sign-in and API

### A.1 Identity (Azure AD B2C)

**VERIFIED** from the embedded `appsettings.json` (resource in `ConfigR.dll`,
`configr-ascii.txt` ~line 5110) and `[common] MsalClientHelper::.ctor` (8538):

| Item | Value |
|---|---|
| IdP | Azure AD B2C, MSAL.NET (`Microsoft.Identity.Client`) |
| Instance | `https://gdhvb2c.b2clogin.com` |
| Domain | `gdhvb2c.onmicrosoft.com` |
| TenantId | `7f3f999b-16cb-4bde-aef7-39728819839b` |
| ClientId (public) | `bd7a7089-43e4-4353-a2dc-5b613515d011` |
| Authority built | `{Instance}/tfp/{Domain}/{policy}` → `https://gdhvb2c.b2clogin.com/tfp/gdhvb2c.onmicrosoft.com/B2C_1A_DimplexToolkitSignupSignin` |
| Sign-up/sign-in policy | `B2C_1A_DimplexToolkitSignupSignin` |
| Social-only policy | `B2C_1A_DimplexToolkitSignupSignin_SocialOnly` |
| Password-reset policy | `B2C_1A_DimplexToolkitPasswordReset` |
| Change-phone policy | `B2C_1A_DimplexToolkitChangePhoneNumber` |
| API scope | `https://gdhvb2c.onmicrosoft.com/Mobile/read` |
| Android redirect URI | `msalbd7a7089-43e4-4353-a2dc-5b613515d011://auth` (built in `.ctor`: `"msal" + ClientId + "://auth"`) |
| iOS keychain group | `com.microsoft.adalcache` |
| Token cache | MSAL cross-plat cache (`AzureAdb2cConfig.CacheFileName`/`CacheDir`) |

The `B2C_1A_` prefix means these are **Identity Experience Framework custom
policies**, not built-in user flows.

**Flows actually used** (`[common] MsalClientHelper`):

| Method | Flow | Evidence |
|---|---|---|
| `SignInUserInteractivelyAsync` (9331 MoveNext) | `AcquireTokenInteractive` → `.WithB2CAuthority(signupSignin)` → `.WithAccount` → `.WithParentActivityOrWindow` → `.WithSystemWebViewOptions` → `.WithExtraQueryParameters(ui_locales)` → `ExecuteAsync`. **Fallback** when no system web view / non-interactive host: `AcquireTokenWithDeviceCode` (IL shows `IsSystemWebViewAvailable`/`IsUserInteractive` gate, then `AcquireTokenWithDeviceCode` with a `DeviceCodeResult.Message` → `Console.WriteLine` callback `b__15_1`). | VERIFIED |
| `AcquireAccessToken` (8818) | `AcquireTokenSilent(scopes, account).WithB2CAuthority` — silent refresh from cache | VERIFIED |
| `ResetPasswordAsync` (9000) | `AcquireTokenInteractive().WithB2CAuthority(resetPassword)` | VERIFIED |
| `SignOutUserAsync` / `GetAccountByPolicy` / `FetchSignedInUserFromCache` | cache + `RemoveAsync` | VERIFIED |

- **No ROPC.** `AcquireTokenByUsernamePassword` / `UsernamePassword` appears
  **nowhere** in either assembly (0 hits in `common-il.txt` and
  `configr-ilfull.txt`). No ROPC policy (`B2C_1_ROPC…`) is configured. So there
  is no non-interactive username/password flow exposed by this client.
- **Device code flow IS configured** as the non-interactive fallback (see
  above). The MSAL type `AcquireTokenWithDeviceCodeParameterBuilder` and
  `DeviceCodeResult` are present. This is the most important finding for HA
  feasibility (§A.4).
- **No client secret.** It is a **public client** (`PublicClientApplicationBuilder`),
  so PKCE is used for the interactive code flow (MSAL default for public
  clients). Searches for `client_secret`/`ClientSecret`/`x-api-key`/`ApiKey`/
  `Ocp-Apim-Subscription-Key`/`SubscriptionKey` returned **0 hits**. No API
  subscription key exists.
- Telemetry: `DownstreamApi.AIInstrumentationKey` is an Application Insights
  connection string whose `InstrumentationKey=<GUID>` member exists in
  `appsettings.json`; value **REDACTED**. Not needed for OTA.
- API auth header: `[common] Client::GetAuthenticationResult` (6190) obtains the
  token; API calls send `Authorization: Bearer <access_token>` (standard MSAL
  `DownstreamApi`). The token audience is the `Mobile/read` scope above.

### A.2 API base URL and the exact calls

**VERIFIED.** Base URL `DownstreamApi.ApiBaseUrl = https://mobileapi.gdhv-iot.com`
(guest/ext base `https://app-mobileapiext-gdhv.azurewebsites.net`), `ApiVersion`
`1.0`. All endpoints are `POST`/`GET` under `{ApiBaseUrl}`. Paths read from
`ldstr` in `APIService` (configr-ilfull.txt):

| Purpose | Method | Path | IL |
|---|---|---|---|
| List user's appliances + groups | `GetAppliancesAndGroups` | `/api/ToolkitV2/GetAppliancesAndGroups` | 1296106 |
| Verify a heater before claim | `VerifyApplianceIdAndCode` | `/api/ToolkitV2/VerifyApplianceIdAndCodeV2` | 1300409 |
| Register/claim a heater | `AddAppliance` | `/api/ToolkitV2/AddAppliance` | 1294771 |
| Claim already-registered | `ClaimProduct` | `/api/ToolkitV2/ClaimProduct` | 1295465 |
| Release/unlink | `ReleaseProduct` / `UnlinkProduct` / `RemoveProduct` | `/api/ToolkitV2/{ReleaseProduct,UnlinkProduct,RemoveProduct}` | 1298388/1299451/1298579 |
| App-version gate | `GetMinimumAppVersionByMobileAppName` | `/api/Identity/GetMinimumAppVersionByMobileAppName` | 1297304 |
| Record new installed version | `UpdateFirmwareVersion` | `/api/ToolkitV2/UpdateFirmwareVersion` | 1299642 |
| Report update result/history | `AddFirmwareUpdateHistory` | `/api/BluetoothFirmware/AddBluetoothFirmwareHistory` | 1294967 |
| **Download the firmware ZIP** | `GetEsp32FirmwareFiles` / `GetFirmwareFiles` | **not an `/api/` path** — see below | 1293955/1293931 |

**Firmware download (the key call), VERIFIED:**

- `APIService::GetEsp32FirmwareFiles` (1293955) and `GetFirmwareFiles` (1293931)
  both call `[common] IClient::DownloadZip(url, 50000)` where `url` =
  `Appliance.LatestBluetoothFirmware.Location` and `50000` is the timeout (ms).
- `[common] Client::DownloadZip` (6176) is a plain
  `HttpClient.GetAsync(url)` → check `StatusCode == 200` → `ReadAsByteArrayAsync`.
  It sets only `HttpClient.Timeout`. **No `Authorization`/`Bearer` header is
  added.** → **INFERENCE:** `Location` is a pre-signed absolute URL (Azure Blob
  SAS or equivalent). Host/scheme are server data, absent from the APK.
- So "check latest firmware" and "get the ZIP" are a **two-step** process:
  1. `GetAppliancesAndGroups` returns each `Appliance` with a nested
     `LatestBluetoothFirmware` (`ToolkitBluetoothFirmware` — fields
     `Id, Version, Location, MinAppVersion`, setters at 1366568–1366601).
  2. Download `Location` with an unauthenticated GET.
- There is **no** `GetFirmwareFiles` request body that returns a Location; the
  Location is already embedded in the appliance object from
  `GetAppliancesAndGroups`.

**"Update available" decision, VERIFIED** (`FirmwareDetails::get_HasNewFirmware`,
see earlier doc): `new Version(Appliance.FirmwareVersion)` (cloud-stored
installed version) `< new Version(LatestBluetoothFirmware.Version)`.

**Report-result call, VERIFIED** — `AddBluetoothFirmwareHistory` request
(`AddFirmwareUpdateHistoryRequest`): `ApplianceId, DateStarted, DateFinished,
FirmwareFileName, StartingFirmwareVersion, FinishingFirmwareVersion, Success,
ExtraInfo`. `UpdateFirmwareVersion` request (`UpdateFirmwareRequest`):
`ApplianceId, NewVersion`.

### A.3 Does the heater have to be registered first?

**Corrected 2026-11-25 (live): no.** `POST /api/ToolkitV2/VerifyApplianceIdAndCodeV2`
with `{ApplianceId, SecurityCode, CheckIfAlreadyHasWallController:false}` for an
unclaimed heater returns `ResultCode` 1 (`Valid`) and `ApplianceDetails`
including `LatestBluetoothFirmware{Id, Version, Location, MinAppVersion}`,
`FirmwareVersion` (cloud record), `BleIdentifier` (`AM2086<Dimplex>`),
`ApplianceModel`, and per-appliance `AuthKey`/`EncKey` (secrets; never log).
`ApplianceId` and `SecurityCode` come from the heater's QR label
(`<12-hex id>,<6-digit code>,0,<model>,<series>`); the BLE address and the BLE
pairing PIN are **not** these values (verify returns `NotValid`, 0).
API calls need ConfigR's default headers (`Common Client::AddDefaultHeaders`):
`app_name: DimplexToolkit`, `api_version: 1.0`, `app_version`, `app_device_os`,
`device_version`, `device_manufacturer`; without them every call is HTTP 500.
Sign-in needs `ui_locales` `en` or `de` (hosted page templates); without it the
policy fails with `AADB2C90037`.

The original static reading follows (superseded for the download):

**VERIFIED (effectively yes).** The firmware `Location` only ever reaches the
app inside an `Appliance` object returned by `GetAppliancesAndGroups`, which
lists the **signed-in user's registered appliances**. There is no "look up
firmware by product code" endpoint that works without an appliance record. So
the heater must be claimed to the account for the cloud to return a download
URL.

Registration flow (VERIFIED method/paths; request field names VERIFIED):

1. `VerifyApplianceIdAndCodeV2` — validates the identifier + code pair.
2. `AddAppliance` with `AddApplianceRequest` (setters 1353817+): `HeaterId`,
   `HeaterSecurityCode`, `HeaterFriendlyName`, `WallControllerId`,
   `WallControllerSecurityCode`, `WallControllerFriendlyName`,
   `FirmwareVersion`, `GroupId/GroupName`, `RetryCount`, `Heater`,
   `WallController`, `ToolkitGroupParams`.
   - **INFERENCE:** `HeaterId` is the appliance/serial identifier printed on the
     unit (the AMxxxx-style code), and `HeaterSecurityCode` is the unit's
     security/registration code (not the BLE pairing PIN, which is a separate
     GATT-layer value). The split is confirmed by the Heater + WallController
     pairing in the same request.
3. Firmware matching to a downloaded ZIP item is done **client-side by EP
   number**, not by the registration id — see §B.1.

Registration is required for firmware, but it is the same registration used for
all cloud control; there is no firmware-only registration.

### A.4 Feasibility for Home Assistant (ranked)

The integration is local-BLE today. Cloud firmware would add a cloud dependency.
Ranked by realism given no embeddable secret and custom B2C policies:

1. **Device code flow (RECOMMENDED).** The app's own public client
   (`ClientId bd7a7089-…`) already supports `AcquireTokenWithDeviceCode` against
   `B2C_1A_DimplexToolkitSignupSignin`. A HA config-flow step can run the OAuth
   2.0 **device authorization grant** directly against B2C using the **app's own
   public client id** (public, not a secret) and scope `…/Mobile/read`: show the
   user a `verification_uri` + `user_code`, they authenticate in a browser, HA
   polls the token endpoint. No redirect URI registration, no secret, no
   embedded webview. Risk: B2C device-code support for IEF custom policies must
   be confirmed live; reusing the vendor's client id is a grey-area ToS matter;
   refresh-token lifetime/rotation unknown.
2. **Interactive PKCE with manual redirect paste.** Use the app's redirect
   scheme `msalbd7a7089-…://auth` (or a localhost loopback if B2C allows it for
   this client) and have the user paste the final redirect URL. Risk: the custom
   scheme cannot be caught by HA; loopback may not be a registered reply URL for
   this client, so this may simply fail.
3. **HA `application_credentials` / standard OAuth2 helper: not usable** as-is —
   the redirect URI cannot be registered on Dimplex's B2C app, and there is no
   client secret to configure. Rule out.
4. **ROPC: not available** — no ROPC policy is configured and the client never
   calls `AcquireTokenByUsernamePassword`.

**Recommendation:** implement cloud sign-in as **device code grant against B2C
using the app's public client id and `Mobile/read` scope**, behind a clearly
optional "firmware update" feature, and treat the vendor-client-id reuse and ToS
as an explicit risk to surface to users/maintainers. All of OTA's BLE mechanics
(below) are implementable locally regardless of the sign-in choice; only the ZIP
fetch needs the token (and the ZIP URL itself is then unauthenticated).

---

## B. ESP32 OTA over BLE (exact bytes)

All GATT writes target the single command characteristic
**`00060001-F8CE-11E4-ABF4-0002A5D5C51D`** (service
`00060000-F8CE-11E4-ABF4-0002A5D5C51D`), class `Esp32CommandParameter` (ctor
UUID VERIFIED). Notifications/indications arrive on the same characteristic.
The OTA-enable parameter is `Esp32OtaEnableParameter` UUID
`0000300E-0000-1000-8000-00805F9B34FB` (16-bit `300E`), default `Mode = 1`
(ctor VERIFIED).

### B.1 ZIP layout and `release_config.json`

**VERIFIED** (`APIService::GetEsp32FirmwareFiles` 1293955,
`Esp32OtaReleaseConfigItem` getters):

- The downloaded bytes are a ZIP. It must contain `release_config.json`
  (error "Release json is not in zip file"), deserialized (Newtonsoft) into
  `Esp32OtaReleaseConfig` with an ordered list of `Esp32OtaReleaseConfigItem`.
- Items are processed in order; each item's `FileName` must exist in the ZIP
  (error "Could not find file in zip with name "). Accepted image extensions on
  the ESP32 path: **`.bin`** and **`.gbl`** (IL_0294/_02ab).
- `Esp32OtaReleaseConfigItem` fields (getters at 1358756+):
  `Module, SeqId, Desc, FileName, Version, ProductCodes[], Verification`
  (+ derived `MajorVersion`/`MinorVersion` from `Version.Split('.')`,
  `IsTstVersion` = `FileName.Contains("TST")`, `CrcNumer`, `FileRows`,
  `EnterOtaBytes`, `ExitOtaBytes`, `CrcBytes`, `FullFile`, `IsZigbeeProduct`).
- **EP matching** (`get_SupportedEpNumbers` 1358874, VERIFIED):
  `SupportedEpNumbers = [ FileName.Split('_')[0] ] ∪ ProductCodes`. The heater's
  own EP id is read from `EpIdentifierParameter` `3/0x200A` (6 bytes, e.g.
  `EP4213`); `StartUpdate` aborts with result `EpNumbersDoNotMatch`(22) if it is
  not in that set. So the firmware file name encodes the EP, e.g.
  `EP4213_....bin`, and `ProductCodes` is an optional extra allow-list.
- Multiple files: ordered by `SeqId`; each is enabled/entered/row-written/
  CRC'd/exited independently (the UI loops the per-file state machine).

### B.2 Frame envelope and every frame, in order

**Envelope (ESP32 path), VERIFIED** from `GetEnterOtaBytes` (1359678),
`GetExitOtaBytes` (1359501), `GetCrcBytes` (1359547), `ProcessFile` (1359091),
`CalcChecksum` (1358702):

```
01 | cmd | len(LE16) | payload[len] | cksum(1 byte) | 17
```

- `01` start byte, `17` end byte (fixed).
- `len` = 2-byte little-endian length of `payload` only.
- `cksum` = single byte = `(-(0x17 + Σ bytes[01..last payload byte])) & 0xFF`,
  i.e. chosen so `(0x17 + Σ frame-without-cksum-and-end + cksum) ≡ 0 (mod 256)`.
  CalcChecksum's accumulator is pre-seeded with `0x17` (the end byte) and does
  **not** include the trailing `0x17` or the cksum itself.
- The command byte is **not** bit-7-marked on the ESP32 path (unlike the Cypress
  non-ESP32 path's hardcoded `b8`/`bb`). ESP32 command bytes are literal
  `0x38/0x39/0x35/0x3B`.

| Step | cmd | len | Payload (exact) | Source |
|---|---|---|---|---|
| **EnterOTA** | `0x38` | `0x000D` (13) | `[F0 F1 F2 F3]` (4 ASCII chars = `FileName.Substring(2,4)`, the 4 digits after `EP`) `00 00` `FF` `00` `00` `[sz0 sz1 sz2 sz3]` (`BitConverter.GetBytes((int)FullFile.Length)`, LE) | `GetEnterOtaBytes` |
| **Data row** (non-Zigbee) | `0x39` | chunk length (LE16) | the raw image chunk, `mtu − 11` bytes (236 at MTU 247); last row = remainder | `ProcessFile` IL_016f block |
| **Data row** (Zigbee) | `0x37` | chunk length | same chunking; running CRC-16 updated per row | `ProcessFile` IL_000f block |
| **CRC / verify** | `0x35` | `0x0002` (2) | `[crcLo crcHi]` = `CrcNumer` as 2-byte LE | `GetCrcBytes` |
| **ExitOTA** | `0x3B` | `0x0000` (0) | (none) | `GetExitOtaBytes` |

Worked example of the EnterOTA frame for `FileName="EP4213_v1.bin"`,
`FullFile.Length = 0x0001A2B0`:
`01 38 0D 00 34 32 31 33 00 00 FF 00 00 B0 A2 01 00 CKSUM 17`
(payload = `34 32 31 33 00 00 FF 00 00 B0 A2 01 00`, 13 bytes; `34 32 31 33` =
ASCII "4213").

- **OTA-enable frame (separate characteristic write, not a 01/17 frame):**
  `EnableEsp32Ota` writes `Esp32OtaEnableParameter` (`300E`); its payload is a
  **single byte = `Mode`** (`GetBytesFromObject` 1372068), default `0x01` (UI
  image). `DisableEsp32Ota` writes the same parameter with `Mode = 0`.

**Checksum / CRC algorithms (VERIFIED):**

- Frame checksum `OtaHelper::CalcChecksum` — 8-bit two's complement described
  above.
- Whole-file image CRC (non-Zigbee) `OtaHelper::CalculateFileChecksum`
  (1358531) — **augmented** bitwise CRC: register `0xFFFF`, poly `0x1021`,
  message bits shifted in MSB-first, then 16 zero bits (IL_009e–IL_00fb loop
  `k ≤ 1` over 8 bits). That is the standard **CRC-16/AUG-CCITT** (direct form
  init `0x1D0F`, check value `0xE5CC`). Stored in `CrcNumer`, sent in the `0x35`
  frame. (Corrected 2026-11-25: an earlier note said CCITT-FALSE.)
- `release_config.json` `verification` is **never read** by ConfigR (no
  caller of `Esp32OtaReleaseConfigItem::get_Verification`). For
  `EP4213_IUI_5_0_PRD_240514` it is `0xB8AB`; AUG-CCITT over the image is
  `0x37E5`, CCITT-FALSE `0xED2E`; none of the common CRC-16 variants give
  `0xB8AB`. The heater gets `0x37E5` from ConfigR.
- `Appliance.IsZigbeeProduct` is true only for ApplianceType
  `"Zigbee UHF Wall Tstat"` / `"Zigbee PH Control Unit"`; PLXE is not Zigbee.
- Incremental CRC (Zigbee) `Esp32OtaReleaseConfigItem::Crc16` (1359413) — same
  poly `0x1021`/init `0xFFFF`, accumulated per 236-byte row.

### B.3 Ordered BLE procedure (VERIFIED call order)

From `DialogUpdateEsp32FirmwareViewModel::StartUpdate` (1048302 MoveNext),
`SendRow` (1048347), `CommandCharacteritic_ValueUpdated` (1048323),
`ReadFwVersion` (1048006):

1. `ReadFwVersion`: read `SwVersionParameter` `3/0x2001` (UI version, format
   `UiMajor.UiMinor`, e.g. "1.7"; "0.0" on failure). If it already equals the
   target `Version`, skip flashing, only sync the cloud version.
2. `RequestMtu(247)` (IL_0278/_0317).
3. Parse the ZIP; for each item `ProcessFile(mtu)` builds `FileRows`,
   `EnterOtaBytes`, `CrcBytes`, `ExitOtaBytes`. Chunk size = `mtu − 11` = 236 B.
4. Read `EpIdentifierParameter` `3/0x200A`; abort `EpNumbersDoNotMatch`(22) if
   not in `SupportedEpNumbers`.
5. `UpdateConnectionInterval` → `PairingSettings.ConnectionInterval = 1`
   (**INFERENCE:** Plugin.BLE `High`/low-latency priority).
6. Locate char `…C51D`; `StartUpdatesAsync` (subscribe to notifications); hook
   `DeviceDisconnected`/`DeviceConnectionLost`. **Delay 2000 ms** (IL_0973).
7. `EnableEsp32Ota` → write `300E = Mode` (default 1).
8. Write **EnterOTA** (`0x38`) via `WriteEsp32OtaCommand`.
9. Notification-driven loop (`CommandCharacteritic_ValueUpdated`): each
   notification **stops** the 30 s inactivity timer, then:
   - "entered OTA" ok → `SendRow` (starts the 30 s timer again, writes next row).
   - after each row ack → `SendRow` writes the next row; when
     `rowIndex == FileRows.Count` the final `SendRow` instead writes the
     **CRC** frame (`0x35`, `CrcBytes`) — log "Last row was written. Trying to
     exit bootloader. … CRC:".
   - "File verified" notification → write **ExitOTA** (`0x3B`), log
     "File verified - writing Exit Bootloader bytes".
   - after exit → **delay 1000 ms** (IL_06b2) → `DisableEsp32Ota` (`300E = 0`) →
     log "Update completed - exited bootloader".
10. On success: `Appliance.FirmwareVersion = Latest.Version`,
    `UpdateFirmwareVersion` + `AddBluetoothFirmwareHistory`.

### B.4 Result/status codes (VERIFIED)

`EFirmwareUpdateResult` (enum, 23 values) and the `ldc.i4` constants passed to
`StopUpdateWithResult` in `CommandCharacteritic_ValueUpdated`:

| Code | Name | Raised when |
|---|---|---|
| 10 | `ChipIdsDoNotMatch` | (non-ESP32 path) |
| 11 | `CouldNotEnterBootloader` | notification "Could not enter ota mode: Result code: X" |
| 12 | `RowWriteFailed` | "Could not write row: Result code: X" |
| 14 | `CouldNotExitBootloader` | exit failed |
| 16 | `Timeout` | 30 s inactivity timer fired (`ExecuteInactivityTimeout`) |
| 21 | `CouldNotVerify` | "Could not verify application … Result code: X" (CRC mismatch) |
| 22 | `EpNumbersDoNotMatch` | EP id not in `SupportedEpNumbers` |

`EFirmwareUpdateStep {Connect, EnableOTA, OTAEnabled, EnterBootloader,
WriteLines, ExitBootloader, VerifyApplication}`.

**Notification semantics (INFERENCE from the log strings + branch order):** the
heater replies on `…C51D` after EnterOTA (ack → begin rows), after each row
(ack → next row), after the CRC frame (verify ok → "File verified"), and after
ExitOTA. Each reply carries a result/status byte; a nonzero/error status maps to
the codes above. The exact byte layout of these notifications is **not**
recoverable statically (needs a live capture).

### B.5 Timing, MTU, write mode, reconnect

| Item | Value | Evidence |
|---|---|---|
| MTU request | `RequestMtu(247)` | VERIFIED 1048302 |
| Chunk size | `mtu − 11` = 236 data bytes | VERIFIED `ProcessFile` |
| Delay after subscribe, before enable | 2000 ms | VERIFIED IL_0973 |
| Delay after ExitOTA, before disable | 1000 ms | VERIFIED IL_06b2 |
| Inactivity watchdog | `DeviceTimer(ExecuteInactivityTimeout, TimeSpan.FromSeconds(30))`, started per row, stopped on each notification; fire → result 16 | VERIFIED `.ctor` IL_0046 |
| Connection interval | `ConnectionInterval = 1` (low-latency) | VERIFIED |
| Write mode | `ICharacteristic.WriteAsync` with **no** `WriteType` override anywhere → Plugin.BLE default. Since `…C51D` advertises write-with-response, the default resolves to **write-with-response**. | INFERENCE (no `set_WriteType` in `configr-ilfull.txt`; char props from live scan) |
| Reconnect to verify | ESP32 path does **not** drop/rediscover (unlike the non-ESP32 "OTA enabled. Dropping connection to rediscover" path). It stays on the same link; `Adapter_FirmwareDeviceDisconnected` during OTA = failure (StopUpdateWithResult), not an expected reboot. | VERIFIED |
| Post-update version re-read | Not re-read over BLE after exit on the ESP32 path; the app sets `FirmwareVersion` from the target and syncs cloud. A fresh connection's `ReadFwVersion` would show the new `3/0x2001`. | VERIFIED |

### B.6 Failure / recovery / bricking

- **Mid-transfer failure:** the update simply stops (`StopUpdateWithResult` with
  the code above) and the inactivity timer or a disconnect ends it. **No
  resume/retry of a partial image** on the ESP32 path — there is no row-position
  checkpoint (the non-ESP32 Cypress path has "Restarting update at row position"
  / `FirmwareFileRow.HalfState`; the ESP32 path has nothing equivalent).
- **A/B / bootloader safety:** no managed-side evidence of A/B partitions or a
  "keep old image" step beyond the device-side `VerifyApplication` stage name.
  Whether the ESP32 bootloader rolls back on a bad image is a device-firmware
  property, **not observable statically**. INFERENCE: standard ESP32 OTA uses
  two app partitions + a verified boot, so a failed/incomplete transfer should
  leave the previous image bootable — but this must be proven on hardware before
  any unattended flashing.
- No "bricking" guard, confirmation dialog aside, exists in the app; it relies on
  the device bootloader.

### B.7 Version check

`get_HasNewFirmware`: `Version(Appliance.FirmwareVersion) <
Version(LatestBluetoothFirmware.Version)`. At flash time `ReadFwVersion` reads
`3/0x2001` (UI version `Major.Minor`) live and compares with `Version.Equals`;
equal → skip flash, sync cloud only. (Zigbee products read
`FirmwareVersionParameter` `3/0x2012` instead; not relevant to panel heaters.)

---

## Open questions for a live test

1. **Notification byte layout on `…C51D`** after EnterOTA / each row / CRC /
   ExitOTA: which byte is the status, success value, and the meaning of nonzero
   codes (the IL only shows the host-side result-code mapping, not the wire
   format).
2. **Write mode**: confirm write-with-response vs write-without-response for the
   row frames at MTU 247 (affects throughput and flow control).
3. **Does the ESP32 reboot/disconnect after ExitOTA**, and how long until it
   re-advertises? The app disables OTA and sets the version without reconnect;
   a real client may need to wait and re-read `3/0x2001`.
4. **Bootloader rollback behaviour** on an interrupted/failed transfer (is the
   old image preserved? any brick risk?). Must be verified before unattended use.
5. **B2C device-code grant** actually works against the `B2C_1A_…SignupSignin`
   IEF custom policy with the app's public client id and `Mobile/read` scope
   (token + refresh lifetime).
6. **`Location` URL** shape (SAS vs other), its TTL, and whether a fresh
   `GetAppliancesAndGroups` is needed per download.
7. **`HeaterId` / `HeaterSecurityCode`** exact source on the physical unit
   (label vs BLE) for registration, if registration is to be automated.
8. **MinAppVersion / GetMinimumAppVersionByMobileAppName** gate — whether the
   server refuses to serve firmware to a non-ConfigR client (the header/app-name
   it checks).

## Recommended implementation plan

### Cloud part
- Make firmware update an **opt-in** feature separate from local BLE control.
- Sign-in: **OAuth 2.0 device authorization grant** against
  `https://gdhvb2c.b2clogin.com/tfp/gdhvb2c.onmicrosoft.com/B2C_1A_DimplexToolkitSignupSignin`,
  client id `bd7a7089-43e4-4353-a2dc-5b613515d011` (public), scope
  `https://gdhvb2c.onmicrosoft.com/Mobile/read`, offline_access for refresh.
  Store refresh token in HA. Fallbacks §A.4 only if device-code is rejected.
- `GET`/`POST {ApiBaseUrl}/api/ToolkitV2/GetAppliancesAndGroups` with
  `Authorization: Bearer`; pick the `Appliance` matching the user's heater;
  read `LatestBluetoothFirmware {Version, Location}`.
- Compare `Version` with the live `3/0x2001` UI version; if newer, `GET`
  `Location` (no auth header) → ZIP bytes.
- After a run, `POST /api/ToolkitV2/UpdateFirmwareVersion` and
  `/api/BluetoothFirmware/AddBluetoothFirmwareHistory` (optional, courtesy).
- Surface the vendor-client-id reuse + ToS risk explicitly; gate behind a
  maintainer decision.

### BLE part
- Parse ZIP → `release_config.json` → ordered items; for each `.bin` item read
  `FileName`, `Version`, `ProductCodes`, derive EP = `FileName.split('_')[0]`.
- Connect, `RequestMtu(247)`, set low-latency connection interval.
- Read `3/0x200A` EP id; abort if not in `{EP} ∪ ProductCodes`.
- Build frames exactly per §B.2 (envelope `01|cmd|lenLE16|payload|cksum|17`,
  `CalcChecksum` 8-bit two's complement seeded with `0x17`; row cmd **`0x39`**,
  236-byte chunks; whole-file **CRC-16/AUG-CCITT** in the `0x35` frame).
- Subscribe to `…C51D`; write `300E = 1`; wait 2 s; EnterOTA(`0x38`); drive rows
  from notifications with a 30 s per-row watchdog; after last row send CRC
  (`0x35`); on "verified" send ExitOTA(`0x3B`); wait 1 s; write `300E = 0`.
- Treat any disconnect during OTA as failure. Do **not** attempt partial resume.
- Keep it behind a big warning and a successful hardware dry-run (open question
  4) before enabling unattended.

---

## Provenance and limits

- Same acquisition as [`configr-firmware-analysis.md`](configr-firmware-analysis.md)
  §2 (Play delivery, 2026-11-24; ECMA-335 IL lister with full branch targets in
  `configr-ilfull.txt`). This pass read branch structure, so the §B command-byte
  mapping and checksum width are now VERIFIED, superseding the earlier inference.
- Notification wire formats, device-side bootloader behaviour, the `Location`
  URL, and B2C device-code support cannot be settled statically — see Open
  Questions.
- No secret values (client secret [none exists], API/subscription key [none
  exists], instrumentation key [exists, redacted], SAS token [server-side]) are
  reproduced.

## Live findings 2026-11-25: latest PLX050E firmware

- `EP4213_IUI_5_0_PRD_240514.zip` (552,647 bytes, MD5 matches the blob's
  `Content-MD5`; sha256 `06389400d4adcadea953b73a0e68c302550fbed29cf2cfaae403b7894efc9e94`).
- `release_config.json`: one file, module `UI`, seqid 1, desc `PLXE_EU_ESP32`,
  version `5.0`, verification `0xB8AB` (unused, §B.3).
- Image: ESP32 app image (magic `0xE9`, chip id 0), project `GD_PLXE_Graphic`,
  ESP-IDF `v4.4.4-dirty`; its `esp_app_desc` version string is
  `EP4213_IUI_4_0_PRD_240327` (4.0), not 5.0.
- Version check: ConfigR compares the heater's UI version (`3/0x2001`) with the
  file version; all four test heaters report UI 1.7 / RF 5, so ConfigR would
  flash. No changelog exists in the API, the APK or the ZIP; older images are
  not offered by the API.

## Live capture 2026-11-25: AM2086 OTA channel, read-only

Temporary probe build (not merged), 12:17–12:23 UTC; main redeployed after.
No characteristic was written; subscribing wrote only the CCCD.

- `…C51D` properties: read, write, write-without-response, notify, indicate;
  one descriptor (CCCD `0x2902`). Subscribing succeeds.
- `C51D` read: 506 zero bytes. No notifications in 20 s at idle: the heater
  only answers after a command, so the reply format still needs the first
  EnterOTA exchange.
- `3/0x2001` selector 1: `01 01 07` → UI 1.7. `3/0x200A`: `EP4213` (ASCII),
  matching the file's EP prefix. `1/0x300E` (OTA enable): `00`.

## Brick-risk evidence from the image (static)

- The running app performs the update (`esp_ota_begin`, `esp_ota_end`,
  `esp_ota_set_boot_partition` present). An ESP32 app cannot overwrite the
  partition it runs from, so the image goes to the other slot and the boot
  slot changes only after `esp_ota_end` validates it: an interrupted transfer
  leaves the current firmware booting. (INFERENCE from ESP-IDF's OTA model.)
- The image carries an appended SHA-256 (header byte 23 = 1) that matches; the
  bootloader rejects a corrupt image. No secure-boot/flash-encryption markers.
- Not settled: app rollback after a bad-but-valid image (`PENDING_VERIFY`
  appears once; `esp_ota_mark_app_valid_cancel_rollback` is not a string in
  the image, so this cannot be decided statically).
