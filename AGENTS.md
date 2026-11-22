# Repository agent guidance

## Dimplex panel heater pairing

- The panel heater's Bluetooth setting is always enabled during testing and operation.
- The panel heater has no separate Bluetooth pairing mode or pairing-mode control.
- Pairing begins when the BLE central initiates the connection; the panel heater then shows the six-digit PIN on its display.
- Do not ask the user to toggle Bluetooth or put the panel heater into a separate pairing mode.
- A missing PIN display means the central did not successfully initiate the pairing connection; investigate the proxy, HA client, route selection, and BLE security flow instead. 
