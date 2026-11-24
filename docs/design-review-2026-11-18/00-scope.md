# Scope

- **Surface:** Home Assistant's native climate more-info controls for one Dimplex PLXE Series C panel heater, plus the integration's related configuration entities.
- **Primary user:** A Home Assistant user controlling a purchased Dimplex panel heater locally over BLE.
- **Primary task:** Select a heating behavior and target temperature without hidden countdowns, silent expiry, or controls that imply unsupported semantics.
- **Constraints:** Use the native HA climate entity; preserve verified device semantics; do not invent schedule writes; keep unsupported schedule fields out of the UI; use official Dimplex names.
- **Sources:** `custom_components/dimplex_ble/climate.py`, `select.py`, `switch.py`, `translations/en.json`, `icons.json`, `README.md`, capability research, and the official PLXE Series C manual (Issue 7), pages 13–18.
- **Unavailable evidence:** No live HA screenshot or frontend DOM inspection was performed in this audit; aesthetic, contrast, focus, and interaction-count findings are code-level/inferred only.
- **Glossary/ADR status:** No project `GLOSSARY.md` or applicable `docs/adr/` record was found by repository search.
