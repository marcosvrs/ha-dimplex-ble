from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import homeassistant.config_entries as config_entries
import pytest
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_REAUTH, SOURCE_USER
from homeassistant.data_entry_flow import InvalidData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble import config_flow
from custom_components.dimplex_ble.client import DimplexConnectionError
from custom_components.dimplex_ble.const import (
    CONF_ADDRESS,
    CONF_ADDRESS_TYPE,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    DOMAIN,
)
from custom_components.dimplex_ble.pairing import (
    DEBUG_LOG_PREFIX,
    DimplexPairingError,
    DimplexPairingInProgressError,
)


@pytest.fixture(autouse=True)
def mock_flow_dependencies(hass, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep config-flow tests independent of host adapters and proxy runtime."""
    monkeypatch.setattr(config_entries, "async_process_deps_reqs", AsyncMock())
    monkeypatch.setattr(
        hass.config_entries, "async_setup", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_request_active_scan",
        AsyncMock(),
    )
    pairing_tasks: list[asyncio.Future[None]] = []

    async def start_pairing(*_args):
        task = asyncio.get_running_loop().create_future()
        pairing_tasks.append(task)
        return task

    async def send_passkey(*_args):
        task = pairing_tasks[-1]
        if not task.done():
            task.set_result(None)

    monkeypatch.setattr(
        config_flow, "async_start_pairing", AsyncMock(side_effect=start_pairing)
    )
    monkeypatch.setattr(
        config_flow, "async_send_passkey", AsyncMock(side_effect=send_passkey)
    )

def service_info(
    address: str,
    name: str | None,
    rssi: int = -50,
    *,
    device_name: str | None = None,
    source: str | None = "proxy",
    connectable: bool = True,
):
    return SimpleNamespace(
        device=SimpleNamespace(address=address, name=device_name),
        name=name,
        rssi=rssi,
        source=source,
        connectable=connectable,
    )

async def start_bluetooth_flow(hass, info):
    """Open discovery and retry if the initial pairing request could not start."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=info,
    )
    if result["type"] == "form" and result["step_id"] == "pair":
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )
    return result


async def start_user_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


@pytest.mark.parametrize("name", [None, "", "Radiator"])
async def test_rule_discovery_rejects_non_dimplex_advertisement(hass, name, caplog):
    caplog.set_level(logging.DEBUG, logger="custom_components.dimplex_ble.config_flow")
    info = service_info("AA:BB:CC:DD:EE:01", name)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=info,
    )
    assert result["type"] == "abort"
    assert result["reason"] == "not_dimplex"
    assert "Rejected a Bluetooth discovery that is not Dimplex" in caplog.messages

async def test_rule_bluetooth_discovery_aborts_when_no_pairing_proxy(
    hass, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG, logger="custom_components.dimplex_ble.config_flow")
    info = service_info("AA:BB:CC:DD:EE:02", "heater<Dimplex>")
    discovered = Mock(return_value=[])
    monkeypatch.setattr(
        config_flow.bluetooth, "async_discovered_service_info", discovered
    )
    proxy_selection = Mock(return_value=None)
    monkeypatch.setattr(config_flow, "select_proxy_source", proxy_selection)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=info,
    )
    assert result["type"] == "abort"
    assert result["reason"] == "proxy_passkey_support_missing"
    assert (
        "Rejected a Dimplex discovery because no passkey-capable route was found"
        in caplog.messages
    )
    proxy_selection.assert_called_once_with(
        hass, format_mac("AA:BB:CC:DD:EE:02"), [info]
    )

async def test_rule_auto_discovery_uses_advertised_name_for_flow_and_entry_title(
    hass, monkeypatch
):
    info = service_info(
        "aa:bb:cc:dd:ee:03",
        None,
        device_name="sample-heater <Dimplex>",
        source="proxy-1",
    )
    discovered = Mock(return_value=[info])
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        discovered,
    )
    proxy_selection = Mock(return_value="proxy-1")
    monkeypatch.setattr(config_flow, "select_proxy_source", proxy_selection)
    client = SimpleNamespace(async_pair=AsyncMock())
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(config_flow, "DimplexBluetoothClient", client_factory)
    pairing_task = asyncio.get_running_loop().create_future()
    pairing_task.set_result(None)
    start_pairing = AsyncMock(return_value=pairing_task)
    monkeypatch.setattr(config_flow, "async_start_pairing", start_pairing)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=info,
    )
    assert result["type"] == "form"
    assert result["step_id"] == "passkey"
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    assert flow.context["title_placeholders"] == {"name": "sample-heater"}
    configured = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: "012345"}
    )
    assert flow._pairing_retry_task is None
    discovered.assert_called_once_with(hass, connectable=True)
    address = format_mac("aa:bb:cc:dd:ee:03")
    assert start_pairing.await_count == 1
    client_factory.assert_called_once_with(
        hass, address, "proxy-1", info.device
    )
    assert configured["title"] == "Dimplex Heater sample-heater"
    assert configured["result"].unique_id == address
    assert configured["data"] == {
        CONF_ADDRESS: format_mac("aa:bb:cc:dd:ee:03"),
        CONF_PROXY_SOURCE: "proxy-1",
    }
    assert "passkey" not in configured["data"]

async def test_rule_discovery_starts_pairing_when_add_is_selected(
    hass, monkeypatch
):
    info = service_info("AA:BB:CC:DD:EE:41", "heater<Dimplex>")
    monkeypatch.setattr(
        config_flow, "select_proxy_source", Mock(return_value="proxy")
    )
    client = SimpleNamespace(async_pair=AsyncMock())
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(config_flow, "DimplexBluetoothClient", client_factory)
    pairing_task = asyncio.get_running_loop().create_future()
    start_pairing = AsyncMock(return_value=pairing_task)
    monkeypatch.setattr(config_flow, "async_start_pairing", start_pairing)

    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=info,
    )

    assert discovered["type"] == "form"
    assert discovered["step_id"] == "passkey"
    start_pairing.assert_awaited_once_with(
        hass, "proxy", format_mac(info.device.address), client.async_pair
    )
    client_factory.assert_called_once_with(
        hass, format_mac(info.device.address), "proxy", info.device
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    assert flow._pairing_task is pairing_task
    retry_task = flow._pairing_retry_task
    assert retry_task is not None

    pairing_task.set_result(None)
    await retry_task


async def test_rule_discovery_flow_close_cancels_pairing(hass, monkeypatch):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(
        config_flow,
        "DimplexBluetoothClient",
        Mock(return_value=client),
    )
    pairing_task = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(
        config_flow, "async_start_pairing", AsyncMock(return_value=pairing_task)
    )
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:67", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    retry_task = flow._pairing_retry_task
    assert retry_task is not None

    hass.config_entries.flow.async_abort(discovered["flow_id"])
    await asyncio.sleep(0)

    assert discovered["flow_id"] not in hass.config_entries.flow._progress
    assert pairing_task.cancelled()
    assert retry_task.cancelled()
    with pytest.raises(asyncio.CancelledError):
        await retry_task


async def test_flow_removal_cancels_pairing_when_retry_task_is_absent(
    hass, monkeypatch
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:68", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    pairing_task = asyncio.get_running_loop().create_future()
    flow._pairing_task = pairing_task
    flow._pairing_retry_task = None

    flow.async_remove()

    assert pairing_task.cancelled()
    hass.config_entries.flow.async_abort(discovered["flow_id"])

async def test_rule_bluetooth_discovery_keeps_multiple_heaters_as_entries(
    hass, monkeypatch
):
    infos = [
        service_info("AA:BB:CC:DD:EE:31", "sample-heater-1<Dimplex>"),
        service_info("AA:BB:CC:DD:EE:32", "sample-heater-2<Dimplex>"),
    ]
    monkeypatch.setattr(config_flow, "select_proxy_source", Mock(return_value="proxy"))
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(
        config_flow, "DimplexBluetoothClient", Mock(return_value=client)
    )

    created_entries = []
    for info in infos:
        discovered = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_BLUETOOTH},
            data=info,
        )
        created_entries.append(
            await hass.config_entries.flow.async_configure(
                discovered["flow_id"], {CONF_PASSKEY: "012345"}
            )
        )

    assert [result["title"] for result in created_entries] == [
        "Dimplex Heater sample-heater-1",
        "Dimplex Heater sample-heater-2",
    ]
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_rule_bluetooth_without_display_name_uses_generic_title(
    hass, monkeypatch
):
    reserved_titles: set[str] = set()
    hass.data.setdefault(DOMAIN, {})["reserved_device_titles"] = reserved_titles
    monkeypatch.setattr(config_flow, "select_proxy_source", Mock(return_value="proxy"))
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(
        config_flow, "DimplexBluetoothClient", Mock(return_value=client)
    )
    started = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:33", "<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[started["flow_id"]]
    assert "title_placeholders" not in flow.context
    assert started["step_id"] == "passkey"
    assert started["description_placeholders"] == {
        "name": "the selected heater"
    }
    created = await hass.config_entries.flow.async_configure(
        started["flow_id"], {CONF_PASSKEY: "012345"}
    )
    assert created["title"] == "Dimplex Heater 1"
    assert flow._next_device_title() == "Dimplex Heater 2"
    assert reserved_titles == {"Dimplex Heater 1", "Dimplex Heater 2"}
async def test_rule_bluetooth_duplicate_is_aborted_before_pairing(hass, monkeypatch):
    proxy_selection = Mock(return_value="proxy")
    monkeypatch.setattr(config_flow, "select_proxy_source", proxy_selection)
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(config_flow, "DimplexBluetoothClient", lambda *_: client)
    info = service_info("AA:BB:CC:DD:EE:04", "heater<Dimplex>")
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info
    )
    created = await hass.config_entries.flow.async_configure(
        first["flow_id"], {CONF_PASSKEY: "123456"}
    )
    assert created["type"] == "create_entry"

    duplicate = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=info
    )
    assert duplicate["type"] == "abort"
    assert duplicate["reason"] == "already_configured"



async def test_rule_manual_flow_scans_when_cache_empty(hass, monkeypatch):
    discovered = Mock(return_value=[])
    monkeypatch.setattr(
        config_flow.bluetooth, "async_discovered_service_info", discovered
    )
    scan = AsyncMock()
    monkeypatch.setattr(config_flow.bluetooth, "async_request_active_scan", scan)
    result = await start_user_flow(hass)
    assert result["type"] == "abort"
    assert result["reason"] == "no_heaters_found"
    assert discovered.call_args_list == [call(hass, connectable=True)]
    scan.assert_awaited_once_with(hass, duration=5.0)


async def test_rule_manual_flow_aborts_when_only_unsupported_heaters_are_visible(
    hass, monkeypatch
):
    infos = [service_info("AA:BB:CC:DD:EE:05", "heater<Dimplex>")]
    discovered = Mock(return_value=infos)
    monkeypatch.setattr(
        config_flow.bluetooth, "async_discovered_service_info", discovered
    )
    proxy_selection = Mock(return_value=None)
    monkeypatch.setattr(config_flow, "select_proxy_source", proxy_selection)
    result = await start_user_flow(hass)
    assert result["type"] == "abort"
    assert result["reason"] == "proxy_passkey_support_missing"
    discovered.assert_called_once_with(hass, connectable=True)
    proxy_selection.assert_called_once_with(
        hass, format_mac("AA:BB:CC:DD:EE:05"), infos
    )

async def test_rule_manual_flow_skips_unsupported_heater_and_keeps_supported_one(
    hass, monkeypatch
):
    infos = [
        service_info("AA:BB:CC:DD:EE:05", "heater-unavailable<Dimplex>", -20),
        service_info(
            "AA:BB:CC:DD:EE:17",
            "heater-host<Dimplex>",
            -10,
            source="host",
        ),
        service_info(
            "AA:BB:CC:DD:EE:17",
            "heater-proxy<Dimplex>",
            -42,
            source="proxy-1",
        ),
        service_info(
            "AA:BB:CC:DD:EE:17",
            "heater-weaker-proxy<Dimplex>",
            -60,
            source="proxy-1",
        ),
    ]
    discovered = Mock(return_value=infos)
    monkeypatch.setattr(
        config_flow.bluetooth, "async_discovered_service_info", discovered
    )
    proxy_selection = Mock(
        side_effect=[None, "proxy-1"]
    )
    monkeypatch.setattr(config_flow, "select_proxy_source", proxy_selection)
    result = await start_user_flow(hass)
    options = next(iter(result["data_schema"].schema.values())).config["options"]
    assert options == [
        {
            "value": format_mac("AA:BB:CC:DD:EE:17"),
            "label": "Dimplex heater-proxy (-42 dBm)",
        }
    ]
    discovered.assert_called_once_with(hass, connectable=True)
    assert proxy_selection.call_args_list == [
        call(hass, format_mac("AA:BB:CC:DD:EE:05"), infos),
        call(hass, format_mac("AA:BB:CC:DD:EE:17"), infos),
    ]

async def test_rule_manual_labels_show_advertised_names_for_multiple_heaters(
    hass, monkeypatch
):
    infos = [
        service_info("AA:BB:CC:DD:EE:08", "unrelated", -4),
        service_info(
            "AA:BB:CC:DD:EE:06",
            None,
            -73,
            device_name="sample-heater-2 <Dimplex>",
            source="route-06",
        ),
        service_info(
            "AA:BB:CC:DD:EE:07",
            "sample-heater-1<Dimplex>",
            -31,
            source="route-07",
        ),
    ]
    discovered = Mock(return_value=infos)
    scan = AsyncMock()
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        discovered,
    )
    monkeypatch.setattr(config_flow.bluetooth, "async_request_active_scan", scan)
    proxy_selection = Mock(
        side_effect=lambda _hass, address, _service_infos: f"route-{address[-2:]}"
    )
    monkeypatch.setattr(config_flow, "select_proxy_source", proxy_selection)
    result = await start_user_flow(hass)
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    scan.assert_awaited_once_with(hass, duration=5.0)
    assert proxy_selection.call_args_list == [
        call(hass, format_mac("AA:BB:CC:DD:EE:06"), infos),
        call(hass, format_mac("AA:BB:CC:DD:EE:07"), infos),
    ]
    schema = result["data_schema"]
    options = next(iter(schema.schema.values())).config["options"]
    assert options == [
        {
            "value": format_mac("AA:BB:CC:DD:EE:07"),
            "label": "Dimplex sample-heater-1 (-31 dBm)",
        },
        {
            "value": format_mac("AA:BB:CC:DD:EE:06"),
            "label": "Dimplex sample-heater-2 (-73 dBm)",
        },
    ]
    assert "AA:BB:CC:DD:EE:06" not in str(options)
    assert "AA:BB:CC:DD:EE:07" not in str(options)


async def test_rule_manual_route_name_prefers_selected_proxy_and_falls_back_safely(
    hass, monkeypatch
):
    east = "AA:BB:CC:DD:EE:40"
    south = "AA:BB:CC:DD:EE:41"
    infos = [
        service_info(east, "sample-heater-1<Dimplex>", -45, source="host"),
        service_info(
            east,
            None,
            -30,
            device_name="sample-heater-2<Dimplex>",
            source="proxy",
        ),
        service_info(south, "sample-heater-3<Dimplex>", -60, source="host"),
        service_info(south, "unrelated", -45, source="proxy"),
    ]
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        Mock(return_value=infos),
    )
    monkeypatch.setattr(config_flow, "select_proxy_source", Mock(return_value="proxy"))

    result = await start_user_flow(hass)
    options = next(iter(result["data_schema"].schema.values())).config["options"]

    assert options == [
        {
            "value": format_mac(east),
            "label": "Dimplex sample-heater-2 (-30 dBm)",
        },
        {
            "value": format_mac(south),
            "label": "Dimplex sample-heater-3 (-45 dBm)",
        },
    ]


async def test_rule_manual_labels_number_heaters_without_local_names(hass, monkeypatch):
    infos = [
        service_info("AA:BB:CC:DD:EE:42", "<Dimplex>", -20),
        service_info("AA:BB:CC:DD:EE:43", "<Dimplex>", -40),
    ]
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        Mock(return_value=infos),
    )
    monkeypatch.setattr(config_flow, "select_proxy_source", Mock(return_value="proxy"))

    result = await start_user_flow(hass)
    options = next(iter(result["data_schema"].schema.values())).config["options"]

    assert [option["label"] for option in options] == [
        "Dimplex heater 1 (-20 dBm)",
        "Dimplex heater 2 (-40 dBm)",
    ]

async def test_rule_manual_selection_rejects_unknown_address_without_identifier_entry(
    hass, monkeypatch
):
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        lambda *_args, **_kwargs: [
            service_info("AA:BB:CC:DD:EE:09", "heater<Dimplex>")
        ],
    )
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    result = await start_user_flow(hass)
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    invalid = await flow.async_step_user(
        {CONF_ADDRESS: format_mac("AA:BB:CC:DD:EE:99")}
    )
    assert invalid["type"] == "form"
    assert invalid["errors"] == {"base": "device_not_found"}


async def test_rule_manual_selection_persists_route_and_advertised_nonconflicting_title(
    hass, monkeypatch
):
    reserved_titles = {
        "Dimplex Heater radiator-bedroom",
        "Dimplex Heater radiator-bedroom (2)",
    }
    hass.data.setdefault(DOMAIN, {})["reserved_device_titles"] = reserved_titles
    address = format_mac("AA:BB:CC:DD:EE:10")
    infos = [
        service_info(
            "AA:BB:CC:DD:EE:10",
            "radiator-bedroom<Dimplex>",
            source="proxy-bedroom",
        )
    ]
    discovered = Mock(return_value=infos)
    monkeypatch.setattr(
        config_flow.bluetooth, "async_discovered_service_info", discovered
    )
    proxy_selection = Mock(return_value="proxy-bedroom")
    monkeypatch.setattr(config_flow, "select_proxy_source", proxy_selection)
    client = SimpleNamespace(async_pair=AsyncMock())
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(config_flow, "DimplexBluetoothClient", client_factory)
    result = await start_user_flow(hass)
    selected = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: address}
    )
    assert selected["step_id"] == "passkey"
    flow = hass.config_entries.flow._progress[selected["flow_id"]]
    assert flow.context["title_placeholders"] == {"name": "radiator-bedroom"}
    assert selected["description_placeholders"] == {"name": "radiator-bedroom"}
    pin_input = {CONF_PASSKEY: "654321"}
    created = await hass.config_entries.flow.async_configure(
        selected["flow_id"], pin_input
    )
    assert created["title"] == "Dimplex Heater radiator-bedroom (3)"
    assert created["result"].unique_id == address
    assert reserved_titles == {
        "Dimplex Heater radiator-bedroom",
        "Dimplex Heater radiator-bedroom (2)",
        "Dimplex Heater radiator-bedroom (3)",
    }
    discovered.assert_called_once_with(hass, connectable=True)
    proxy_selection.assert_called_once_with(hass, address, infos)
    client_factory.assert_called_once_with(
        hass, address, "proxy-bedroom", infos[0].device
    )
    assert created["data"] == {
        CONF_ADDRESS: address,
        CONF_PROXY_SOURCE: "proxy-bedroom",
    }

async def test_rule_title_reservation_ignores_other_domains(hass, monkeypatch):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    started = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:20", "radiator-north<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[started["flow_id"]]
    unrelated_entry = MockConfigEntry(
        domain="other_integration", title="Dimplex Heater radiator-north"
    )
    unrelated_entry.add_to_hass(hass)
    hass.data.pop(DOMAIN, None)
    assert flow._next_device_title() == "Dimplex Heater radiator-north"
    assert hass.data[DOMAIN]["reserved_device_titles"] == {
        "Dimplex Heater radiator-north"
    }


@pytest.mark.parametrize("passkey", ["12345", "1234567", "12 456", "１２３４５６"])
async def test_rule_invalid_passkey_is_not_persisted(hass, monkeypatch, passkey):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    result = await start_bluetooth_flow(
        hass, service_info("AA:BB:CC:DD:EE:11", "heater<Dimplex>")
    )
    invalid = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSKEY: passkey}
    )
    assert invalid["type"] == "form"
    assert invalid["errors"] == {"base": "invalid_passkey"}
    assert not hass.config_entries.async_entries(DOMAIN)
    hass.config_entries.flow.async_abort(result["flow_id"])


@pytest.mark.parametrize("passkey", [None, "12345"])
async def test_rule_invalid_passkey_direct_step_removes_supplied_value(
    hass, monkeypatch, passkey
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    started = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:18", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[started["flow_id"]]
    user_input = {} if passkey is None else {CONF_PASSKEY: passkey}
    result = await flow.async_step_passkey(user_input)
    assert user_input == {}
    assert result["errors"] == {"base": "invalid_passkey"}


async def test_rule_successful_direct_passkey_step_clears_value(hass, monkeypatch):
    proxy_selection = Mock(return_value="proxy-1")
    monkeypatch.setattr(config_flow, "select_proxy_source", proxy_selection)
    client = SimpleNamespace(async_pair=AsyncMock())
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(config_flow, "DimplexBluetoothClient", client_factory)
    address = format_mac("AA:BB:CC:DD:EE:19")
    info = service_info(address, "heater<Dimplex>", source="proxy-1")
    info.device.details = {"address_type": 1}
    started = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=info,
    )
    flow = hass.config_entries.flow._progress[started["flow_id"]]
    user_input = {CONF_PASSKEY: "999999"}
    created = await flow.async_step_passkey(user_input)
    assert user_input == {}
    assert created["type"] == "create_entry"
    assert created["data"] == {
        CONF_ADDRESS: address,
        CONF_PROXY_SOURCE: "proxy-1",
        CONF_ADDRESS_TYPE: 1,
    }
    client_factory.assert_called_once_with(hass, address, "proxy-1", info.device)


@pytest.mark.parametrize(
    ("passkey", "valid"),
    [
        ("000000", True),
        ("999999", True),
        ("12345", False),
        ("1234567", False),
        ("12 456", False),
        ("１２３４５６", False),
        ("AAAAAA", False),
    ],
)
def test_rule_valid_passkey_requires_six_ascii_digits(passkey, valid):
    assert config_flow.DimplexBleConfigFlow._valid_passkey(passkey) is valid


@pytest.mark.parametrize("passkey", [None, 123456])
async def test_rule_nonstring_passkey_is_rejected_by_flow_schema(
    hass, monkeypatch, passkey
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    result = await start_bluetooth_flow(
        hass, service_info("AA:BB:CC:DD:EE:15", "heater<Dimplex>")
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSKEY: passkey}
        )
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_rule_passkey_form_is_password_input_without_autocomplete(
    hass, monkeypatch
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    result = await start_bluetooth_flow(
        hass, service_info("AA:BB:CC:DD:EE:12", "heater<Dimplex>")
    )
    selector = next(iter(result["data_schema"].schema.values()))
    assert selector.config["type"] == "password"
    assert selector.config["autocomplete"] == "off"
    assert result["errors"] == {}
    hass.config_entries.flow.async_abort(result["flow_id"])



def test_rule_config_flow_starts_without_selected_device_or_proxy():
    flow = config_flow.DimplexBleConfigFlow()
    assert flow._address is None
    assert flow._proxy_source is None
    assert flow._advertised_name is None


def test_rule_user_form_requests_its_declared_step(monkeypatch):
    flow = config_flow.DimplexBleConfigFlow()
    form = Mock(return_value={"form": True})
    monkeypatch.setattr(flow, "async_show_form", form)
    flow._devices = {"AA:BB:CC:DD:EE:21": ("proxy", -42, None)}
    assert flow._show_user_form() == {"form": True}
    assert form.call_args.kwargs["step_id"] == "user"
    assert form.call_args.kwargs["errors"] == {}

@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (DimplexPairingError("proxy refused"), "proxy_passkey_support_missing"),
        (DimplexConnectionError("offline"), "pairing_failed"),
        (HomeAssistantError("action failed"), "pairing_failed"),
    ],
)
async def test_rule_pairing_failures_clear_pin_and_keep_entry_uncreated(
    hass, monkeypatch, error, reason
):
    monkeypatch.setattr(
        config_flow, "select_proxy_source", lambda *_: "proxy-1"
    )
    monkeypatch.setattr(
        config_flow, "async_send_passkey", AsyncMock(side_effect=error)
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:13", "heater<Dimplex>"),
    )
    passkey_input = {CONF_PASSKEY: "112233"}
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    failed = await flow.async_step_passkey(passkey_input)
    assert passkey_input == {}
    assert failed["type"] == "form"
    assert failed["errors"] == {"base": reason}
    assert not hass.config_entries.async_entries(DOMAIN)
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_rule_manual_selection_waits_for_pin_before_creating_entry(
    hass, monkeypatch
):
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        lambda *_args, **_kwargs: [
            service_info("AA:BB:CC:DD:EE:14", "heater<Dimplex>")
        ],
    )
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    result = await start_user_flow(hass)
    selected = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ADDRESS: format_mac("AA:BB:CC:DD:EE:14")},
    )
    assert selected["step_id"] == "passkey"
    assert not hass.config_entries.async_entries(DOMAIN)
    hass.config_entries.flow.async_abort(selected["flow_id"])


@pytest.mark.parametrize("missing_field", ["address", "proxy_source"])
async def test_rule_passkey_step_aborts_if_proxy_route_disappears(
    hass, monkeypatch, missing_field
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:16", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[result["flow_id"]]
    setattr(flow, f"_{missing_field}", None)

    aborted = await flow.async_step_passkey()

    assert aborted["type"] == "abort"
    assert aborted["reason"] == "proxy_unavailable"


async def test_rule_add_starts_pairing_immediately(hass, monkeypatch):
    events: list[str] = []
    address = format_mac("AA:BB:CC:DD:EE:50")
    pairing_complete = asyncio.get_running_loop().create_future()
    passkey_received = asyncio.Event()
    monkeypatch.setattr(
        config_flow, "select_proxy_source", Mock(return_value="proxy")
    )
    client = SimpleNamespace(async_pair=AsyncMock())
    client_factory = Mock(return_value=client)
    monkeypatch.setattr(config_flow, "DimplexBluetoothClient", client_factory)

    async def start_pairing(_hass, _source, _address, _pair):
        events.append("start")
        return pairing_complete

    async def send_passkey(_hass, _source, _address, _passkey):
        events.append("passkey")
        passkey_received.set()

    start = AsyncMock(side_effect=start_pairing)
    send = AsyncMock(side_effect=send_passkey)
    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    monkeypatch.setattr(config_flow, "async_send_passkey", send)

    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info(address, "heater<Dimplex>"),
    )

    assert discovered["step_id"] == "passkey"
    assert events == ["start"]
    start.assert_awaited_once_with(hass, "proxy", address, client.async_pair)
    send.assert_not_awaited()
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    refreshed = await flow.async_step_passkey()
    assert refreshed["step_id"] == "passkey"
    start.assert_awaited_once()

    submission = asyncio.create_task(
        hass.config_entries.flow.async_configure(
            discovered["flow_id"], {CONF_PASSKEY: "123456"}
        )
    )
    try:
        await asyncio.wait_for(passkey_received.wait(), timeout=1)
        assert events == ["start", "passkey"]
        assert not submission.done()
        assert not hass.config_entries.async_entries(DOMAIN)

        pairing_complete.set_result(None)
        created = await submission
    finally:
        if not pairing_complete.done():
            pairing_complete.set_result(None)
        if not submission.done():
            try:
                await submission
            except Exception:
                pass
        elif not submission.cancelled():
            submission.exception()
        if discovered["flow_id"] in hass.config_entries.flow._progress:
            hass.config_entries.flow.async_abort(discovered["flow_id"])

    assert created["type"] == "create_entry"
    assert flow._pairing_retry_task is None
    send.assert_awaited_once_with(hass, "proxy", address, "123456")
    client.async_pair.assert_not_awaited()

async def test_pairing_retry_scheduler_keeps_active_task(hass, monkeypatch):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:65", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    flow._pairing_retry_task = asyncio.get_running_loop().create_future()
    create_task = Mock(side_effect=lambda coroutine: coroutine.close())
    monkeypatch.setattr(hass, "async_create_task", create_task)

    flow._async_ensure_pairing_retry()

    create_task.assert_not_called()
    flow._pairing_retry_task.cancel()
    hass.config_entries.flow.async_abort(discovered["flow_id"])




async def test_pairing_retry_skips_when_another_attempt_starts_during_wait(
    hass, monkeypatch
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:67", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    if flow._pairing_retry_task is not None:
        flow._pairing_retry_task.cancel()
    if flow._pairing_task is not None:
        flow._pairing_task.cancel()
    flow._pairing_task = None
    pairing_task = asyncio.get_running_loop().create_future()

    async def wake_retry(_delay):
        flow._pairing_task = pairing_task

    monkeypatch.setattr(config_flow.asyncio, "sleep", wake_retry)
    pairing_task.set_result(None)
    await flow._async_retry_pairing_while_flow_active()

    hass.config_entries.flow.async_abort(discovered["flow_id"])
async def test_rule_title_allocator_uses_first_available_suffix(hass, monkeypatch):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    reserved_titles = {
        "Dimplex Heater radiator-gap",
        "Dimplex Heater radiator-gap (3)",
    }
    hass.data.setdefault(DOMAIN, {})["reserved_device_titles"] = reserved_titles
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:66", "radiator-gap<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]

    assert flow._next_device_title() == "Dimplex Heater radiator-gap (2)"
    assert "Dimplex Heater radiator-gap (2)" in reserved_titles
    hass.config_entries.flow.async_abort(discovered["flow_id"])



@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (DimplexPairingInProgressError("busy"), "pairing_in_progress"),
        (DimplexPairingError("missing action"), "proxy_passkey_support_missing"),
        (DimplexConnectionError("offline"), "pairing_failed"),
        (HomeAssistantError("service unavailable"), "pairing_failed"),
    ],
)
async def test_rule_pairing_start_errors_are_shown_before_pin_entry(
    hass, monkeypatch, error, reason
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    start = AsyncMock(side_effect=error)
    monkeypatch.setattr(config_flow, "async_start_pairing", start)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:51", "<Dimplex>"),
    )

    assert result["type"] == "form"
    assert result["step_id"] == "pair"
    assert result["errors"] == {"base": reason}
    assert result["data_schema"] is not None
    assert result["description_placeholders"] == {
        "name": "the selected heater"
    }
    start.assert_awaited_once()
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_rule_pairing_start_shows_in_progress_when_proxy_is_busy(
    hass, monkeypatch
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    start = AsyncMock(side_effect=DimplexPairingInProgressError("busy"))
    monkeypatch.setattr(config_flow, "async_start_pairing", start)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:52", "heater<Dimplex>"),
    )

    assert result["type"] == "form"
    assert result["step_id"] == "pair"
    assert result["errors"] == {"base": "pairing_in_progress"}
    assert start.await_count == 1
    assert not hass.config_entries.async_entries(DOMAIN)

async def test_pair_retry_form_restarts_a_failed_initial_request(
    hass, monkeypatch
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(
        config_flow, "DimplexBluetoothClient", Mock(return_value=client)
    )
    pairing_task = asyncio.get_running_loop().create_future()
    start = AsyncMock(
        side_effect=[DimplexConnectionError("offline"), pairing_task]
    )
    monkeypatch.setattr(config_flow, "async_start_pairing", start)

    failed = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:72", "heater<Dimplex>"),
    )
    assert failed["step_id"] == "pair"
    assert failed["errors"] == {"base": "pairing_failed"}

    retried = await hass.config_entries.flow.async_configure(
        failed["flow_id"], {}
    )
    assert retried["step_id"] == "passkey"
    assert start.await_count == 2

    flow = hass.config_entries.flow._progress[failed["flow_id"]]
    retry_task = flow._pairing_retry_task
    assert retry_task is not None
    hass.config_entries.flow.async_abort(failed["flow_id"])
    await asyncio.sleep(0)

    assert pairing_task.cancelled()
    with pytest.raises(asyncio.CancelledError):
        await retry_task



async def test_rule_pairing_task_already_succeeded_creates_entry_without_relaying_pin(
    hass, monkeypatch
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    completed = asyncio.get_running_loop().create_future()
    completed.set_result(None)
    start = AsyncMock(return_value=completed)
    send = AsyncMock()
    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    monkeypatch.setattr(config_flow, "async_send_passkey", send)

    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:52", "heater<Dimplex>"),
    )
    created = await hass.config_entries.flow.async_configure(
        discovered["flow_id"], {CONF_PASSKEY: "123456"}
    )

    assert created["type"] == "create_entry"
    start.assert_awaited_once()
    send.assert_not_awaited()

async def test_pairing_retries_are_bounded_after_start_errors(hass, monkeypatch, caplog):
    """Stop automatic retries after the configured limit."""
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    caplog.set_level(
        logging.DEBUG, logger="custom_components.dimplex_ble.config_flow"
    )
    monkeypatch.setattr(config_flow, "PAIRING_RETRY_DELAY", 0)
    first = asyncio.get_running_loop().create_future()
    attempts = 0
    original_sleep = asyncio.sleep
    sleep_calls = 0

    async def immediate_sleep(_delay):
        nonlocal sleep_calls
        sleep_calls += 1
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", immediate_sleep)

    async def start_pairing(_hass, source, address, _pair):
        nonlocal attempts
        attempts += 1
        assert source == "proxy"
        assert address == format_mac("AA:BB:CC:DD:EE:53")
        if attempts > 1:
            raise DimplexConnectionError("proxy offline")
        return first

    monkeypatch.setattr(
        config_flow, "async_start_pairing", AsyncMock(side_effect=start_pairing)
    )
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:53", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    assert discovered["step_id"] == "passkey"
    assert attempts == 1

    first.set_exception(DimplexConnectionError("offline"))
    await flow._pairing_retry_task

    assert sleep_calls == 2
    assert attempts == config_flow.PAIRING_MAX_AUTOMATIC_RETRIES
    retry_messages = [record.getMessage() for record in caplog.records]
    assert retry_messages.count(
        f"{DEBUG_LOG_PREFIX} Pairing retry could not start (pairing_failed)"
    ) == 2
    assert (
        f"{DEBUG_LOG_PREFIX} Automatic pairing retry limit reached"
    ) in retry_messages

    hass.config_entries.flow.async_abort(discovered["flow_id"])


async def test_rule_pin_after_expired_attempt_is_sent_to_fresh_attempt(
    hass, monkeypatch, caplog
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    caplog.set_level(
        logging.DEBUG, logger="custom_components.dimplex_ble.config_flow"
    )
    failed = asyncio.get_running_loop().create_future()
    failed.set_exception(DimplexConnectionError("expired"))
    replacement = asyncio.get_running_loop().create_future()
    start = AsyncMock(side_effect=[failed, replacement])
    send_called = asyncio.Event()

    async def send_passkey(*_args):
        send_called.set()

    send = AsyncMock(side_effect=send_passkey)
    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    monkeypatch.setattr(config_flow, "async_send_passkey", send)
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:54", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    retry_task = flow._pairing_retry_task
    assert retry_task is not None
    retry_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await retry_task

    pin_input = {CONF_PASSKEY: "123456"}
    submission = asyncio.create_task(
        hass.config_entries.flow.async_configure(
            discovered["flow_id"], pin_input
        )
    )
    try:
        await asyncio.wait_for(send_called.wait(), timeout=1)
        address = format_mac("AA:BB:CC:DD:EE:54")
        assert [args.args[1:3] for args in start.await_args_list] == [
            ("proxy", address),
            ("proxy", address),
        ]
        send.assert_awaited_once_with(
            hass, "proxy", format_mac("AA:BB:CC:DD:EE:54"), "123456"
        )
        assert (
            f"{DEBUG_LOG_PREFIX} Submitting PIN to the selected proxy; value redacted"
            in caplog.messages
        )
        assert "123456" not in "\n".join(caplog.messages)
        assert not submission.done()
        replacement.set_result(None)
    finally:
        if not replacement.done():
            replacement.set_result(None)
        if not submission.done():
            await submission
    created = submission.result()

    assert created["type"] == "create_entry"
    assert CONF_PASSKEY not in created["data"]


async def test_pair_step_reprompts_and_aborts_when_proxy_route_disappears(
    hass, monkeypatch
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:60", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]

    prompt = await flow.async_step_pair()
    assert prompt["step_id"] == "pair"
    assert prompt["errors"] == {}
    assert prompt["data_schema"] is not None
    assert prompt["description_placeholders"] == {"name": "heater"}

    flow._address = None
    aborted = await flow.async_step_pair()
    assert aborted["type"] == "abort"
    assert aborted["reason"] == "proxy_unavailable"

    monkeypatch.setattr(config_flow, "PAIRING_RETRY_DELAY", 0)
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(
        config_flow, "DimplexBluetoothClient", Mock(return_value=client)
    )
    completed = asyncio.get_running_loop().create_future()
    completed.set_result(None)
    start = AsyncMock(return_value=completed)
    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    await flow._async_retry_pairing_while_flow_active()
    start.assert_not_awaited()

    flow._address = format_mac("AA:BB:CC:DD:EE:60")
    flow._proxy_source = None
    await flow._async_retry_pairing_while_flow_active()
    start.assert_not_awaited()
    hass.config_entries.flow.async_abort(discovered["flow_id"])


async def test_cancelled_pairing_task_is_restarted_for_pin(hass, monkeypatch):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(
        config_flow, "DimplexBluetoothClient", Mock(return_value=client)
    )
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:61", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    cancelled = asyncio.get_running_loop().create_future()
    cancelled.cancel()
    flow._pairing_task = cancelled
    replacement = asyncio.get_running_loop().create_future()
    start = AsyncMock(return_value=replacement)
    send_called = asyncio.Event()

    async def send_passkey(*_args):
        send_called.set()
        replacement.set_result(None)

    send = AsyncMock(side_effect=send_passkey)
    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    monkeypatch.setattr(config_flow, "async_send_passkey", send)
    pin_input = {CONF_PASSKEY: "123456"}

    created = await flow.async_step_passkey(pin_input)

    assert created["type"] == "create_entry"
    assert pin_input == {}
    assert send_called.is_set()
    start.assert_awaited_once()
    hass.config_entries.flow.async_abort(discovered["flow_id"])


async def test_pin_retry_start_error_is_shown_in_retry_form(
    hass, monkeypatch
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(
        config_flow, "DimplexBluetoothClient", Mock(return_value=client)
    )
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:62", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    failed = asyncio.get_running_loop().create_future()
    failed.set_exception(DimplexConnectionError("expired"))
    flow._pairing_task = failed
    start = AsyncMock(side_effect=DimplexConnectionError("proxy offline"))
    send = AsyncMock()
    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    monkeypatch.setattr(config_flow, "async_send_passkey", send)
    pin_input = {CONF_PASSKEY: "123456"}

    result = await flow.async_step_passkey(pin_input)

    assert result["step_id"] == "pair"
    assert result["errors"] == {"base": "pairing_failed"}
    assert pin_input == {}
    start.assert_awaited_once()
    send.assert_not_awaited()
    hass.config_entries.flow.async_abort(discovered["flow_id"])


async def test_pin_submission_handles_pairing_start_without_task(hass, monkeypatch):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(
        config_flow, "DimplexBluetoothClient", Mock(return_value=client)
    )
    start = AsyncMock(return_value=None)
    send = AsyncMock()
    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    monkeypatch.setattr(config_flow, "async_send_passkey", send)
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:63", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    pin_input = {CONF_PASSKEY: "123456"}

    result = await flow.async_step_passkey(pin_input)

    assert result["step_id"] == "pair"
    assert result["errors"] == {"base": "pairing_failed"}
    assert pin_input == {}
    assert flow._pairing_retry_task is not None
    send.assert_not_awaited()
    hass.config_entries.flow.async_abort(discovered["flow_id"])
    await asyncio.sleep(0)


async def test_pairing_retry_does_not_duplicate_a_pin_started_attempt(
    hass, monkeypatch, caplog
):
    caplog.set_level(
        logging.DEBUG, logger="custom_components.dimplex_ble.config_flow"
    )
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    monkeypatch.setattr(config_flow, "PAIRING_RETRY_DELAY", 0.01)
    first = asyncio.get_running_loop().create_future()
    replacement = asyncio.get_running_loop().create_future()
    start = AsyncMock(side_effect=[first, replacement])
    send_called = asyncio.Event()

    async def send_passkey(*_args):
        send_called.set()

    send = AsyncMock(side_effect=send_passkey)
    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    monkeypatch.setattr(config_flow, "async_send_passkey", send)
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:64", "heater<Dimplex>"),
    )
    first.set_exception(DimplexConnectionError("expired"))
    submission = asyncio.create_task(
        hass.config_entries.flow.async_configure(
            discovered["flow_id"], {CONF_PASSKEY: "123456"}
        )
    )
    await asyncio.wait_for(send_called.wait(), timeout=1)
    await asyncio.sleep(0.02)
    assert not any(
        "Pairing retry could not start" in message for message in caplog.messages
    )
    assert start.await_count == 2

    replacement.set_result(None)
    created = await submission
    assert created["type"] == "create_entry"


async def test_wrong_pin_and_bounded_expiry_retries(hass, monkeypatch):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    monkeypatch.setattr(config_flow, "PAIRING_RETRY_DELAY", 0)
    attempts = [
        asyncio.get_running_loop().create_future()
        for _ in range(config_flow.PAIRING_MAX_AUTOMATIC_RETRIES)
    ]
    attempt_started = [asyncio.Event() for _ in attempts]
    starts: list[tuple[str, str]] = []

    async def start_pairing(_hass, proxy, address, _pair):
        index = len(starts)
        starts.append((proxy, address))
        attempt_started[index].set()
        return attempts[index]

    start = AsyncMock(side_effect=start_pairing)
    passkey_sent = asyncio.Event()

    async def send_passkey(*_args):
        passkey_sent.set()

    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    monkeypatch.setattr(
        config_flow, "async_send_passkey", AsyncMock(side_effect=send_passkey)
    )
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:70", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    retry_task = flow._pairing_retry_task
    assert retry_task is not None

    submission = asyncio.create_task(
        hass.config_entries.flow.async_configure(
            discovered["flow_id"], {CONF_PASSKEY: "123456"}
        )
    )
    try:
        await asyncio.wait_for(passkey_sent.wait(), timeout=1)
        attempts[0].set_exception(DimplexConnectionError("authentication failed"))
        result = await asyncio.wait_for(submission, timeout=1)

        assert result["step_id"] == "passkey"
        assert result["errors"] == {"base": "pairing_failed"}
        assert not hass.config_entries.async_entries(DOMAIN)
        await asyncio.wait_for(attempt_started[1].wait(), timeout=1)

        attempts[1].set_exception(DimplexConnectionError("expired"))
        await asyncio.wait_for(attempt_started[2].wait(), timeout=1)
        attempts[2].set_exception(DimplexConnectionError("expired"))
        await asyncio.wait_for(retry_task, timeout=1)

        assert start.await_count == config_flow.PAIRING_MAX_AUTOMATIC_RETRIES
        assert starts == [
            ("proxy", format_mac("AA:BB:CC:DD:EE:70"))
        ] * config_flow.PAIRING_MAX_AUTOMATIC_RETRIES
        assert not attempts[2].cancelled()
    finally:
        if discovered["flow_id"] in hass.config_entries.flow._progress:
            hass.config_entries.flow.async_abort(discovered["flow_id"])
        for attempt in attempts:
            if not attempt.done():
                attempt.cancel()
        if not submission.done():
            submission.cancel()
        await asyncio.gather(submission, retry_task, return_exceptions=True)


async def test_passkey_relay_failure_does_not_stop_retries_while_flow_is_open(
    hass, monkeypatch
):
    monkeypatch.setattr(config_flow, "select_proxy_source", lambda *_: "proxy")
    monkeypatch.setattr(config_flow, "PAIRING_RETRY_DELAY", 0)
    client = SimpleNamespace(async_pair=AsyncMock())
    monkeypatch.setattr(
        config_flow, "DimplexBluetoothClient", Mock(return_value=client)
    )
    attempts = [
        asyncio.get_running_loop().create_future() for _ in range(2)
    ]
    attempt_started = [asyncio.Event() for _ in attempts]

    async def start_pairing(_hass, _proxy, _address, _pair):
        index = sum(event.is_set() for event in attempt_started)
        attempt_started[index].set()
        return attempts[index]

    start = AsyncMock(side_effect=start_pairing)
    monkeypatch.setattr(config_flow, "async_start_pairing", start)
    monkeypatch.setattr(
        config_flow,
        "async_send_passkey",
        AsyncMock(side_effect=HomeAssistantError("proxy action failed")),
    )
    discovered = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=service_info("AA:BB:CC:DD:EE:71", "heater<Dimplex>"),
    )
    flow = hass.config_entries.flow._progress[discovered["flow_id"]]
    retry_task = flow._pairing_retry_task
    assert retry_task is not None

    try:
        result = await hass.config_entries.flow.async_configure(
            discovered["flow_id"], {CONF_PASSKEY: "123456"}
        )
        assert result["step_id"] == "passkey"
        assert result["errors"] == {"base": "pairing_failed"}
        assert not retry_task.done()

        attempts[0].set_exception(DimplexConnectionError("expired"))
        await asyncio.wait_for(attempt_started[1].wait(), timeout=1)
        assert start.await_count == 2

        hass.config_entries.flow.async_abort(discovered["flow_id"])
        await asyncio.sleep(0)

        assert attempts[1].cancelled()
        assert retry_task.cancelled()
        await asyncio.sleep(0.01)
        assert start.await_count == 2
    finally:
        if discovered["flow_id"] in hass.config_entries.flow._progress:
            hass.config_entries.flow.async_abort(discovered["flow_id"])
        for attempt in attempts:
            if not attempt.done():
                attempt.cancel()
        if not retry_task.done():
            retry_task.cancel()
        await asyncio.gather(retry_task, return_exceptions=True)
 
async def test_reauth_repairs_existing_entry(hass, monkeypatch):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater AM6532",
        unique_id="AA:BB:CC:DD:EE:20",
        data={
            CONF_ADDRESS: "AA:BB:CC:DD:EE:20",
            CONF_PROXY_SOURCE: "proxy",
        },
    )
    entry.add_to_hass(hass)
    monkeypatch.setattr(config_flow, "ble_device_from_source", Mock(return_value=object()))

    started = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert started["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {CONF_PASSKEY: "764343"}
    )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"


async def test_reauth_rejects_invalid_pin(hass, monkeypatch):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater AM6532",
        unique_id="AA:BB:CC:DD:EE:20",
        data={
            CONF_ADDRESS: "AA:BB:CC:DD:EE:20",
            CONF_PROXY_SOURCE: "proxy",
        },
    )
    entry.add_to_hass(hass)
    monkeypatch.setattr(config_flow, "ble_device_from_source", Mock(return_value=object()))

    started = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {CONF_PASSKEY: "123"}
    )

    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_passkey"}

async def test_reauth_aborts_without_identity(hass):
    flow = config_flow.DimplexBleConfigFlow()
    flow.hass = hass
    result = await flow.async_step_reauth({})
    assert result["type"] == "abort"
    assert result["reason"] == "proxy_unavailable"


async def test_reauth_aborts_without_pairing_target(hass):
    flow = config_flow.DimplexBleConfigFlow()
    flow.hass = hass
    result = await flow.async_step_reauth_confirm({CONF_PASSKEY: "764343"})
    assert result["type"] == "abort"
    assert result["reason"] == "proxy_unavailable"


async def test_reauth_reports_pairing_start_error(hass, monkeypatch):
    flow = config_flow.DimplexBleConfigFlow()
    flow.hass = hass
    flow._address = "AA:BB:CC:DD:EE:20"
    flow._proxy_source = "proxy"
    monkeypatch.setattr(
        flow, "_async_start_pairing", AsyncMock(return_value="pairing_failed")
    )

    result = await flow.async_step_reauth_confirm({CONF_PASSKEY: "764343"})

    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "pairing_failed"}


async def test_reauth_reports_missing_pairing_task(hass, monkeypatch):
    flow = config_flow.DimplexBleConfigFlow()
    flow.hass = hass
    flow._address = "AA:BB:CC:DD:EE:20"
    flow._proxy_source = "proxy"
    monkeypatch.setattr(
        flow, "_async_start_pairing", AsyncMock(return_value=None)
    )

    result = await flow.async_step_reauth_confirm({CONF_PASSKEY: "764343"})

    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "pairing_failed"}


async def test_reauth_reports_passkey_relay_error(hass, monkeypatch):
    flow = config_flow.DimplexBleConfigFlow()
    flow.hass = hass
    flow._address = "AA:BB:CC:DD:EE:20"
    flow._proxy_source = "proxy"
    task = asyncio.get_running_loop().create_future()
    task.set_result(None)
    monkeypatch.setattr(flow, "_pairing_task", task)
    monkeypatch.setattr(flow, "_async_start_pairing", AsyncMock(return_value=None))
    monkeypatch.setattr(
        config_flow,
        "async_send_passkey",
        AsyncMock(side_effect=HomeAssistantError("relay failed")),
    )

    result = await flow.async_step_reauth_confirm({CONF_PASSKEY: "764343"})

    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "pairing_failed"}
