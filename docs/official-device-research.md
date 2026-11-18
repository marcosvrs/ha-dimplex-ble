# Official device and BLE research

Reviewed 2026-11-17. This note separates official product documentation from live BLE observations; it does not claim the manuals specify the GATT protocol.

## Dimplex product documentation

The official [PLXE Series C long manual, Issue 7](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20-%20Series%20C%20Long%20Manual%20-%20Issue%207.pdf) covers PLX050E. Its technical details (p. 6) list “Bluetooth for software updates,” but the manual contains no GATT services, characteristics, payloads, security sequence, firmware-transfer procedure, update image, rollback procedure, or BLE error mapping. The existence of Bluetooth software updates is not evidence that this integration can install firmware.

The manual documents user-facing settings and behavior, not that those settings are exposed through BLE:

- Timer profiles have four heating periods per day; the supported modes include User Timer, Home All Day, Out All Day, Manual, Eco, Frost Protect, Away, Boost, Setback, Advance, Open Window Detection, and Adaptive Start (pp. 6, 13–24).
- Setback only modifies Timer mode; it overrides an “Heating Off” period and is limited to 7–18 °C (p. 23).
- Open Window Detection can interrupt an active mode and switch the heater to “Heating Off” when it detects an open window. Adaptive Start applies to timer operation (p. 24).
- The Comms menu lists available wireless methods but gives no BLE service or command details (p. 23).
- Advanced settings can significantly change operation; Dimplex recommends that only trained professionals modify them. The manual identifies service temperature offset and factory reset in this section (p. 25). This is a product-level caution, not a characteristic-specific write policy.
- The display fault table (p. 26) lists: 20–24 component thermistor hardware fault; 30 and 32–34 component overheat; 40 internal serial communications error; 41 heating element fault; 50 open window detected; 52 heat output disabled; 53 time loss; 54 occupancy sensor not detected; 55 unknown error; 60 wireless communication module error; 61 hub connection error. The manual says fault codes are parameters Central Control can read, but it does not define their BLE representation. Only code 50 has been experimentally correlated with the BLE ErrorStatus value in the separate live-research notes; do not assume every display code is encoded identically.

The official [Dimplex Control compatibility page](https://www.dimplex.co.uk/products/smart-controls/dimplex-control/compatibility) describes the Hub/RF-module ecosystem and supported product families. It does not document direct third-party BLE GATT access or establish that the Control app's cloud/RF path is required for local BLE.

The available official [Q-Rad RF Series G manual, Issue 3](https://www.dimplex.co.uk/sites/g/files/emiian551/files/media_import/medias/docus/13/QRAD%20RF%20Instruction%20Manual%20Series%20G%20-%20Issue%203.pdf) says “Bluetooth for software updates” (p. 4) and lists wireless-module and hub communication faults. It is Series G for Q-Rad RF models, not the Series D Q-Rad E firmware tested in the project; do not transfer its model-specific details to Series D.

## Transport documentation

- Home Assistant's [Bluetooth integration documentation](https://www.home-assistant.io/integrations/bluetooth/) describes BLE discovery and adapter operation. It does not define Dimplex behavior.
- ESPHome's [Bluetooth Proxy documentation](https://esphome.io/components/bluetooth_proxy/) states that Home Assistant aggregates ESPHome proxies with local adapters and that active ESP32 proxies can carry GATT connections. It documents a default of three ESP32 connection slots and recommends staying at or below five for stability. This supports distributed reach; it is not a heater protocol specification.
- ESPHome's [BLE Client documentation](https://esphome.io/components/ble_client/) documents six-digit passkey handling for a directly configured ESPHome BLE client. That is distinct from the stock Bluetooth Proxy pairing request; the latter has no PIN input. This project therefore adds only a short-lived passkey relay to the proxy while leaving discovery and GATT transport on Home Assistant's Bluetooth/ESPHome Proxy path.
- Home Assistant's [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/) defines Platinum as a set of concrete core-integration rules; a custom repository cannot claim official Platinum status without the required Core review and acceptance.

## Limits and safe-use boundary

The official sources reviewed do not publish a Dimplex BLE/GATT specification, per-characteristic write-safety matrix, BLE encoding for display errors, or detailed software-update procedure. Product manuals document settings and warn against unqualified changes to advanced settings, but do not authorize arbitrary BLE writes. Use the empirical GATT map and blocked-field list in the project's protocol notes as separate evidence; keep unknown values raw, and do not probe credential, bootloader, or observed disconnecting characteristics.
