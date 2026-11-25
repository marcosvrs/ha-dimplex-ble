"""The Dimplex account: one sign-in for every heater's firmware lookups.

The refresh token lives in the integration-level "Dimplex" entry (90 days,
renewed on every use); the password is used for the sign-in and dropped. A
refused token starts Home Assistant's native re-authentication of that entry.
"""

from __future__ import annotations

import asyncio

import aiohttp
from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from . import cloud
from .const import DOMAIN
from .settings import CONF_REFRESH_TOKEN, settings_entry


class DimplexAccount:
    """HTTP session and token handling for firmware lookups."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Create the session every heater shares."""
        self._hass = hass
        self._lock = asyncio.Lock()
        # Not tied to the entry whose setup created it (HA closes such
        # sessions on that entry's unload). B2C's cookies are sent by hand
        # (cloud.async_sign_in).
        self.session = async_create_clientsession(
            hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
        )
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, self._async_close)

    @property
    def signed_in(self) -> bool:
        """Return whether the Dimplex entry holds a sign-in."""
        entry = settings_entry(self._hass)
        return entry is not None and bool(entry.data.get(CONF_REFRESH_TOKEN))

    async def async_access_token(self) -> str:
        """Return a fresh access token; ask to sign in again when refused."""
        async with self._lock:
            entry = settings_entry(self._hass)
            token = None if entry is None else entry.data.get(CONF_REFRESH_TOKEN)
            if entry is None or not token:
                raise cloud.AuthError("Not signed in")
            try:
                access, renewed = await cloud.async_refresh(self.session, token)
            except cloud.AuthError:
                self._hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_REFRESH_TOKEN: None}
                )
                entry.async_start_reauth(self._hass)
                raise
            if renewed != token:
                self._hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_REFRESH_TOKEN: renewed}
                )
            return access

    async def _async_close(self, event: Event) -> None:
        await self.session.close()


def account(hass: HomeAssistant) -> DimplexAccount:
    """Return the one account object shared by all heaters."""
    key = f"{DOMAIN}_account"
    if key not in hass.data:
        hass.data[key] = DimplexAccount(hass)
    shared: DimplexAccount = hass.data[key]
    return shared
