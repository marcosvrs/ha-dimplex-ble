# ConfigR firmware, OTA and BLE-connection analysis (static)

Static reverse engineering of the official ConfigR Android app (Glen Dimplex),
focused on heater firmware delivery, the BLE OTA procedure, and ConfigR's BLE
connection lifecycle. Section 6 (heater parameter inventory versus this
integration) is in [`configr-entity-gap.md`](configr-entity-gap.md).

Evidence labels: **VERIFIED** = read directly from the decompiled IL/metadata,
strings or resources at the cited location; **INFERENCE** = reasoned from
verified facts, not directly proven.

## 1. Scope and authorization boundary

- Package `com.Dimplex.DimplexToolkit` (ConfigR), acquired by the user from
  Google Play with their own account.
- Static analysis only: the app was never installed or run, no emulator was
  used, and no discovered endpoint was contacted.
- No credential, token, tenant/client identifier, instrumentation key or
  signature value is reproduced here. Values are written as `REDACTED`.
- Raw extracted material (APK copies, unpacked splits, managed assemblies, IL
  dumps) stays in the session scratchpad and is not in the repository.

## 2. Acquisition record

| Item | Value |
|---|---|
| Source | Google Play delivery API via `apkeep` (`-d google-play`, `split_apk=true,include_dex_metadata=true`) |
| Timestamp | 2026-11-24T18:05:44Z |
| Tools | apkeep 1.1.0, jadx 1.5.7, apksigner (build-tools 35.0.0), aapt2 (build-tools 35.0.0), unzip, strings, rg, a purpose-written ECMA-335 IL lister (Python, stdlib only) |
| Package / version | `com.Dimplex.DimplexToolkit`, versionName `3.12.2`, versionCode `86011`, compileSdk 36 (aapt2 `dump badging`) |
| Runtime | .NET MAUI on **Mono AOT** (not NativeAOT): `libmonosgen-2.0.so`, `libmonodroid.so`, `libaot-*.dll.so`, `libassembly-store.so` |

### Artifact manifest

| File | Split role | SHA-256 |
|---|---|---|
| `com.Dimplex.DimplexToolkit.apk` | base (dex, assets, resources) | `7db25a4211cb8b2054c06278ec44a412557ac7de0ea54cc9d64b906d67004bad` |
| `com.Dimplex.DimplexToolkit.config.arm64_v8a.apk` | ABI (native libs + managed assembly store) | `a32f5875d4c089a969e4e70b68e7017731efc3429dee754054dde140db70f598` |
| `com.Dimplex.DimplexToolkit.config.en.apk` | language | `c68e5c9888895a3de2c5ac5714a671f844477b558d87f0a5dc3335938a08dd63` |
| `com.Dimplex.DimplexToolkit.config.xxhdpi.apk` | density | `2aba88a5fc4888383f5e47f7602fd093d8246347c45b11ba4e92eb24e0cbfbec` |
| `com.Dimplex.DimplexToolkit.dm` | DexMetadata (`primary.prof`, `primary.vdex`, `manifest.json`) | `9d7c7ad35e32fba49dc5415be621904f7f8d54c61b1adb068fb4ad7b2a93eda4` |

### Signature verification (`apksigner verify --verbose --print-certs`)

| File | Result | v1 | v2 | v3 | SourceStamp |
|---|---|---|---|---|---|
| base | Verifies | false | false | true | verified |
| config.arm64_v8a | Verifies | true | true | true | verified |
| config.en | Verifies | true | true | true | verified |
| config.xxhdpi | Verifies | true | true | true | verified |

All four splits have one signer with the same certificate:

- SHA-256 digest `7c57b7875679f09c0c9a5ed140e05cba2f075e3a68ad15e4a19bc411a40ea20c`
- DN `CN=Android, OU=Android, O=Google Inc., L=Mountain View, ST=California, C=US`

A valid signature proves integrity under that certificate. Provenance comes
from the direct Play acquisition. The generic DN was not compared with
publisher-controlled evidence.

## 3. Analysis method

1. Hashed and unpacked every split into separate directories. `.dm` was listed
   only.
2. Ran jadx over the base APK (`--show-bad-code --deobf`). It finished with 150
   decompiler errors in library code. The Java/Kotlin layer is MAUI/AndroidX/ML
   Kit glue. It has no Dimplex firmware, OTA or GATT logic, because the app
   logic is managed .NET code.
3. `libassembly-store.so` (arm64 split) is an ELF whose `payload` section holds
   an `XABA` assembly store with 223 `XALZ` entries. Each entry is
   `"XALZ" + u32 index + u32 size + LZ4 block`. A stdlib-only Python LZ4 block
   decoder extracted 223 PE/.NET assemblies. `ConfigR.dll` (7.6 MB) and
   `GlenDimplex.Mobile.Common.dll` were identified by content.
4. ilspy/dotnet are not installed. A small ECMA-335 metadata reader parsed
   `#~`, `#Strings`, `#US`, `#Blob`, TypeDef/MethodDef/MemberRef/Field/Param/
   Constant/FieldRVA/NestedClass. It listed every method body with its `ldstr`
   literals, integer constants and resolved call/field targets (no branch
   structure). It also dumped all enums with their values and the bytes of
   `<PrivateImplementationDetails>` static arrays. Control flow is therefore
   reconstructed from call order. A claim that depends on branch order is
   labelled **INFERENCE**.
5. Searched strings across native libs, assets, resources and the embedded
   `appsettings.json` resource. Secrets were redacted before display.

All locations below are `ConfigR.dll` (assembly store entry `0148`) type and
method names unless they say otherwise. Async logic lives in compiler state
machines `<Method>d__N::MoveNext`.

## 4. Findings

### 4.1 Firmware source: downloaded at runtime, not bundled (Q1)

- **VERIFIED, no bundled firmware.** No `.bin/.hex/.cyacd/.cyacd2/.gbl/.img/.ota/.zip`
  or `release_config*` file exists in any split, in assets, or as an embedded
  ZIP (`PK\x03\x04`) inside `ConfigR.dll`/`GlenDimplex.Mobile.Common.dll`. The
  only binary assets are fonts, images, a Rive animation and ML Kit barcode
  models.
- **VERIFIED, per-appliance cloud pointer.** `Model.Appliance` has
  `LatestBluetoothFirmware` of type `Model.ToolkitBluetoothFirmware` with
  `Id, Version, Location, MinAppVersion`. `Appliance` objects come from the
  cloud (`/api/ToolkitV2/GetAppliancesAndGroups` and the user-context calls).
  The server therefore chooses which firmware applies to each appliance. No
  local product-code-to-firmware table exists.
- **VERIFIED, download.** `APIService.GetEsp32FirmwareFiles` /
  `GetFirmwareFiles` call `IClient.DownloadZip(url, 50000)`, where the URL is
  `LatestBluetoothFirmware.Location`. In
  `GlenDimplex.Mobile.Common.DownstreamApi.Client.<DownloadZip>` the call is a
  plain `HttpClient.GetAsync` with a timeout and an HTTP 200 check. That method
  adds no `Authorization`/`Bearer` header.
  - **INFERENCE:** `Location` is a pre-signed absolute URL, likely an Azure
    Blob SAS URL. The host and scheme are server data and are not in the APK.
- **VERIFIED, package format.** A ZIP with `release_config.json` (the error
  string reads "Release json is not in zip file"). It is deserialized with
  Newtonsoft into `Esp32OtaReleaseConfig.Ota_File_Structure` or
  `OtaReleaseConfig`. Items are ordered by `SeqId`. Each item's `FileName` must
  exist in the ZIP.
  - ESP32 item fields: `Module, SeqId, Desc, FileName, Version, ProductCodes, Verification`.
  - Derived values: `MajorVersion`/`MinorVersion` from `Version.Split('.')`.
    `IsTstVersion` is true when `FileName` contains `"TST"`, and then the
    version string gets `.01`. `SupportedEpNumbers` comes from
    `FileName.Split('_')` plus `ProductCodes`.
  - Accepted image types for the ESP32 path: entries ending `.bin` or `.gbl`.
  - Non-ESP32 items: `ChipId`, `Rows`, `FirmwareType` (enum
    `eFirmwareType {Stack=0, Application=1, ACC=2}`), `Mode`. They are parsed
    as text rows split on `:` (0x3A) and hex-decoded
    (`ByteArrayHelper.ConvertHexStringToByteArray`). The first record yields a
    4-byte `ChipId`. This is the Cypress `.cyacd` layout (format **INFERENCE**
    from structure).
- **VERIFIED, cloud endpoints** (relative to `DownstreamApi.ApiBaseUrl` =
  `https://mobileapi.gdhv-iot.com`, from the embedded `appsettings.json`):
  - `/api/ToolkitV2/UpdateFirmwareVersion` (`UpdateFirmwareRequest {ApplianceId, NewVersion}`)
  - `/api/BluetoothFirmware/AddBluetoothFirmwareHistory`
    (`AddFirmwareUpdateHistoryRequest {ApplianceId, DateStarted, DateFinished, FirmwareFileName, StartingFirmwareVersion, FinishingFirmwareVersion, Success, ExtraInfo}`)
  - `/api/Identity/GetMinimumAppVersionByMobileAppName` (app-version gate; the
    resource asks the user to update ConfigR before installing new firmware)
  - The complete endpoint list (43 `/api/...` paths) is in the scratchpad IL
    dump.
- **VERIFIED, auth.** Azure AD B2C (`Instance https://gdhvb2c.b2clogin.com`,
  scope `https://gdhvb2c.onmicrosoft.com/Mobile/read`). MSAL
  (`MsalClientHelper.AcquireAccessToken`) issues a `Bearer` token for API
  calls. Tenant/client/policy IDs and the Application Insights key are present
  in `appsettings.json` and are **REDACTED** here.
- **VERIFIED, "update available" decision.**
  `FirmwareDetails.get_HasNewFirmware` compares
  `new Version(Appliance.FirmwareVersion)` (the cloud-stored installed version)
  with `new Version(LatestBluetoothFirmware.Version)` using `Version >`. A
  Zigbee branch compares `Major`/`Minor` only. A parse error logs "Could not
  parse new firmware version number…" and returns false.
  - At update time (`DialogUpdateEsp32FirmwareViewModel.<StartUpdate>`) the app
    reads the live version over BLE (`ReadFwVersion`). If it already equals the
    target (`Version.Equals`), it skips flashing and only syncs the cloud
    version.
  - `ReadFwVersion` reads `FirmwareVersionParameter` (`3/0x2012`, `UiMajor.UiMinor`)
    for Zigbee products. Otherwise it reads `SwVersionParameter` (`3/0x2001`
    selector, `UIVersion`).
  - **INFERENCE:** for a panel heater that takes the ESP32 path, the version
    compared is the UI version from `3/0x2001`. On AM6532 that is "1.7"; the
    RF byte `3/0x2009` is not used.

### 4.2 BLE OTA procedure and chips (Q2)

There are two paths. The choice is **VERIFIED** in
`ConsumerHomepageViewModel.<NewFirmwareAsync>` (the same pattern appears in
`ControlPage`, `ProductDetails`, `SupportPage` and `AddProductBLE`): if the
connected device exposes characteristic `00060001-F8CE-11E4-ABF4-0002A5D5C51D`
(or GLNDIMPLEX alias `00060001-0009-474C-4E44-494D504C4558`), the app navigates
to `DialogUpdateEsp32Firmware`. Otherwise it navigates to
`DialogUpdateNonEsp32Firmware`.

(`474C-4E44-494D504C4558` is ASCII "GLNDIMPLEX". Every parameter has a Bluetooth-SIG-base UUID
and a GLNDIMPLEX-base alternative, `BluetoothParameter.AlternativeBluetoothParamId`.)

**Common framing, VERIFIED.** Both paths use the Cypress Bootloader Host
Protocol frame: `01 | cmd | len LE16 | payload | checksum LE16 | 17`.
`OtaHelper.CalcChecksum` computes the two's-complement 16-bit sum with end byte
`0x17`. Static arrays decoded from `<PrivateImplementationDetails>`:

- Cypress Enter Bootloader: `01 b8 00 00 c7 ff 17`
- Cypress Exit Bootloader: `01 bb 00 00 c4 ff 17`

The checksums `0xFFC7`/`0xFFC4` match command bytes `0x38`/`0x3B`. Bit 7 is
set in the transmitted command byte. **INFERENCE:** this is a Dimplex marker,
for example "forward to the target MCU".

#### ESP32 path (`DialogUpdateEsp32FirmwareViewModel`, `BLEService`), VERIFIED call order

1. Read the firmware version and skip if already current. Call
   `RequestMtu(247)`.
2. Download and parse the ZIP. `Esp32OtaReleaseConfigItem.ProcessFile(mtu)`
   splits the image into chunks of `mtu − 11` bytes (236 data bytes at MTU 247)
   and frames each chunk:
   - Normal products use command `0x37`.
   - The image CRC is CRC-16/CCITT (poly `0x1021`, init `0xFFFF`,
     `Esp32OtaReleaseConfigItem.Crc16`).
   - The Zigbee variant uses command `0x39` rows and
     `OtaHelper.CalculateFileChecksum`.
   - Which branch maps to Zigbee is **INFERENCE** from order.
3. Read `EpIdentifierParameter` (`3/0x200A`, 6 bytes, e.g. `EP4213`). Abort
   with `EpNumbersDoNotMatch` (22) if it is not in `SupportedEpNumbers`.
4. Call `UpdateConnectionInterval` (`FirmwareDetails` sets
   `PairingSettings.ConnectionInterval = 1`). **INFERENCE:** this is
   Plugin.BLE `High` priority. Delay 500 ms.
5. Find characteristic `…C51D` (service `00060000-F8CE-11E4-ABF4-0002A5D5C51D`,
   `Esp32CommandParameter`, size 250, write-only). Subscribe to `ValueUpdated`
   and call `StartUpdatesAsync`. Hook `DeviceDisconnected`/`DeviceConnectionLost`.
   Wait 2 s.
6. Call `EnableEsp32Ota`, which writes `Esp32OtaEnableParameter` `1/0x300E`
   with the `Mode` byte (default 1).
7. Write `EnterOtaBytes`: cmd `0x38`, len 13. The payload is built from
   `FileName.Substring(2,4)` plus constant/`0xFF` bytes plus
   `BitConverter.GetBytes(FullFile.Length)`, which encodes the image size.
8. Each step waits for a notification on `C51D`, with a 30 s inactivity timer
   (`TimeSpan.FromSeconds(30)`; timeout gives result `Timeout`=16).
   - The first notification is "entered OTA". Then rows are sent one at a time
     (`SendRow`). Each row waits for a notification acknowledgement.
   - Failures map to `CouldNotEnterBootloader`(11) / `RowWriteFailed`(12).
9. After the last row, write the CRC frame (cmd `0x35`, len 2). On "File
   verified", write `ExitOtaBytes` (cmd `0x3B`, len 0), wait 1 s, then call
   `DisableEsp32Ota` (`0x300E` = 0). Failures map to `CouldNotVerify`(21) /
   `CouldNotExitBootloader`(14).
10. Set `Appliance.FirmwareVersion = Latest.Version`, call
    `UpdateFirmwareVersion` and `AddBluetoothFirmwareHistory`.

`EFirmwareUpdateStep {Connect, EnableOTA, OTAEnabled, EnterBootloader, WriteLines, ExitBootloader, VerifyApplication}`
and `EFirmwareUpdateResult` (23 values) are VERIFIED enums.

#### Non-ESP32 path (`DialogUpdateNonEsp32FirmwareViewModel`), VERIFIED strings/calls

- The app writes `OtaEnableParameter` `1/0x100E` with mode
  `OTAMode {UI=1, ACC=2}`. It then drops the connection to rediscover
  ("OTA enabled. Dropping connection to rediscover").
- It rescans for the bootloader identity. `FirmwareDetails` defines
  `OtaAppliancePrefix "AL"` and `OtaReplacedPrefix "BL"`, and
  `GenerateOTAIdentifier` swaps `AL`→`BL` in the advertised name.
- Commands go to `CommandParameter`: service `00060000-F8CE-11E4-ABF4-0002A5D5C51C`,
  char `…C51C`, size 266.
  - Enter bootloader. The reported chip ID is checked against the file
    ("Chip ID's DO NOT match"; result `ChipIdsDoNotMatch`=10).
  - Rows use Program Row `0x39` / Send Data `0x37`, split into 128-byte halves
    (`BLEService.<WriteFirmwareUpdateRow>`, `FirmwareFileRow.HalfState`).
  - Resync is supported ("Restarting update at row position").
  - Exit bootloader `0x3B`.
- The ZIP may contain a `Stack` (bootloader) file
  ("Firmware zip contains stack (bootloader) file - checking version").

#### Chips

| Path | Evidence | Identification |
|---|---|---|
| ESP32 | type names `Esp32*`, `.bin` images, single command characteristic `…C51D`, CRC-16 image check | **INFERENCE:** Espressif ESP32 running Dimplex firmware that re-implements the Cypress-style frame |
| Zigbee | `IsZigbeeProduct`, `.gbl` | **INFERENCE:** Silicon Labs EFR32 (`.gbl` is the Gecko Bootloader image format) |
| Non-ESP32 | Cypress bootloader service UUID family `00060000-F8CE-11E4-ABF4-0002A5D5C51x`, `.cyacd` rows, chip-ID check, `Stack`/`Application` split | **INFERENCE:** Cypress/Infineon PSoC/PRoC BLE |

Two firmware targets exist (VERIFIED): `OTAMode.UI` and `OTAMode.ACC`, and
`SwVersionParameter` exposes `UIVersion`, `RfVersion` and `AccVersion`
(`ESwVersionComponent {CC=0, UI=1, RF=2}`). For the ESP32 path only the
`Esp32OtaEnable.Mode` byte (default 1 = UI) selects the target.

The live AM6532 inventory (in [`capability-matrix.md`](capability-matrix.md))
exposes `1/0x300E` and not `1/0x100E`. **INFERENCE:** the PLX Series C panel
heater is on the ESP32 path. Whether its GATT table contains service
`00060000-F8CE-11E4-ABF4-0002A5D5C51D` has not been checked.

### 4.3 Embedded firmware versions or release notes (Q3)

- **VERIFIED negative.** No firmware version strings for products (for example
  "RF 5", "UI 1.7"), no EP numbers, and no changelog/release-note text mention
  Bluetooth, connection, advertising or watchdog anywhere in `ConfigR.dll`,
  `GlenDimplex.Mobile.Common.dll`, resources or native libraries.
- The only firmware user texts are generic:
  - "A new product update is now available."
  - "Your product was successfully updated."
  - "Before you can install the new product firmware you need to update…"
- Versions and release descriptions (`Desc`) exist only in the downloaded
  `release_config.json`. Firmware change history cannot be recovered
  statically from the APK.

### 4.4 BLE connection handling (Q4)

| Behaviour | Evidence (VERIFIED unless marked) |
|---|---|
| Connect parameters | `BLEService` `<TryConnectAsync>b__0`: `IAdapter.ConnectToDeviceAsync(device, new ConnectParameters(false, true, 0), token)`, i.e. `autoConnect=false`, `forceBleTransport=true` (Plugin.BLE argument order **INFERENCE** from its public signature) |
| Connect timeout/retries | `<ConnectToDevice>d__45`: 10 000 ms token per attempt, retry loop "Retrying connection. Attempt N", then "All connection attempts failed". `PairingSettings` default `NumOfTimesToRetryConnection = 3`. On iOS, 1 s delay after connect (`DelayIfiOSAsync`) |
| Error classification | `HandleConnectionException` matches "Peer removed pairing information", "error: 133" and `TaskCanceledException` (timeout). Enum `EApplianceConnectionResult` includes `ConnectionDroppedMayNeverHaveBeenBonded` and `BLEPeerRemovedBondError` |
| Bonding (Android) | `<PairWithDevice>d__6`: `BluetoothExtension.CreateBond` (native `createBond`, `BOND_STATE_CHANGED` receiver). On immediate failure it uses "CONNECT -> DISCONNECT -> CONNECT -> BOND" (connect, 1 s, `DisconnectDeviceAsync` with 5 s token, 600 ms, reconnect, 2 s, bond again). A second failure logs "Two immediate fails - device may never have been bonded" |
| Pairing window | `ReadCharacteristicToTestConnection*` reads a protected characteristic with a **35 000 ms** token to trigger PIN entry. A fast failure is logged as "Immediate BLE failure (likely appliance 2 min pairing window expired)". A slow failure is logged as "User did not enter a pin code" |
| Service discovery | optional `Adapter.Refresh` (Android cache) + 2 s, then `GetServicesAsync` with a 22 s token, retried once with a 15 s token after 1.4 s, then 1.5 s settle before MTU |
| MTU | default `PairingSettings.MtuSize = 80`; firmware connections use 512, then OTA requests 247. 9 s token, retried; "Will just ignore and use 20 (default)" |
| Clock write | `<ReadCharacteristicToTestConnectionAndWriteRealTimeClock>d__33` runs **once per connection**. It reads `0/0x0006` (or `0/0x0106` with seconds) and writes the phone time only if the drift is greater than 60 s ("RTC difference <= 1 minute, no update needed"). `PairingSettings.IsWriteRtc` gates it; firmware connections set it false |
| Keep-alive/heartbeat | **None found.** There is no periodic BLE timer or polling loop in `BLEService`/view models. The only `DeviceTimer` instances are the 30 s OTA inactivity timers and QC-history UI timers. "Status refreshes every 30 seconds" is a UI resource string |
| Disconnect | explicit `DisconnectFromDevice`/`CloseActiveBleConnectionsIfAny` (5 s token each) when leaving flows, before scanning, after provisioning, and on app background (`BaseViewModel.<OnSleep>`) |
| Link loss | `Adapter_DeviceDisconnected` only removes the device from `_connectedDevices`. View models show "The product has disconnected. Do you want to try to reconnect?" or "Device disconnected. Reconnecting...", so reconnect is user-confirmed or a single re-run of the connect flow, not a background loop |
| Wait before reconnect | resource: "please wait 10 seconds for {1} to go to sleep" (`ToReconnectToTheProduct`) |
| Bond removal | `BluetoothExtension.RemoveBond` (reflection `removeBond`) exists but has **no caller** in `ConfigR.dll`. On iOS the user is asked to delete the bond (`EAdvancedVerificationStep.AskUserToDeleteBond`) |
| Bond limit | "Peer removed bond message - user is using iOS and the bond limit was reached" (`DialogUpdateNonEsp32FirmwareViewModel`). The app treats `Peer removed pairing information` as the heater dropping a bond because its bond table is full |
| Power-cycle guidance | Resources: "Turn off and back on again at isolation switch for a soft reset". Also "switch it off, wait at least 5 seconds, then switch it on". Advanced Verification steps `AskUserToPowerOff → WaitToPowerOn → AskUserToPowerOn` |
| Max connections | No limit constant. `_connectedDevices` is a dictionary, and group control connects sequentially (`DialogConnectToAppliancesBLEViewModel.ProcessMatchedDevicesSequentially`) |

### 4.5 Other protocol facts not previously documented (Q5)

These are VERIFIED from enums and parameter (de)serializers.

| Item | Facts |
|---|---|
| `HeatingModeParameter` `0/0x1001` | 6 bytes: `[Mode, TimerMode, TemperatureSetting, Duration, Temperature, …]`. Mode: `Unknown=0, Timer=1, Manual=2, Eco=3, Frost=4, Boost=5, Standby=6`. TimerMode: `OutAllDay=1, HomeAllDay=2, UserTimer=3, Holiday=4, Away=5`. Temp: `Eco=1, Low=2, High=3`. Duration: `30 min=1, 1 h=2, 2 h=3` |
| `AdvanceHeatingParameter` `0/0x100D` | `Off=0, On=1, OneHourBoost=2 … FourHourBoost=5, AutoBoost=6, AhOneHourBoost=12…15`, and `0x81…0x8D` for 1–12 h boost variants |
| Runback (ConfigR "Boost") | `0/0x1017` `[IsActive, Duration]`; duration enum (INFERENCE that it is this byte) `ERunbackParamTime {30=0, 60=1, 90=2, 120=3, 240=4, 360=5, 480=6, 180=18}` min. `0/0x1027` remaining minutes (16-bit). `0/0x1028` boost setpoint |
| `SetbackParameter` `0/0x1016` | `[IsActive bool, Temperature]` |
| `AwayModeParameter` `0/0x102A` | `[IsActive, date bytes 1..5 (ByteArrayHelper.ReadDate), Temperature @6]` |
| `SpConfigParameter` `2/0x1019` | **room-temperature offset** in signed tenths: byte ≤100 means `+b/10`, otherwise `(b−256)/10` °C (`eSetTemperatureModes.RoomTemperatureOffset`) |
| `FunctionRuleParameter` `3/0x1037` | bit flags `BoostTemperatureAdjustable`, `BoostDurationAdjustable`, `AdvanceAdjustable` (lock-settings page) |
| `HeatingModeSetpointParameter` `3/0x7021` | `[ReadWrite, Mode(Manual=2/Boost=5), int, dec/100]` |
| `PresetButtonSetpointParameter` `3/0x7022` | `[ReadWrite, Button(Preset2=3/Preset1=4), int, dec/100]` |
| `ButtonPressOperationParameter` `0/0x7020` | `[ButtonNumber, EOperationType, EOperation]`. `EOperationType`: `Comfort=1, Eco=2, Standby=3, Boost=4, PairingMode=5, HalfHeat=6, FullHeat=7, Advance=8, Buzzer=9, Preset1=10, Preset2=11` |
| `SoundParameter` `2/0x0005` | `[IsActive, Tone, Volume]` |
| `ErrorToneParameter` `3/0x7029` | `NoTone=0, Single=1, Dual=2` |
| `HeatDemandParameter` `3/0x1024` | `[Band, Temperature]` |
| `HeatSettingParameter` `0/0x4012` | `NoHeat=0, Half=1, Full=2, FanOnly=4` |
| `LastKeyPressValue` `1/0x0007` | `Auto=21, Off=22, Manual=23` |
| `PowerLoadingParameter` `3/0x000A` | `Element1Power`, `Element2Power` (16-bit) |
| `RuntimeParameter` `4/0x7008` | `[RuntimeMode, 16-bit minutes]`, modes `Daily/Critical/Incremental/Adaptive/Fan` |
| `PcbStatusParameter` `3/0x7024` | `[TestStatus, Major, Minor, …]` |
| Fault codes (`EFaultCodeIds`) | `20` thermistor, `21` core thermistor, `22` LCD thermistor, `30` excessive room temp, `32` LCD overheat, `34` fan, `40` serial comm, `50` open-window detection, `52` heat disabled, `53` time loss, `60` wireless comm, `62` CC signal, `63` charge times, `99` unknown |
| Security | `SecurityParameter` `2/0x1033` (33 bytes). `ESecurityStatus {NoSecurity, AuthorizationRequired, Authorized, IvWrite, Authorize, SetAuthorizationRequired, SetAuthorizationNotRequired}` drives the "PIN lock" setting (`LockSettingsPageViewModel`). A `PLXE NC` branch in `PairWithDevice` reads and writes the security status after connect |
| Typos in ConfigR labels | `HeatDemandParameter` has the UUID `00001024-…` (`3/0x1024`, as the capability matrix says), but its 16-bit label string is `"0124"`. `ChargeTimesParameter` is labelled `"0101C"` for UUID `0x101C`. The UUID is authoritative |

## 5. Actionable conclusion for the "stops advertising after abnormal link loss" bug

ConfigR never keeps a link alive. It never relies on supervision timeout to end
a link: it always disconnects explicitly, including when the app goes to the
background. It tells users to wait about 10 s before reconnecting so the
product can "go to sleep". It treats a stuck product as a mains power-cycle
case ("isolation switch … soft reset").

The app therefore contains no workaround: abnormal link loss is not a case
ConfigR handles. **INFERENCE:** the advertising stall is a heater-firmware
fault, plausibly in the ESP32 path's disconnect-reason handling.

Most actionable items:

- Keep the integration's always-explicit disconnect.
- Wait at least 10 s after any abnormal loss before reconnecting.
- Cap connection retries (ConfigR: 3 × 10 s).
- Never bond repeatedly, because the heater evicts bonds when its table is full.
- Check whether a newer UI firmware exists. `Appliance.LatestBluetoothFirmware`
  versus the `3/0x2001` UI version is visible only through the cloud API or the
  ConfigR "product update" banner. A ConfigR-delivered ESP32 update is the only
  in-band fix path.

## 6. Heater parameter inventory and integration gap

See [`configr-entity-gap.md`](configr-entity-gap.md).

## 7. Negative findings and limits

- No firmware images, versions, EP-to-firmware mapping or release notes are in
  the APK (searched by extension, magic `PK\x03\x04`, `release_config`, and
  regexes for versions and `EP\d{4}`). Server data can change at any time.
- The Java/Kotlin layer has no Dimplex BLE/OTA logic (jadx sources searched for
  firmware/OTA/bootloader terms).
- No periodic BLE keep-alive exists in managed code. Plugin.BLE/Android may
  still send link-layer traffic; that is outside static scope.
- The IL lister drops branch structure. The order of ESP32 versus Zigbee
  framing, Plugin.BLE enum meanings, and the meaning of bit 7 in the Cypress
  command byte are INFERENCE.
- NativeAOT is not used. AOT `.so` files duplicate the managed IL, which was
  analysed instead.
- The `release_config.json` JSON property names (Newtonsoft attributes) were
  not decoded from custom-attribute blobs.

## 8. Reproduction (placeholders)

```sh
WORK=<scratch>/configr-re; mkdir -p "$WORK"/{play,unpacked,managed}
apkeep -a com.Dimplex.DimplexToolkit -d google-play \
  -o split_apk=true,include_dex_metadata=true -i <mode-600-apkeep.ini> --accept-tos "$WORK/play"
(cd "$WORK/play" && shasum -a 256 * > "$WORK/SHA256SUMS.txt")
export JAVA_HOME="$(brew --prefix openjdk)/libexec/openjdk.jdk/Contents/Home"
for a in "$WORK"/play/*.apk; do
  ~/Library/Android/sdk/build-tools/35.0.0/apksigner verify --verbose --print-certs "$a"
  unzip -q "$a" -d "$WORK/unpacked/$(basename "$a" .apk)"
done
~/Library/Android/sdk/build-tools/35.0.0/aapt2 dump badging "$WORK/play/com.Dimplex.DimplexToolkit.apk"
jadx --show-bad-code --deobf --output-dir "$WORK/jadx" "$WORK/play/com.Dimplex.DimplexToolkit.apk"
# Mono assembly store: split at each "XALZ", read <u32 idx><u32 size>, LZ4-block-decode the rest
python3 -I <scripts>/xalz.py "$WORK/unpacked/<arm64 split>/lib/arm64-v8a/libassembly-store.so" "$WORK/managed"
# ECMA-335 lister: method bodies with ldstr/ldc/call targets; ENUMS=1 / FIELDRVA=<name> / PARAMS=<method> modes
python3 -I <scripts>/ildump.py "$WORK/managed/<ConfigR.dll entry>" > "$WORK/configr-il.txt"
```

## 9. Cleanup and retention

- Kept in the session scratchpad only, not the repo: unpacked splits, extracted
  assemblies, jadx output, IL/enum dumps, `SHA256SUMS.txt`, `apksigner-*.txt`,
  and the scripts `xalz.py`, `ildump.py`, `params.py`.
- The `.apkeep.ini` credential file was not read. Deleting it and removing the
  Play "Android Device" session remain with the user.
- No secrets, tokens or authenticated headers are in this report.
