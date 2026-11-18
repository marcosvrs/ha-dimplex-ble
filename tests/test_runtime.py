from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import probatio
import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble import (
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
    CONF_ADDRESS,
    CONF_PROXY_SOURCE,
    DOMAIN,
    PLATFORMS,
)
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.diagnostics import async_get_config_entry_diagnostics
from custom_components.dimplex_ble.protocol import HeaterSnapshot
from custom_components.dimplex_ble.select import DimplexDisplayUnitSelect
from custom_components.dimplex_ble.sensor import (
    DimplexErrorCodeSensor,
    DimplexOperatingModeSensor,
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
    )


def make_coordinator(hass, *, data=None, client=None, entry=None):
    config_entry = entry or MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    client = client or SimpleNamespace(address=ADDRESS)
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
    assert len(queued) == 2
    assert hass.data[DOMAIN]["cancel_discovery"] is register.return_value


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
    assert [type(entity) for entity in added["sensor"]] == [
        DimplexErrorCodeSensor,
        DimplexOperatingModeSensor,
    ]

async def test_rule_setup_entry_keeps_entities_registered_if_heater_is_offline(
    hass, monkeypatch
):
    from custom_components.dimplex_ble.client import DimplexConnectionError

    client = SimpleNamespace(
        address=ADDRESS,
        async_read_snapshot=AsyncMock(
            side_effect=DimplexConnectionError("not connected")
        ),
    )
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(
        "custom_components.dimplex_ble.DimplexBluetoothClient", client_factory
    )
    forwarded = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", forwarded)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    await async_setup_entry(hass, entry)
    client_factory.assert_called_once_with(hass, ADDRESS, PROXY)
    assert entry.runtime_data.client is client
    assert entry.runtime_data.hass is hass
    assert entry.runtime_data.config_entry is entry
    assert entry.runtime_data.last_update_success is False
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
    assert await async_unload_entry(hass, entry) is True
    unload_platforms.assert_awaited_once_with(entry, PLATFORMS)

    cancel = Mock()
    hass.data[DOMAIN] = {"cancel_discovery": cancel}
    assert await async_unload(hass) is True
    cancel.assert_called_once_with()
    assert "cancel_discovery" not in hass.data[DOMAIN]
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


async def test_rule_coordinator_unavailable_update_is_retryable(hass):
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
    assert client.async_read_snapshot.await_count == 3


def test_rule_entity_device_identity_is_stable_and_does_not_expose_proxy(hass):
    _, coordinator = make_coordinator(hass, data=snapshot())
    entity = DimplexClimate(coordinator)
    assert entity.unique_id == "aa:bb:cc:dd:ee:20_heater"
    assert entity.device_info["identifiers"] == {(DOMAIN, "aa:bb:cc:dd:ee:20")}
    assert entity.device_info["connections"] == {("bluetooth", "aa:bb:cc:dd:ee:20")}
    assert entity.device_info["name"] == "Dimplex Heater 1"
    assert entity.device_info["model"] == "QRAD100E"
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
    assert entity.min_temp == super(DimplexClimate, entity).min_temp
    assert entity.max_temp == super(DimplexClimate, entity).max_temp




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
    assert entity.temperature_unit == UnitOfTemperature.CELSIUS
    assert entity.target_temperature_step == 1
    assert entity.supported_features == ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.native_current_temperature == 19.5
    assert entity.native_target_temperature == 21.0
    assert entity.min_temp == 5.0
    assert entity.max_temp == 30.0


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
    assert mode_sensor.entity_registry_enabled_default is False


def test_rule_diagnostic_sensors_are_empty_before_first_snapshot(hass):
    _, coordinator = make_coordinator(hass)
    assert DimplexErrorCodeSensor(coordinator).native_value is None
    assert DimplexErrorCodeSensor(coordinator).extra_state_attributes is None
    assert DimplexOperatingModeSensor(coordinator).native_value is None


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

    assert result == {
        "entry": {"address": "REDACTED", "proxy_source": "REDACTED"},
        "bond_owner": {
            "configured": True,
            "proxy_entry_loaded": True,
            "route_available": True,
            "passkey_relay_supported": True,
        },
        "coordinator": {"last_update_success": False, "data": asdict(snapshot())},
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
