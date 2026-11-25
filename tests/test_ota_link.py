"""Firmware update over the BLE link against the fake proxy."""

from __future__ import annotations

import pytest
from bleak.exc import BleakError
from conftest import ADDRESS, PIN, PROXY, FakeProxy, gatt_failure
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble import ota
from custom_components.dimplex_ble.const import (
    CONF_ADDRESS,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    DOMAIN,
)
from custom_components.dimplex_ble.protocol import OTA_COMMAND_UUID, OTA_ENABLE

pytestmark = pytest.mark.usefixtures("skip_dependencies")

OK = bytes.fromhex("0100e817")
REFUSED = bytes((0x01, 0x05, (-(0x17 + 1 + 5)) & 0xFF, 0x17))
IMAGE = ota.Image("EP4213", "5.0", bytes(range(256)) * 2)  # 512 bytes, 3 rows


@pytest.fixture(autouse=True)
def fast(monkeypatch) -> None:
    monkeypatch.setattr("custom_components.dimplex_ble.link.OTA_SUBSCRIBE_WAIT_S", 0)
    monkeypatch.setattr(ota, "REPLY_TIMEOUT", 0.05)
    monkeypatch.setattr(ota, "EXIT_SETTLE_S", 0)


def ota_heater(proxy: FakeProxy, answer=lambda cmd: OK) -> FakeProxy:
    """The heater's update channel: answer each frame on C51D."""
    proxy.services.add(OTA_ENABLE)
    service = next(iter(proxy.services))
    char = type(service.characteristics[0])(
        OTA_COMMAND_UUID, ["read", "write", "notify"]
    )
    service.characteristics.append(char)
    proxy.frames = []

    def on_write(address, data):
        if address is None and len(data) >= 6 and data[:1] == b"\x01":
            proxy.frames.append(data[1])
            reply = answer(data[1])
            if reply is not None:
                proxy.clients[-1].notify_from_heater(OTA_COMMAND_UUID, reply)

    proxy.on_write = on_write
    return proxy


async def setup(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater AM2086",
        unique_id=ADDRESS.lower(),
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY, CONF_PASSKEY: PIN},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def mode_writes(proxy: FakeProxy) -> list[bytes]:
    from custom_components.dimplex_ble.protocol import gatt_uuid_candidates

    uuids = {pair[1] for pair in gatt_uuid_candidates(*OTA_ENABLE)}
    return [e[2] for e in proxy.events if e[0:1] == ("write",) and e[1] in uuids]


async def test_an_update_uses_a_fresh_link_and_every_frame_is_acknowledged(
    hass, fake_bt
) -> None:
    proxy = ota_heater(fake_bt.add())
    coordinator = await setup(hass)
    proxy.events.clear()
    seen: list[float] = []

    await coordinator.link.async_flash(IMAGE, seen.append)

    assert ("connect", False, False) in proxy.events  # no cache: MTU exchanged
    assert proxy.frames == [ota.ENTER, ota.ROW, ota.ROW, ota.ROW, ota.VERIFY, ota.EXIT]
    assert mode_writes(proxy) == [b"\x01", b"\x00"]
    assert proxy.events[-1] == "disconnect" and seen[-1] == 1.0


@pytest.mark.parametrize(
    "answer", [lambda cmd: REFUSED if cmd == ota.ROW else OK, lambda cmd: None]
)
async def test_a_refusal_or_silence_exits_and_leaves_update_mode(
    hass, fake_bt, answer
) -> None:
    proxy = ota_heater(fake_bt.add(), answer)
    coordinator = await setup(hass)

    with pytest.raises(ota.UpdateError):
        await coordinator.link.async_flash(IMAGE)

    assert proxy.frames[-1] == ota.EXIT
    assert mode_writes(proxy) == [b"\x01", b"\x00"]
    assert proxy.events[-1] == "disconnect"


async def test_a_small_link_or_missing_channel_is_refused_before_any_write(
    hass, fake_bt
) -> None:
    proxy = ota_heater(fake_bt.add())
    proxy.fresh_mtu = 185
    coordinator = await setup(hass)

    with pytest.raises(ota.UpdateError, match="MTU 185"):
        await coordinator.link.async_flash(IMAGE)

    assert proxy.frames == [] and mode_writes(proxy) == []
    assert proxy.events[-1] == "disconnect"


async def test_a_lost_link_during_the_update_is_an_update_error(hass, fake_bt) -> None:
    proxy = ota_heater(fake_bt.add())
    coordinator = await setup(hass)

    original = proxy.on_write

    def on_write(address, data):
        if address is None and data[1:2] == bytes((ota.ROW,)):
            raise gatt_failure(133)
        original(address, data)

    proxy.on_write = on_write

    with pytest.raises(ota.UpdateError, match="dropped the link") as raised:
        await coordinator.link.async_flash(IMAGE)
    assert isinstance(raised.value.__cause__, BleakError)
    assert proxy.events[-1] == "disconnect"


async def test_a_dropped_link_is_noticed_within_a_second(
    hass, fake_bt, monkeypatch
) -> None:
    """Rehearsal 2026-11-25 attempt 3: the heater rebooted mid-update."""
    import time

    monkeypatch.setattr(ota, "REPLY_TIMEOUT", 30)
    proxy = ota_heater(fake_bt.add())

    original = proxy.on_write

    def on_write(address, data):
        if address is None and len(data) >= 6 and data[1] == ota.ROW:
            proxy.clients[-1].is_connected = False
            return
        original(address, data)

    proxy.on_write = on_write
    coordinator = await setup(hass)
    started = time.monotonic()

    with pytest.raises(ota.UpdateError, match="dropped the link"):
        await coordinator.link.async_flash(IMAGE)

    assert time.monotonic() - started < 5
