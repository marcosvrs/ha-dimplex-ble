"""Contract tests for HeaterLink against a fake ESPHome proxy."""

from __future__ import annotations

import asyncio
import logging

import pytest
from bleak.exc import BleakError
from conftest import ADDRESS, PIN, PROXY, FakeProxy, gatt_failure, pair_failure
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.dimplex_ble.errors import (
    CREDENTIAL_REJECTED_CODES,
    CharacteristicError,
    HeaterAuthError,
    HeaterUnavailableError,
    ResponderMissingError,
    pair_error_code,
    to_homeassistant_error,
)
from custom_components.dimplex_ble.link import CONNECT_TIMEOUT, HeaterLink
from custom_components.dimplex_ble.protocol import (
    DimplexProtocolError,
)
from custom_components.dimplex_ble.responder import async_arm, has_responder

SETPOINT = (0, 0x1023)


def make_link(hass, passkey: str | None = PIN) -> HeaterLink:
    return HeaterLink(hass, ADDRESS.lower(), PROXY, passkey)


def first_connect(proxy):
    return next(e for e in proxy.events if isinstance(e, tuple) and e[0] == "connect")


def kinds(events):
    return [e if isinstance(e, str) else e[0] for e in events]


async def test_connect_then_pair_then_gatt_in_order(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    link = make_link(hass)

    snapshot, _, _ = await link.async_snapshot()

    assert snapshot.model == "QRAD100E"
    assert snapshot.setpoint_c == 21
    first_read = kinds(proxy.events).index("read")
    # The heater starts SMP as soon as the link is up, so the responder is
    # targeted before connect (hardware 2026-11-24 16:13).
    assert kinds(proxy.events)[:first_read] == [
        "action",
        "connect",
        "pair_start",
        "pair_end",
        "action",
    ]
    assert proxy.events[1] == ("connect", False, True)
    assert proxy.actions == [
        ("dimplex_pin_arm", {"address": ADDRESS, "passkey": 12345}),
        ("dimplex_pin_clear", {}),
    ]
    assert proxy.clients[0].timeout == CONNECT_TIMEOUT
    assert link.connected


async def test_live_link_is_reused_and_a_dropped_one_reconnects(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    link = make_link(hass)
    await link.async_snapshot()
    await link.async_snapshot()
    assert kinds(proxy.events).count("connect") == 1

    proxy.clients[0].is_connected = False
    proxy.clients[0].disconnected_callback()
    await link.async_snapshot()

    assert kinds(proxy.events).count("connect") == 2
    assert kinds(proxy.events).count("pair_start") == 2


async def read(link: HeaterLink, address: tuple[int, int]) -> bytes | None:
    """Read one setting the way every poll does."""
    _, values, _ = await link.async_snapshot((address,))
    return values.get(address)


async def test_proxy_api_reconnect_invalidates_the_cached_client(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    link = make_link(hass)
    await link.async_snapshot()

    proxy.reconnect_api()
    proxy.disconnect_error = BleakError("API connection is gone")
    await read(link, (2, 0x0005))

    assert len(proxy.clients) == 2
    assert "disconnect" in proxy.events
    assert kinds(proxy.events).count("pair_start") == 2


async def test_no_advertisement_on_the_pinned_proxy_means_no_connect(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    proxy.sees_heater = False
    other = fake_bt.add(FakeProxy("AA:AA:AA:AA:AA:AA"))
    link = make_link(hass)

    with pytest.raises(HeaterUnavailableError):
        await link.async_snapshot()

    assert proxy.events == [] and other.events == []
    assert link.route() is None


async def test_missing_scanner_means_no_connect(hass, fake_bt) -> None:
    with pytest.raises(HeaterUnavailableError):
        await make_link(hass).async_snapshot()


async def test_connect_failure_is_transient(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.connect_error = TimeoutError()

    with pytest.raises(HeaterUnavailableError):
        await make_link(hass).async_snapshot()

    assert "pair_start" not in proxy.events


@pytest.mark.parametrize("code", sorted(CREDENTIAL_REJECTED_CODES))
async def test_rejected_pin_codes_are_auth_errors(hass, fake_bt, code) -> None:
    proxy = fake_bt.add()
    proxy.pair_errors = [pair_failure(code)]

    with pytest.raises(HeaterAuthError):
        await make_link(hass).async_snapshot()

    assert kinds(proxy.events)[-3:] == ["pair_end", "disconnect", "action"]


@pytest.mark.parametrize(
    "error",
    [
        # Never observed on hardware; only 81 is a confirmed wrong PIN (H8).
        pair_failure(78),
        pair_failure(80),
        pair_failure(88),
        pair_failure(99),
        pair_failure(102),
        TimeoutError(),
        BleakError("BLE device disconnected during pair operation"),
        NotImplementedError("Pairing is not available"),
    ],
)
async def test_other_pair_failures_are_transient(hass, fake_bt, error) -> None:
    proxy = fake_bt.add()
    proxy.pair_errors = [error]

    with pytest.raises(HeaterUnavailableError):
        await make_link(hass).async_snapshot()

    assert kinds(proxy.events)[-2:] == ["disconnect", "action"]
    assert proxy.actions[-1] == ("dimplex_pin_clear", {})


@pytest.mark.parametrize("att", [0x05, 0x0F, 0x0E])
async def test_gatt_errors_after_pairing_are_transient(hass, fake_bt, att) -> None:
    proxy = fake_bt.add()
    proxy.read_error = gatt_failure(att)
    link = make_link(hass)

    with pytest.raises(HeaterUnavailableError) as err:
        await link.async_snapshot()

    assert not isinstance(err.value, HeaterAuthError)
    assert proxy.events[-1] == "disconnect"
    assert not link.connected


async def test_responder_missing_blocks_pairing(hass, fake_bt) -> None:
    proxy = fake_bt.add(FakeProxy(responder=False))

    with pytest.raises(ResponderMissingError):
        await make_link(hass).async_snapshot()

    # Arming precedes connect, so a proxy without the responder is never
    # connected to at all.
    assert "connect" not in kinds(proxy.events)


async def test_proxy_api_down_blocks_pairing(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.api_connected = False

    with pytest.raises(HeaterUnavailableError):
        await make_link(hass).async_snapshot()

    assert "pair_start" not in proxy.events


async def test_no_stored_pin_arms_target_only(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    await make_link(hass, passkey=None).async_pair(None)

    assert proxy.actions[0] == (
        "dimplex_pin_arm",
        {"address": ADDRESS, "passkey": 1_000_000},
    )
    assert first_connect(proxy) == ("connect", False, False)


async def test_cancel_during_pair_never_disconnects_before_pair_ends(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    proxy.pair_gate = hass.loop.create_future()
    link = make_link(hass)
    task = hass.async_create_task(link.async_snapshot())
    await asyncio.sleep(0)
    assert proxy.events[-1] == "pair_start"

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # a second cancel must not abort the wait either
    await asyncio.sleep(0)
    assert "disconnect" not in proxy.events

    proxy.pair_errors = [pair_failure(99)]
    proxy.pair_gate.set_result(None)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert kinds(proxy.events)[-3:] == ["pair_end", "disconnect", "action"]


async def test_cancel_after_pair_success_disconnects(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.pair_gate = hass.loop.create_future()
    task = hass.async_create_task(make_link(hass).async_snapshot())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    proxy.pair_gate.set_result(None)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert "read" not in kinds(proxy.events)
    assert kinds(proxy.events)[-2:] == ["disconnect", "action"]


async def test_cancel_during_gatt_disconnects_and_reraises(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.read_gate = hass.loop.create_future()
    task = hass.async_create_task(make_link(hass).async_snapshot())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert proxy.events[-1] == "disconnect"


async def test_close_waits_for_in_flight_pair_then_closes_once(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.pair_gate = hass.loop.create_future()
    link = make_link(hass)
    poll = hass.async_create_task(link.async_snapshot())
    await asyncio.sleep(0)
    close = hass.async_create_task(link.async_close())
    await asyncio.sleep(0)
    assert "disconnect" not in proxy.events

    proxy.pair_gate.set_result(None)
    await poll
    await close
    await link.async_close()

    assert proxy.events.count("disconnect") == 1
    assert kinds(proxy.events).index("disconnect") > kinds(proxy.events).index("read")
    with pytest.raises(HeaterUnavailableError):
        await link.async_snapshot()


async def test_disconnect_errors_are_swallowed(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.disconnect_error = TimeoutError()
    link = make_link(hass)
    await link.async_snapshot()

    await link.async_close()

    assert proxy.events[-1] == "disconnect"


async def test_snapshot_and_write_are_serialized(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.read_gate = hass.loop.create_future()
    link = make_link(hass)
    snapshot = hass.async_create_task(link.async_snapshot())
    await asyncio.sleep(0)
    write = hass.async_create_task(link.async_write(SETPOINT, b"\x16\x00"))
    await asyncio.sleep(0)
    assert "write" not in kinds(proxy.events)

    proxy.read_gate.set_result(None)
    await snapshot
    await write
    assert kinds(proxy.events)[-1] == "write"


async def test_one_proxy_runs_one_pairing_at_a_time(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.pair_gate = hass.loop.create_future()
    first = hass.async_create_task(make_link(hass).async_snapshot())
    await asyncio.sleep(0)
    second_link = HeaterLink(hass, "AA:BB:CC:DD:EE:21", PROXY, PIN)
    second = hass.async_create_task(second_link.async_snapshot())
    await asyncio.sleep(0)
    assert kinds(proxy.events).count("pair_start") == 1

    proxy.pair_gate.set_result(None)
    await first
    await second
    starts = [i for i, e in enumerate(proxy.events) if e == "pair_start"]
    ends = [i for i, e in enumerate(proxy.events) if e == "pair_end"]
    assert ends[0] < starts[1]


async def test_write_uses_the_characteristic_write_mode(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.services.add((0, 0x1007), ("write-without-response",))
    link = make_link(hass)

    await link.async_write(SETPOINT, b"\x16\x00")
    await link.async_write((0, 0x1007), b"\x01")

    writes = [e for e in proxy.events if e[0] == "write"]
    assert writes[0][2:] == (b"\x16\x00", True)
    assert writes[1][2:] == (b"\x01", False)


async def test_write_rejects_missing_or_read_only_characteristics(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    proxy.services.add((0, 0x100B), ("read",))
    link = make_link(hass)

    with pytest.raises(CharacteristicError):
        await link.async_write((0, 0x102A), b"\x00")
    with pytest.raises(CharacteristicError):
        await link.async_write((0, 0x100B), b"\x00")
    assert link.connected


async def test_gatt_failure_during_write_drops_the_link(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    link = make_link(hass)
    await link.async_snapshot()

    async def fail(*args, **kwargs):
        raise gatt_failure(0x0E)

    proxy.clients[0].write_gatt_char = fail
    with pytest.raises(HeaterUnavailableError):
        await link.async_write(SETPOINT, b"\x16\x00")
    assert not link.connected


async def test_optional_and_selector_reads_skip_unusable_characteristics(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    proxy.services.services.pop("00000000-0001-1000-8000-00805f9b34fb")
    proxy.services.add((1, 0x0002), ("read",))
    proxy.services.add((3, 0x2001), ("write",))
    proxy.services.add((0, 0x1007), ("write",))
    proxy.services.add((0, 0x102A), ("read", "write"), vendor=True)
    proxy.values[(0, 0x102A)] = bytes((0, 26, 11, 30, 0, 18, 7))
    proxy.services.add((1, 0x0008), ("read", "write-without-response"))

    snapshot, _, _ = await make_link(hass).async_snapshot()

    assert snapshot.away_active is False
    assert snapshot.current_temperature_c is not None
    assert snapshot.ui_version is None
    assert snapshot.heating_active is None


async def test_group_zero_characteristic_is_found_in_any_service(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    stray = proxy.services.add((1, 0x0003))
    stray.uuid = "00001005-0000-1000-8000-00805f9b34fb"
    del proxy.services.services["00000000-0000-1000-8000-00805f9b34fb"]
    proxy.values[(0, 0x1005)] = b"\x01"

    assert await read(make_link(hass), (0, 0x1005)) == b"\x01"


async def test_gatt_services_metadata(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    services = await make_link(hass).async_gatt_services()

    assert services[0]["uuid"] == "00000000-0003-1000-8000-00805f9b34fb"
    assert services[0]["characteristics"][0]["properties"] == ["read", "write"]

    proxy.services = None
    link = make_link(hass)
    assert await link.async_gatt_services() == []
    assert await read(link, (0, 0x1005)) is None


async def test_logs_name_the_heater_and_never_the_pin(hass, fake_bt, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.dimplex_ble")
    proxy = fake_bt.add()
    proxy.pair_errors = [pair_failure(81)]
    link = make_link(hass)
    with pytest.raises(HeaterAuthError):
        await link.async_snapshot()
    await link.async_snapshot()
    proxy.clients[-1].disconnected_callback()
    await link.async_close()

    records = [
        r for r in caplog.records if r.name.startswith("custom_components.dimplex_ble")
    ]
    messages = [r.getMessage() for r in records]
    for tag in (
        "CONNECT_REQUEST",
        "CONNECT_RESULT",
        "PAIR_REQUEST",
        "PAIR_RESULT",
        "RESPONDER_ARM",
        "RESPONDER_CLEAR",
        "DISCONNECT",
        "GATT",
    ):
        assert any(m.startswith(tag) for m in messages), tag
    assert all(ADDRESS in m for m in messages)
    assert any("code=81" in m for m in messages)
    assert not any(PIN in m or "12345" in m for m in caplog.text.splitlines())


def test_pair_error_code_parses_only_the_bleak_esphome_message() -> None:
    assert pair_error_code(pair_failure(81)) == 81
    assert pair_error_code(BleakError("Pairing failed due to error: x")) is None
    assert pair_error_code(TimeoutError("Pairing failed due to error: 81")) is None


@pytest.mark.parametrize(
    ("error", "error_type", "key"),
    [
        (HeaterUnavailableError(), HomeAssistantError, "cannot_connect"),
        (ResponderMissingError(), HomeAssistantError, "responder_missing"),
        (HeaterAuthError(), HomeAssistantError, "reauth_required"),
        (CharacteristicError(), ServiceValidationError, "characteristic_unavailable"),
        (DimplexProtocolError(), ServiceValidationError, "invalid_gatt_request"),
    ],
)
def test_errors_translate_once(error, error_type, key) -> None:
    translated = to_homeassistant_error(error)
    assert type(translated) is error_type
    assert translated.translation_key == key


async def test_unknown_proxy_has_no_responder(hass, fake_bt) -> None:
    fake_bt.add(FakeProxy("AA:AA:AA:AA:AA:AA"))

    assert not has_responder(hass, PROXY)
    with pytest.raises(ResponderMissingError):
        await async_arm(hass, PROXY, ADDRESS, None)


async def test_failed_clear_does_not_fail_the_operation(hass, fake_bt) -> None:
    proxy = fake_bt.add()

    def drop_api(data) -> None:
        proxy.api_connected = False

    proxy.on_arm = drop_api

    assert (await make_link(hass).async_snapshot())[0].model == "QRAD100E"
    assert proxy.actions == [
        ("dimplex_pin_arm", {"address": ADDRESS, "passkey": 12345})
    ]


async def test_cancel_during_connect_waits_for_connect_then_closes(
    hass, fake_bt
) -> None:
    """RC-3: an abandoned connect orphans the forming link in IDF 5.5.5."""
    proxy = fake_bt.add()
    proxy.connect_gate = hass.loop.create_future()
    task = hass.async_create_task(make_link(hass).async_snapshot())
    await asyncio.sleep(0)
    assert kinds(proxy.events) == ["action", "connect"]

    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert kinds(proxy.events) == ["action", "connect"]

    proxy.connect_gate.set_result(None)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert kinds(proxy.events) == ["action", "connect", "disconnect", "action"]


async def test_cancel_during_failing_connect_needs_no_disconnect(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.connect_gate = hass.loop.create_future()
    proxy.connect_error = TimeoutError()
    task = hass.async_create_task(make_link(hass).async_snapshot())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)

    proxy.connect_gate.set_result(None)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert kinds(proxy.events) == ["action", "connect", "action"]


async def test_snapshot_reads_every_verified_setting(hass, fake_bt) -> None:
    """Regression: Adaptive Start (0x1008) was dropped from the snapshot reads."""
    fake_bt.add()

    snapshot, _, _ = await make_link(hass).async_snapshot()

    assert snapshot.preemptive_heating is True


def test_only_the_hardware_confirmed_wrong_pin_code_starts_reauth() -> None:
    assert CREDENTIAL_REJECTED_CODES == {81}


async def test_cancel_right_after_the_arm_was_sent_still_clears(hass, fake_bt) -> None:
    """The proxy applied the arm, HA had no reply yet: the PIN must not stay armed."""
    proxy = fake_bt.add()
    reply = hass.loop.create_future()
    execute = proxy.api.execute_service

    async def slow_arm_reply(service, data):
        await execute(service, data)
        if service.name == "dimplex_pin_arm":
            await reply

    proxy.api.execute_service = slow_arm_reply
    task = hass.async_create_task(make_link(hass).async_snapshot())
    await asyncio.sleep(0)
    assert proxy.actions == [
        ("dimplex_pin_arm", {"address": ADDRESS, "passkey": 12345})
    ]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proxy.actions[-1] == ("dimplex_pin_clear", {})
    assert "connect" not in kinds(proxy.events)


async def test_pin_reaches_only_a_pairing_that_owns_the_proxy_responder(
    hass, fake_bt
) -> None:
    """One responder state per proxy: a heater waiting for the lock must not
    retarget it away from the heater that is pairing (RC-2)."""
    proxy = fake_bt.add()
    proxy.pair_gate = hass.loop.create_future()
    other = HeaterLink(hass, "AA:BB:CC:DD:EE:21", PROXY, None)
    pairing = hass.async_create_task(other.async_pair(None))
    await asyncio.sleep(0)
    waiting = make_link(hass, None)
    waiting_pair = hass.async_create_task(waiting.async_pair(None))
    await asyncio.sleep(0)
    before = list(proxy.actions)

    with pytest.raises(HeaterUnavailableError):
        await waiting.async_deliver_pin(12345)
    assert proxy.actions == before

    await other.async_deliver_pin(54321)
    assert proxy.actions[-1] == (
        "dimplex_pin_arm",
        {"address": "AA:BB:CC:DD:EE:21", "passkey": 54321},
    )
    proxy.pair_gate.set_result(None)
    await pairing
    await waiting_pair
    # Each pairing cleared the responder only after its own pair() ended.
    clears = [i for i, a in enumerate(proxy.actions) if a[0] == "dimplex_pin_clear"]
    assert len(clears) == 2
    with pytest.raises(HeaterUnavailableError):
        await other.async_deliver_pin(54321)  # that pairing has ended
