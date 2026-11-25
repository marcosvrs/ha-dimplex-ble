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
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    CONF_SYNC_CLOCK,
    DOMAIN,
    UPDATE_INTERVAL,
)
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
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


async def drop_link(hass, entry, proxy: FakeProxy, *, silent: bool = True) -> None:
    """The heater drops the link during an update (supervision timeout).

    Like AM1382 on 2026-11-25: the loss surfaces as a GATT error mid-read.
    """
    from conftest import gatt_failure

    proxy.read_error = gatt_failure(133)
    proxy.sees_heater = not silent
    await entry.runtime_data.async_refresh()
    proxy.read_error = None


# --- Feature A: stalled-heater repair --------------------------------------


async def test_abnormal_drop_and_silence_raise_the_repair_once(
    hass, fake_bt, caplog
) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    caplog.set_level(logging.INFO, "custom_components.dimplex_ble")

    await drop_link(hass, entry, proxy)
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

    await drop_link(hass, entry, proxy)
    await advance(hass, 5)
    fake_bt.advertise(OTHER)  # another proxy hears it: the radio is alive
    await advance(hass, STALL_WINDOW + 5)

    assert issue(hass, entry) is None


async def test_successful_snapshot_clears_the_repair(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    await drop_link(hass, entry, proxy)
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
    await drop_link(hass, entry, proxy)

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
        await drop_link(hass, entry, proxy)
        assert coordinator.stall._cancel_timer is None
    finally:
        hass.set_state(CoreState.running)


async def test_proxy_offline_at_drop_raises_nothing(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)

    proxy.runtime.available = False  # ESPHome API down: a proxy problem
    await drop_link(hass, entry, proxy)
    await advance(hass, STALL_WINDOW + 1)

    assert issue(hass, entry) is None


async def test_proxy_reconnecting_within_the_window_raises_nothing(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)

    await drop_link(hass, entry, proxy)
    proxy.reconnect_api()  # the proxy rebooted: new scanner object
    await advance(hass, STALL_WINDOW + 1)

    assert issue(hass, entry) is None


async def test_removing_the_entry_removes_its_repair(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    await drop_link(hass, entry, proxy)
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


async def test_shared_settings_apply_to_every_heater(hass, fake_bt) -> None:
    """Clock sync and Boost length live in the Dimplex entry; heaters reload."""
    from conftest import add_settings

    fake_bt.add()
    settings = add_settings(hass)
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    assert coordinator.update_interval == UPDATE_INTERVAL
    assert coordinator.link.sync_clock  # the default
    assert not entry.supports_options

    result = await hass.config_entries.options.async_init(settings.entry_id)
    assert result["type"] is FlowResultType.FORM
    defaults = result["data_schema"]({})
    assert (defaults[CONF_SYNC_CLOCK], defaults[CONF_BOOST_MINUTES]) == (True, 60)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SYNC_CLOCK: False, CONF_BOOST_MINUTES: 30.0}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert settings.options == {CONF_SYNC_CLOCK: False, CONF_BOOST_MINUTES: 30}
    reloaded: DimplexCoordinator = entry.runtime_data
    assert reloaded is not coordinator
    assert not reloaded.link.sync_clock


async def test_per_poll_refresh_connects_pairs_reads_then_disconnects(
    hass, fake_bt, caplog
) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    caplog.set_level(logging.DEBUG, "custom_components.dimplex_ble")
    coordinator = await setup(hass, entry)

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
    await advance(hass, 11)  # nothing before the next update
    assert proxy.events == []
    await advance(hass, UPDATE_INTERVAL.total_seconds() + 1)
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


SOUND_READ = ("read", "00000005-0000-1000-8000-00805f9b34fb")


async def test_per_poll_sound_due_is_read_inside_the_poll_connection(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    proxy.values[(2, 0x0005)] = b"\x01\x00\x00"
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)

    # Setup: one connection carried the snapshot and the sound read.
    assert kinds(proxy.events).count("connect") == 1
    assert proxy.events.count(SOUND_READ) == 1
    assert coordinator.settings.get(SOUND) == b"\x01\x00\x00"
    assert proxy.events.index(SOUND_READ) < len(proxy.events) - 1
    assert proxy.events[-1] == "disconnect"

    # Sound not due: later polls do not read it.
    proxy.events.clear()
    await advance(hass, UPDATE_INTERVAL.total_seconds() + 1)
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
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)

    assert coordinator.last_update_success
    assert coordinator.settings.get(SOUND) is None
    assert kinds(proxy.events).count("connect") == 1
    assert proxy.events[-1] == "disconnect"
    # Nothing was confirmed, so the next poll reads the settings again.
    assert coordinator._settings_due


async def test_per_poll_commands_connect_and_disconnect(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
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


def per_poll_link(hass) -> HeaterLink:
    return HeaterLink(hass, ADDRESS, PROXY, PIN)


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
    assert kinds(proxy.events).count("connect") == 1  # read and write: one link
    assert coordinator.settings[SETBACK] == b"\x01\x0a"


async def test_per_poll_releases_the_link_after_a_refused_write(hass, fake_bt) -> None:
    """Review round 7: a non-GATT failure must not keep the proxy slot."""
    from custom_components.dimplex_ble.errors import CharacteristicError
    from custom_components.dimplex_ble.protocol import SETBACK

    proxy = fake_bt.add()
    coordinator = await setup(hass, make_entry(hass))
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
    coordinator = await setup(hass, make_entry(hass))
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


async def test_a_read_failure_then_silence_raises_the_repair(hass, fake_bt) -> None:
    """AM1382 2026-11-25 08:19: the timeout surfaced as a GATT read error first."""
    from conftest import gatt_failure

    proxy = fake_bt.add()
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    proxy.read_error = gatt_failure(133)
    proxy.sees_heater = False

    await coordinator.async_refresh()
    await advance(hass, STALL_WINDOW + 1)

    assert issue(hass, entry) is not None


async def test_a_read_failure_on_a_heater_still_heard_raises_nothing(
    hass, fake_bt
) -> None:
    from conftest import gatt_failure

    proxy = fake_bt.add()
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    proxy.read_error = gatt_failure(133)

    await coordinator.async_refresh()
    proxy.read_error = None
    fake_bt.advertise(PROXY)
    await advance(hass, STALL_WINDOW + 1)

    assert issue(hass, entry) is None


@pytest.mark.parametrize("failing", ["settings", "schedules"])
async def test_a_link_lost_in_a_slow_read_is_not_heard(
    hass, fake_bt, monkeypatch, failing
) -> None:
    """Codex review: the earlier snapshot must not clear the stall window."""
    from conftest import gatt_failure

    from custom_components.dimplex_ble import link as link_module

    proxy = fake_bt.add()
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    coordinator.async_settings_due()
    coordinator.async_schedules_due()

    async def lose_link(*args, **kwargs):
        proxy.clients[-1].is_connected = False  # supervision timeout
        proxy.sees_heater = False
        raise gatt_failure(133)

    if failing == "settings":
        monkeypatch.setattr(link_module, "_read_optional", _settings_breaker(lose_link))
    else:
        monkeypatch.setattr(link_module, "_read_schedules", lose_link)

    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    await advance(hass, STALL_WINDOW + 1)

    assert issue(hass, entry) is not None


def _settings_breaker(lose_link):
    """Fail the first slow-setting read (Sound), not the snapshot reads."""
    from custom_components.dimplex_ble import link as link_module
    from custom_components.dimplex_ble.protocol import SOUND

    real = link_module._read_optional

    async def read(client, address):
        if address == SOUND:
            return await lose_link()
        return await real(client, address)

    return read


async def test_a_heater_silent_between_updates_raises_the_repair(hass, fake_bt) -> None:
    """No link between updates: silence is noticed as 'proxy does not see it'.

    Polls keep finding it unseen; the window they start is not postponed.
    """
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    proxy.sees_heater = False  # stalled while no update was running

    await advance(hass, UPDATE_INTERVAL.total_seconds() + 1)  # first unseen poll
    assert issue(hass, entry) is None
    await advance(hass, UPDATE_INTERVAL.total_seconds() + STALL_WINDOW + 2)

    assert issue(hass, entry) is not None


async def test_a_link_dropped_by_the_heater_mid_update_raises_the_repair(
    hass, fake_bt
) -> None:
    """bleak-esphome reports the link-down while our read still waits."""
    from conftest import gatt_failure

    proxy = fake_bt.add()
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    proxy.read_gate = hass.loop.create_future()
    refresh = hass.async_create_task(coordinator.async_refresh())
    await asyncio.sleep(0.01)

    client = proxy.clients[-1]
    client.is_connected = False
    proxy.sees_heater = False
    client.disconnected_callback()  # supervision timeout, error=8
    proxy.read_error = gatt_failure(133)
    proxy.read_gate.set_result(None)
    await refresh
    await advance(hass, STALL_WINDOW + 1)

    assert issue(hass, entry) is not None


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_a_heater_silent_through_setup_retries_raises_the_repair(
    hass, fake_bt, monkeypatch
) -> None:
    """AM6532 2026-11-25 12:23: stalled in the first update after a restart.

    Every setup retry builds a new monitor; the silence start must survive.
    """
    from custom_components.dimplex_ble import stall

    clock = _Clock()
    monkeypatch.setattr(stall, "monotonic", clock)
    proxy = fake_bt.add()
    proxy.sees_heater = False
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert issue(hass, entry) is None

    clock.now += STALL_WINDOW + 1
    await advance(hass, 700)  # the next setup retry runs

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert issue(hass, entry) is not None


async def test_a_heater_back_before_the_window_ends_raises_nothing(
    hass, fake_bt, monkeypatch
) -> None:
    """The next retry finds the heater: setup loads and the silence is over."""
    from custom_components.dimplex_ble import stall

    clock = _Clock()
    monkeypatch.setattr(stall, "monotonic", clock)
    proxy = fake_bt.add()
    proxy.sees_heater = False
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    proxy.sees_heater = True  # power-cycled within the minute
    await hass.config_entries.async_reload(entry.entry_id)  # the next retry
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert not stall.silent_since(hass)

    proxy.sees_heater = False  # a later, separate silence starts afresh
    await entry.runtime_data.async_refresh()
    clock.now += STALL_WINDOW - 1
    await advance(hass, 1)
    assert issue(hass, entry) is None
    assert stall.silent_since(hass)


@pytest.mark.parametrize("gap", ["heard", "proxy_rebooted"])
async def test_an_interrupted_silence_between_retries_starts_afresh(
    hass, fake_bt, monkeypatch, gap
) -> None:
    """Codex review: nothing listens between retries; the carried start must
    not survive a sighting or a proxy reboot in that gap."""
    from custom_components.dimplex_ble import stall

    clock = _Clock()
    monkeypatch.setattr(stall, "monotonic", clock)
    proxy = fake_bt.add()
    proxy.sees_heater = False
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    clock.now += 30
    if gap == "heard":
        fake_bt.advertise(PROXY)  # no monitor is listening now
        proxy.sees_heater = False
    else:
        proxy.reconnect_api()
    clock.now += STALL_WINDOW - 20  # 70 s since the first start, 40 s since the gap
    await advance(hass, 700)

    assert issue(hass, entry) is None


async def test_a_sighting_long_before_the_retry_counts_from_the_sighting(
    hass, fake_bt, monkeypatch
) -> None:
    """Codex review: silence is measured from the last sighting, not the retry."""
    from custom_components.dimplex_ble import stall

    clock = _Clock()
    monkeypatch.setattr(stall, "monotonic", clock)
    proxy = fake_bt.add()
    proxy.sees_heater = False
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    clock.now += 10
    fake_bt.advertise(PROXY)  # heard once in the gap, then silent again
    proxy.sees_heater = False
    clock.now += STALL_WINDOW + 5  # 65 s after that sighting
    await advance(hass, 700)

    assert issue(hass, entry) is not None


async def test_a_weaker_proxy_hearing_the_heater_counts_as_a_sighting(
    hass, fake_bt, monkeypatch
) -> None:
    """Codex review: the newest sighting on any scanner, not the preferred one."""
    from custom_components.dimplex_ble import stall

    clock = _Clock()
    monkeypatch.setattr(stall, "monotonic", clock)
    proxy = fake_bt.add()
    other = fake_bt.add(FakeProxy(OTHER))
    proxy.sees_heater = other.sees_heater = False
    proxy.scanner.discovered_device_timestamps[ADDRESS] = clock.now - 100  # stale
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    clock.now += 30
    fake_bt.advertise(OTHER)  # only the far proxy hears it, in the gap
    other.sees_heater = False
    clock.now += 40  # 70 s since the start, 40 s since that sighting
    await advance(hass, 700)

    assert issue(hass, entry) is None


async def test_a_sighting_between_retries_clears_a_shown_repair(
    hass, fake_bt, monkeypatch
) -> None:
    """Codex review: a repair from before must not outlive a gap sighting."""
    from custom_components.dimplex_ble import stall

    clock = _Clock()
    monkeypatch.setattr(stall, "monotonic", clock)
    proxy = fake_bt.add()
    proxy.sees_heater = False
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    clock.now += STALL_WINDOW + 1
    await advance(hass, 700)
    assert issue(hass, entry) is not None

    fake_bt.advertise(PROXY)  # heard in the gap, setup still fails after
    proxy.sees_heater = False
    clock.now += 5
    await advance(hass, 1400)

    assert issue(hass, entry) is None


async def test_a_sighting_after_a_proxy_reconnect_clears_a_shown_repair(
    hass, fake_bt, monkeypatch
) -> None:
    """Codex review round 4: the scanner changed and the heater was heard."""
    from custom_components.dimplex_ble import stall

    clock = _Clock()
    monkeypatch.setattr(stall, "monotonic", clock)
    proxy = fake_bt.add()
    proxy.sees_heater = False
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    clock.now += STALL_WINDOW + 1
    await advance(hass, 700)
    assert issue(hass, entry) is not None

    proxy.reconnect_api()  # new scanner object
    clock.now += 1
    fake_bt.advertise(PROXY)  # heard in the gap
    proxy.sees_heater = False
    clock.now += 5
    await advance(hass, 1400)

    assert issue(hass, entry) is None


async def test_a_sighting_before_a_proxy_reconnect_still_counts(
    hass, fake_bt, monkeypatch
) -> None:
    """Codex review round 5: heard on the old scanner, then the proxy reconnects."""
    from custom_components.dimplex_ble import stall

    clock = _Clock()
    monkeypatch.setattr(stall, "monotonic", clock)
    proxy = fake_bt.add()
    proxy.sees_heater = False
    entry = make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    clock.now += STALL_WINDOW + 1
    await advance(hass, 700)
    assert issue(hass, entry) is not None

    clock.now += 1
    fake_bt.advertise(PROXY)  # heard on the old scanner
    proxy.sees_heater = False
    proxy.reconnect_api()  # then the proxy reconnects
    clock.now += 5
    await advance(hass, 1400)

    assert issue(hass, entry) is None
