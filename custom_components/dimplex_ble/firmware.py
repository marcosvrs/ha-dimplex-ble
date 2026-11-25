"""The Dimplex account shared by every heater: sign-in once, tokens on disk.

Only the refresh token is kept (90 days, renewed on every use); the password
is used for the one sign-in and dropped.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from . import cloud
from .const import DOMAIN

STORAGE_VERSION = 1
SIGN_IN_ISSUE = "cloud_sign_in"


class DimplexAccount:
    """Refresh token and HTTP session for firmware lookups."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind to the shared token store."""
        self._hass = hass
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.cloud", private=True
        )
        self._refresh_token: str | None = None
        self._loaded = False
        self._lock = asyncio.Lock()
        # B2C's cookies are sent by hand (cloud.async_sign_in).
        self.session = async_create_clientsession(
            hass, cookie_jar=aiohttp.DummyCookieJar()
        )

    async def async_signed_in(self) -> bool:
        """Return whether a refresh token is stored."""
        await self._async_load()
        return self.signed_in

    @property
    def signed_in(self) -> bool:
        """Return whether a refresh token is stored (after the first load)."""
        return self._refresh_token is not None

    async def async_sign_in(self, email: str, password: str) -> None:
        """Sign in once; keep only the refresh token."""
        token = await cloud.async_sign_in(self.session, email, password)
        await self._async_save(token)
        ir.async_delete_issue(self._hass, DOMAIN, SIGN_IN_ISSUE)

    async def async_access_token(self) -> str:
        """Return a fresh access token; ask for a sign-in when refused."""
        async with self._lock:
            await self._async_load()
            if self._refresh_token is None:
                raise cloud.AuthError("Not signed in")
            try:
                access, renewed = await cloud.async_refresh(
                    self.session, self._refresh_token
                )
            except cloud.AuthError:
                await self._async_save(None)
                ir.async_create_issue(
                    self._hass,
                    DOMAIN,
                    SIGN_IN_ISSUE,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=SIGN_IN_ISSUE,
                )
                raise
            await self._async_save(renewed)
            return access

    async def _async_load(self) -> None:
        if not self._loaded:
            data = await self._store.async_load() or {}
            self._refresh_token = data.get("refresh_token")
            self._loaded = True

    async def _async_save(self, token: str | None) -> None:
        self._refresh_token = token
        await self._store.async_save({"refresh_token": token})


def account(hass: HomeAssistant) -> DimplexAccount:
    """Return the one account object shared by all heaters."""
    key = f"{DOMAIN}_account"
    if key not in hass.data:
        hass.data[key] = DimplexAccount(hass)
    shared: DimplexAccount = hass.data[key]
    return shared
