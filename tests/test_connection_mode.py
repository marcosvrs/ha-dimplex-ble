"""Stalled-heater repair and the per-poll connection mode against a fake proxy."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest
from aioesphomeapi.api_pb2 import ExecuteServiceArgument, ExecuteServiceRequest
from conftest import ADDRESS, PIN, PROXY, FakeProxy
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.dimplex_ble import async_remove_entry
from custom_components.dimplex_ble.const import (
    CONF_ADDRESS,
    CONF_BOOST_MINUTES,
    CONF_KEEP_CONNECTED,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    CONF_SYNC_CLOCK,
    DOMAIN,
    POLL_INTERVAL_PER_POLL,
)
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.dimplex_ble.link import HeaterLink
from custom_components.dimplex_ble.protocol import SOUND
from custom_components.dimplex_ble.stall import STALL_WINDOW, StallMonitor

pytestmark = pytest.mark.usefixtures("skip_dependencies")

OTHER = "14:C1:9F:22:4C:DA"


def make_entry(hass, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Panel Heater AM6532",
        unique_id=ADDRESS.lower(),
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY, CONF_PASSKEY: PIN},
        options=options,
    )
    entry.add_to_hass(hass)
    return entry


async def setup(hass, entry) -> DimplexCoordinator:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


async def advance(hass, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def issue(hass, entry):
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"heater_stalled_{entry.entry_id}"
    )


def kinds(events):
    return [e if isinstance(e, str) else e[0] for e in events]


def drop_link(proxy: FakeProxy, *, silent: bool = True) -> None:
    """The heater ends the held link (supervision timeout), not us."""
    client = proxy.clients[-1]
    client.is_connected = False
    proxy.sees_heater = not silent
    client.disconnected_callback()


# --- Feature A: stalled-heater repair --------------------------------------


async def test_abnormal_drop_and_silence_raise_the_repair_once(
    hass, fake_bt, caplog
) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    caplog.set_level(logging.INFO, "custom_components.dimplex_ble")

    drop_link(proxy)
    await advance(hass, STALL_WINDOW - 1)
    assert issue(hass, entry) is None

    await advance(hass, STALL_WINDOW + 1)
    found = issue(hass, entry)
    assert found is not None
    assert found.severity is ir.IssueSeverity.WARNING
    assert found.is_fixable is False
    assert found.translation_key == "heater_stalled"
    assert found.translation_placeholders == {"name": entry.title}
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name.startswith("custom_components")
    ]
    assert len(warnings) == 1
    assert ADDRESS in warnings[0].getMessage()

    # The user power-cycles the heater: any scanner hearing it clears the repair.
    fake_bt.advertise(OTHER)
    await hass.async_block_till_done()
    assert issue(hass, entry) is None
    assert any(
        r.levelno == logging.INFO and "responds again" in r.getMessage()
        for r in caplog.records
    )


async def test_advertisement_within_the_window_raises_nothing(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    fake_bt.add(FakeProxy(OTHER))
    entry = make_entry(hass)
    await setup(hass, entry)

    drop_link(proxy)
    await advance(hass, 5)
    fake_bt.advertise(OTHER)  # another proxy hears it: the radio is alive
    await advance(hass, STALL_WINDOW + 5)

    assert issue(hass, entry) is None


async def test_successful_snapshot_clears_the_repair(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    drop_link(proxy)
    await advance(hass, STALL_WINDOW + 1)
    assert issue(hass, entry) is not None

    proxy.sees_heater = True  # route back, no advertisement callback involved
    await advance(hass, STALL_WINDOW + 15)

    assert issue(hass, entry) is None


async def test_our_disconnects_never_raise_the_repair(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    client = proxy.clients[0]

    # bleak-esphome calls back for our own disconnect too.
    await coordinator.link.async_close()
    proxy.sees_heater = False
    client.disconnected_callback()
    await advance(hass, STALL_WINDOW + 1)
    assert issue(hass, entry) is None

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_cancels_a_pending_window(hass, fake_bt) -> None:
    """No timer survives the entry (the harness fails on lingering timers)."""
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    drop_link(proxy)

    assert await hass.config_entries.async_unload(entry.entry_id)
    proxy.clients[0].disconnected_callback()  # a late link-down after unload
    await advance(hass, STALL_WINDOW + 1)

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert issue(hass, entry) is None


async def test_drop_while_home_assistant_stops_raises_nothing(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    hass.set_state(CoreState.stopping)
    try:
        drop_link(proxy)
        assert coordinator.stall._cancel_timer is None
    finally:
        hass.set_state(CoreState.running)


async def test_proxy_offline_at_drop_raises_nothing(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)

    proxy.runtime.available = False  # ESPHome API down: a proxy problem
    drop_link(proxy)
    await advance(hass, STALL_WINDOW + 1)

    assert issue(hass, entry) is None


async def test_proxy_reconnecting_within_the_window_raises_nothing(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)

    drop_link(proxy)
    proxy.reconnect_api()  # the proxy rebooted: new scanner object
    await advance(hass, STALL_WINDOW + 1)

    assert issue(hass, entry) is None


async def test_removing_the_entry_removes_its_repair(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    drop_link(proxy)
    await advance(hass, STALL_WINDOW + 1)
    assert issue(hass, entry) is not None

    await async_remove_entry(hass, entry)

    assert issue(hass, entry) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_second_window_never_raises_twice(hass, fake_bt, caplog) -> None:
    proxy = fake_bt.add()
    monitor = StallMonitor(hass, "entry", "Heater", ADDRESS, PROXY)
    caplog.set_level(logging.WARNING, "custom_components.dimplex_ble")

    monitor.async_link_lost(proxy.scanner)
    await advance(hass, STALL_WINDOW + 1)
    monitor.async_link_lost(proxy.scanner)
    monitor.async_link_lost(proxy.scanner)  # restarts, never stacks timers
    await advance(hass, 2 * STALL_WINDOW + 2)

    assert ir.async_get(hass).async_get_issue(DOMAIN, "heater_stalled_entry")
    assert (
        len([r for r in caplog.records if r.name.startswith("custom_components")]) == 1
    )
    monitor.async_stop()
    monitor.async_link_lost(proxy.scanner)  # stopped: ignored
    assert monitor._cancel_timer is None
    monitor.async_heard()
    assert not ir.async_get(hass).async_get_issue(DOMAIN, "heater_stalled_entry")


# --- Feature B: connection mode option -------------------------------------


async def test_options_flow_switches_the_mode_and_reloads(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    assert coordinator.link.keep_connected
    assert coordinator.update_interval == timedelta(seconds=10)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"]({})
    assert schema == {
        CONF_KEEP_CONNECTED: True,
        CONF_SYNC_CLOCK: True,
        CONF_BOOST_MINUTES: 60,
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_KEEP_CONNECTED: False, CONF_SYNC_CLOCK: False, CONF_BOOST_MINUTES: 30.0},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_KEEP_CONNECTED: False,
        CONF_SYNC_CLOCK: False,
        CONF_BOOST_MINUTES: 30,
    }
    reloaded: DimplexCoordinator = entry.runtime_data
    assert reloaded is not coordinator
    assert not reloaded.link.keep_connected
    assert reloaded.update_interval == timedelta(seconds=POLL_INTERVAL_PER_POLL)
    assert proxy.events[-1] == "disconnect"  # the reloaded entry ended its poll
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["data_schema"]({}) == {
        CONF_KEEP_CONNECTED: False,
        CONF_SYNC_CLOCK: False,
        CONF_BOOST_MINUTES: 30,
    }
    assert not reloaded.link.sync_clock


async def test_per_poll_refresh_connects_pairs_reads_then_disconnects(
    hass, fake_bt, caplog
) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass, **{CONF_KEEP_CONNECTED: False})
    caplog.set_level(logging.DEBUG, "custom_components.dimplex_ble")
    coordinator = await setup(hass, entry)

    assert f"MODE per_poll {ADDRESS}" in caplog.text
    cycle = kinds(proxy.events)
    first_read = cycle.index("read")
    assert cycle[:first_read] == [
        "action",
        "connect",
        "pair_start",
        "pair_end",
        "action",
    ]
    # The first poll reads the due sound value inside its own connection.
    assert cycle.count("connect") == cycle.count("disconnect") == 1
    assert not coordinator.link.connected

    proxy.events.clear()
    await advance(hass, 11)  # the persistent interval does not apply
    assert proxy.events == []
    await advance(hass, POLL_INTERVAL_PER_POLL + 1)
    assert kinds(proxy.events)[:5] == [
        "action",
        "connect",
        "pair_start",
        "pair_end",
        "action",
    ]
    assert kinds(proxy.events)[-1] == "disconnect"
    assert len(proxy.clients) == 2  # setup poll, this poll
    assert not coordinator.link.connected
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["link"]["mode"] == "per_poll"


SOUND_READ = ("read", "00000005-0000-1000-8000-00805f9b34fb")


async def test_per_poll_sound_due_is_read_inside_the_poll_connection(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    proxy.values[(2, 0x0005)] = b"\x01\x00\x00"
    entry = make_entry(hass, **{CONF_KEEP_CONNECTED: False})
    coordinator = await setup(hass, entry)

    # Setup: one connection carried the snapshot and the sound read.
    assert kinds(proxy.events).count("connect") == 1
    assert proxy.events.count(SOUND_READ) == 1
    assert coordinator.settings.get(SOUND) == b"\x01\x00\x00"
    assert proxy.events.index(SOUND_READ) < len(proxy.events) - 1
    assert proxy.events[-1] == "disconnect"

    # Sound not due: later polls do not read it.
    proxy.events.clear()
    await advance(hass, POLL_INTERVAL_PER_POLL + 1)
    assert kinds(proxy.events).count("connect") == 1
    assert SOUND_READ not in proxy.events

    # The slow schedule only marks it due; no connection of its own.
    proxy.events.clear()
    proxy.values[(2, 0x0005)] = b"\x00\x00\x00"
    coordinator.async_settings_due()
    assert proxy.events == []
    assert coordinator.settings.get(SOUND) == b"\x01\x00\x00"

    # The next poll reads it inside its single connection.
    await coordinator.async_refresh()
    assert kinds(proxy.events).count("connect") == 1
    assert proxy.events.count(SOUND_READ) == 1
    assert proxy.events[-1] == "disconnect"
    assert coordinator.settings.get(SOUND) == b"\x00\x00\x00"

    # And only once.
    proxy.events.clear()
    await coordinator.async_refresh()
    assert SOUND_READ not in proxy.events


async def test_per_poll_sound_failure_keeps_the_snapshot(
    hass, fake_bt, monkeypatch
) -> None:
    from conftest import gatt_failure

    from custom_components.dimplex_ble import link as link_module
    from custom_components.dimplex_ble.protocol import SOUND

    real = link_module._read_optional

    async def read(client, address):
        if address == SOUND:
            raise gatt_failure(1)
        return await real(client, address)

    monkeypatch.setattr(link_module, "_read_optional", read)
    proxy = fake_bt.add()
    entry = make_entry(hass, **{CONF_KEEP_CONNECTED: False})
    coordinator = await setup(hass, entry)

    assert coordinator.last_update_success
    assert coordinator.settings.get(SOUND) is None
    assert kinds(proxy.events).count("connect") == 1
    assert proxy.events[-1] == "disconnect"
    # Nothing was confirmed, so the next poll reads the settings again.
    assert coordinator._settings_due


async def test_per_poll_commands_connect_and_disconnect(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass, **{CONF_KEEP_CONNECTED: False})
    coordinator = await setup(hass, entry)
    proxy.events.clear()

    await coordinator.async_write((0, 0x1005), b"\x01", refresh=False)

    assert kinds(proxy.events) == [
        "action",
        "connect",
        "pair_start",
        "pair_end",
        "action",
        "write",
        "disconnect",
    ]
    assert not coordinator.link.connected


async def test_persistent_mode_keeps_the_link(hass, fake_bt, caplog) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass, **{CONF_KEEP_CONNECTED: True})
    caplog.set_level(logging.DEBUG, "custom_components.dimplex_ble")
    coordinator = await setup(hass, entry)

    await advance(hass, 11)

    assert f"MODE persistent {ADDRESS}" in caplog.text
    assert kinds(proxy.events).count("connect") == 1
    assert "disconnect" not in proxy.events
    assert coordinator.link.connected


def per_poll_link(hass) -> HeaterLink:
    return HeaterLink(hass, ADDRESS, PROXY, PIN, keep_connected=False)


async def test_per_poll_cancel_during_connect_waits_for_connect(hass, fake_bt) -> None:
    """RC-3: the forming link runs to its end before the clean disconnect."""
    proxy = fake_bt.add()
    proxy.connect_gate = hass.loop.create_future()
    task = hass.async_create_task(per_poll_link(hass).async_snapshot())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert kinds(proxy.events) == ["action", "connect"]

    proxy.connect_gate.set_result(None)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert kinds(proxy.events) == ["action", "connect", "disconnect", "action"]


async def test_per_poll_cancel_during_pair_waits_for_pair(hass, fake_bt) -> None:
    """RC-2: never disconnect while SMP runs; the bond would be deleted."""
    proxy = fake_bt.add()
    proxy.pair_gate = hass.loop.create_future()
    task = hass.async_create_task(per_poll_link(hass).async_snapshot())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert "disconnect" not in proxy.events

    proxy.pair_gate.set_result(None)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert kinds(proxy.events)[-3:] == ["pair_end", "disconnect", "action"]
    assert "read" not in kinds(proxy.events)


async def test_per_poll_disconnect_is_ours_not_a_stall(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    link = per_poll_link(hass)
    lost = []
    link.on_link_lost = lost.append

    await link.async_snapshot()
    proxy.clients[0].disconnected_callback()  # the callback for our disconnect

    assert lost == []
    assert proxy.events[-1] == "disconnect"


# --- Review round 2 ------------------------------------------------------------


def make_entry_without_pin(hass) -> MockConfigEntry:
    """Set up on a proxy that already held the bond: no PIN was stored."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Panel Heater AM6532",
        unique_id=ADDRESS.lower(),
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    entry.add_to_hass(hass)
    return entry


async def test_a_pin_request_without_a_stored_pin_asks_the_user(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry_without_pin(hass)
    await setup(hass, entry)

    fake_bt.passkey_request(proxy, address="AA:BB:CC:DD:EE:99")  # another heater
    fake_bt.passkey_request(proxy, device_id="unknown-device")  # not our proxy
    await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    fake_bt.passkey_request(proxy)
    await hass.async_block_till_done()
    [flow] = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flow["context"]["source"] == "reauth"


async def test_a_stored_pin_answers_by_itself(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    await setup(hass, make_entry(hass))

    fake_bt.passkey_request(proxy)
    await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_a_repair_raised_before_a_reload_is_cleared_when_heard(
    hass, fake_bt
) -> None:
    fake_bt.add()
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    ir.async_create_issue(
        hass,
        DOMAIN,
        coordinator.stall.issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="heater_stalled",
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    entry.runtime_data.stall.async_heard()

    assert issue(hass, entry) is None


# --- Review round 3 ------------------------------------------------------------


async def test_the_pin_never_reaches_the_esphome_debug_log(
    hass, fake_bt, caplog
) -> None:
    fake_bt.add()
    await setup(hass, make_entry(hass))  # arms the responder with the PIN
    logger = logging.getLogger("aioesphomeapi.connection")
    caplog.set_level(logging.DEBUG, "aioesphomeapi.connection")

    # As aioesphomeapi logs it on Linux: the protobuf request object.
    logger.debug(
        "%s: Sending %s: %s",
        "proxy",
        "ExecuteServiceRequest",
        ExecuteServiceRequest(
            key=0,
            args=[
                ExecuteServiceArgument(string_=ADDRESS),
                ExecuteServiceArgument(int_=int(PIN)),
            ],
        ),
    )
    # On Windows it is converted with MessageToDict first.
    logger.debug(
        "%s: Sending %s: %s",
        "proxy",
        "ExecuteServiceRequest",
        {"key": 0, "args": [{"string": ADDRESS}, {"int": int(PIN)}]},
    )
    logger.debug(
        "%s: Sending %s: %s",
        "proxy",
        "ExecuteServiceRequest",
        {"key": 7, "args": [{"int": 42}]},
    )

    assert PIN not in caplog.text
    assert caplog.text.count("**REDACTED**") == 2
    assert "42" in caplog.text  # other actions are logged unchanged


async def test_setback_keeps_the_heater_temperature_in_one_operation(
    hass, fake_bt
) -> None:
    from custom_components.dimplex_ble.protocol import SETBACK

    proxy = fake_bt.add()
    proxy.services.add(SETBACK)
    proxy.values[SETBACK] = b"\x00\x0a"  # 10 degC, set on the heater's menu
    coordinator = await setup(hass, make_entry(hass))
    coordinator.settings[SETBACK] = b"\x00\x08"  # cached before the change
    proxy.events.clear()

    await coordinator.async_set_setback(True)

    assert [e[2] for e in proxy.events if e[0] == "write"][-1] == b"\x01\x0a"
    assert kinds(proxy.events).count("connect") == 0  # over the held link
    assert coordinator.settings[SETBACK] == b"\x01\x0a"


async def test_per_poll_releases_the_link_after_a_refused_write(hass, fake_bt) -> None:
    """Review round 7: a non-GATT failure must not keep the proxy slot."""
    from custom_components.dimplex_ble.errors import CharacteristicError
    from custom_components.dimplex_ble.protocol import SETBACK

    proxy = fake_bt.add()
    coordinator = await setup(hass, make_entry(hass, **{CONF_KEEP_CONNECTED: False}))
    proxy.events.clear()

    with pytest.raises(CharacteristicError):
        await coordinator.link.async_write(SETBACK, b"\x01\x08")  # not on this heater

    assert "connect" in kinds(proxy.events)
    assert kinds(proxy.events).count("disconnect") == 1
    assert not coordinator.link.connected


async def test_per_poll_leaves_away_and_sets_the_mode_in_one_connection(
    hass, fake_bt
) -> None:
    """Review round 10: two connections would be 10 s apart (settle)."""
    from custom_components.dimplex_ble.protocol import AWAY_MODE

    proxy = fake_bt.add()
    proxy.services.add(AWAY_MODE)
    proxy.values[AWAY_MODE] = bytes((1, 26, 11, 26, 30, 18, 7))  # Away on
    coordinator = await setup(hass, make_entry(hass, **{CONF_KEEP_CONNECTED: False}))
    proxy.events.clear()

    await coordinator.async_set_heating_mode(bytes((2, 0, 0, 0, 0, 0)))

    writes = [e[1][4:8] for e in proxy.events if e[0] == "write"]
    assert writes[writes.index("102a") + 1] == "1001"
    first_poll = proxy.events.index("disconnect")
    assert kinds(proxy.events[:first_poll]).count("connect") == 1
    assert "102a" in [e[1][4:8] for e in proxy.events[:first_poll] if e[0] == "write"]
    assert "1001" in [e[1][4:8] for e in proxy.events[:first_poll] if e[0] == "write"]


async def test_starting_away_keeps_the_temperature_set_on_the_heater(
    hass, fake_bt
) -> None:
    """Codex review: start Away from the live record, not the last poll."""
    from custom_components.dimplex_ble.protocol import AWAY_MODE

    proxy = fake_bt.add()
    proxy.services.add(AWAY_MODE)
    proxy.values[AWAY_MODE] = bytes((0, 26, 11, 26, 30, 18, 12))  # Away off, 12 C
    coordinator = await setup(hass, make_entry(hass))
    proxy.values[AWAY_MODE] = bytes((0, 26, 11, 26, 30, 18, 15))  # changed: 15 C
    proxy.events.clear()

    await coordinator.async_set_away_mode(
        until=dt_util.now().replace(tzinfo=None) + timedelta(hours=13)
    )

    [away] = [e[2] for e in proxy.events if e[0] == "write" and e[1][4:8] == "102a"]
    assert away[0] == 1 and away[6] == 15


async def test_a_malformed_away_record_never_writes_an_unheated_away(
    hass, fake_bt
) -> None:
    """Codex review: an Away target outside 7-18 C falls back to 7 C."""
    from custom_components.dimplex_ble.protocol import AWAY_MODE

    proxy = fake_bt.add()
    proxy.services.add(AWAY_MODE)
    proxy.values[AWAY_MODE] = bytes(7)  # all zeros: target 0 C
    coordinator = await setup(hass, make_entry(hass))
    proxy.events.clear()

    await coordinator.async_set_away_mode(
        until=dt_util.now().replace(tzinfo=None) + timedelta(hours=13)
    )

    [away] = [e[2] for e in proxy.events if e[0] == "write" and e[1][4:8] == "102a"]
    assert away[6] == 7
