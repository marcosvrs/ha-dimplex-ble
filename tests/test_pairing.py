from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, call

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.dimplex_ble import pairing
from custom_components.dimplex_ble.pairing import (
    DEBUG_LOG_PREFIX,
    DimplexPairingError,
    DimplexPairingInProgressError,
    async_cancel_pairing,
    async_send_passkey,
    async_start_pairing,
    ble_device_from_source,
    find_proxy_entry,
    proxy_action_name,
    proxy_pairing_lock,
    proxy_supports_passkey,
    select_proxy_source,
)


def make_entry(
    source: str,
    *,
    name: str = "heater-proxy",
    encrypted: bool = True,
    unique_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        data={
            pairing.CONF_BLUETOOTH_MAC_ADDRESS: source,
            pairing.CONF_DEVICE_NAME: name,
            pairing.CONF_NOISE_PSK: "fake-encryption-key" if encrypted else "",
        },
        unique_id=unique_id,
    )


def make_candidate(
    source: str | None,
    rssi: int,
    device: object | None = None,
    *,
    address: str = "heater",
    connectable: bool = True,
    scanner: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        source=source,
        rssi=rssi,
        device=device if device is not None else SimpleNamespace(address=address),
        connectable=connectable,
        scanner=scanner or SimpleNamespace(get_allocations=lambda: None),
    )


def make_hass(entries: list[object] | None = None) -> MagicMock:
    hass = MagicMock()
    hass.config_entries.async_loaded_entries.return_value = entries or []
    hass.services.has_service.return_value = True
    hass.services.async_call = AsyncMock()
    hass.data = {}
    return hass


def test_pairing_lock_reuses_casefolded_source_and_separates_proxies() -> None:
    hass = make_hass()
    first = proxy_pairing_lock(hass, "AA:BB")
    assert proxy_pairing_lock(hass, "aa:bb") is first
    second = proxy_pairing_lock(hass, "other")
    assert second is not first
    assert not first.locked()
    assert hass.data[pairing.DOMAIN][pairing._PAIRING_LOCKS] == {
        "aa:bb": first,
        "other": second,
    }


def test_find_proxy_entry_matches_loaded_adapter_case_insensitively() -> None:
    entry = make_entry("AA:BB")
    hass = make_hass([make_entry("other"), entry])
    assert find_proxy_entry(hass, "aa:bb") is entry
    assert find_proxy_entry(hass, "missing") is None
    hass.config_entries.async_loaded_entries.assert_has_calls(
        [call(pairing.ESPHOME_DOMAIN), call(pairing.ESPHOME_DOMAIN)]
    )


def test_find_proxy_entry_matches_entry_identity_when_ble_address_falls_back() -> None:
    entry = make_entry("different", unique_id="AA-BB-CC-DD-EE-FF")
    hass = make_hass([entry])

    assert find_proxy_entry(hass, "aabbccddeeff") is entry



@pytest.mark.parametrize("source", ["none", "xxxx"])
def test_find_proxy_entry_ignores_missing_adapter_addresses(source: str) -> None:
    hass = make_hass([SimpleNamespace(data={})])

    assert find_proxy_entry(hass, source) is None


def test_proxy_action_name_normalizes_device_name_and_custom_action() -> None:
    entry = make_entry("proxy", name="upstairs-heater")
    assert proxy_action_name(entry) == "upstairs_heater_set_dimplex_pairing_passkey"
    assert proxy_action_name(entry, "custom") == "upstairs_heater_custom"


@pytest.mark.parametrize("name", [None, "", 42])
def test_proxy_action_name_rejects_missing_or_nonstring_device_name(name: object) -> None:
    entry = SimpleNamespace(data={pairing.CONF_DEVICE_NAME: name})
    with pytest.raises(DimplexPairingError) as err:
        proxy_action_name(entry)
    assert str(err.value) == "The ESPHome proxy has no registered device name"


def test_proxy_support_requires_encryption_entry_and_all_relay_actions() -> None:
    encrypted = make_entry("proxy")
    hass = make_hass([encrypted])
    expected_actions = [
        "heater_proxy_arm_dimplex_pairing",
        "heater_proxy_set_dimplex_pairing_passkey",
        "heater_proxy_cancel_dimplex_pairing",
    ]
    assert proxy_supports_passkey(hass, "proxy") is True
    hass.services.has_service.return_value = False
    assert proxy_supports_passkey(hass, "proxy") is False
    assert proxy_supports_passkey(hass, "missing") is False
    unencrypted = make_entry("plain", encrypted=False)
    hass.config_entries.async_loaded_entries.return_value = [unencrypted]
    assert proxy_supports_passkey(hass, "plain") is False
    assert hass.services.has_service.call_args_list == [
        *(call(pairing.ESPHOME_DOMAIN, action) for action in expected_actions),
        call(pairing.ESPHOME_DOMAIN, expected_actions[0]),
    ]



def test_proxy_support_rejects_missing_passkey_action() -> None:
    hass = make_hass([make_entry("proxy")])
    hass.services.has_service.side_effect = lambda _domain, action: not action.endswith(
        "_set_dimplex_pairing_passkey"
    )

    assert proxy_supports_passkey(hass, "proxy") is False

def test_proxy_support_accepts_entry_identity_scanner_fallback() -> None:
    entry = make_entry("other", unique_id="AA-BB-CC-DD-EE-FF")
    hass = make_hass([entry])

    assert proxy_supports_passkey(hass, "aabbccddeeff")
    assert hass.services.has_service.call_args_list == [
        call(pairing.ESPHOME_DOMAIN, "heater_proxy_arm_dimplex_pairing"),
        call(pairing.ESPHOME_DOMAIN, "heater_proxy_set_dimplex_pairing_passkey"),
        call(pairing.ESPHOME_DOMAIN, "heater_proxy_cancel_dimplex_pairing"),
    ]

def test_select_proxy_source_accepts_entry_identity_fallback() -> None:
    entry = make_entry("other", unique_id="AA-BB-CC-DD-EE-FF")
    service_infos = [make_candidate("aabbccddeeff", -50)]
    hass = make_hass([entry])

    assert select_proxy_source(hass, "heater", service_infos) == "aabbccddeeff"
    assert hass.services.has_service.call_args_list == [
        call(pairing.ESPHOME_DOMAIN, "heater_proxy_arm_dimplex_pairing"),
        call(pairing.ESPHOME_DOMAIN, "heater_proxy_set_dimplex_pairing_passkey"),
        call(pairing.ESPHOME_DOMAIN, "heater_proxy_cancel_dimplex_pairing"),
    ]


def test_select_proxy_source_chooses_strongest_eligible_connectable_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_infos = [
        make_candidate("weak", -80, address="AA-BB"),
        make_candidate("strong", -30, address="aa:bb"),
        make_candidate("no-source", -10, address="AA:BB"),
        make_candidate("other-address", -1, address="other"),
        make_candidate("non-connectable", -1, address="AA:BB", connectable=False),
    ]
    passkey_support = Mock(
        side_effect=lambda _hass, source: source
        in {"weak", "strong", "other-address", "non-connectable"}
    )
    monkeypatch.setattr(pairing, "proxy_supports_passkey", passkey_support)
    hass = make_hass()
    assert select_proxy_source(hass, "aa:bb", service_infos) == "strong"
    passkey_support.assert_has_calls(
        [
            call(hass, "weak"),
            call(hass, "strong"),
            call(hass, "no-source"),
        ]
    )

def test_select_proxy_source_prefers_capacity_before_rssi(
    hass, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not fill a proxy's last slot when another eligible proxy is freer."""
    service_infos = [
        make_candidate(
            "full",
            -10,
            address="AA:BB",
            scanner=SimpleNamespace(
                get_allocations=lambda: SimpleNamespace(free=0, slots=3)
            ),
        ),
        make_candidate(
            "strong",
            -30,
            address="AA:BB",
            scanner=SimpleNamespace(
                get_allocations=lambda: SimpleNamespace(free=1, slots=3)
            ),
        ),
        make_candidate(
            "balanced",
            -80,
            address="AA:BB",
            scanner=SimpleNamespace(
                get_allocations=lambda: SimpleNamespace(free=2, slots=3)
            ),
        ),
    ]
    scanners = {
        candidate.source: candidate.scanner
        for candidate in service_infos
        if candidate.source
    }
    monkeypatch.setattr(
        pairing.bluetooth,
        "async_scanner_by_source",
        lambda _hass, source: scanners[source],
    )
    monkeypatch.setattr(pairing, "proxy_supports_passkey", lambda *_: True)

    assert select_proxy_source(hass, "aa:bb", service_infos) == "balanced"


def test_proxy_support_rejects_entry_without_registered_device_name() -> None:
    hass = make_hass([make_entry("proxy", name="")])

    assert proxy_supports_passkey(hass, "proxy") is False
    hass.services.has_service.assert_not_called()


def test_select_proxy_source_returns_none_without_eligible_candidate(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    service_infos = [make_candidate("plain", -20)]
    monkeypatch.setattr(pairing, "proxy_supports_passkey", lambda *_: False)
    monkeypatch.setattr(
        pairing.bluetooth,
        "async_scanner_devices_by_address",
        Mock(return_value=[]),
    )
    with caplog.at_level("WARNING", logger=pairing.__name__):
        selected = select_proxy_source(make_hass(), "heater", service_infos)

    assert selected is None
    assert caplog.messages == [
        "No passkey-capable ESPHome route "
        "(connectable_candidates=1, candidates_with_source=1, matching_entries=0)"
    ]
    assert "plain" not in caplog.text


def test_select_proxy_source_uses_cached_scanner_route(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    service_infos = [make_candidate("host-adapter", -25)]
    cached_route = SimpleNamespace(
        scanner=SimpleNamespace(source="proxy"),
    )
    scanner_devices = Mock(return_value=[cached_route])
    monkeypatch.setattr(
        pairing.bluetooth,
        "async_scanner_devices_by_address",
        scanner_devices,
    )
    passkey_support = Mock(
        side_effect=lambda _hass, source: source == "proxy",
    )
    monkeypatch.setattr(pairing, "proxy_supports_passkey", passkey_support)
    hass = make_hass()

    with caplog.at_level("DEBUG", logger=pairing.__name__):
        selected = select_proxy_source(hass, "heater", service_infos)

    assert selected == "proxy"
    scanner_devices.assert_called_once_with(hass, "HEATER", connectable=True)
    assert passkey_support.call_args_list == [
        call(hass, "host-adapter"),
        call(hass, "proxy"),
    ]
    assert caplog.messages == [
        "[DEBUG-DIMPLEX-PIN-20261117] Using a cached passkey-capable "
        "Bluetooth route not present in the current discovery snapshot"
    ]

def test_ble_device_lookup_is_pinned_to_bond_owner(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    expected = SimpleNamespace(address="heater")
    scanner_devices = [
        SimpleNamespace(
            scanner=SimpleNamespace(source="other"),
            ble_device=SimpleNamespace(address="heater"),
        ),
        SimpleNamespace(
            scanner=SimpleNamespace(source="PROXY"),
            ble_device=expected,
        ),
    ]
    devices = Mock(return_value=scanner_devices)
    monkeypatch.setattr(
        pairing.bluetooth, "async_scanner_devices_by_address", devices
    )
    hass = make_hass()
    caplog.set_level("DEBUG", logger=pairing.__name__)
    assert ble_device_from_source(hass, "heater", "proxy") is expected
    assert ble_device_from_source(hass, "heater", "missing") is None
    devices.assert_has_calls(
        [
            call(hass, "HEATER", connectable=True),
            call(hass, "HEATER", connectable=True),
        ]
    )
    assert caplog.messages == [
        f"{DEBUG_LOG_PREFIX} Found a source-specific Bluetooth route",
        f"{DEBUG_LOG_PREFIX} No source-specific Bluetooth route is currently available",
    ]


def test_ble_device_lookup_normalizes_mac_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
    devices = Mock(
        return_value=[
            SimpleNamespace(
                scanner=SimpleNamespace(source="PROXY"),
                ble_device=expected,
            )
        ]
    )
    monkeypatch.setattr(
        pairing.bluetooth, "async_scanner_devices_by_address", devices
    )
    hass = make_hass()

    assert ble_device_from_source(hass, "aa:bb:cc:dd:ee:ff", "proxy") is expected
    devices.assert_called_once_with(
        hass, "AA:BB:CC:DD:EE:FF", connectable=True
    )


def test_ble_device_lookup_ignores_missing_scanner_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    devices = Mock(
        return_value=[
            SimpleNamespace(
                scanner=SimpleNamespace(source="other"),
                ble_device=SimpleNamespace(address="heater"),
            )
        ]
    )
    monkeypatch.setattr(
        pairing.bluetooth, "async_scanner_devices_by_address", devices
    )
    hass = make_hass()
    assert ble_device_from_source(hass, "heater", "proxy") is None
    devices.assert_called_once_with(hass, "HEATER", connectable=True)



async def test_pairing_starts_after_arm_and_cleans_up_after_pin_submission() -> None:
    events: list[tuple[str, object]] = []
    hass = make_hass([make_entry("proxy")])

    async def record_action(_domain, action, data, **_kwargs) -> None:
        events.append((action, data))

    hass.services.async_call = AsyncMock(side_effect=record_action)
    hass.async_create_task = lambda coroutine: asyncio.create_task(coroutine)
    pair_started = asyncio.Event()
    pair_gate = asyncio.get_running_loop().create_future()

    async def pair() -> None:
        events.append(("pair", None))
        pair_started.set()
        await pair_gate

    task = await async_start_pairing(
        hass, "proxy", "AA:BB:CC:DD:EE:01", pair
    )
    await pair_started.wait()
    assert [event[0] for event in events] == [
        "heater_proxy_arm_dimplex_pairing",
        "pair",
    ]
    assert proxy_pairing_lock(hass, "proxy").locked()

    await async_send_passkey(hass, "proxy", "AA:BB:CC:DD:EE:01", "123456")
    pair_gate.set_result(None)
    await task

    assert [event[0] for event in events] == [
        "heater_proxy_arm_dimplex_pairing",
        "pair",
        "heater_proxy_set_dimplex_pairing_passkey",
        "heater_proxy_cancel_dimplex_pairing",
    ]
    assert events[0][1] == {"address": "AABBCCDDEE01"}
    assert events[2][1] == {"address": "AABBCCDDEE01", "passkey": "123456"}
    assert not proxy_pairing_lock(hass, "proxy").locked()


async def test_pairing_start_rejects_a_busy_proxy_without_arming() -> None:
    hass = make_hass([make_entry("proxy")])
    lock = proxy_pairing_lock(hass, "proxy")
    await lock.acquire()
    try:
        with pytest.raises(DimplexPairingInProgressError) as err:
            await async_start_pairing(hass, "proxy", "AA:BB", AsyncMock())
        assert str(err.value) == (
            "The selected ESPHome proxy is already pairing with another heater"
        )
    finally:
        lock.release()
    hass.services.async_call.assert_not_awaited()


async def test_pairing_start_releases_lock_when_proxy_cannot_be_armed() -> None:
    hass = make_hass()
    with pytest.raises(DimplexPairingError):
        await async_start_pairing(hass, "proxy", "AA:BB", AsyncMock())
    assert not proxy_pairing_lock(hass, "proxy").locked()


async def test_pairing_timeout_clears_relay_and_releases_proxy_lock(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger=pairing.__name__)
    monkeypatch.setattr(pairing, "PAIRING_TIMEOUT", 0.01)
    hass = make_hass([make_entry("proxy")])
    hass.async_create_task = lambda coroutine: asyncio.create_task(coroutine)

    async def never_pair() -> None:
        await asyncio.get_running_loop().create_future()

    task = await async_start_pairing(hass, "proxy", "AA:BB", never_pair)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, timeout=0.1)
    assert any(
        record.getMessage()
        == f"{DEBUG_LOG_PREFIX} BLE pairing attempt timed out after 0 seconds"
        for record in caplog.records
    )
    assert hass.services.async_call.await_args_list[-1].args[1] == (
        "heater_proxy_cancel_dimplex_pairing"
    )
    assert not proxy_pairing_lock(hass, "proxy").locked()

async def test_pairing_cancellation_clears_relay_and_releases_proxy_lock(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = make_hass([make_entry("proxy")])
    hass.async_create_task = lambda coroutine: asyncio.create_task(coroutine)
    caplog.set_level("WARNING", logger=pairing.__name__)
    pair_started = asyncio.Event()

    async def never_pair() -> None:
        pair_started.set()
        await asyncio.get_running_loop().create_future()

    task = await async_start_pairing(hass, "proxy", "AA:BB", never_pair)
    await pair_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any(
        record.getMessage()
        == f"{DEBUG_LOG_PREFIX} BLE pairing task cancelled"
        for record in caplog.records
    )

    assert hass.services.async_call.await_args_list[-1].args[1] == (
        "heater_proxy_cancel_dimplex_pairing"
    )
    assert not proxy_pairing_lock(hass, "proxy").locked()


async def test_pairing_failure_clears_relay_and_releases_proxy_lock(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING", logger=pairing.__name__)
    hass = make_hass([make_entry("proxy")])
    hass.async_create_task = lambda coroutine: asyncio.create_task(coroutine)

    async def fail_pair() -> None:
        raise RuntimeError("pair rejected")

    task = await async_start_pairing(hass, "proxy", "AA:BB", fail_pair)
    with pytest.raises(RuntimeError, match="pair rejected"):
        await task
    failure_record = next(
        record for record in caplog.records if record.exc_info is not None
    )
    assert (
        failure_record.getMessage()
        == f"{DEBUG_LOG_PREFIX} BLE pairing attempt failed (RuntimeError)"
    )
    assert failure_record.exc_info is not None
    assert failure_record.exc_info[0] is RuntimeError
    assert hass.services.async_call.await_args_list[-1].args[1] == (
        "heater_proxy_cancel_dimplex_pairing"
    )
    assert not proxy_pairing_lock(hass, "proxy").locked()


async def test_pairing_cleanup_failure_does_not_leak_proxy_lock(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = make_hass([make_entry("proxy")])
    caplog.set_level("DEBUG", logger=pairing.__name__)
    hass.async_create_task = lambda coroutine: asyncio.create_task(coroutine)

    async def fail_cancel(_domain, action, _data, **_kwargs) -> None:
        if action == "heater_proxy_cancel_dimplex_pairing":
            raise HomeAssistantError("proxy disconnected")

    hass.services.async_call = AsyncMock(side_effect=fail_cancel)
    task = await async_start_pairing(hass, "proxy", "AA:BB", AsyncMock())
    await task
    assert not proxy_pairing_lock(hass, "proxy").locked()
    warning = next(
        record for record in caplog.records if record.levelname == "WARNING"
    )
    assert warning.getMessage() == (
        f"{DEBUG_LOG_PREFIX} ESPHome pairing action "
        "cancel_dimplex_pairing failed (HomeAssistantError)"
    )
    cleanup_record = next(
        record
        for record in caplog.records
        if record.levelname == "DEBUG"
        and "Could not clear" in record.getMessage()
    )
    assert cleanup_record.getMessage() == (
        f"{DEBUG_LOG_PREFIX} Could not clear the ESPHome pairing relay"
    )
    assert cleanup_record.exc_info is not None
    assert cleanup_record.exc_info[0] is HomeAssistantError


async def test_send_passkey_routes_normalized_address_and_pin_to_proxy_action() -> None:
    entry = make_entry("AA:BB", name="kitchen-proxy")
    hass = make_hass([entry])
    await async_send_passkey(hass, "aa:bb", "a1:b2:c3", "123456")
    hass.services.async_call.assert_awaited_once_with(
        pairing.ESPHOME_DOMAIN,
        "kitchen_proxy_set_dimplex_pairing_passkey",
        {"address": "a1b2c3", "passkey": "123456"},
        blocking=True,
    )

@pytest.mark.parametrize(
    ("entries", "service", "message"),
    [
        ([], True, "The selected ESPHome Bluetooth proxy is unavailable"),
        (
            [make_entry("proxy", encrypted=False)],
            True,
            "The ESPHome proxy native API must be encrypted",
        ),
        (
            [make_entry("proxy")],
            False,
            "The ESPHome proxy needs the Dimplex passkey component installed",
        ),
    ],
)
async def test_send_passkey_rejects_ineligible_proxy_without_disclosing_secrets(
    entries: list[object], service: bool, message: str
) -> None:
    hass = make_hass(entries)
    hass.services.has_service.return_value = service
    with pytest.raises(DimplexPairingError) as err:
        await async_send_passkey(hass, "proxy", "private-address", "654321")
    assert str(err.value) == message
    assert "private-address" not in str(err.value)
    assert "654321" not in str(err.value)
    if entries and service is False:
        hass.services.has_service.assert_called_once_with(
            pairing.ESPHOME_DOMAIN,
            "heater_proxy_set_dimplex_pairing_passkey",
        )
    hass.services.async_call.assert_not_awaited()


async def test_cancel_pairing_routes_to_proxy_clear_action() -> None:
    hass = make_hass([make_entry("AA:BB", name="living-proxy")])
    await async_cancel_pairing(hass, "aa:bb")
    hass.services.async_call.assert_awaited_once_with(
        pairing.ESPHOME_DOMAIN,
        "living_proxy_cancel_dimplex_pairing",
        {},
        blocking=True,
    )
