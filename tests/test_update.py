"""Firmware update entity, the Dimplex sign-in fields and the shared account."""

from __future__ import annotations

import pytest
from conftest import ADDRESS, PIN, PROXY, add_settings
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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
from custom_components.dimplex_ble.firmware import account
from custom_components.dimplex_ble.protocol import GDID, PRODUCT_CODE, UI_VERSION
from custom_components.dimplex_ble.settings import CONF_REFRESH_TOKEN

pytestmark = pytest.mark.usefixtures("skip_dependencies")

GDID_RAW = bytes.fromhex("3a00c0230401")
FIRMWARE = cloud.Firmware("5.0", "https://blob.example/f.zip", "1.7.0")


def heater(fake_bt, gdid: bytes | None = GDID_RAW, *, ep: bytes | None = b"EP4213"):
    proxy = fake_bt.add()
    for address, value in (
        (GDID, gdid),
        (PRODUCT_CODE, ep),
        (UI_VERSION, b"\x01\x01\x07"),
    ):
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


@pytest.fixture(autouse=True)
def no_confirm_wait(monkeypatch) -> None:
    from custom_components.dimplex_ble import update

    monkeypatch.setattr(update, "CONFIRM_WAIT_S", 0)


@pytest.fixture
def signed_in(hass):
    return add_settings(hass, data={CONF_REFRESH_TOKEN: "R"})


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

    calls["sign_in_fn"] = sign_in

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
    proxy = heater(fake_bt)
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
        proxy.values[UI_VERSION] = b"\x01\x05\x00"  # the heater now runs 5.0

    monkeypatch.setattr(cloud, "async_download", download)
    monkeypatch.setattr(ota, "load_release", load)
    monkeypatch.setattr(coordinator.link, "async_flash", flash)

    await hass.services.async_call(
        "update", "install", {"entity_id": entity_id(hass)}, blocking=True
    )

    assert flashed == [image]
    (device,) = dr.async_entries_for_config_entry(
        dr.async_get(hass), coordinator.config_entry.entry_id
    )
    assert device.sw_version.startswith("UI 5.0")  # the device page follows
    assert hass.states.get(entity_id(hass)).attributes["in_progress"] is False


@pytest.mark.usefixtures("signed_in")
@pytest.mark.parametrize("failure", ["download", "flash", "version"])
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
    version = "4.0" if failure == "version" else "5.0"
    monkeypatch.setattr(ota, "load_release", lambda z, ep: ota.Image(ep, version, b"x"))
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
        "version": "firmware_download_failed",
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


async def test_a_refused_sign_in_starts_reauthentication(
    hass, fake_bt, dimplex, monkeypatch, signed_in
) -> None:
    heater(fake_bt)

    async def refused(session, token):
        raise cloud.AuthError("invalid_grant")

    monkeypatch.setattr(cloud, "async_refresh", refused)
    await setup(hass, make_entry(hass))

    assert not account(hass).signed_in
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    assert flows[0]["step_id"] == "reauth_account"
    with pytest.raises(cloud.AuthError):
        await account(hass).async_access_token()


async def test_the_menu_adds_the_dimplex_entry_with_a_sign_in(
    hass, fake_bt, dimplex, monkeypatch
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["heater", "settings"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SYNC_CLOCK: False,
            CONF_BOOST_MINUTES: 45.0,
            "email": "me@x",
            "password": "p",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_REFRESH_TOKEN: "R"}  # no email, no password
    assert result["options"] == {CONF_SYNC_CLOCK: False, CONF_BOOST_MINUTES: 45}
    assert account(hass).signed_in

    monkeypatch.setattr(
        "homeassistant.components.bluetooth.async_discovered_service_info",
        lambda hass: [],
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["reason"] == "no_heaters_found"  # straight to heaters: no menu


@pytest.mark.parametrize(
    ("error", "user", "expected"),
    [
        (None, {"email": "me@x"}, "incomplete_sign_in"),
        (cloud.AuthError("no"), {"email": "me@x", "password": "p"}, "invalid_auth"),
        (cloud.CloudError("x"), {"email": "me@x", "password": "p"}, "cannot_connect"),
    ],
)
async def test_sign_in_errors(
    hass, fake_bt, dimplex, monkeypatch, error, user, expected
) -> None:
    if error is not None:

        async def fail(session, email, password):
            raise error

        monkeypatch.setattr(cloud, "async_sign_in", fail)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SYNC_CLOCK: True, CONF_BOOST_MINUTES: 60.0, **user}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_without_a_sign_in_the_settings_offer_it(hass, fake_bt, dimplex) -> None:
    entry = add_settings(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    shared = {CONF_SYNC_CLOCK: True, CONF_BOOST_MINUTES: 60.0}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert {str(key) for key in result["data_schema"].schema} >= {"email", "password"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**shared, "email": "me@x"}
    )
    assert result["errors"] == {"base": "incomplete_sign_in"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**shared, "email": "me@x", "password": "p"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_REFRESH_TOKEN] == "R"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "email" not in {str(key) for key in result["data_schema"].schema}


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_signing_in_again(hass, fake_bt, dimplex, monkeypatch, source) -> None:
    entry = add_settings(hass, data={CONF_REFRESH_TOKEN: None})
    assert await hass.config_entries.async_setup(entry.entry_id)
    result = await (
        entry.start_reauth_flow(hass)
        if source == "reauth"
        else entry.start_reconfigure_flow(hass)
    )
    assert result["step_id"] == "reauth_account"

    async def refused(session, email, password):
        raise cloud.AuthError("no")

    monkeypatch.setattr(cloud, "async_sign_in", refused)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "me@x", "password": "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    monkeypatch.setattr(cloud, "async_sign_in", dimplex["sign_in_fn"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "me@x", "password": "p"}
    )
    await hass.async_block_till_done()

    assert result["reason"] == f"{source}_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "R"


async def test_the_shared_session_outlives_a_heater_reload(hass, fake_bt) -> None:
    """Jarvis 2026-11-25: the session closed with the entry that created it."""
    from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE

    heater(fake_bt)
    entry = make_entry(hass)
    await setup(hass, entry)
    session = account(hass).session

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not session.closed

    hass.bus.async_fire(EVENT_HOMEASSISTANT_CLOSE)
    await hass.async_block_till_done()
    assert session.closed


@pytest.mark.usefixtures("signed_in")
async def test_an_unconfirmed_version_is_reported(
    hass, fake_bt, dimplex, monkeypatch
) -> None:
    """Codex review: never report success while the heater keeps 1.7."""
    heater(fake_bt)
    coordinator = await setup(hass, make_entry(hass))

    async def download(session, firmware):
        return b"zip"

    async def flash(img, progress):
        return None  # the heater keeps its old firmware

    monkeypatch.setattr(cloud, "async_download", download)
    monkeypatch.setattr(ota, "load_release", lambda z, ep: ota.Image(ep, "5.0", b"x"))
    monkeypatch.setattr(coordinator.link, "async_flash", flash)

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            "update", "install", {"entity_id": entity_id(hass)}, blocking=True
        )
    assert raised.value.translation_key == "firmware_not_confirmed"
    assert hass.states.get(entity_id(hass)).attributes["installed_version"] == "1.7"
