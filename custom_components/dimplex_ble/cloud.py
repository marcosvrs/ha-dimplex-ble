"""Dimplex cloud: sign in, look up a heater's latest firmware, download it.

The cloud is only used to find and fetch firmware; the heater itself is always
controlled locally. Dimplex's sign-in (Azure AD B2C, policy
``B2C_1A_DimplexToolkitSignupSignin``) only returns to the ConfigR app's
``msal…://auth`` address, which no browser can hand to Home Assistant, so the
integration fills in Dimplex's own sign-in form once (email and password,
never stored) and keeps the refresh token (90 days, renewed on every use).
Evidence: docs/ota-research.md, "Live findings 2026-11-25".
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

CLIENT_ID = "bd7a7089-43e4-4353-a2dc-5b613515d011"  # ConfigR (public client)
REDIRECT_URI = f"msal{CLIENT_ID}://auth"
POLICY = "B2C_1A_DimplexToolkitSignupSignin"
TENANT_PATH = f"/gdhvb2c.onmicrosoft.com/{POLICY}"
B2C_HOST = "https://gdhvb2c.b2clogin.com"
OAUTH = f"{B2C_HOST}{TENANT_PATH}/oauth2/v2.0"
SCOPE = "openid offline_access https://gdhvb2c.onmicrosoft.com/Mobile/read"
API = "https://mobileapi.gdhv-iot.com"
# The API answers HTTP 500 without ConfigR's default headers
# (Common Client::AddDefaultHeaders).
API_HEADERS = {
    "app_name": "DimplexToolkit",
    "api_version": "1.0",
    "app_version": "3.12.2",
    "app_device_os": "Android",
    "device_version": "14",
    "device_manufacturer": "Google",
    "Accept": "application/json",
}
TIMEOUT = aiohttp.ClientTimeout(total=30)
VERIFY_VALID = 1  # EVerifyApplianceResult.Valid


class CloudError(Exception):
    """The Dimplex cloud could not be reached or answered unexpectedly."""


class AuthError(CloudError):
    """Wrong email/password, or the refresh token is no longer accepted."""


@dataclass(frozen=True, slots=True)
class Firmware:
    """The latest firmware Dimplex offers for one heater."""

    version: str
    url: str  # pre-signed, valid for hours: never log it
    installed: str | None  # Dimplex's record of the installed version


@dataclass(frozen=True, slots=True)
class Label:
    """The heater's QR label: ``<id>,<code>,0,<model>,<series>``."""

    appliance_id: str
    security_code: str  # a credential: never log it

    @classmethod
    def parse(cls, text: str) -> Label:
        """Parse the label text; raise ``ValueError`` when it is not one."""
        parts = [part.strip() for part in text.split(",")]
        if (
            len(parts) < 2
            or not re.fullmatch(r"[0-9A-Fa-f]{12}([0-9A-Fa-f]{4})?", parts[0])
            or not re.fullmatch(r"\d{6}", parts[1])
        ):
            raise ValueError("Not a Dimplex QR label")
        return cls(parts[0].upper(), parts[1])


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _page_json(html: str, name: str) -> dict[str, Any]:
    match = re.search(rf"var {name} = (\{{.*?\}});", html, re.S)
    if match is None:
        raise CloudError(f"Sign-in page without {name}")
    value: dict[str, Any] = json.loads(match.group(1))
    return value


async def async_sign_in(
    session: aiohttp.ClientSession, email: str, password: str
) -> str:
    """Sign in on Dimplex's page as a browser would; return the refresh token.

    The page keeps its state in cookies. aiohttp's cookie jar does not send
    back the ``x-ms-cpim-cache|…`` cookie correctly (the form then answers a
    bare 400), so they are kept here and sent by hand; ``session`` should use
    ``aiohttp.DummyCookieJar``.
    """
    cookies: dict[str, str] = {}

    def keep(resp: aiohttp.ClientResponse) -> None:
        cookies.update({name: morsel.value for name, morsel in resp.cookies.items()})

    def sent() -> dict[str, str]:
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    query = urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "response_mode": "query",
            "scope": SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            # Without a hosted page language the policy fails (AADB2C90037).
            "ui_locales": "en",
            "prompt": "login",
        }
    )
    async with session.get(f"{OAUTH}/authorize?{query}", timeout=TIMEOUT) as resp:
        keep(resp)
        settings = _page_json(await resp.text(), "SETTINGS")
    csrf, tx = settings["csrf"], settings["transId"]
    params = {"tx": tx, "p": POLICY}
    headers = {"X-CSRF-TOKEN": csrf, "X-Requested-With": "XMLHttpRequest"}
    async with session.post(
        f"{B2C_HOST}{TENANT_PATH}/SelfAsserted",
        params=params,
        data={"request_type": "RESPONSE", "email": email, "password": password},
        headers={**headers, **sent()},
        timeout=TIMEOUT,
    ) as resp:
        keep(resp)
        text = await resp.text()
    try:
        result = json.loads(text)
    except ValueError as err:
        raise CloudError(f"Sign-in form answered HTTP {resp.status}") from err
    if str(result.get("status")) != "200":
        raise AuthError("Dimplex did not accept the email or password")
    async with session.get(
        f"{B2C_HOST}{TENANT_PATH}/api/{settings['api']}/confirmed",
        params={"rememberMe": "false", "csrf_token": csrf, **params},
        headers=sent(),
        allow_redirects=False,
        timeout=TIMEOUT,
    ) as resp:
        location = resp.headers.get("Location", "")
    reply = parse_qs(urlparse(location).query)
    if not location.startswith(REDIRECT_URI) or reply.get("state") != [state]:
        raise CloudError("Sign-in ended without a code")
    if "code" not in reply:
        raise AuthError("Dimplex refused the sign-in")
    tokens = await _token(
        session,
        {
            "grant_type": "authorization_code",
            "code": reply["code"][0],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    refresh: str = tokens["refresh_token"]
    return refresh


async def _token(
    session: aiohttp.ClientSession, form: dict[str, str]
) -> dict[str, Any]:
    async with session.post(
        f"{OAUTH}/token",
        data={"client_id": CLIENT_ID, "scope": SCOPE, **form},
        timeout=TIMEOUT,
    ) as resp:
        body: dict[str, Any] = await resp.json(content_type=None)
    if resp.status == 400:
        raise AuthError("Dimplex no longer accepts the sign-in")
    if resp.status != 200 or "access_token" not in body:
        raise CloudError(f"Token endpoint answered HTTP {resp.status}")
    return body


async def async_refresh(
    session: aiohttp.ClientSession, refresh_token: str
) -> tuple[str, str]:
    """Return a fresh (access token, refresh token)."""
    tokens = await _token(
        session, {"grant_type": "refresh_token", "refresh_token": refresh_token}
    )
    access: str = tokens["access_token"]
    renewed: str = tokens.get("refresh_token", refresh_token)
    return access, renewed


async def async_latest_firmware(
    session: aiohttp.ClientSession, access_token: str, label: Label
) -> Firmware:
    """Ask Dimplex for the heater's latest firmware (no account changes)."""
    async with session.post(
        f"{API}/api/ToolkitV2/VerifyApplianceIdAndCodeV2",
        json={
            "ApplianceId": label.appliance_id,
            "SecurityCode": label.security_code,
            "CheckIfAlreadyHasWallController": False,
        },
        headers={**API_HEADERS, "Authorization": f"Bearer {access_token}"},
        timeout=TIMEOUT,
    ) as resp:
        if resp.status == 401:
            raise AuthError("Dimplex rejected the access token")
        if resp.status != 200:
            raise CloudError(f"Firmware lookup answered HTTP {resp.status}")
        body: dict[str, Any] = await resp.json(content_type=None)
    if body.get("ResultCode") != VERIFY_VALID:
        raise ValueError("Dimplex does not know this label")
    details = body["ApplianceDetails"]
    latest = details.get("LatestBluetoothFirmware") or {}
    if not latest.get("Version") or not latest.get("Location"):
        raise CloudError("No firmware offered for this heater")
    return Firmware(
        latest["Version"], latest["Location"], details.get("FirmwareVersion")
    )


async def async_download(session: aiohttp.ClientSession, firmware: Firmware) -> bytes:
    """Download the firmware ZIP (pre-signed link, no sign-in) and check its MD5."""
    async with session.get(
        firmware.url, timeout=aiohttp.ClientTimeout(total=120)
    ) as resp:
        if resp.status != 200:
            raise CloudError(f"Firmware download answered HTTP {resp.status}")
        data = await resp.read()
        md5 = resp.headers.get("Content-MD5")
    if md5 and base64.b64encode(hashlib.md5(data).digest()).decode() != md5:
        raise CloudError("Firmware download is corrupt (MD5 mismatch)")
    return data
