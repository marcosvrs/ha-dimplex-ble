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
    UNIQUE_ID_SUFFIXES,
    UNVERIFIED_LEGACY_SUFFIXES,
)
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.dimplex_ble.errors import HeaterAuthError

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


async def test_setup_removes_only_entities_the_integration_no_longer_creates(
    hass, fake_bt, caplog
) -> None:
    """Orphans of earlier versions go; current unique_ids (frozen) stay."""
    fake_bt.add()
    entry = make_entry(hass)
    other = MockConfigEntry(domain=DOMAIN, unique_id="other")
    other.add_to_hass(hass)
    registry = er.async_get(hass)
    mac = ADDRESS.lower()
    kept = {
        "switch": f"{mac}_preemptive_heating",
        "light": f"{mac}_pre_emptive_heating",  # unverified legacy: left alone
        "sensor": f"{mac}_operating_mode_raw",
        "select": f"{mac}_temperature_unit",
        "number": f"{mac}_temperature_offset",
    }
    for domain, unique_id in kept.items():
        registry.async_get_or_create(domain, DOMAIN, unique_id, config_entry=entry)
    orphans = [
        registry.async_get_or_create(
            platform, DOMAIN, f"{mac}_{suffix}", config_entry=entry
        ).entity_id
        for platform, suffix in (
            ("sensor", "raw_readonly_3_1022"),
            ("text", "raw_0_1002"),
            ("sensor", "temperature_units"),
            ("number", "minimum_temperature"),
            ("number", "maximum_temperature"),
        )
    ]
    foreign = registry.async_get_or_create(
        "sensor", DOMAIN, f"{mac}_raw_readonly_1_0007", config_entry=other
    ).entity_id
    caplog.set_level(logging.DEBUG, "custom_components.dimplex_ble")

    assert await setup(hass, entry)

    for entity_id in orphans:
        assert registry.async_get(entity_id) is None
    assert registry.async_get(foreign) is not None  # another entry's entity
    for domain, unique_id in kept.items():
        assert registry.async_get_entity_id(domain, DOMAIN, unique_id) is not None
    assert "Removed 5 orphaned entities" in caplog.text
    assert {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    } <= {
        f"{mac}_{suffix}" for suffix in UNIQUE_ID_SUFFIXES | UNVERIFIED_LEGACY_SUFFIXES
    }


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

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    pairings = proxy.events.count("pair_start")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
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
    proxy.pair_errors = [pair_failure(99)]

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert hass.states.get(UNIT).state == "unavailable"
    assert reauth_flows(hass) == []

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=22))
    await hass.async_block_till_done()
    assert hass.states.get(UNIT).state == "celsius"


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
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
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

    assert proxy.events.count(("read", "00000005-0000-1000-8000-00805f9b34fb")) > reads
    assert coordinator.sound_value == b"\x00\x00\x00"


async def test_sound_read_failures_and_skips(hass) -> None:
    entry = make_entry(hass)
    link = SimpleNamespace(
        address=ADDRESS,
        source=PROXY,
        keep_connected=True,
        async_read=AsyncMock(
            side_effect=[HeaterAuthError(), b"\x07\x00\x00", None, None]
        ),
    )
    coordinator = DimplexCoordinator(hass, entry, link)
    coordinator.sound_value = b"\x01\x00\x00"

    await coordinator.async_refresh_settings()
    await coordinator.async_refresh_settings()
    coordinator.last_update_success = False
    await coordinator.async_refresh_settings()

    assert coordinator.sound_value == b"\x01\x00\x00"
    # A failure ends that round; the next round reads every slow setting.
    assert link.async_read.await_count == 4


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
        "connected": True,
        "mode": "persistent",
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
