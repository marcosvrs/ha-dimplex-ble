# Development

This page explains how to set up the project, run the same checks as continuous integration (CI), test without hardware and ship a change. Read [architecture.md](architecture.md) first for how the integration works. Every rule here comes from the repository's scripts, workflows and history.

## Local setup

The project uses one virtual environment in `.venv` with the pinned tools from `requirements-test.txt`. CI builds it the same way with Python 3.14 and uv.

```bash
uv venv --python 3.14
uv pip install --python .venv/bin/python -r requirements-test.txt
.venv/bin/pre-commit install
```

The first two lines match the CI install step exactly. The third line installs both git hooks that `.pre-commit-config.yaml` names in `default_install_hook_types`: the pre-commit hook and the pre-push hook. `requirements-test.txt` pins pytest, `pytest-homeassistant-custom-component`, coverage, mutmut, mypy, Ruff, pre-commit and aioesphomeapi.

### Test harness version

`pytest-homeassistant-custom-component` pins an older Home Assistant than the integration targets. That harness cannot add the climate entity, because the entity uses the newer `native_*` climate interface. Tests therefore observe availability on other entities, such as the display unit select (see the comment at the top of `tests/test_runtime.py`).

## Quality gates

CI runs three jobs on every push, pull request and merge queue entry. Run the same commands locally before every push, because CI is a confirmation step, not a debugger.

### What CI runs

The workflows in `.github/workflows/` define three jobs:

- **Tests, strict typing, and lint** (`ci.yml`): installs `.venv` as above, runs `ruff format --check custom_components/dimplex_ble`, then runs `bash scripts/run_quality_gates.sh`
- **Home Assistant manifest validation** (`ci.yml`): runs the hassfest action against the repository
- **HACS integration validation** (`validate.yml`): runs the Home Assistant Community Store (HACS) action, also once a day on a schedule

### The local command

The format check is a separate CI step: neither the pre-commit hooks nor the gate script run it. Run both commands:

```bash
.venv/bin/ruff format --check custom_components/dimplex_ble
bash scripts/run_quality_gates.sh
```

The first command fails when any file in the integration needs reformatting. The second command runs every remaining gate in order and stops at the first failure. The pre-push hook runs the second command too.

### Inside run_quality_gates.sh

[`scripts/run_quality_gates.sh`](../../scripts/run_quality_gates.sh) runs five steps:

- **pre-commit on all files**: `check-json`, `check-yaml --unsafe` (so ESPHome tags such as `!secret` and `!lambda` pass the syntax check), `check-merge-conflict`, `detect-private-key`, `ruff check` and strict mypy
- **pytest with coverage**: fails below 100% line coverage of `custom_components/dimplex_ble` (`fail_under = 100`, branch coverage off) and writes `coverage.json`
- **Function coverage**: [`scripts/check_function_coverage.py`](../../scripts/check_function_coverage.py) fails unless every function in the integration ran at least once
- **mutmut**: runs mutation testing over the integration; an exit code of 1 (surviving mutants) does not stop the script
- **Mutation score**: [`scripts/check_mutation_score.py`](../../scripts/check_mutation_score.py) requires 99% killed mutants, excluding two confirmed equivalent mutants

The Ruff rules are `E`, `F`, `I`, `UP`, `B` and `ASYNC`, with line length 88 and `E501` ignored. mypy runs in strict mode on `custom_components/dimplex_ble` only, for Python 3.14. The full-gate hook has the `pre-push` stage, so `pre-commit run --all-files` does not call the gate script recursively.

### Mutation testing is advisory

The mutation score only warns unless `REQUIRE_MUTATION_SCORE=1` is set. The script prints "Mutation score is advisory" when the score is below the threshold. A new surviving mutant that is not in the equivalent list also makes the score check fail, so read its output.

```bash
REQUIRE_MUTATION_SCORE=1 bash scripts/run_quality_gates.sh
```

This command makes the mutation threshold a hard failure, as it would be if CI enforced it.

### The mutmut cache

mutmut keeps its mutants and results in `mutants/`, which git ignores, and reuses them on the next run. The `[tool.mutmut]` table in `pyproject.toml` decides when cached results are discarded. A change to any `tests/*.py` file invalidates them (`cache_invalidation_files`), and `on_dependency_change = "rerun"` retests mutants whose dependencies changed. Delete `mutants/` to force a clean run. `process_isolation = "forkserver"` avoids an interpreter shutdown crash that made `mutmut run` exit with 139 ([ledger](research/investigation-2026-11-24/ledger.md)).

### Jobs that cannot run locally

HACS validation runs only as a GitHub Action. hassfest also runs as an action; run the container image the action uses if you have a container runtime. When you cannot run a job locally, say so in the push or pull request description.

## Test layout

All tests live in `tests/` and run against fakes of the ESPHome proxy, never against hardware. `asyncio_mode = "auto"` lets every `async def test_*` run without a marker.

### Test files

Each file covers one area:

- **`test_boost.py`**: Boost start, end and interaction with Away, against a fake heater that behaves like the hardware
- **`test_clock.py`**: clock check and clock sync once per connection
- **`test_cloud.py`**: the Dimplex cloud client against a mocked web session
- **`test_config_flow.py`**: commissioning, reauthentication, reconfiguration and the proxy survey
- **`test_connection_mode.py`**: the stalled-heater repair and the per-poll connection model
- **`test_datetime.py`**: the Away return time entity
- **`test_discovery.py`**: advertisement name matching
- **`test_energy.py`**: estimated energy integration
- **`test_entities.py`**: entity values, availability and writes
- **`test_link.py`**: contract tests for `HeaterLink`: call order, errors, cancellation
- **`test_link_settle.py`**: the 10 second settle time
- **`test_number.py`**: temperature numbers and the offset
- **`test_ota.py`** and **`test_ota_link.py`**: image checks, frames and the update over the fake link
- **`test_protocol.py`**: byte encoding and decoding
- **`test_runtime.py`**: entry lifecycle, coordinator, diagnostics, actions and the dashboard category rule
- **`test_schedule.py`**: schedule reads, saves, the backup and the pending queue
- **`test_switch.py`**: switches, including Setback and Advance
- **`test_translations.py`**: every key the code produces has a text and an icon
- **`test_update.py`**: the update entity, the sign-in fields and the shared account

### The fake proxy and client

[`tests/conftest.py`](../../tests/conftest.py) replaces Home Assistant's Bluetooth functions and the ESPHome entry lookup with fakes. Its classes model each seam the integration touches:

- **`FakeBluetooth`** (fixture `fake_bt`): patches scanner lookup, device lookup, advertisement callbacks and the loaded ESPHome entries; `add()` adds a proxy, `advertise()` plays one advertisement and `passkey_request()` fires the firmware's event
- **`FakeProxy`**: one proxy with its runtime data, scanner, application programming interface (API) client and heater state; attributes such as `connect_error`, `pair_errors`, `read_error`, `pair_gate` and `sees_heater` inject failures and delays
- **`FakeScanner`**: a scanner whose connector builds `FakeClient` objects and reports slot allocations
- **`FakeClient`**: a bleak-esphome client stand-in that serves `HEATER_VALUES`, emulates schedule records and records every call
- **`FakeApiClient`**: records `execute_service` calls, so tests can check which responder actions ran

Every fake call appends to `proxy.events`, a shared log of actions, connects, pairs, reads, writes and disconnects. Tests assert on that log to prove ordering rules:

```python
async def test_connect_then_pair_then_gatt_in_order(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    link = make_link(hass)

    await link.async_snapshot()

    first_read = kinds(proxy.events).index("read")
    assert kinds(proxy.events)[:first_read] == [
        "action", "connect", "pair_start", "pair_end", "action",
    ]
    assert proxy.events[-1] == "disconnect"
```

This shortened test from `tests/test_link.py` proves that the responder is armed before the connect, cleared after pairing and that the link is released at the end.

### Autouse fixtures and helpers

Three fixtures apply to every test, and three helpers build common inputs:

- **`no_heater_settle`**: sets the settle time to 0; `test_link_settle.py` turns it back on
- **`esphome_connection_logger_at_info`**: keeps the `aioesphomeapi.connection` logger at INFO, as hosts should
- **`enable_local_custom_integrations`**: lets Home Assistant load the integration from the repository
- **`pair_failure(code)`** and **`gatt_failure(att)`**: build the exact errors bleak-esphome raises
- **`add_settings(hass, ...)`**: adds the Dimplex entry with optional data and options

## The heater emulator

[`firmware/test/heater_emulator.yaml`](../../firmware/test/heater_emulator.yaml) turns a spare ESP32 into a fake panel heater. It exists to rehearse the firmware update through Home Assistant without risking a real heater.

### What it serves

The emulator advertises as `AM9086<Dimplex>` and pairs with a static passkey. It serves the Generic Attribute Profile (GATT) layout that Home Assistant polls, filled with values captured from a real heater, and sets its local maximum transmission unit (MTU) to 517. It also plays the heater's over-the-air (OTA) update channel. It checks every frame's envelope, length and checksum, and verifies the image with CRC-16/AUG-CCITT (a 16-bit cyclic redundancy check).

### Failure cases per attempt

Each install attempt plays the next case at row 50 of the image:

- **Attempt 0**: refuses the row
- **Attempt 1**: stops answering, so Home Assistant must time out
- **Attempt 2**: reboots, so the link drops
- **Attempt 3 and later**: accepts the image, then reports display version 5.0

The attempt counter and the reported version persist across reboots. Erase the ESP32's flash before flashing it again to start from attempt 0.

### Rules for the emulator

The emulator is test equipment, and three rules keep it safe:

- **Never commit its personal identification number (PIN)**: the passkey comes from `!secret emulator_passkey` in a local `secrets.yaml`, which git ignores; never write a PIN into the configuration or commit a secrets file
- **Never flash it to a proxy**: it replaces the whole firmware, has no Bluetooth proxy, no Wi-Fi and no ESPHome API
- **Use a spare board on Universal Serial Bus (USB)**: destructive experiments belong on a test ESP32, never on a production proxy

## Validating the ESPHome firmware

Validate and compile every firmware change before anyone flashes it. Use an ESPHome command line tool at version 2026.11.0 or newer, installed outside `.venv` (for example with `uv tool install esphome`).

### Dummy secrets

Both configurations read `!secret` values from a `secrets.yaml` next to them. Create throwaway files with generated values, never real ones:

```bash
cd firmware
{
  echo "wifi_ssid: dummy-ssid"
  echo "wifi_password: dummy-password"
  echo "living_room_api_key: \"$(openssl rand -base64 32)\""
} > secrets.yaml
echo "emulator_passkey: $(python3 -c 'import secrets; print(100000 + secrets.randbelow(900000))')" > test/secrets.yaml
```

The first block writes the three secrets the example proxy needs, with a random 32-byte API encryption key. The last line writes a random six-digit passkey for the emulator. Git ignores every `secrets.yaml`.

### Config and compile

```bash
cd firmware
esphome config example-bluetooth-proxy.yaml
esphome compile example-bluetooth-proxy.yaml
cd test
esphome config heater_emulator.yaml
esphome compile heater_emulator.yaml
```

`esphome config` resolves packages and secrets and validates the schema. `esphome compile` builds the firmware without flashing. The example proxy pulls its base package from GitHub at a pinned commit, so compiling it needs network access. The responder package is checked as part of the example proxy, because it is included there.

### Rules for real proxies

The archive records two operational rules that protect the proxies ([findings](research/esphome-ble-findings.md#operational-rules)):

- **Bounded log sessions**: a proxy accepts at most 5 API clients; never leave `esphome logs` running, and flash with `esphome run --no-logs`
- **No routine bond resets**: never erase a proxy's stored bonds as a fix; a failed pairing can delete a good bond

## Deploying to a Home Assistant host

A test deployment copies the integration folder to the host and restarts Home Assistant. Nothing else on the host changes.

```bash
rsync -a --delete --exclude __pycache__ \
  custom_components/dimplex_ble/ \
  <user>@<home-assistant-host>:<config-dir>/custom_components/dimplex_ble/
```

This command mirrors `custom_components/dimplex_ble` into the `custom_components` folder of the Home Assistant configuration directory. Replace the placeholders with your own host and path. Restart Home Assistant afterwards, because a reload does not pick up changed Python modules.

### Logging for a test run

Turn on debug logs for the integration, but keep the ESPHome connection logger at INFO, because its DEBUG lines contain action arguments:

```yaml
logger:
  default: warning
  logs:
    custom_components.dimplex_ble: debug
    bleak_esphome: debug
    aioesphomeapi.connection: info
```

Every debug line of the integration names the heater address and a step, such as `CONNECT_RESULT`, `PAIR_RESULT` or `ROUTE_NOT_SEEN`. [`scripts/soak_report.py`](../../scripts/soak_report.py) reads a Home Assistant log on standard input and counts polls, connects, pairings, failures and stall repairs per heater.

## Commits and pull requests

The history follows a consistent commit style. CI runs on pushes, pull requests and the merge queue.

### Commit messages

Commit subjects follow these conventions:

- **Type prefix**: `feat`, `fix`, `refactor`, `docs`, `test`, `chore` or `ci`, with an optional scope such as `(ota)` or `(config-flow)`
- **Subject**: lower case, present tense, describing the behavior a user sees, for example `fix: only the climate entity appears on dashboards by default`
- **Body**: the evidence behind the change (heater, date, raw bytes before and after, and whether the value was restored), or the review that found the problem
- **Trailer**: commits written with an artificial intelligence assistant end with a `Co-Authored-By` line

### Branches and pull requests

Feature work uses prefixed branches such as `feat/` and `refactor/`. Dependency updates arrive as Dependabot pull requests titled `chore(deps): bump <package> from <old> to <new>`. Push once, after the full local gate set passes.

### What never goes into the repository

`.gitignore` and the pre-commit hooks keep private data out. Never commit `secrets.yaml`, raw captures (`captures/`), local ESPHome probes (`esphome/`), PINs, tokens, keys, email addresses, hostnames or Internet Protocol (IP) addresses. `detect-private-key` catches private keys, but not the rest.

## Evidence rule for new heater features

A new heater feature needs hardware evidence before it gets code. The archive records many byte meanings that the manual or the app suggested and the heater contradicted, for example setpoint limits that accept writes and never apply ([final report](research/investigation-2026-11-24/final-report.md#4-rejected-hypotheses-selection)).

### The three steps

Every feature goes through three steps in order:

- **Live read in two states**: read the characteristic with the heater in two known states set on the heater itself, and record both raw values
- **Bounded, reversible write**: write one value inside the documented range, confirm the change on the heater display and by a readback, then restore the original value
- **Implementation with tests**: add the encoding to `protocol.py`, teach the fakes the observed behavior and keep the gates at 100%

The `get_gatt_services` action lists services and characteristic properties without reading values. Use it to find a characteristic before the first read. Record each result with the date, the heater model, the raw bytes and the restore in the commit body and in the research archive. The Boost temperature commit (`d4d7e7e`) shows the expected form.

### What stays out

Some heater parameters must not be exposed at all, such as the PIN lock, factory reset, test modes and pairing mode ([ledger](research/investigation-2026-11-24/ledger.md)). When a pairing or reconnect fails during an experiment, isolate Home Assistant, the ESPHome API client, the proxy's bond state, the route and the heater before blaming the heater, as `AGENTS.md` requires.
