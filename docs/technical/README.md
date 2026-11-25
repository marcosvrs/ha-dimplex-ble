# Technical documentation

These pages explain how the integration works and how to change it. User documentation is in the [README](../../README.md) and [docs/user](../user).

## Current references

The current references describe the code on `main`:

- **[Architecture](architecture.md)**: config entries, modules, the per-poll connection, pairing, repairs and write paths
- **[Bluetooth protocol](protocol.md)**: every characteristic the integration reads or writes, with encodings and evidence
- **[Firmware update](firmware-update.md)**: the Dimplex cloud lookup, file checks and the Bluetooth update sequence
- **[Development](development.md)**: setup, quality gates, tests, the heater emulator and deployment

## Research archive

The archive in [research](research) holds the dated evidence the references cite. Its files are records: they aren't updated, and some describe designs the code no longer uses. When a record and the code disagree, the code and the current references are correct.

| Record | Contents |
| --- | --- |
| [investigation-2026-11-24](research/investigation-2026-11-24) | Pairing, bond and stall investigation: the evidence ledger, the final report and the design addendum for the PIN responder |
| [design-review-2026-11-18](research/design-review-2026-11-18) | Review of the climate entity design |
| [esphome-ble-findings.md](research/esphome-ble-findings.md) | ESPHome and ESP-IDF Bluetooth findings: connection lifecycle, proxy capacity, bonding |
| [capability-matrix.md](research/capability-matrix.md) | Field-by-field capability boundary for the PLX050E Series C, with live reads |
| [capability-matrix-source-research.md](research/capability-matrix-source-research.md) | Sources behind the capability matrix |
| [configr.md](research/configr.md) | Overview of what the ConfigR app does over Bluetooth |
| [configr-entity-gap.md](research/configr-entity-gap.md) | Every ConfigR heater parameter compared with the integration's entities |
| [configr-clock-schedule-boost.md](research/configr-clock-schedule-boost.md) | ConfigR's clock, schedule and Boost encodings |
| [configr-firmware-analysis.md](research/configr-firmware-analysis.md) | ConfigR's firmware update path and the update characteristics |
| [ota-research.md](research/ota-research.md) | Cloud sign-in, firmware lookup and update frames, with live findings |
| [schedule-format.md](research/schedule-format.md) | The 31-byte schedule record |
| [ble-mapping-research.md](research/ble-mapping-research.md) | Third-party projects' Bluetooth mappings for Dimplex products |
| [dimplex-manual-research.md](research/dimplex-manual-research.md) | What the Dimplex manuals document for the PLXE and related products |
| [dimplex-ble-hardware-research.md](research/dimplex-ble-hardware-research.md) | Bluetooth hardware of the heaters |
| [ble-esphome-load-balancing-roaming-research.md](research/ble-esphome-load-balancing-roaming-research.md) | Why the integration pins each heater to one proxy |
| [source-audit.md](research/source-audit.md) | Audit of the sources used by the early design |
| [upstream-issue-drafts.md](research/upstream-issue-drafts.md) | Draft reports for ESPHome and Home Assistant |
| [troubleshooting.md](research/troubleshooting.md) | The previous troubleshooting guide with developer notes on log events and bond loss |
