from __future__ import annotations

import logging
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import probatio
import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STOP,
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble import (
    _any_discovery_flow_in_progress,
    _discovery_flow_in_progress,
    async_remove_config_entry_device,
    async_setup,
    async_setup_entry,
    async_unload,
    async_unload_entry,
)
from custom_components.dimplex_ble import climate as climate_platform
from custom_components.dimplex_ble import select as select_platform
from custom_components.dimplex_ble import sensor as sensor_platform
from custom_components.dimplex_ble import services as integration_services
from custom_components.dimplex_ble.climate import DimplexClimate
from custom_components.dimplex_ble.const import (
    CLIENTS,
    CONF_ADDRESS,
    CONF_ADDRESS_TYPE,
    CONF_PROXY_SOURCE,
    DOMAIN,
    PLATFORMS,
)
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.dimplex_ble.protocol import HeaterSnapshot
from custom_components.dimplex_ble.select import DimplexDisplayUnitSelect
from custom_components.dimplex_ble.sensor import (
    EXPERIMENTAL_READ_ONLY_FIELDS,
    DimplexCalculatedPowerSensor,
    DimplexErrorCodeSensor,
    DimplexExperimentalRawSensor,
    DimplexLcdTemperatureSensor,
    DimplexOperatingModeSensor,
    DimplexOutputPowerSensor,
    DimplexRatedPowerSensor,
)

ADDRESS = "AA:BB:CC:DD:EE:20"
PROXY = "esphome-proxy"


def snapshot(
    *,
    model="QRAD100E",
    setpoint=21,
    minimum=5,
    maximum=30,
    current=19.5,
    error=48879,
    error_hex="efbe",
    mode_hex="0201",
    unit=0,
    rated_power=None,
    power_loading_hex=None,
    lcd_temperature=None,
    trac_status_hex=None,
    heating_active=None,
    trac_output_power_percent=None,
    product_code=None,
    ui_version=None,
    rf_firmware_revision=None,
):
    return HeaterSnapshot(
        model=model,
        setpoint_c=setpoint,
        minimum_temperature_c=minimum,
        maximum_temperature_c=maximum,
        current_temperature_c=current,
        error_code=error,
        error_status_hex=error_hex,
        operating_mode_hex=mode_hex,
        display_unit=unit,
        rated_power_w=rated_power,
        power_loading_hex=power_loading_hex,
        lcd_temperature_c=lcd_temperature,
        trac_status_hex=trac_status_hex,
        heating_active=heating_active,
        trac_output_power_percent=trac_output_power_percent,
        product_code=product_code,
        ui_version=ui_version,
        rf_firmware_revision=rf_firmware_revision,
    )


def make_coordinator(hass, *, data=None, client=None, entry=None):
    config_entry = entry or MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    client = client or SimpleNamespace(
        address=ADDRESS, async_read_raw_fields=AsyncMock(return_value={})
    )
    if not hasattr(client, "address"):
        client.address = ADDRESS
    coordinator = DimplexCoordinator(hass, config_entry, client)
    coordinator.data = data
    return config_entry, coordinator


async def test_rule_setup_registers_services_and_passive_discovery(hass, monkeypatch):
    register = Mock(return_value=lambda: None)
    setup_services = Mock()
    monkeypatch.setattr(
        "custom_components.dimplex_ble.bluetooth.async_register_callback",
        register,
    )
    monkeypatch.setattr(
        "custom_components.dimplex_ble.async_setup_services", setup_services
    )
    assert await async_setup(hass, {}) is True
    setup_services.assert_called_once_with(hass)
    assert register.call_args.args[0] is hass
    assert register.call_args.args[2] == {"connectable": True}
    from homeassistant.components.bluetooth import BluetoothScanningMode

    assert register.call_args.args[3] is BluetoothScanningMode.PASSIVE
    assert hass.data[DOMAIN]["cancel_discovery"] is register.return_value


async def test_rule_homeassistant_stop_closes_persistent_clients(hass, monkeypatch):
    monkeypatch.setattr(
        "custom_components.dimplex_ble.bluetooth.async_register_callback",
        Mock(return_value=Mock()),
    )
    monkeypatch.setattr("custom_components.dimplex_ble.async_setup_services", Mock())
    await async_setup(hass, {})

    client = SimpleNamespace(async_shutdown=AsyncMock())
    hass.data[DOMAIN][CLIENTS].append(client)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    client.async_shutdown.assert_awaited_once()

def test_discovery_flow_in_progress_matches_only_address_and_preserves_filter(
    hass, monkeypatch
):
    progress = Mock()
    monkeypatch.setattr(
        hass.config_entries.flow,
        "async_progress_by_handler",
        progress,
    )
    progress.return_value = [{"context": {"unique_id": ADDRESS}}]
    assert _discovery_flow_in_progress(hass, ADDRESS.lower()) is True
    progress.assert_called_once_with(DOMAIN, include_uninitialized=True)

    progress.reset_mock()
    progress.return_value = [{"context": {"unique_id": "AA:BB:CC:DD:EE:99"}}]
    assert _discovery_flow_in_progress(hass, ADDRESS) is False

    progress.return_value = [{"context": {}}]
    assert _discovery_flow_in_progress(hass, ADDRESS) is False

def test_any_discovery_flow_in_progress_blocks_other_heaters(hass, monkeypatch):
    progress = Mock(return_value=[{"context": {}}])
    monkeypatch.setattr(
        hass.config_entries.flow,
        "async_progress_by_handler",
        progress,
    )

    assert _any_discovery_flow_in_progress(hass) is True
    progress.assert_called_once_with(DOMAIN, include_uninitialized=True)

    progress.return_value = []
    assert _any_discovery_flow_in_progress(hass) is False

async def test_rule_advertisement_callback_queues_matching_and_ignores_nonmatching(
    hass, monkeypatch
):
    register = Mock(return_value=Mock())
    monkeypatch.setattr(
        "custom_components.dimplex_ble.bluetooth.async_register_callback", register
    )
    monkeypatch.setattr("custom_components.dimplex_ble.async_setup_services", Mock())
    queued = []

    def capture_task(coroutine):
        queued.append(coroutine)
        coroutine.close()

    monkeypatch.setattr(hass, "async_create_task", capture_task)
    await async_setup(hass, {})
    discovered = register.call_args.args[1]
    info = SimpleNamespace(
        name=None,
        device=SimpleNamespace(address="aa:bb:cc:dd:ee:21", name="heater<Dimplex>"),
    )
    discovered(info, None)
    discovered(info, None)
    discovered(
        SimpleNamespace(name="Other", device=SimpleNamespace(address=ADDRESS)),
        None,
    )
    assert len(queued) == 1
    assert hass.data[DOMAIN]["cancel_discovery"] is register.return_value


async def test_rule_advertisement_flow_is_executed_and_manifest_names_are_skipped(
    hass, monkeypatch
):
    register = Mock(return_value=Mock())
    monkeypatch.setattr(
        "custom_components.dimplex_ble.bluetooth.async_register_callback", register
    )
    monkeypatch.setattr("custom_components.dimplex_ble.async_setup_services", Mock())
    flow_init = AsyncMock(return_value={"type": "abort"})
    monkeypatch.setattr(hass.config_entries.flow, "async_init", flow_init)
    await async_setup(hass, {})
    discovered = register.call_args.args[1]

    discovered(
        SimpleNamespace(
            name="heater<Dimplex>",
            device=SimpleNamespace(address=ADDRESS, name=None),
        ),
        None,
    )
    await hass.async_block_till_done()
    flow_init.assert_awaited_once_with(
        DOMAIN,
        context={"source": "bluetooth"},
        data=SimpleNamespace(
            name="heater<Dimplex>",
            device=SimpleNamespace(address=ADDRESS, name=None),
        ),
    )
    assert hass.data[DOMAIN]["pending_discovery"] == set()

    discovered(
        SimpleNamespace(
            name="AL1234",
            device=SimpleNamespace(address="aa:bb:cc:dd:ee:21", name=None),
        ),
        None,
    )
    assert flow_init.await_count == 1


async def test_rule_advertisement_callback_ignores_configured_heater(hass, monkeypatch):
    register = Mock(return_value=Mock())
    monkeypatch.setattr(
        "custom_components.dimplex_ble.bluetooth.async_register_callback", register
    )
    monkeypatch.setattr("custom_components.dimplex_ble.async_setup_services", Mock())
    queued = []

    def capture_task(coroutine):
        queued.append(coroutine)
        coroutine.close()

    monkeypatch.setattr(hass, "async_create_task", capture_task)
    configured = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:22",
        data={CONF_ADDRESS: "AA:BB:CC:DD:EE:22", CONF_PROXY_SOURCE: PROXY},
    )
    configured.add_to_hass(hass)
    await async_setup(hass, {})
    discovered = register.call_args.args[1]
    discovered(
        SimpleNamespace(
            name="heater<Dimplex>",
            device=SimpleNamespace(address="aa:bb:cc:dd:ee:22", name=None),
        ),
        None,
    )
    assert queued == []


async def test_rule_platform_setup_exposes_expected_entities(hass):
    _, coordinator = make_coordinator(hass, data=snapshot())
    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = coordinator
    added = {}

    def collect(platform):
        def add(entities):
            added[platform] = entities

        return add

    await climate_platform.async_setup_entry(hass, entry, collect("climate"))
    await select_platform.async_setup_entry(hass, entry, collect("select"))
    await sensor_platform.async_setup_entry(hass, entry, collect("sensor"))
    assert len(added["climate"]) == 1
    assert isinstance(added["climate"][0], DimplexClimate)
    assert len(added["select"]) == 1
    assert isinstance(added["select"][0], DimplexDisplayUnitSelect)
    assert [type(entity) for entity in added["sensor"][:6]] == [
        DimplexErrorCodeSensor,
        DimplexOperatingModeSensor,
        DimplexRatedPowerSensor,
        DimplexLcdTemperatureSensor,
        DimplexOutputPowerSensor,
        DimplexCalculatedPowerSensor,
    ]
    assert len(added["sensor"]) == 6 + len(EXPERIMENTAL_READ_ONLY_FIELDS)
    assert all(
        isinstance(entity, DimplexExperimentalRawSensor)
        for entity in added["sensor"][6:]
    )


async def test_experimental_sensor_keeps_entities_when_initial_read_fails(hass):
    from custom_components.dimplex_ble.client import DimplexConnectionError

    _, coordinator = make_coordinator(
        hass,
        client=SimpleNamespace(
            address=ADDRESS,
            async_read_raw_fields=AsyncMock(
                side_effect=DimplexConnectionError("proxy unavailable")
            ),
        ),
    )
    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = coordinator
    added = []

    await sensor_platform.async_setup_entry(hass, entry, added.extend)

    assert len(added) == 6 + len(EXPERIMENTAL_READ_ONLY_FIELDS)


async def test_experimental_read_only_sensor_preserves_raw_value(hass):
    raw_read = AsyncMock(
        return_value={(1, 0x0007): b"\x06", (3, 0x000B): b"\x01\x02"}
    )
    _, coordinator = make_coordinator(
        hass,
        client=SimpleNamespace(address=ADDRESS, async_read_raw_fields=raw_read),
    )
    entry = MockConfigEntry(domain=DOMAIN)
    entry.runtime_data = coordinator
    added = []

    await sensor_platform.async_setup_entry(hass, entry, added.extend)

    entity = next(
        entity
        for entity in added
        if isinstance(entity, DimplexExperimentalRawSensor)
        and entity._field.address == (3, 0x000B)
    )
    assert entity.native_value == "0102"
    assert entity.extra_state_attributes == {
        "service_group": 3,
        "characteristic": 0x000B,
        "raw_bytes": 2,
        "raw_value": "0102",
    }
    assert not hasattr(entity, "async_set_value")

    key_entity = next(
        entity
        for entity in added
        if isinstance(entity, DimplexExperimentalRawSensor)
        and entity._field.address == (1, 0x0007)
    )
    assert key_entity.native_value == "key code 6"
    assert key_entity.extra_state_attributes["last_key_code"] == 6
    assert key_entity.extra_state_attributes["decoded_value"] == "key code 6"

    empty_entity = DimplexExperimentalRawSensor(
        coordinator, EXPERIMENTAL_READ_ONLY_FIELDS[0], None
    )
    assert empty_entity.native_value is None
    raw_read.assert_awaited_once_with(
        tuple(field.address for field in EXPERIMENTAL_READ_ONLY_FIELDS)
    )


async def test_rule_setup_entry_retries_if_heater_is_offline(hass, monkeypatch):
    client = SimpleNamespace(address=ADDRESS, async_shutdown=AsyncMock())
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(
        "custom_components.dimplex_ble.DimplexBluetoothClient", client_factory
    )
    captured_device = object()
    monkeypatch.setattr(
        "custom_components.dimplex_ble.ble_device_from_source",
        Mock(return_value=captured_device),
    )
    first_refresh = AsyncMock(side_effect=ConfigEntryNotReady)
    monkeypatch.setattr(
        DimplexCoordinator, "async_config_entry_first_refresh", first_refresh
    )
    forwarded = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", forwarded)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    with pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)
    client_factory.assert_called_once_with(hass, ADDRESS, PROXY, captured_device)
    first_refresh.assert_awaited_once()
    assert entry.runtime_data.client is client
    assert entry.runtime_data.hass is hass
    assert entry.runtime_data.config_entry is entry
    client.async_shutdown.assert_awaited_once()
    forwarded.assert_not_awaited()


async def test_rule_setup_entry_uses_stored_address_type_when_route_is_missing(
    hass, monkeypatch
):
    client = SimpleNamespace(address=ADDRESS, async_shutdown=AsyncMock())
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(
        "custom_components.dimplex_ble.DimplexBluetoothClient", client_factory
    )
    find_device = Mock(side_effect=[None, None])
    monkeypatch.setattr(
        "custom_components.dimplex_ble.ble_device_from_source", find_device
    )
    active_scan = AsyncMock()
    monkeypatch.setattr(
        "custom_components.dimplex_ble.bluetooth.async_request_active_scan",
        active_scan,
    )
    first_refresh = AsyncMock()
    monkeypatch.setattr(
        DimplexCoordinator, "async_config_entry_first_refresh", first_refresh
    )
    forwarded = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", forwarded)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_PROXY_SOURCE: PROXY,
            CONF_ADDRESS_TYPE: 1,
        },
    )

    await async_setup_entry(hass, entry)

    active_scan.assert_awaited_once_with(hass, duration=5.0)
    assert client_factory.call_args.args[:3] == (hass, ADDRESS, PROXY)
    assert client_factory.call_args.args[3].details["address_type"] == 1
    first_refresh.assert_awaited_once()
    forwarded.assert_awaited_once_with(entry, PLATFORMS)


async def test_rule_setup_entry_forwards_platforms_after_first_refresh(
    hass, monkeypatch
):
    client = SimpleNamespace(address=ADDRESS)
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(
        "custom_components.dimplex_ble.DimplexBluetoothClient", client_factory
    )
    captured_device = object()
    monkeypatch.setattr(
        "custom_components.dimplex_ble.ble_device_from_source",
        Mock(return_value=captured_device),
    )
    first_refresh = AsyncMock()
    monkeypatch.setattr(
        DimplexCoordinator, "async_config_entry_first_refresh", first_refresh
    )
    forwarded = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", forwarded)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    await async_setup_entry(hass, entry)
    client_factory.assert_called_once_with(hass, ADDRESS, PROXY, captured_device)
    first_refresh.assert_awaited_once()
    forwarded.assert_awaited_once_with(entry, PLATFORMS)

async def test_rule_config_entry_unload_and_integration_discovery_unload(
    hass, monkeypatch
):
    unload_platforms = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", unload_platforms)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    client = SimpleNamespace()
    hass.data[DOMAIN] = {CLIENTS: [client]}
    entry.runtime_data = SimpleNamespace(client=client, async_shutdown=AsyncMock())
    assert await async_unload_entry(hass, entry) is True
    assert hass.data[DOMAIN][CLIENTS] == []
    unload_platforms.assert_awaited_once_with(entry, PLATFORMS)

    cancel = Mock()
    hass.data[DOMAIN] = {"cancel_discovery": cancel, "pending_discovery": {ADDRESS}}
    assert await async_unload(hass) is True
    cancel.assert_called_once_with()
    assert "cancel_discovery" not in hass.data[DOMAIN]
    assert "pending_discovery" not in hass.data[DOMAIN]
    assert await async_unload(hass) is True
    assert await async_unload(hass) is True

    hass.data.pop(DOMAIN)
    assert await async_unload(hass) is True


@pytest.mark.parametrize("present, expected", [(True, False), (False, True)])
async def test_rule_device_removal_only_allowed_when_heater_is_absent(
    hass, monkeypatch, present, expected
):
    address_present = Mock(return_value=present)
    monkeypatch.setattr(
        "custom_components.dimplex_ble.bluetooth.async_address_present",
        address_present,
    )
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY}
    )
    assert await async_remove_config_entry_device(hass, entry, object()) is expected
    address_present.assert_called_once_with(hass, ADDRESS, connectable=True)


async def test_rule_coordinator_returns_snapshot_and_wraps_connection_failures(hass):
    client = SimpleNamespace(async_read_snapshot=AsyncMock(return_value=snapshot()))
    entry, coordinator = make_coordinator(hass, client=client)
    assert coordinator.config_entry is entry
    assert coordinator.hass is hass
    assert coordinator.client is client
    assert coordinator.name == DOMAIN
    assert coordinator.update_interval.total_seconds() == 10
    assert coordinator.always_update is False
    await coordinator.async_refresh()
    assert coordinator.data == snapshot()


async def test_rule_coordinator_shutdown_closes_client(hass):
    client = SimpleNamespace(
        async_read_snapshot=AsyncMock(return_value=snapshot()),
        async_shutdown=AsyncMock(),
    )
    _, coordinator = make_coordinator(hass, client=client)
    await coordinator.async_shutdown()
    client.async_shutdown.assert_awaited_once()


async def test_rule_coordinator_unavailable_update_is_retryable(hass, caplog):
    caplog.set_level(logging.INFO, logger="custom_components.dimplex_ble.coordinator")
    from custom_components.dimplex_ble.client import DimplexConnectionError


    client = SimpleNamespace(
        async_read_snapshot=AsyncMock(
            side_effect=DimplexConnectionError("proxy unavailable")
        )
    )
    _, coordinator = make_coordinator(hass, client=client)
    for _ in range(2):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
    assert client.async_read_snapshot.await_count == 2
    with pytest.raises(UpdateFailed) as err:
        await coordinator._async_update_data()
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "cannot_connect"
    assert isinstance(err.value.__cause__, DimplexConnectionError)
    client.async_read_snapshot.side_effect = None
    client.async_read_snapshot.return_value = snapshot()
    await coordinator.async_refresh()
    messages = [record.getMessage() for record in caplog.records]
    assert sum("Error fetching dimplex_ble data" in message for message in messages) == 1
    assert sum("Fetching dimplex_ble data recovered" in message for message in messages) == 1


async def test_rule_coordinator_authentication_failure_requests_reauth(hass):
    from homeassistant.exceptions import ConfigEntryAuthFailed

    from custom_components.dimplex_ble.client import DimplexAuthenticationError

    client = SimpleNamespace(
        async_read_snapshot=AsyncMock(
            side_effect=DimplexAuthenticationError("authentication required")
        ),
        async_retry_authenticated_connection=AsyncMock(
            side_effect=DimplexAuthenticationError("bond was lost")
        ),
    )
    _, coordinator = make_coordinator(hass, client=client)

    with pytest.raises(ConfigEntryAuthFailed) as err:
        await coordinator._async_update_data()

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "authentication_failed"
    client.async_retry_authenticated_connection.assert_awaited_once()


async def test_rule_coordinator_auth_retry_connection_failure_is_retryable(hass):
    from custom_components.dimplex_ble.client import (
        DimplexAuthenticationError,
        DimplexConnectionError,
    )

    client = SimpleNamespace(
        async_read_snapshot=AsyncMock(
            side_effect=DimplexAuthenticationError("authentication required")
        ),
        async_retry_authenticated_connection=AsyncMock(
            side_effect=DimplexConnectionError("proxy unavailable")
        ),
    )
    _, coordinator = make_coordinator(hass, client=client)

    with pytest.raises(UpdateFailed) as err:
        await coordinator._async_update_data()

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "cannot_connect"


async def test_rule_coordinator_retries_clean_authenticated_connection(hass):
    from custom_components.dimplex_ble.client import DimplexAuthenticationError

    client = SimpleNamespace(
        async_read_snapshot=AsyncMock(
            side_effect=DimplexAuthenticationError("authentication required")
        ),
        async_retry_authenticated_connection=AsyncMock(return_value=snapshot()),
    )
    _, coordinator = make_coordinator(hass, client=client)

    assert await coordinator._async_update_data() == snapshot()
    client.async_retry_authenticated_connection.assert_awaited_once()


def test_rule_entity_device_identity_is_stable_and_does_not_expose_proxy(hass):
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            product_code="EP4213",
            ui_version="1.7",
            rf_firmware_revision=5,
        ),
    )
    entity = DimplexClimate(coordinator)
    assert entity.unique_id == "aa:bb:cc:dd:ee:20_heater"
    assert entity.device_info["identifiers"] == {(DOMAIN, "aa:bb:cc:dd:ee:20")}
    assert entity.device_info["connections"] == {("bluetooth", "aa:bb:cc:dd:ee:20")}
    assert entity.device_info["name"] == "Dimplex Heater 1"
    assert entity.device_info["model"] == "QRAD100E"
    assert entity.device_info["model_id"] == "EP4213"
    assert entity.device_info["sw_version"] == "UI 1.7 / RF 5"
    assert PROXY not in str(entity.device_info)


def test_rule_device_model_is_unknown_before_the_first_poll(hass):
    _, coordinator = make_coordinator(hass)
    entity = DimplexClimate(coordinator)
    assert entity.device_info["name"] == "Dimplex Heater 1"
    assert entity.device_info["model"] is None



def test_rule_climate_uses_ha_temperature_limits_without_reported_snapshot(hass):
    _, coordinator = make_coordinator(hass)
    entity = DimplexClimate(coordinator)
    assert entity.native_current_temperature is None
    assert entity.native_target_temperature is None
    assert entity.min_temp == 7.0
    assert entity.max_temp == 35.0




async def test_rule_climate_translates_protocol_rejections(hass):
    from custom_components.dimplex_ble.protocol import DimplexProtocolError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(
            side_effect=DimplexProtocolError("bad value")
        )
    )
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(ServiceValidationError) as err:
        await DimplexClimate(coordinator).async_set_temperature(temperature=22)
    assert err.value.translation_key == "invalid_gatt_request"



def test_rule_error_sensor_omits_raw_bytes_when_unreported(hass):
    _, coordinator = make_coordinator(hass, data=snapshot(error_hex=None))
    sensor = DimplexErrorCodeSensor(coordinator)
    assert sensor.native_value == 48879
    assert sensor.extra_state_attributes is None



@pytest.mark.parametrize(
    ("service_name", "error", "translation_key"),
    [
        ("read_gatt", "protected", "protected_characteristic"),
        ("read_gatt", "protocol", "invalid_gatt_request"),
        ("write_gatt", "protected", "protected_characteristic"),
        ("write_gatt", "protocol", "invalid_gatt_request"),
    ],
)
async def test_rule_raw_access_translates_protocol_rejections(
    hass, monkeypatch, service_name, error, translation_key
):
    from custom_components.dimplex_ble.protocol import (
        DimplexProtocolError,
        ProtectedCharacteristicError,
    )

    error_type = (
        ProtectedCharacteristicError
        if error == "protected"
        else DimplexProtocolError
    )
    client = SimpleNamespace(
        async_read_raw=AsyncMock(side_effect=error_type("rejected")),
        async_write_raw=AsyncMock(side_effect=error_type("rejected")),
    )
    _, coordinator = make_coordinator(hass, client=client)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    entry.runtime_data = coordinator
    _install_service_entry_lookup(hass, monkeypatch, entry)
    integration_services.async_setup_services(hass)
    args = {"device_id": "registry-device", "service_group": 0, "characteristic": 1}
    if service_name == "write_gatt":
        args["payload"] = "01"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, service_name, args, blocking=True, return_response=True
        )
    assert err.value.translation_key == translation_key


def test_rule_climate_exposes_only_reported_temperature_and_supported_capabilities(
    hass,
):
    _, coordinator = make_coordinator(hass, data=snapshot())
    entity = DimplexClimate(coordinator)
    assert entity.available is True
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.hvac_modes == [HVACMode.HEAT]
    assert entity.native_temperature_unit == UnitOfTemperature.CELSIUS
    assert entity.target_temperature_step == 1
    assert entity.supported_features == (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    assert entity.native_current_temperature == 19.5
    assert entity.native_target_temperature == 21.0
    assert entity.min_temp == 5.0
    assert entity.max_temp == 30.0


@pytest.mark.parametrize(
    ("heating_active", "expected"),
    [(True, HVACAction.HEATING), (False, HVACAction.IDLE), (None, None)],
)
def test_rule_climate_reports_only_directly_reported_hvac_action(
    hass, heating_active, expected
):
    _, coordinator = make_coordinator(
        hass, data=snapshot(heating_active=heating_active)
    )
    assert DimplexClimate(coordinator).hvac_action == expected


def test_rule_diagnostic_sensors_expose_lcd_temperature_and_output_power(
    hass,
):
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            rated_power=500,
            lcd_temperature=29.0,
            trac_status_hex="010164",
            trac_output_power_percent=100,
        ),
    )
    lcd_sensor = DimplexLcdTemperatureSensor(coordinator)
    output_sensor = DimplexOutputPowerSensor(coordinator)
    power_sensor = DimplexCalculatedPowerSensor(coordinator)
    assert lcd_sensor.native_value == 29.0
    assert lcd_sensor.device_class == "temperature"
    assert lcd_sensor.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert lcd_sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert lcd_sensor.entity_registry_enabled_default is False
    assert output_sensor.native_value == 100
    assert output_sensor.extra_state_attributes == {"trac_raw": "010164"}
    assert output_sensor.native_unit_of_measurement == PERCENTAGE
    assert output_sensor.state_class == "measurement"
    assert output_sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert output_sensor.entity_registry_enabled_default is False
    assert power_sensor.native_value == 500
    assert power_sensor.device_class == "power"
    assert power_sensor.native_unit_of_measurement == "W"
    assert power_sensor.state_class == "measurement"
    assert power_sensor.entity_category is None



@pytest.mark.parametrize(
    ("mode_hex", "expected"),
    [("030000000000", "eco"), ("040000000000", None), ("020000000000", None)],
)
def test_rule_climate_reports_only_verified_presets(hass, mode_hex, expected):
    _, coordinator = make_coordinator(hass, data=snapshot(mode_hex=mode_hex))
    entity = DimplexClimate(coordinator)
    assert entity.preset_modes == ["eco"]
    assert entity.preset_mode == expected



def test_rule_climate_ignores_malformed_reported_preset(hass):
    _, coordinator = make_coordinator(hass, data=snapshot(mode_hex="not-hex"))
    assert DimplexClimate(coordinator).preset_mode is None
    _, empty_coordinator = make_coordinator(hass)
    assert DimplexClimate(empty_coordinator).preset_mode is None


async def test_rule_climate_translates_preset_write_failures(hass):
    from custom_components.dimplex_ble.client import DimplexConnectionError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(side_effect=DimplexConnectionError("offline"))
    )
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(HomeAssistantError) as err:
        await DimplexClimate(coordinator).async_set_preset_mode("eco")
    assert err.value.translation_key == "cannot_connect"
    assert err.value.translation_domain == DOMAIN
    assert isinstance(err.value.__cause__, DimplexConnectionError)

async def test_rule_climate_writes_verified_preset_mode(hass):
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=b"\x03"))
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    coordinator.async_request_refresh = AsyncMock()

    await DimplexClimate(coordinator).async_set_preset_mode("eco")

    assert client.async_write_raw.await_args == (
        (0, 0x1001, b"\x03\x00\x00\x00\x00\x00"),
    )
    coordinator.async_request_refresh.assert_awaited_once()


async def test_rule_climate_rejects_unverified_preset_mode(hass):
    _, coordinator = make_coordinator(hass, data=snapshot())
    with pytest.raises(ServiceValidationError) as err:
        await DimplexClimate(coordinator).async_set_preset_mode("boost")
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "invalid_preset"

@pytest.mark.parametrize(
    "data",
    [None, snapshot(minimum=None), snapshot(maximum=None)],
)
def test_rule_climate_is_unavailable_without_snapshot_or_reported_range(hass, data):
    _, coordinator = make_coordinator(hass, data=data)
    assert DimplexClimate(coordinator).available is False


async def test_rule_climate_rejects_invalid_and_unavailable_setpoints(hass):
    _, coordinator = make_coordinator(hass, data=snapshot())
    entity = DimplexClimate(coordinator)
    for value in (True, "21", 20.5, float("nan"), float("inf"), 4, 31):
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_set_temperature(temperature=value)
        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "invalid_setpoint"

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_temperature()
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "invalid_setpoint"
    for missing_bound in (snapshot(minimum=None), snapshot(maximum=None)):
        coordinator.data = missing_bound
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_set_temperature(temperature=21)
        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "setpoint_unavailable"
    coordinator.data = None
    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_temperature(temperature=21)
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "setpoint_unavailable"


async def test_rule_climate_writes_inclusive_reported_boundaries(hass):
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=b"\x16\x00"))
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexClimate(coordinator)
    for temperature, payload in ((5, b"\x05\x00"), (30, b"\x1e\x00")):
        await entity.async_set_temperature(temperature=temperature)
        assert client.async_write_raw.await_args_list[-1].args == (0, 0x1023, payload)
    assert client.async_write_raw.await_count == 2
    assert coordinator.async_request_refresh.await_count == 2


async def test_rule_climate_translates_ble_write_failures(hass):
    from custom_components.dimplex_ble.client import DimplexConnectionError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(
            side_effect=DimplexConnectionError("offline")
        )
    )
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(HomeAssistantError) as err:
        await DimplexClimate(coordinator).async_set_temperature(temperature=22)
    assert err.value.translation_key == "cannot_connect"
    assert err.value.translation_domain == DOMAIN
    assert isinstance(err.value.__cause__, DimplexConnectionError)


async def test_rule_select_preserves_reported_unit_and_validates_writes(hass):
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=b"\x01"))
    _, coordinator = make_coordinator(hass, data=snapshot(unit=1), client=client)
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexDisplayUnitSelect(coordinator)
    assert entity.current_option == "fahrenheit"
    assert entity.options == ["celsius", "fahrenheit"]
    assert entity.entity_category == EntityCategory.CONFIG
    await entity.async_select_option("celsius")
    client.async_write_raw.assert_awaited_once_with(0, 0x1005, b"\x00")
    coordinator.async_request_refresh.assert_awaited_once_with()

    for invalid in (None, "kelvin"):
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_select_option(invalid)
        assert err.value.translation_key == "invalid_temperature_unit"
        assert err.value.translation_domain == DOMAIN


@pytest.mark.parametrize(
    "unit, expected",
    [
        (None, None),
        (2, None),
        (0, "celsius"),
        (1, "fahrenheit"),
    ],
)
def test_rule_select_uses_only_known_reported_units(hass, unit, expected):
    data = snapshot(unit=unit) if unit is not None else None
    _, coordinator = make_coordinator(hass, data=data)
    if unit == 2:
        coordinator.data = snapshot(unit=2)
    assert DimplexDisplayUnitSelect(coordinator).current_option == expected


async def test_rule_select_translates_ble_write_failures(hass):
    from custom_components.dimplex_ble.client import DimplexConnectionError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(
            side_effect=DimplexConnectionError("offline")
        )
    )
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(HomeAssistantError) as err:
        await DimplexDisplayUnitSelect(coordinator).async_select_option("fahrenheit")
    assert err.value.translation_key == "cannot_connect"
    assert err.value.translation_domain == DOMAIN


async def test_rule_select_preserves_characteristic_error_translation(hass):
    from custom_components.dimplex_ble.client import DimplexCharacteristicError

    failure = DimplexCharacteristicError("missing")
    client = SimpleNamespace(async_write_raw=AsyncMock(side_effect=failure))
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(ServiceValidationError) as err:
        await DimplexDisplayUnitSelect(coordinator).async_select_option("fahrenheit")
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "characteristic_unavailable"
    assert err.value.__cause__ is failure


def test_rule_diagnostic_sensors_preserve_unknown_error_and_raw_mode(hass):
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(error=64206, error_hex="ee fa", mode_hex="fe01"),
    )
    error_sensor = DimplexErrorCodeSensor(coordinator)
    mode_sensor = DimplexOperatingModeSensor(coordinator)
    assert error_sensor.native_value == 64206
    assert error_sensor.extra_state_attributes == {"raw_bytes": "ee fa"}
    assert error_sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert mode_sensor.native_value == "fe01"
    assert mode_sensor.entity_category == EntityCategory.DIAGNOSTIC
    rated_sensor = DimplexRatedPowerSensor(
        make_coordinator(
            hass,
            data=snapshot(rated_power=500, power_loading_hex="f401980800000000"),
        )[1]
    )
    assert rated_sensor.native_value == 500
    assert rated_sensor.extra_state_attributes == {
        "power_loading_raw": "f401980800000000"
    }
    assert rated_sensor.device_class == "power"
    assert rated_sensor.native_unit_of_measurement == "W"
    assert rated_sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert rated_sensor.entity_registry_enabled_default is False
    assert mode_sensor.entity_registry_enabled_default is False


def test_rule_diagnostic_sensors_are_empty_before_first_snapshot(hass):
    _, coordinator = make_coordinator(hass)
    assert DimplexErrorCodeSensor(coordinator).native_value is None
    assert DimplexErrorCodeSensor(coordinator).extra_state_attributes is None
    assert DimplexOperatingModeSensor(coordinator).native_value is None


@pytest.mark.parametrize(
    "entity_type",
    [DimplexRatedPowerSensor, DimplexOutputPowerSensor],
)
def test_rule_diagnostic_sensor_raw_attributes_are_empty_without_payload(
    hass, entity_type
):
    _, coordinator = make_coordinator(hass, data=snapshot())
    assert entity_type(coordinator).extra_state_attributes is None


@pytest.mark.parametrize(
    "data",
    [None, snapshot(rated_power=500), snapshot(trac_output_power_percent=100)],
)
def test_rule_calculated_power_requires_both_source_values(hass, data):
    _, coordinator = make_coordinator(hass, data=data)
    assert DimplexCalculatedPowerSensor(coordinator).native_value is None


async def test_rule_diagnostics_redact_addresses_and_report_bond_owner_health(
    hass, monkeypatch
):
    entry, coordinator = make_coordinator(hass, data=snapshot())
    coordinator.last_update_success = False
    entry.runtime_data = coordinator
    find_proxy_mock = Mock(return_value=object())
    route_mock = Mock(return_value=object())
    passkey_mock = Mock(return_value=True)
    monkeypatch.setattr(
        "custom_components.dimplex_ble.diagnostics.find_proxy_entry",
        find_proxy_mock,
    )
    monkeypatch.setattr(
        "custom_components.dimplex_ble.diagnostics.ble_device_from_source",
        route_mock,
    )
    monkeypatch.setattr(
        "custom_components.dimplex_ble.diagnostics.proxy_supports_passkey",
        passkey_mock,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    expected_snapshot = asdict(snapshot())
    expected_snapshot["model"] = "REDACTED"
    assert result == {
        "entry": {"address": "REDACTED", "proxy_source": "REDACTED"},
        "bond_owner": {
            "configured": True,
            "proxy_entry_loaded": True,
            "route_available": True,
            "passkey_relay_supported": True,
        },
        "coordinator": {
            "last_update_success": False,
            "data": expected_snapshot,
        },
    }
    assert ADDRESS not in str(result)
    find_proxy_mock.assert_called_once_with(hass, PROXY)
    route_mock.assert_called_once_with(hass, ADDRESS, PROXY)
    passkey_mock.assert_called_once_with(hass, PROXY)
    assert PROXY not in str(result)


async def test_rule_diagnostics_report_missing_snapshot_without_bond_route(hass):
    entry, coordinator = make_coordinator(
        hass, entry=MockConfigEntry(domain=DOMAIN, data={})
    )
    entry.runtime_data = coordinator

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["bond_owner"] == {
        "configured": False,
        "proxy_entry_loaded": False,
        "route_available": False,
        "passkey_relay_supported": False,
    }
    assert result["coordinator"]["data"] is None


async def test_rule_diagnostics_reject_non_string_proxy_source(hass, monkeypatch):
    entry, coordinator = make_coordinator(
        hass,
        entry=MockConfigEntry(
            domain=DOMAIN,
            data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: 123},
        ),
    )
    entry.runtime_data = coordinator
    find_proxy_mock = Mock()
    route_mock = Mock()
    passkey_mock = Mock()
    monkeypatch.setattr(
        "custom_components.dimplex_ble.diagnostics.find_proxy_entry",
        find_proxy_mock,
    )
    monkeypatch.setattr(
        "custom_components.dimplex_ble.diagnostics.ble_device_from_source",
        route_mock,
    )
    monkeypatch.setattr(
        "custom_components.dimplex_ble.diagnostics.proxy_supports_passkey",
        passkey_mock,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["bond_owner"] == {
        "configured": False,
        "proxy_entry_loaded": False,
        "route_available": False,
        "passkey_relay_supported": False,
    }
    find_proxy_mock.assert_not_called()
    route_mock.assert_not_called()
    passkey_mock.assert_not_called()


async def test_rule_raw_gatt_services_are_response_actions(hass, monkeypatch):
    client = SimpleNamespace(
        async_get_gatt_services=AsyncMock(return_value=[{"uuid": "service"}])
    )
    _, coordinator = make_coordinator(hass, client=client)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    entry.runtime_data = coordinator
    _install_service_entry_lookup(hass, monkeypatch, entry)
    integration_services.async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "get_gatt_services",
        {"device_id": "registry-device"},
        blocking=True,
        return_response=True,
    )
    assert response == {"services": [{"uuid": "service"}]}
    client.async_get_gatt_services.assert_awaited_once_with()


async def test_rule_raw_gatt_read_returns_hex_bytes(hass, monkeypatch):
    client = SimpleNamespace(async_read_raw=AsyncMock(return_value=b"\x00\xa5\xff"))
    _, coordinator = make_coordinator(hass, client=client)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    entry.runtime_data = coordinator
    _install_service_entry_lookup(hass, monkeypatch, entry)
    integration_services.async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "read_gatt",
        {"device_id": "registry-device", "service_group": 0, "characteristic": 4113},
        blocking=True,
        return_response=True,
    )
    assert response == {"service_group": 0, "characteristic": 4113, "value": "00a5ff"}
    client.async_read_raw.assert_awaited_once_with(0, 4113)


async def test_rule_raw_gatt_write_parses_and_returns_written_and_readback(
    hass, monkeypatch
):
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=b"\x01"))
    _, coordinator = make_coordinator(hass, client=client)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    entry.runtime_data = coordinator
    _install_service_entry_lookup(hass, monkeypatch, entry)
    integration_services.async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "write_gatt",
        {
            "device_id": "registry-device",
            "service_group": 0,
            "characteristic": 4101,
            "payload": "01",
        },
        blocking=True,
        return_response=True,
    )
    assert response == {
        "service_group": 0,
        "characteristic": 4101,
        "written": "01",
        "readback": "01",
    }
    client.async_write_raw.assert_awaited_once_with(0, 4101, b"\x01")


async def test_rule_raw_gatt_write_allows_missing_readback(hass, monkeypatch):
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=None))
    _, coordinator = make_coordinator(hass, client=client)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    entry.runtime_data = coordinator
    _install_service_entry_lookup(hass, monkeypatch, entry)
    integration_services.async_setup_services(hass)
    response = await hass.services.async_call(
        DOMAIN,
        "write_gatt",
        {
            "device_id": "registry-device",
            "service_group": 1,
            "characteristic": 8,
            "payload": "a0",
        },
        blocking=True,
        return_response=True,
    )
    assert response["readback"] is None


def _install_service_entry_lookup(hass, monkeypatch, entry):
    from custom_components.dimplex_ble import services

    monkeypatch.setattr(
        services.service,
        "async_get_device_and_config_entry",
        lambda *_args, **_kwargs: (None, entry),
    )


@pytest.mark.parametrize(
    "data",
    [
        {"device_id": "dev", "service_group": 4, "characteristic": 1},
        {"device_id": "dev", "service_group": 0, "characteristic": 65536},
    ],
)
async def test_rule_raw_read_rejects_out_of_range_gatt_addresses(hass, data):
    integration_services.async_setup_services(hass)
    with pytest.raises(probatio.error.MultipleInvalid):
        await hass.services.async_call(
            DOMAIN, "read_gatt", data, blocking=True, return_response=True
        )


@pytest.mark.parametrize("payload", ["", "xyz", "00" * 513])
async def test_rule_raw_write_rejects_invalid_payloads_before_io(
    hass, monkeypatch, payload
):
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(hass, client=client)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    entry.runtime_data = coordinator
    _install_service_entry_lookup(hass, monkeypatch, entry)
    integration_services.async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "write_gatt",
            {
                "device_id": "registry-device",
                "service_group": 0,
                "characteristic": 1,
                "payload": payload,
            },
            blocking=True,
            return_response=True,
        )
    client.async_write_raw.assert_not_awaited()


@pytest.mark.parametrize(
    "service_name",
    ["get_gatt_services", "read_gatt", "write_gatt"],
)
async def test_rule_raw_services_translate_client_failures(
    hass, monkeypatch, service_name
):
    from custom_components.dimplex_ble.client import DimplexConnectionError

    client = SimpleNamespace(
        async_get_gatt_services=AsyncMock(side_effect=DimplexConnectionError("offline")),
        async_read_raw=AsyncMock(side_effect=DimplexConnectionError("offline")),
        async_write_raw=AsyncMock(side_effect=DimplexConnectionError("offline")),
    )
    _, coordinator = make_coordinator(hass, client=client)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    entry.runtime_data = coordinator
    _install_service_entry_lookup(hass, monkeypatch, entry)
    integration_services.async_setup_services(hass)
    args = {"device_id": "registry-device"}
    if service_name != "get_gatt_services":
        args.update(service_group=0, characteristic=1)
    if service_name == "write_gatt":
        args["payload"] = "01"
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            service_name,
            args,
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "cannot_connect"
