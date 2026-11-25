"""Dimplex cloud client against HA's mocked HTTP session."""

from __future__ import annotations

import base64
import hashlib
import json
from http.cookies import SimpleCookie

import pytest
from yarl import URL

from custom_components.dimplex_ble import cloud

SETTINGS = {"csrf": "CSRF", "transId": "TX", "api": "CombinedSigninAndSignup"}
PAGE = f"<script>var SETTINGS = {json.dumps(SETTINGS)};</script>"
AUTHORIZE = f"{cloud.OAUTH}/authorize"
SELF_ASSERTED = f"{cloud.B2C_HOST}{cloud.TENANT_PATH}/SelfAsserted"
CONFIRMED = f"{cloud.B2C_HOST}{cloud.TENANT_PATH}/api/CombinedSigninAndSignup/confirmed"
TOKEN = f"{cloud.OAUTH}/token"
VERIFY = f"{cloud.API}/api/ToolkitV2/VerifyApplianceIdAndCodeV2"
LABEL = cloud.Label("010423C0003A", "123456")


def cookie(name: str, value: str) -> SimpleCookie:
    jar: SimpleCookie = SimpleCookie()
    jar[name] = value
    return jar


def sign_in_page(aioclient_mock, *, status="200", code=True, state=None) -> None:
    aioclient_mock.get(
        AUTHORIZE, text=PAGE, cookies=cookie("x-ms-cpim-cache|abc_0", "C1")
    )
    aioclient_mock.post(SELF_ASSERTED, text=json.dumps({"status": status}))

    async def confirmed(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import (
            AiohttpClientMockResponse,
        )

        sent = aioclient_mock.mock_calls[0][1]
        reply = state or dict(sent.query)["state"]
        query = f"code=CODE&state={reply}" if code else f"error=x&state={reply}"
        return AiohttpClientMockResponse(
            method, url, 302, headers={"Location": f"{cloud.REDIRECT_URI}?{query}"}
        )

    aioclient_mock.get(CONFIRMED, side_effect=confirmed)


async def test_sign_in_returns_the_refresh_token(hass, aioclient_mock) -> None:
    sign_in_page(aioclient_mock)
    aioclient_mock.post(TOKEN, json={"access_token": "A", "refresh_token": "R"})
    session = aioclient_mock.create_session(hass.loop)

    assert await cloud.async_sign_in(session, "me@example.com", "pw") == "R"

    form = aioclient_mock.mock_calls[1]
    assert form[2] == {
        "request_type": "RESPONSE",
        "email": "me@example.com",
        "password": "pw",
    }
    assert form[3]["X-CSRF-TOKEN"] == "CSRF"
    assert "x-ms-cpim-cache|abc_0=C1" in form[3]["Cookie"]  # sent by hand
    assert aioclient_mock.mock_calls[3][2]["grant_type"] == "authorization_code"
    await session.close()


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": "400"}, cloud.AuthError),
        ({"code": False}, cloud.AuthError),
        ({"state": "other"}, cloud.CloudError),
    ],
    ids=["wrong-password", "refused", "wrong-state"],
)
async def test_sign_in_failures(hass, aioclient_mock, kwargs, error) -> None:
    sign_in_page(aioclient_mock, **kwargs)
    session = aioclient_mock.create_session(hass.loop)
    with pytest.raises(error):
        await cloud.async_sign_in(session, "me@example.com", "pw")
    await session.close()


async def test_a_form_reply_that_is_not_json_is_a_cloud_error(
    hass, aioclient_mock
) -> None:
    aioclient_mock.get(AUTHORIZE, text=PAGE)
    aioclient_mock.post(SELF_ASSERTED, status=400, text="Bad Request")
    session = aioclient_mock.create_session(hass.loop)
    with pytest.raises(cloud.CloudError, match="HTTP 400"):
        await cloud.async_sign_in(session, "me@example.com", "pw")
    await session.close()


async def test_a_page_without_settings_is_a_cloud_error(hass, aioclient_mock) -> None:
    aioclient_mock.get(AUTHORIZE, text="<html></html>")
    session = aioclient_mock.create_session(hass.loop)
    with pytest.raises(cloud.CloudError):
        await cloud.async_sign_in(session, "me@example.com", "pw")
    await session.close()


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [(400, {"error": "invalid_grant"}, cloud.AuthError), (500, {}, cloud.CloudError)],
)
async def test_refresh(hass, aioclient_mock, status, body, error) -> None:
    session = aioclient_mock.create_session(hass.loop)
    aioclient_mock.post(TOKEN, json={"access_token": "A2"})
    assert await cloud.async_refresh(session, "R") == ("A2", "R")
    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN, status=status, json=body)
    with pytest.raises(error):
        await cloud.async_refresh(session, "R")
    await session.close()


def verify_reply(result=1, **latest) -> dict:
    return {
        "ResultCode": result,
        "ApplianceDetails": {
            "FirmwareVersion": "1.7.0",
            "LatestBluetoothFirmware": {
                "Version": "5.0",
                "Location": "https://blob.example/EP4213.zip?sig=x",
                **latest,
            },
        },
    }


async def test_latest_firmware(hass, aioclient_mock) -> None:
    aioclient_mock.post(VERIFY, json=verify_reply())
    session = aioclient_mock.create_session(hass.loop)
    firmware = await cloud.async_latest_firmware(session, "A", LABEL)
    assert (firmware.version, firmware.installed) == ("5.0", "1.7.0")
    sent = aioclient_mock.mock_calls[0]
    assert sent[2]["ApplianceId"] == "010423C0003A"
    assert (
        sent[3]["Authorization"] == "Bearer A"
        and sent[3]["app_name"] == "DimplexToolkit"
    )
    await session.close()


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {}, cloud.AuthError),
        (500, {}, cloud.CloudError),
        (200, verify_reply(result=0), ValueError),
        (200, verify_reply(Location=None), cloud.CloudError),
    ],
    ids=["token", "server", "unknown-label", "no-firmware"],
)
async def test_latest_firmware_failures(
    hass, aioclient_mock, status, body, error
) -> None:
    aioclient_mock.post(VERIFY, status=status, json=body)
    session = aioclient_mock.create_session(hass.loop)
    with pytest.raises(error):
        await cloud.async_latest_firmware(session, "A", LABEL)
    await session.close()


async def test_download_checks_the_md5(hass, aioclient_mock) -> None:
    data = b"zip bytes"
    md5 = base64.b64encode(hashlib.md5(data).digest()).decode()
    firmware = cloud.Firmware("5.0", "https://blob.example/f.zip", None)
    session = aioclient_mock.create_session(hass.loop)
    aioclient_mock.get(URL(firmware.url), content=data, headers={"Content-MD5": md5})
    assert await cloud.async_download(session, firmware) == data
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        URL(firmware.url), content=b"other", headers={"Content-MD5": md5}
    )
    with pytest.raises(cloud.CloudError, match="MD5"):
        await cloud.async_download(session, firmware)
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL(firmware.url), status=403)
    with pytest.raises(cloud.CloudError, match="403"):
        await cloud.async_download(session, firmware)
    await session.close()


def test_label_parsing() -> None:
    label = cloud.Label.parse(" 010423c0003a, 123456 ,0,PLX050E,C")
    assert label == cloud.Label("010423C0003A", "123456")
    for bad in ("", "010423C0003A", "XYZ,123456", "010423C0003A,12345"):
        with pytest.raises(ValueError):
            cloud.Label.parse(bad)
