"""Entry lifecycle, coordinator, diagnostics and services against a fake proxy."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import ADDRESS, PIN, PROXY, FakeProxy, pair_failure
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import EVENT_CALL_SERVICE, EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.dimplex_ble import async_remove_config_entry_device
from custom_components.dimplex_ble.const import (
    CONF_ADDRESS,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    DOMAIN,
)
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.dimplex_ble.errors import HeaterAuthError
from custom_components.dimplex_ble.protocol import SOUND

# The climate entity uses the native_* climate API of HA 2026.11; the pinned test
# harness (HA 2026.10) cannot add it, so availability is observed on the select.
UNIT = "select.dimplex_panel_heater_am6532_display_temperature_unit"


pytestmark = pytest.mark.usefixtures("skip_dependencies")


def make_entry(hass, **data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Panel Heater AM6532",
        unique_id=ADDRESS.lower(),
        # Entries created before the refactor stored a lower-case address and
        # an address_type; both must keep working without a migration.
        data={
            CONF_ADDRESS: ADDRESS.lower(),
            CONF_PROXY_SOURCE: PROXY,
            CONF_PASSKEY: PIN,
            "address_type": 0,
            **data,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def setup(hass, entry) -> bool:
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return result


def reauth_flows(hass) -> list:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_setup_polls_through_the_pinned_proxy(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    service_calls = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, service_calls.append)

    assert await setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(UNIT).state == "celsius"
    assert hass.states.get("switch.dimplex_panel_heater_am6532_sound").state == "on"
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ADDRESS.lower()), entry.entry_id
    )
    assert device is not None and device.model == "QRAD100E"
    # Every advertisement, not only changed ones: a released heater re-sends
    # identical data.
    assert [address for _, address in fake_bt.callbacks] == [ADDRESS]
    # The PIN goes through the proxy's API client, never through a service call.
    assert ("dimplex_pin_arm", {"address": ADDRESS, "passkey": 12345}) in (
        proxy.actions
    )
    assert service_calls == []


async def test_unavailable_heater_retries_setup_without_connecting(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    proxy.sees_heater = False
    entry = make_entry(hass)

    await setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert proxy.events == []


async def test_rejected_pin_at_setup_starts_reauth(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    proxy.pair_errors = [pair_failure(81)]
    entry = make_entry(hass)

    await setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(reauth_flows(hass)) == 1


async def test_runtime_rejection_starts_reauth_and_stops_polling(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    proxy.clients[0].is_connected = False
    proxy.pair_errors = [pair_failure(81)]

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    pairings = proxy.events.count("pair_start")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
    await hass.async_block_till_done()

    assert len(reauth_flows(hass)) == 1
    assert hass.states.get(UNIT).state == "unavailable"
    assert proxy.events.count("pair_start") == pairings
    # Advertisements must not restart pairing with the rejected PIN either.
    fake_bt.advertise(PROXY)
    await hass.async_block_till_done()
    assert proxy.events.count("pair_start") == pairings


async def test_transient_failure_recovers_on_the_next_poll(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    proxy.clients[0].is_connected = False
    proxy.pair_errors = [pair_failure(99), pair_failure(99)]

    # One missed poll keeps the last state; the second in a row does not.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert hass.states.get(UNIT).state == "celsius"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=122))
    await hass.async_block_till_done()
    assert hass.states.get(UNIT).state == "unavailable"
    assert reauth_flows(hass) == []

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=183))
    await hass.async_block_till_done()
    assert hass.states.get(UNIT).state == "celsius"


async def test_only_misses_in_a_row_make_the_heater_unavailable(
    hass, fake_bt, caplog
) -> None:
    """An isolated connect timeout (1-2 % of polls) must not flash unavailable."""
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    coordinator: DimplexCoordinator = entry.runtime_data
    kept = coordinator.data

    proxy.sees_heater = False
    with caplog.at_level(logging.DEBUG):
        await coordinator.async_refresh()
    assert coordinator.last_update_success and coordinator.data is kept
    assert "POLL_MISSED" in caplog.text

    proxy.sees_heater = True  # a good poll resets the count
    await coordinator.async_refresh()
    proxy.sees_heater = False
    await coordinator.async_refresh()
    assert coordinator.last_update_success

    await coordinator.async_refresh()  # the second miss in a row
    assert not coordinator.last_update_success


async def test_advertisement_on_the_pinned_proxy_refreshes_an_unavailable_heater(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    other = fake_bt.add(FakeProxy("14:C1:9F:22:4C:DA"))
    entry = make_entry(hass)
    await setup(hass, entry)
    coordinator: DimplexCoordinator = entry.runtime_data
    proxy.clients[0].is_connected = False
    proxy.sees_heater = False
    for seconds in (61, 122):  # the second missed poll in a row
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
        await hass.async_block_till_done()
    assert hass.states.get(UNIT).state == "unavailable"  # ROUTE_NOT_SEEN
    refreshes = []
    request_refresh = coordinator.async_request_refresh

    async def counted_refresh() -> None:
        refreshes.append(None)
        await request_refresh()

    coordinator.async_request_refresh = counted_refresh

    fake_bt.advertise(other.source)
    await hass.async_block_till_done()
    assert refreshes == []

    # The heater re-advertises identical data on the pinned proxy.
    fake_bt.advertise(PROXY)
    await hass.async_block_till_done()
    assert len(refreshes) == 1
    assert hass.states.get(UNIT).state == "celsius"

    fake_bt.advertise(PROXY)  # available again: no refresh storm
    await hass.async_block_till_done()
    assert len(refreshes) == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fake_bt.callbacks == []


async def test_command_rejection_starts_reauth(hass, fake_bt) -> None:
    fake_bt.add()
    entry = make_entry(hass)
    link = SimpleNamespace(
        address=ADDRESS,
        source=PROXY,
        async_write=AsyncMock(side_effect=HeaterAuthError()),
    )
    coordinator = DimplexCoordinator(hass, entry, link)

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_write((0, 0x1005), b"\x01")

    assert err.value.translation_key == "reauth_required"
    await hass.async_block_till_done()
    assert len(reauth_flows(hass)) == 1


async def test_unload_waits_for_in_flight_pairing_then_closes_once(
    hass, fake_bt
) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    proxy.clients[0].is_connected = False
    proxy.pair_gate = hass.loop.create_future()
    poll = hass.async_create_task(entry.runtime_data.async_refresh())
    await asyncio.sleep(0)
    assert proxy.events[-1] == "pair_start"

    unload = hass.async_create_task(hass.config_entries.async_unload(entry.entry_id))
    for _ in range(5):
        await asyncio.sleep(0)
    assert proxy.events[-1] == "pair_start"

    proxy.pair_gate.set_result(None)
    await poll
    assert await unload
    assert proxy.events[-1] == "disconnect"
    assert proxy.events.count("disconnect") == 2  # the stale link, then the close
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_home_assistant_stop_closes_the_link(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert proxy.events[-1] == "disconnect"


async def test_sound_is_read_on_its_own_slow_schedule(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    coordinator: DimplexCoordinator = entry.runtime_data
    reads = proxy.events.count(("read", "00000005-0000-1000-8000-00805f9b34fb"))
    proxy.values[(2, 0x0005)] = b"\x00\x00\x00"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5, seconds=1))
    await hass.async_block_till_done()
    # The timer marks the settings due; the next poll reads them.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5, seconds=12))
    await hass.async_block_till_done()

    assert proxy.events.count(("read", "00000005-0000-1000-8000-00805f9b34fb")) > reads
    assert coordinator.settings.get(SOUND) == b"\x00\x00\x00"


async def test_diagnostics_redact_the_pin_and_addresses(hass, fake_bt) -> None:
    fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)

    result = await async_get_config_entry_diagnostics(hass, entry)

    text = str(result)
    for secret in (PIN, ADDRESS, ADDRESS.lower(), PROXY):
        assert secret not in text
    # The model is not personal data; support needs it.
    assert result["coordinator"]["data"]["model"] == "QRAD100E"
    # Raw payloads are kept here instead of in entity attributes.
    assert result["coordinator"]["data"]["operating_mode_hex"] == "020000000000"
    assert result["link"] == {
        "route_seen": True,
        "connected": False,  # released after every update
        "responder": True,
        "sync_clock": True,
        "clock_offset_s": None,  # this heater has no clock characteristic
    }
    assert result["coordinator"]["last_exception"] is None
    assert result["coordinator"]["data"]["setpoint_c"] == 21


async def test_diagnostics_before_the_first_snapshot(hass, fake_bt) -> None:
    fake_bt.add(FakeProxy(responder=False))
    entry = make_entry(hass)
    await setup(hass, entry)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"]["data"] is None
    assert result["coordinator"]["last_exception"] == "UpdateFailed"
    assert result["link"]["responder"] is False


async def test_get_gatt_services_action(hass, fake_bt) -> None:
    proxy = fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ADDRESS.lower()), entry.entry_id
    )

    response = await hass.services.async_call(
        DOMAIN,
        "get_gatt_services",
        {"device_id": device.id},
        blocking=True,
        return_response=True,
    )
    assert response["services"][0]["uuid"] == "00000000-0003-1000-8000-00805f9b34fb"

    proxy.clients[0].is_connected = False
    proxy.connect_error = TimeoutError()
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "get_gatt_services",
            {"device_id": device.id},
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "cannot_connect"


@pytest.mark.parametrize(("present", "allowed"), [(True, False), (False, True)])
async def test_device_removal_only_when_absent(
    hass, monkeypatch, present, allowed
) -> None:
    present_mock = Mock(return_value=present)
    monkeypatch.setattr(
        "homeassistant.components.bluetooth.async_address_present", present_mock
    )
    entry = make_entry(hass)

    assert await async_remove_config_entry_device(hass, entry, Mock()) is allowed
    present_mock.assert_called_once_with(hass, ADDRESS, connectable=True)


@pytest.mark.parametrize("code", [78, 80, 88])
async def test_unconfirmed_smp_codes_at_setup_do_not_start_reauth(
    hass, fake_bt, code
) -> None:
    """Only 81 is a hardware-confirmed wrong PIN (ledger H8)."""
    proxy = fake_bt.add()
    proxy.pair_errors = [pair_failure(code)]
    entry = make_entry(hass)

    await setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert reauth_flows(hass) == []


async def test_diagnostics_never_show_when_the_home_is_empty(hass, fake_bt) -> None:
    """Codex review: the Away return time is redacted like the schedule times."""
    from dataclasses import replace
    from datetime import datetime

    fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)
    coordinator = entry.runtime_data
    coordinator.data = replace(
        coordinator.data, away_active=True, away_until=datetime(2026, 11, 26, 18, 30)
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"]["data"]["away_until"] == "**REDACTED**"
    assert "2026-11-26" not in str(result)


async def test_only_the_climate_entity_appears_on_dashboards(hass, fake_bt) -> None:
    """Every other entity is a setting or diagnostic, kept off auto dashboards."""
    fake_bt.add()
    entry = make_entry(hass)
    await setup(hass, entry)

    uncategorized = {
        e.domain
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.entity_category is None
    }
    assert uncategorized <= {"climate"}
