"""Commissioning, reauth and reconfigure against a fake proxy and responder."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest
from conftest import ADDRESS, PIN, PROXY, FakeProxy, pair_failure
from homeassistant.config_entries import (
    SOURCE_BLUETOOTH,
    SOURCE_USER,
    ConfigEntryDisabler,
    ConfigEntryState,
)
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble import config_flow, responder
from custom_components.dimplex_ble.const import (
    CONF_ADDRESS,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    CONF_SYNC_CLOCK,
    DOMAIN,
    PASSKEY_REQUEST_EVENT,
)
from custom_components.dimplex_ble.link import HeaterLink

pytestmark = pytest.mark.usefixtures("skip_dependencies")


OTHER = "14:C1:9F:22:4C:DA"
TARGET_ONLY = ("dimplex_pin_arm", {"address": ADDRESS, "passkey": 1_000_000})
ARM_PIN = ("dimplex_pin_arm", {"address": ADDRESS, "passkey": 12345})
CLEAR = ("dimplex_pin_clear", {})


def advert(name: str = "AM6532<Dimplex>", address: str = ADDRESS.lower()):
    return SimpleNamespace(name=name, address=address)


async def settle() -> None:
    """Let pairing, the event bus and the flow's progress callback run."""
    for _ in range(20):
        await asyncio.sleep(0)


def current_step(hass, flow_id: str) -> str | None:
    flow = next(
        (
            f
            for f in hass.config_entries.flow.async_progress()
            if f["flow_id"] == flow_id
        ),
        None,
    )
    return None if flow is None else flow["step_id"]


async def advance(hass, flow_id: str) -> dict:
    """What the frontend does after a progress step ends: configure again."""
    await settle()
    return await hass.config_entries.flow.async_configure(flow_id)


def passkey_listeners(hass) -> int:
    return hass.bus.async_listeners().get(PASSKEY_REQUEST_EVENT, 0)


async def choose_proxy(hass, fake_bt, *, gate: bool = True, errors: tuple = ()):
    """Discover the heater and pick the proxy: pairing starts in progress."""
    proxy = fake_bt.add()
    proxy.pair_errors = list(errors)
    if gate:
        proxy.pair_gate = hass.loop.create_future()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=advert()
    )
    assert result["step_id"] == "confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROXY_SOURCE: PROXY}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "pairing"
    assert result["progress_action"] == "pairing"
    return proxy, result


async def choose(hass, flow_id: str, option: str) -> dict:
    """Pick a menu option, as the frontend does."""
    return await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": option}
    )


async def pin_ready(hass, fake_bt, proxy: FakeProxy, flow_id: str) -> dict:
    """The heater shows its PIN: the flow offers the PIN form or a re-show."""
    await settle()
    fake_bt.passkey_request(proxy)
    result = await advance(hass, flow_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "pin_ready"
    assert result["menu_options"] == ["pin", "show_pin"]
    assert result["description_placeholders"]["name"].endswith("AM6532")
    assert passkey_listeners(hass) == 0
    return result


async def start(hass, fake_bt) -> tuple[FakeProxy, dict]:
    """Pairing starts, the heater shows its PIN, and the PIN form opens."""
    proxy, result = await choose_proxy(hass, fake_bt)
    result = await pin_ready(hass, fake_bt, proxy, result["flow_id"])
    result = await choose(hass, result["flow_id"], "pin")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pin"
    assert result["errors"] == {}
    return proxy, result


def connects(proxy: FakeProxy) -> int:
    return sum(1 for e in proxy.events if isinstance(e, tuple) and e[0] == "connect")


async def fail_first_pairing(hass, proxy: FakeProxy, *errors) -> None:
    """The first pair() ends (e.g. the 30 s timeout) after the PIN appeared."""
    proxy.pair_errors = list(errors)
    proxy.pair_gate.set_result(None)
    await settle()
    proxy.pair_gate = None


def release_on_pin(proxy: FakeProxy, error: BaseException | None = None) -> None:
    """Let the pending pairing finish when the PIN reaches the responder."""

    def on_arm(data) -> None:
        if data["passkey"] < 1_000_000 and not proxy.pair_gate.done():
            if error is not None:
                proxy.pair_errors.append(error)
            proxy.pair_gate.set_result(None)

    proxy.on_arm = on_arm


async def test_pin_typed_while_pairing_waits_creates_the_entry(hass, fake_bt) -> None:
    proxy, result = await start(hass, fake_bt)
    assert proxy.actions == [TARGET_ONLY]
    assert proxy.events[-1] == "pair_start"
    release_on_pin(proxy)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dimplex Heater AM6532"
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_PROXY_SOURCE: PROXY,
        CONF_PASSKEY: PIN,
    }
    assert result["options"] == {CONF_SYNC_CLOCK: True}  # on by default
    assert proxy.actions[:3] == [TARGET_ONLY, ARM_PIN, CLEAR]
    assert proxy.actions[-1] == CLEAR
    # Fresh service discovery, verified read, then the slot is released.
    assert next(
        e for e in proxy.events if isinstance(e, tuple) and e[0] == "connect"
    ) == ("connect", False, False)
    assert "read" in [e[0] for e in proxy.events if isinstance(e, tuple)]
    flow_events = proxy.events[: proxy.events.index("disconnect") + 1]
    first_read = [e[0] if isinstance(e, tuple) else e for e in flow_events].index(
        "read"
    )
    assert flow_events.index("pair_end") < first_read
    assert flow_events.count("pair_start") == 1


async def test_slow_typist_retries_once_with_the_pin_armed(hass, fake_bt) -> None:
    proxy, result = await start(hass, fake_bt)
    await fail_first_pairing(hass, proxy, pair_failure(99))

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    first_close = proxy.events.index("disconnect")
    flow_events = proxy.events[: proxy.events.index("disconnect", first_close + 1)]
    assert flow_events.count("pair_start") == 2
    second = proxy.actions.index(ARM_PIN)
    assert proxy.actions[second - 1 : second + 2] == [CLEAR, ARM_PIN, CLEAR]


async def test_show_pin_while_pairing_waits_keeps_that_pairing(hass, fake_bt) -> None:
    """The PIN is still on the display: nothing is cancelled or restarted."""
    proxy, result = await choose_proxy(hass, fake_bt)
    result = await pin_ready(hass, fake_bt, proxy, result["flow_id"])
    before = (connects(proxy), proxy.events.count("pair_start"), len(proxy.actions))

    result = await choose(hass, result["flow_id"], "show_pin")

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "pin_still_shown"
    assert result["menu_options"] == ["pin", "show_pin"]
    assert result["description_placeholders"] == {"name": "AM6532"}
    after = (connects(proxy), proxy.events.count("pair_start"), len(proxy.actions))
    assert after == before
    assert "pair_end" not in proxy.events

    result = await choose(hass, result["flow_id"], "pin")
    assert result["step_id"] == "pin"
    release_on_pin(proxy)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSKEY] == PIN


async def test_show_pin_after_the_pairing_timed_out_pairs_again(hass, fake_bt) -> None:
    """The display cleared: a new target-only pairing makes it show the PIN."""
    proxy, result = await choose_proxy(hass, fake_bt)
    result = await pin_ready(hass, fake_bt, proxy, result["flow_id"])
    await fail_first_pairing(hass, proxy, pair_failure(99))
    proxy.pair_gate = hass.loop.create_future()
    before_connects = connects(proxy)
    before_actions = len(proxy.actions)

    result = await choose(hass, result["flow_id"], "show_pin")

    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "pairing"
    await settle()
    assert proxy.actions[before_actions:] == [TARGET_ONLY]
    assert connects(proxy) == before_connects + 1
    assert proxy.events.count("pair_start") == 2
    assert proxy.events[-1] == "pair_start"

    result = await pin_ready(hass, fake_bt, proxy, result["flow_id"])
    result = await choose(hass, result["flow_id"], "pin")
    release_on_pin(proxy)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSKEY] == PIN


@pytest.mark.parametrize("submitted", [{}, {CONF_PASSKEY: ""}, {CONF_PASSKEY: " "}])
async def test_empty_pin_while_pairing_waits_shows_the_menu_again(
    hass, fake_bt, submitted
) -> None:
    proxy, result = await start(hass, fake_bt)
    pairings = proxy.events.count("pair_start")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], submitted
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "pin_still_shown"
    assert proxy.events.count("pair_start") == pairings
    assert proxy.actions == [TARGET_ONLY]
    result = await choose(hass, result["flow_id"], "pin")
    release_on_pin(proxy)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_empty_pin_after_the_pairing_timed_out_pairs_again(hass, fake_bt) -> None:
    proxy, result = await start(hass, fake_bt)
    await fail_first_pairing(hass, proxy, pair_failure(99))
    proxy.pair_gate = hass.loop.create_future()
    before_connects = connects(proxy)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await settle()
    assert proxy.actions[-1] == TARGET_ONLY
    assert connects(proxy) == before_connects + 1
    assert proxy.events.count("pair_start") == 2
    result = await pin_ready(hass, fake_bt, proxy, result["flow_id"])
    result = await choose(hass, result["flow_id"], "pin")
    release_on_pin(proxy)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_repeated_show_pin_and_abort_from_the_menu_keep_the_pairing(
    hass, fake_bt
) -> None:
    """Double submits never restart a waiting pairing; abort never cancels it."""
    proxy, result = await choose_proxy(hass, fake_bt)
    result = await pin_ready(hass, fake_bt, proxy, result["flow_id"])
    for _ in range(3):
        result = await choose(hass, result["flow_id"], "show_pin")
        assert result["step_id"] == "pin_still_shown"
    assert proxy.events.count("pair_start") == 1
    assert proxy.actions == [TARGET_ONLY]

    hass.config_entries.flow.async_abort(result["flow_id"])
    await settle()
    assert "pair_end" not in proxy.events
    assert "disconnect" not in proxy.events

    proxy.pair_errors = [pair_failure(99)]  # the native 30 s timeout
    proxy.pair_gate.set_result(None)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert proxy.events.index("pair_end") < proxy.events.index("disconnect")
    assert proxy.actions[-1] == CLEAR


async def test_show_pin_ignores_requests_for_other_heaters_and_proxies(
    hass, fake_bt
) -> None:
    proxy, result = await choose_proxy(hass, fake_bt)
    other = fake_bt.add(FakeProxy(OTHER))
    result = await pin_ready(hass, fake_bt, proxy, result["flow_id"])
    await fail_first_pairing(hass, proxy, pair_failure(99))
    proxy.pair_gate = hass.loop.create_future()
    result = await choose(hass, result["flow_id"], "show_pin")
    flow_id = result["flow_id"]
    await settle()

    fake_bt.passkey_request(proxy, address="AA:BB:CC:DD:EE:99")
    fake_bt.passkey_request(proxy, device_id=fake_bt.device_id(other))
    await settle()
    assert current_step(hass, flow_id) == "pairing"

    result = await pin_ready(hass, fake_bt, proxy, flow_id)
    result = await choose(hass, flow_id, "pin")
    release_on_pin(proxy)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_PASSKEY: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


def reject_next_pin(hass, proxy: FakeProxy) -> None:
    """The heater rejects the PIN; later pairings wait for their own PIN."""

    def on_arm(data) -> None:
        if data["passkey"] < 1_000_000 and not proxy.pair_gate.done():
            proxy.pair_errors.append(pair_failure(81))
            proxy.pair_gate.set_result(None)
            proxy.pair_gate = hass.loop.create_future()

    proxy.on_arm = on_arm


async def test_wrong_pin_restarts_pairing_so_the_display_shows_it_again(
    hass, fake_bt
) -> None:
    """Hardware 2026-11-24 16:37: after a rejected PIN the display was blank."""
    proxy, result = await start(hass, fake_bt)
    reject_next_pin(hass, proxy)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: "999999"}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert proxy.actions[-1] == TARGET_ONLY  # a new pairing, no PIN armed yet

    await settle()
    fake_bt.passkey_request(proxy)
    result = await advance(hass, result["flow_id"])
    assert result["step_id"] == "pin"
    assert result["errors"] == {"base": "pin_rejected"}

    release_on_pin(proxy)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_wrong_pin_on_the_retry_also_restarts_pairing(hass, fake_bt) -> None:
    proxy, result = await start(hass, fake_bt)
    await fail_first_pairing(hass, proxy, pair_failure(99), pair_failure(81))

    def on_arm(data) -> None:
        # The retry (PIN armed before connect) is answered at once and
        # rejected; the next target-only pairing waits for its own PIN.
        if data["passkey"] >= 1_000_000:
            proxy.pair_gate = hass.loop.create_future()

    proxy.on_arm = on_arm

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await settle()
    fake_bt.passkey_request(proxy)
    result = await advance(hass, result["flow_id"])
    assert result["errors"] == {"base": "pin_rejected"}

    release_on_pin(proxy)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_failed_retry_and_failed_verification_keep_the_form(
    hass, fake_bt
) -> None:
    proxy, result = await start(hass, fake_bt)
    await fail_first_pairing(hass, proxy, pair_failure(99), TimeoutError())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["errors"] == {"base": "pairing_failed"}

    proxy.read_error = TimeoutError()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_pin_that_cannot_reach_the_proxy_keeps_the_pairing(hass, fake_bt) -> None:
    proxy, result = await start(hass, fake_bt)
    proxy.api_connected = False

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert "disconnect" not in proxy.events

    proxy.api_connected = True
    release_on_pin(proxy)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("pin", ["12345", "1234567", "12a456", "١٢٣٤٥٦"])
async def test_pin_must_be_six_ascii_digits(hass, fake_bt, pin) -> None:
    proxy, result = await start(hass, fake_bt)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: pin}
    )

    assert result["errors"] == {"base": "invalid_passkey"}
    assert proxy.actions == [TARGET_ONLY]
    proxy.pair_gate.set_result(None)  # the native pair() always ends (30 s)


async def test_abandoned_flow_waits_for_pairing_before_closing(hass, fake_bt) -> None:
    proxy, result = await start(hass, fake_bt)

    hass.config_entries.flow.async_abort(result["flow_id"])
    await asyncio.sleep(0)
    assert "disconnect" not in proxy.events

    proxy.pair_errors = [pair_failure(99)]
    proxy.pair_gate.set_result(None)
    await hass.async_block_till_done()
    assert proxy.events.index("pair_end") < proxy.events.index("disconnect")
    assert proxy.actions[-1] == CLEAR


def named(source: str, name: str, *, rssi: int | None = -60, free=3, allocated=()):
    """A responder proxy with a friendly name and the given slot usage."""
    proxy = FakeProxy(source)
    proxy.scanner.name = name
    proxy.rssi = rssi
    proxy.scanner.free = free
    proxy.scanner.allocated = list(allocated)
    return proxy


async def proxy_form(hass) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=advert()
    )
    assert result["step_id"] == "confirm"
    return result


def offered(result) -> list[tuple[str, str]]:
    options = result["data_schema"].schema[CONF_PROXY_SOURCE].config["options"]
    return [(o["value"], o["label"]) for o in options]


async def test_proxy_choice_recommends_a_free_quiet_proxy(hass, fake_bt) -> None:
    """Free and quiet beats a stronger but loaded proxy; full ones go last."""
    quiet = fake_bt.add(
        named(
            "AA:00:00:00:00:01",
            "Hall",
            rssi=-80,
            free=2,
            allocated=["11:22:33:44:55:66"],
        )
    )
    loaded = fake_bt.add(
        named(
            "AA:00:00:00:00:02",
            "Bedroom",
            rssi=-50,
            free=1,
            allocated=["11:22:33:44:55:01", "11:22:33:44:55:02"],
        )
    )
    unknown = fake_bt.add(named("AA:00:00:00:00:03", "Garage", rssi=-70, free=None))
    full = fake_bt.add(
        named(
            "AA:00:00:00:00:04", "Office", rssi=-40, free=0, allocated=["1", "2", "3"]
        )
    )
    plain = fake_bt.add(FakeProxy("AA:00:00:00:00:05", responder=False))
    plain.scanner.name = "Kitchen"
    offline = fake_bt.add(named("AA:00:00:00:00:06", "Attic"))
    offline.runtime.available = False

    result = await proxy_form(hass)

    # Friendly names and one short tag; never a MAC address or dBm.
    assert offered(result) == [
        (quiet.source, "Hall · Recommended"),
        (loaded.source, "Bedroom · ⚠ already serves 2 heaters"),
        (unknown.source, "Garage"),
        (full.source, "Office · ⚠ full"),
    ]
    assert result["data_schema"]({})[CONF_PROXY_SOURCE] == quiet.source
    assert result["errors"] == {}
    assert result["description_placeholders"]["proxy_notes"] == (
        "\n\nNot shown: Attic, Kitchen (missing Dimplex firmware or offline)."
    )
    assert result["description_placeholders"]["name"] == "AM6532"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_full_proxy_is_never_the_default_when_another_has_room(
    hass, fake_bt
) -> None:
    full = fake_bt.add(named("AA:00:00:00:00:01", "Office", rssi=-30, free=0))
    busy = fake_bt.add(
        named("AA:00:00:00:00:02", "Hall", rssi=-90, free=1, allocated=["1", "2"])
    )

    result = await proxy_form(hass)

    assert [value for value, _ in offered(result)] == [busy.source, full.source]
    assert result["data_schema"]({})[CONF_PROXY_SOURCE] == busy.source
    assert offered(result)[1][1] == "Office · ⚠ full"
    # Every proxy is full or busy: still selectable, with a form-level warning.
    assert result["errors"] == {"base": "proxies_busy"}
    hass.config_entries.flow.async_abort(result["flow_id"])


@pytest.mark.parametrize(
    ("rssi", "allocated", "label", "errors"),
    [
        (-127, [], "Hall · Recommended", {}),
        (0, [], "Hall · Recommended", {}),
        (None, [], "Hall · Recommended", {}),
        (
            -60,
            ["1", "2"],
            "Hall · ⚠ already serves 2 heaters",
            {"base": "proxies_busy"},
        ),
    ],
)
async def test_single_proxy_is_preselected_with_its_warnings(
    hass, fake_bt, rssi, allocated, label, errors
) -> None:
    proxy = fake_bt.add(
        named(PROXY, "Hall", rssi=rssi, free=3 - len(allocated), allocated=allocated)
    )

    result = await proxy_form(hass)

    assert offered(result) == [(proxy.source, label)]
    assert result["data_schema"]({})[CONF_PROXY_SOURCE] == proxy.source
    assert result["errors"] == errors
    assert result["description_placeholders"]["proxy_notes"] == ""
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_equal_proxies_are_ordered_by_name(hass, fake_bt) -> None:
    fake_bt.add(named("AA:00:00:00:00:01", "Bravo"))
    fake_bt.add(named("AA:00:00:00:00:02", "Alpha"))
    unknown_b = fake_bt.add(named("AA:00:00:00:00:03", "Delta", free=None))
    unknown_b.scanner.free = None
    fake_bt.add(named("AA:00:00:00:00:04", "Charlie", free=None))

    result = await proxy_form(hass)

    labels = [label for _, label in offered(result)]
    assert labels == ["Alpha · Recommended", "Bravo", "Charlie", "Delta"]
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_heaters_pinned_to_a_proxy_count_as_its_connections(
    hass, fake_bt
) -> None:
    """Persistent heater links hold a slot even while momentarily dropped."""
    pinned = fake_bt.add(named("AA:00:00:00:00:01", "Hall", rssi=-40))
    other = fake_bt.add(named("AA:00:00:00:00:02", "Bedroom", rssi=-90))
    for index, disabled in enumerate((None, None, ConfigEntryDisabler.USER)):
        MockConfigEntry(
            domain=DOMAIN,
            unique_id=f"aa:bb:cc:dd:ee:0{index}",
            data={
                CONF_ADDRESS: f"AA:BB:CC:DD:EE:0{index}",
                CONF_PROXY_SOURCE: pinned.source,
            },
            disabled_by=disabled,
        ).add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, source="ignore", unique_id="x").add_to_hass(hass)

    result = await proxy_form(hass)

    assert [value for value, _ in offered(result)] == [other.source, pinned.source]
    assert offered(result)[1][1] == "Hall · ⚠ already serves 2 heaters"
    assert result["errors"] == {}
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_only_proxies_without_the_firmware_abort_and_say_so(
    hass, fake_bt
) -> None:
    plain = fake_bt.add(FakeProxy(OTHER, responder=False))
    plain.scanner.name = "Kitchen"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=advert()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_proxy"
    assert result["description_placeholders"]["proxy_notes"] == (
        "\n\nNot shown: Kitchen (missing Dimplex firmware or offline)."
    )


async def test_discovery_aborts(hass, fake_bt) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=advert("Phone")
    )
    assert result["reason"] == "not_dimplex"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=advert()
    )
    assert result["reason"] == "no_proxy"

    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS.lower()).add_to_hass(hass)
    fake_bt.add()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=advert()
    )
    assert result["reason"] == "already_configured"


async def test_user_step_lists_unconfigured_heaters(hass, fake_bt, monkeypatch) -> None:
    fake_bt.add()
    infos = [
        advert(),
        advert("AM1382", "AA:BB:CC:DD:EE:21"),
        advert("Phone", "AA:BB:CC:DD:EE:22"),
        advert("AM2086", "AA:BB:CC:DD:EE:23"),
    ]
    monkeypatch.setattr(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        lambda hass: infos,
    )
    MockConfigEntry(domain=DOMAIN, unique_id="aa:bb:cc:dd:ee:23").add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    options = result["data_schema"].schema[CONF_ADDRESS].config["options"]
    assert options == [
        {"value": ADDRESS, "label": "AM6532"},
        {"value": "AA:BB:CC:DD:EE:21", "label": "AM1382"},
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["step_id"] == "confirm"
    hass.config_entries.flow.async_abort(result["flow_id"])

    infos.clear()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["reason"] == "no_heaters_found"


def make_entry(hass, source: str = PROXY) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater AM6532",
        unique_id=ADDRESS.lower(),
        data={
            CONF_ADDRESS: ADDRESS.lower(),
            CONF_PROXY_SOURCE: source,
            CONF_PASSKEY: PIN,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_reauth_repairs_with_a_new_pin(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.pair_errors = [pair_failure(81)]
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    pairings = proxy.events.count("pair_start")

    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    assert result["step_id"] == "reauth_proxy"
    assert CONF_SYNC_CLOCK not in result["data_schema"]({})  # a repair, not settings
    assert proxy.events.count("pair_start") == pairings  # nothing until the user acts
    proxy.pair_gate = hass.loop.create_future()
    release_on_pin(proxy)
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PROXY_SOURCE: PROXY}
    )
    await pin_ready(hass, fake_bt, proxy, flow["flow_id"])
    result = await choose(hass, flow["flow_id"], "pin")
    assert result["step_id"] == "pin"
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {CONF_PASSKEY: "654321"}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSKEY] == "654321"
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_without_a_visible_proxy_stays_open(
    hass, fake_bt, monkeypatch
) -> None:
    monkeypatch.setattr(config_flow, "ADVERTISEMENT_WAIT", 0.01)
    fake_bt.add(FakeProxy(OTHER, responder=False))
    entry = make_entry(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert not result.get("errors")  # nothing released before the user confirms
    assert fake_bt.callbacks == []

    configure = hass.async_create_task(
        hass.config_entries.flow.async_configure(result["flow_id"], {})
    )
    await settle()
    assert [address for _, address in fake_bt.callbacks] == [ADDRESS]
    fake_bt.advertise(OTHER)  # a proxy without the responder does not count
    result = await configure

    assert result["step_id"] == "reauth_proxy"
    assert result["errors"] == {"base": "no_proxy"}
    assert fake_bt.callbacks == []  # the wait always unsubscribes

    # Submitting the empty form looks again.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "reauth_proxy"
    assert result["errors"] == {"base": "no_proxy"}


async def test_reconfigure_moves_the_bond_to_another_proxy(hass, fake_bt) -> None:
    old = fake_bt.add()
    new = fake_bt.add(FakeProxy(OTHER))
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    old_events = len(old.events)

    result = await entry.start_reconfigure_flow(hass)
    assert result["data_schema"]({})[CONF_PROXY_SOURCE] == PROXY
    new.pair_gate = hass.loop.create_future()
    release_on_pin(new)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROXY_SOURCE: OTHER}
    )
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert old.events[old_events:] == ["disconnect"]  # the running link let go
    await pin_ready(hass, fake_bt, new, result["flow_id"])
    result = await choose(hass, result["flow_id"], "pin")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: PIN}
    )
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PROXY_SOURCE] == OTHER
    assert "pair_start" not in old.events[old_events:]
    assert "dimplex_pin_arm" not in [a[0] for a in old.actions[2:]]
    assert new.events.count("pair_start") == 2  # commission, then the reload
    assert entry.state is ConfigEntryState.LOADED


async def test_abandoned_reconfigure_restores_the_entry(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reconfigure_flow(hass)
    proxy.pair_gate = hass.loop.create_future()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROXY_SOURCE: PROXY}
    )
    assert entry.state is ConfigEntryState.NOT_LOADED
    await settle()
    assert passkey_listeners(hass) == 1
    hass.config_entries.flow.async_abort(result["flow_id"])
    assert passkey_listeners(hass) == 0
    proxy.pair_gate.set_result(None)
    proxy.pair_gate = None
    await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    assert proxy.actions[-1] == ("dimplex_pin_clear", {})


async def test_reconfigure_releases_the_link_before_listing_proxies(
    hass, fake_bt
) -> None:
    """A connected heater does not advertise, so the flow must let go first."""
    old = fake_bt.add()
    new = fake_bt.add(FakeProxy(OTHER))
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    old.sees_heater = False  # the persistent link keeps the heater silent
    new.sees_heater = False

    flow = hass.async_create_task(entry.start_reconfigure_flow(hass))
    await settle()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not flow.done()
    # The released heater re-sends identical data: only a per-advertisement
    # callback (not a change-driven one) reports it.
    fake_bt.advertise(OTHER)
    result = await flow

    assert "errors" not in result or not result["errors"]
    assert result["data_schema"]({})[CONF_PROXY_SOURCE] == OTHER
    assert fake_bt.callbacks == []  # entry unloaded, flow wait unsubscribed


async def test_bonded_proxy_finishes_reconfigure_without_a_pin(hass, fake_bt) -> None:
    """A proxy that still holds a valid bond pairs at once: no PIN appears."""
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    before = len(proxy.actions)

    result = await entry.start_reconfigure_flow(hass)
    assert result["data_schema"]({})[CONF_SYNC_CLOCK] is True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROXY_SOURCE: PROXY, CONF_SYNC_CLOCK: False}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    result = await advance(hass, result["flow_id"])
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.options == {CONF_SYNC_CLOCK: False}
    assert not entry.runtime_data.link.sync_clock
    assert entry.data[CONF_PASSKEY] == PIN  # kept
    assert entry.state is ConfigEntryState.LOADED
    assert passkey_listeners(hass) == 0
    # Pairing never asked for a PIN; then the reload uses the stored one.
    assert proxy.actions[before:] == [TARGET_ONLY, CLEAR, ARM_PIN, CLEAR]


async def test_bonded_proxy_creates_an_entry_without_a_pin(hass, fake_bt) -> None:
    proxy, result = await choose_proxy(hass, fake_bt, gate=False)

    result = await advance(hass, result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY}
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED
    # Commissioning (pair, clear), then the new entry's link: without
    # a stored PIN it arms the responder target-only.
    assert proxy.actions == [TARGET_ONLY, CLEAR, TARGET_ONLY, CLEAR]


async def test_passkey_requests_from_elsewhere_are_ignored(hass, fake_bt) -> None:
    proxy, result = await choose_proxy(hass, fake_bt)
    other = fake_bt.add(FakeProxy(OTHER))
    flow_id = result["flow_id"]
    await settle()

    fake_bt.passkey_request(proxy, address="AA:BB:CC:DD:EE:99")
    fake_bt.passkey_request(proxy, device_id=fake_bt.device_id(other))
    fake_bt.passkey_request(proxy, device_id="unknown-device")
    hass.bus.async_fire(PASSKEY_REQUEST_EVENT, {"address": ADDRESS})
    await settle()
    assert current_step(hass, flow_id) == "pairing"
    # A configure while pairing still waits only shows the progress again.
    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["type"] is FlowResultType.SHOW_PROGRESS

    fake_bt.passkey_request(proxy, address=ADDRESS.lower())
    result = await advance(hass, flow_id)
    assert result["step_id"] == "pin_ready"
    proxy.pair_gate.set_result(None)


async def test_pairing_that_ends_without_a_request_offers_a_retry(
    hass, fake_bt
) -> None:
    proxy, result = await choose_proxy(
        hass, fake_bt, gate=False, errors=(pair_failure(99),)
    )

    result = await advance(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pairing_failed"
    assert result["errors"] == {"base": "pairing_not_started"}
    assert passkey_listeners(hass) == 0

    proxy.read_error = TimeoutError()  # paired, but the verification read fails
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    result = await advance(hass, result["flow_id"])
    assert result["step_id"] == "pairing_failed"
    assert result["errors"] == {"base": "cannot_connect"}

    proxy.read_error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await advance(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert proxy.events.count("pair_start") == 4  # three attempts, then setup


async def test_reconfigure_offers_a_farther_proxy_heard_after_the_nearest(
    hass, fake_bt
) -> None:
    """Hardware 2026-11-24 16:25: only the nearest proxy was offered."""
    near = fake_bt.add()
    far = fake_bt.add(FakeProxy(OTHER))
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    far.sees_heater = False  # its sighting expired while the link held the heater

    flow = hass.async_create_task(entry.start_reconfigure_flow(hass))
    await settle()
    assert not flow.done()  # the nearest proxy reported; the window is still open
    fake_bt.advertise(OTHER)
    result = await flow

    offered = {
        o["value"]
        for o in result["data_schema"].schema[CONF_PROXY_SOURCE].config["options"]
    }
    assert offered == {near.source, OTHER}


async def test_reconfigure_marks_the_current_proxy_without_its_own_link(
    hass, fake_bt
) -> None:
    """The released link and this heater's own entry never count against it."""
    current = fake_bt.add(named(PROXY, "Hall", rssi=-80))
    other = fake_bt.add(named(OTHER, "Bedroom", rssi=-50))
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # The slot of the just-released link may still read as used.
    current.scanner.free = 1
    current.scanner.allocated = [ADDRESS, "11:22:33:44:55:66"]

    result = await entry.start_reconfigure_flow(hass)

    assert offered(result) == [
        (current.source, "Hall · current"),
        (other.source, "Bedroom"),
    ]
    assert result["data_schema"]({})[CONF_PROXY_SOURCE] == PROXY
    hass.config_entries.flow.async_abort(result["flow_id"])
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED


async def test_proxy_labels_use_the_device_name_never_the_mac(hass, fake_bt) -> None:
    """The ESPHome device name (user's rename first), else the scanner name."""
    renamed = fake_bt.add(named("AA:00:00:00:00:01", "x"))
    device = dr.async_get(hass).async_get(fake_bt.device_id(renamed))
    dr.async_get(hass).async_update_device(
        device.id, name="living-room-proxy", name_by_user="Living Room Proxy"
    )
    plain = fake_bt.add(named("AA:00:00:00:00:02", "x"))
    device = dr.async_get(hass).async_get(fake_bt.device_id(plain))
    dr.async_get(hass).async_update_device(device.id, name="Hall Proxy")
    # A nameless ESPHome device: the scanner name, without its MAC.
    nameless = fake_bt.add(
        named("AA:00:00:00:00:03", "garage-proxy (AA:00:00:00:00:03)")
    )
    device = dr.async_get(hass).async_get(fake_bt.device_id(nameless))
    dr.async_get(hass).async_update_device(device.id, name=None)
    missing = fake_bt.add(FakeProxy("AA:00:00:00:00:04", responder=False))
    missing.scanner.name = "kitchen-proxy (AA:00:00:00:00:04)"

    result = await proxy_form(hass)

    # Equal proxies are ordered by the name the user sees.
    assert [label for _, label in offered(result)] == [
        "Hall Proxy · Recommended",
        "Living Room Proxy",
        "garage-proxy",
    ]
    assert result["description_placeholders"]["proxy_notes"] == (
        "\n\nNot shown: kitchen-proxy (missing Dimplex firmware or offline)."
    )
    hass.config_entries.flow.async_abort(result["flow_id"])


@pytest.mark.parametrize(
    ("free", "allocated", "current", "recommended", "label"),
    [
        (0, ["1", "2", "3"], True, True, "Hall · ⚠ full"),
        (1, ["1", "2"], True, True, "Hall · ⚠ already serves 2 heaters"),
        (2, ["1"], True, True, "Hall · current"),
        (2, ["1"], False, True, "Hall · Recommended"),
        (None, [], False, False, "Hall"),
    ],
)
def test_proxy_label_shows_at_most_one_tag_by_priority(
    free, allocated, current, recommended, label
) -> None:
    candidate = config_flow._Candidate(
        source=PROXY,
        name="Hall",
        rssi=-60,
        slots=3,
        free=free,
        connections=len(allocated),
        current=current,
    )
    assert candidate.label(recommended=recommended) == label


async def test_a_scanner_without_esphome_entry_has_no_device_name(
    hass, fake_bt
) -> None:
    assert responder.proxy_name(hass, "hci0") is None


async def test_abandoned_flow_never_clears_another_heaters_pairing(
    hass, fake_bt
) -> None:
    """The proxy has ONE responder state: heater A's flow cleanup must not
    disarm heater B's pairing on the same proxy (RC-2: B's bond would go)."""
    proxy, result = await start(hass, fake_bt)
    await fail_first_pairing(hass, proxy, pair_failure(99))  # A's pairing ended
    proxy.pair_gate = hass.loop.create_future()
    other = HeaterLink(hass, "AA:BB:CC:DD:EE:21", PROXY, PIN)
    b_pairing = hass.async_create_task(other.async_snapshot())
    await settle()
    assert proxy.events[-1] == "pair_start"
    b_armed = len(proxy.actions)

    hass.config_entries.flow.async_abort(result["flow_id"])
    await settle()
    assert proxy.actions[b_armed:] == []  # B's PIN stays armed

    proxy.pair_gate.set_result(None)
    await b_pairing
    await hass.async_block_till_done()
    assert proxy.actions[b_armed:] == [CLEAR]  # B's own clear after its pair()


async def test_flow_removed_while_the_pin_is_submitted_waits_for_that_pairing(
    hass, fake_bt
) -> None:
    proxy, result = await start(hass, fake_bt)
    submit = hass.async_create_task(
        hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSKEY: PIN})
    )
    await settle()
    assert proxy.actions == [TARGET_ONLY, ARM_PIN]  # pair() still waits

    hass.config_entries.flow.async_abort(result["flow_id"])
    await settle()
    assert "disconnect" not in proxy.events
    assert proxy.actions == [TARGET_ONLY, ARM_PIN]  # nothing cleared mid-SMP

    proxy.pair_errors = [pair_failure(99)]
    proxy.pair_gate.set_result(None)
    with suppress(UnknownFlow):
        await submit
    await hass.async_block_till_done()
    assert proxy.events.index("pair_end") < proxy.events.index("disconnect")
    assert proxy.actions == [TARGET_ONLY, ARM_PIN, CLEAR]


async def test_flow_removed_before_a_rejected_pin_starts_no_new_pairing(
    hass, fake_bt
) -> None:
    proxy, result = await start(hass, fake_bt)
    submit = hass.async_create_task(
        hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSKEY: "999999"}
        )
    )
    await settle()
    hass.config_entries.flow.async_abort(result["flow_id"])
    await settle()

    proxy.pair_errors = [pair_failure(81)]
    proxy.pair_gate.set_result(None)
    with suppress(UnknownFlow):
        await submit
    await hass.async_block_till_done()
    assert proxy.events.count("pair_start") == 1
    assert proxy.actions[-1] == CLEAR


async def test_a_chosen_proxy_is_not_surveyed_again(hass, fake_bt) -> None:
    """Architecture review round 6: submitting the choice skips the wait/survey."""
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    proxy.sees_heater = False  # a second survey would now find no proxy

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PROXY_SOURCE: PROXY}
    )

    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert fake_bt.callbacks == []  # no second advertisement wait
    proxy.sees_heater = True
    await advance(hass, result["flow_id"])
    await hass.async_block_till_done()
