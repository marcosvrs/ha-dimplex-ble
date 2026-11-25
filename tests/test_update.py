"""Firmware update entity, the Dimplex sign-in fields and the shared account."""

from __future__ import annotations

import pytest
from conftest import ADDRESS, PIN, PROXY
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble import cloud, ota
from custom_components.dimplex_ble.const import (
    CONF_ADDRESS,
    CONF_BOOST_MINUTES,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    CONF_SYNC_CLOCK,
    DOMAIN,
)
from custom_components.dimplex_ble.firmware import SIGN_IN_ISSUE, account
from custom_components.dimplex_ble.protocol import GDID, PRODUCT_CODE

pytestmark = pytest.mark.usefixtures("skip_dependencies")

GDID_RAW = bytes.fromhex("3a00c0230401")
FIRMWARE = cloud.Firmware("5.0", "https://blob.example/f.zip", "1.7.0")


def heater(fake_bt, gdid: bytes | None = GDID_RAW, *, ep: bytes | None = b"EP4213"):
    proxy = fake_bt.add()
    for address, value in ((GDID, gdid), (PRODUCT_CODE, ep)):
        if value is not None:
            proxy.services.add(address)
            proxy.values[address] = value
    return proxy


def make_entry(hass, address: str = ADDRESS) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater AM2086",
        unique_id=address.lower(),
        data={CONF_ADDRESS: address, CONF_PROXY_SOURCE: PROXY, CONF_PASSKEY: PIN},
    )
    entry.add_to_hass(hass)
    return entry


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def entity_id(hass) -> str:
    return er.async_get(hass).async_get_entity_id(
        "update", DOMAIN, f"{ADDRESS.lower()}_firmware"
    )


@pytest.fixture
def signed_in(hass_storage) -> None:
    hass_storage[f"{DOMAIN}.cloud"] = {
        "version": 1,
        "key": f"{DOMAIN}.cloud",
        "data": {"refresh_token": "R"},
    }


@pytest.fixture
def dimplex(monkeypatch):
    """The Dimplex cloud, as the integration calls it."""
    calls: dict[str, list] = {"refresh": [], "latest": [], "sign_in": []}

    async def refresh(session, token):
        calls["refresh"].append(token)
        return "A", "R2"

    async def latest(session, access, label):
        calls["latest"].append(label)
        return FIRMWARE

    async def sign_in(session, email, password):
        calls["sign_in"].append(email)
        return "R"

    monkeypatch.setattr(cloud, "async_refresh", refresh)
    monkeypatch.setattr(cloud, "async_latest_firmware", latest)
    monkeypatch.setattr(cloud, "async_sign_in", sign_in)
    return calls


async def test_not_signed_in_the_entity_is_unavailable(hass, fake_bt, dimplex) -> None:
    heater(fake_bt)
    await setup(hass, make_entry(hass))

    assert hass.states.get(entity_id(hass)).state == STATE_UNAVAILABLE
    assert dimplex["latest"] == []


@pytest.mark.usefixtures("signed_in")
async def test_signed_in_it_offers_dimplex_latest_firmware(
    hass, fake_bt, dimplex
) -> None:
    heater(fake_bt)
    await setup(hass, make_entry(hass))

    state = hass.states.get(entity_id(hass))
    assert state.attributes["latest_version"] == "5.0"
    # The label is read from the heater: its id (0x2002) and the pairing PIN.
    assert dimplex["latest"] == [cloud.Label("010423C0003A", str(PIN).zfill(6))]


@pytest.mark.usefixtures("signed_in")
async def test_an_unread_heater_id_keeps_it_unavailable(hass, fake_bt, dimplex) -> None:
    heater(fake_bt, gdid=bytes(6))
    await setup(hass, make_entry(hass))

    assert hass.states.get(entity_id(hass)).state == STATE_UNAVAILABLE


async def update_entity(hass) -> None:
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": entity_id(hass)}, blocking=True
    )


@pytest.mark.usefixtures("signed_in")
async def test_a_failed_check_keeps_the_last_answer(
    hass, fake_bt, dimplex, monkeypatch
) -> None:
    heater(fake_bt)
    await setup(hass, make_entry(hass))

    async def down(*args):
        raise cloud.CloudError("down")

    monkeypatch.setattr(cloud, "async_latest_firmware", down)
    await update_entity(hass)

    assert hass.states.get(entity_id(hass)).attributes["latest_version"] == "5.0"


@pytest.mark.usefixtures("signed_in")
async def test_install_downloads_checks_and_flashes(
    hass, fake_bt, dimplex, monkeypatch
) -> None:
    heater(fake_bt)
    coordinator = await setup(hass, make_entry(hass))
    image = ota.Image("EP4213", "5.0", b"x")
    flashed = []

    async def download(session, firmware):
        return b"zip"

    def load(zip_bytes, ep):
        assert (zip_bytes, ep) == (b"zip", "EP4213")
        return image

    async def flash(img, progress):
        progress(0.5)
        progress(1.0)
        flashed.append(img)

    monkeypatch.setattr(cloud, "async_download", download)
    monkeypatch.setattr(ota, "load_release", load)
    monkeypatch.setattr(coordinator.link, "async_flash", flash)

    await hass.services.async_call(
        "update", "install", {"entity_id": entity_id(hass)}, blocking=True
    )

    assert flashed == [image]
    assert hass.states.get(entity_id(hass)).attributes["in_progress"] is False


@pytest.mark.usefixtures("signed_in")
@pytest.mark.parametrize("failure", ["download", "flash"])
async def test_install_failures_are_reported(
    hass, fake_bt, dimplex, monkeypatch, failure
) -> None:
    heater(fake_bt)
    coordinator = await setup(hass, make_entry(hass))

    async def download(session, firmware):
        if failure == "download":
            raise cloud.CloudError("403")
        return b"zip"

    async def flash(img, progress):
        raise ota.UpdateError("No reply to row")

    monkeypatch.setattr(cloud, "async_download", download)
    monkeypatch.setattr(ota, "load_release", lambda z, ep: ota.Image(ep, "5.0", b"x"))
    monkeypatch.setattr(coordinator.link, "async_flash", flash)

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            "update",
            "install",
            {"entity_id": entity_id(hass)},
            blocking=True,
        )
    expected = {
        "download": "firmware_download_failed",
        "flash": "firmware_update_failed",
    }[failure]
    assert raised.value.translation_key == expected, raised.value
    assert hass.states.get(entity_id(hass)).attributes["in_progress"] is False


@pytest.mark.usefixtures("signed_in")
async def test_install_needs_the_heater_model(hass, fake_bt, dimplex) -> None:
    heater(fake_bt, ep=None)
    await setup(hass, make_entry(hass))  # the heater reports no EP id

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "update", "install", {"entity_id": entity_id(hass)}, blocking=True
        )


@pytest.mark.usefixtures("signed_in")
async def test_a_refused_sign_in_raises_a_repair_and_forgets_the_token(
    hass, fake_bt, dimplex, monkeypatch, hass_storage
) -> None:
    heater(fake_bt)

    async def refused(session, token):
        raise cloud.AuthError("invalid_grant")

    monkeypatch.setattr(cloud, "async_refresh", refused)
    await setup(hass, make_entry(hass))

    assert ir.async_get(hass).async_get_issue(DOMAIN, SIGN_IN_ISSUE) is not None
    assert not account(hass).signed_in
    with pytest.raises(cloud.AuthError):
        await account(hass).async_access_token()


async def test_sign_in_fields_show_until_signed_in(hass, fake_bt, dimplex) -> None:
    heater(fake_bt)
    entry = make_entry(hass)
    other = make_entry(hass, "AA:BB:CC:DD:EE:21")
    await setup(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert {str(key) for key in result["data_schema"].schema} >= {"email", "password"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SYNC_CLOCK: True,
            CONF_BOOST_MINUTES: 60.0,
            "email": "me@x",
            "password": "p",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_SYNC_CLOCK: True,
        CONF_BOOST_MINUTES: 60,
    }  # no secrets
    assert dimplex["sign_in"] == ["me@x"] and account(hass).signed_in
    assert other.state is not None  # the other heater was scheduled for a reload

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "email" not in {str(key) for key in result["data_schema"].schema}


@pytest.mark.parametrize(
    ("error", "user", "expected"),
    [
        (None, {"email": "me@x"}, "incomplete_sign_in"),
        (cloud.AuthError("no"), {"email": "me@x", "password": "p"}, "invalid_auth"),
        (
            cloud.CloudError("down"),
            {"email": "me@x", "password": "p"},
            "cannot_connect",
        ),
    ],
)
async def test_sign_in_errors(
    hass, fake_bt, dimplex, monkeypatch, error, user, expected
) -> None:
    heater(fake_bt)
    entry = make_entry(hass)
    await setup(hass, entry)
    if error is not None:

        async def fail(session, email, password):
            raise error

        monkeypatch.setattr(cloud, "async_sign_in", fail)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SYNC_CLOCK: True, CONF_BOOST_MINUTES: 60.0, **user}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
