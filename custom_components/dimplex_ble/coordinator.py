"""Coordinator for a single Dimplex panel heater."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import cast

from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .client import (
    DimplexAuthenticationError,
    DimplexBluetoothClient,
    DimplexConnectionError,
    DimplexError,
)
from .commissioning import async_pair_with_passkey
from .const import (
    CONF_ADDRESS,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .protocol import EXPERIMENTAL_RAW_FIELDS, GattAddress, HeaterSnapshot

_LOGGER = logging.getLogger(__name__)
_AUTHENTICATION_FAILURE_THRESHOLD = 3
_EXPERIMENTAL_FIELDS_PER_POLL = 3
_AUTOMATIC_REPAIR_TIMEOUT = 30.0
_AUTOMATIC_REPAIR_COOLDOWN = 300.0


type DimplexConfigEntry = ConfigEntry[DimplexCoordinator]


class DimplexCoordinator(DataUpdateCoordinator[HeaterSnapshot]):
    """Fetch and share the latest mapped panel heater values."""

    config_entry: DimplexConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: DimplexConfigEntry,
        client: DimplexBluetoothClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_UPDATE_INTERVAL,
            always_update=False,
        )
        self.client = client
        self._cancel_advertisement_callback: Callable[[], None] | None = None
        self._authentication_failures = 0
        self._automatic_repair_until = 0.0
        self._raw_field_offset = 0
        self.raw_fields: dict[GattAddress, bytes] = {}

    @callback
    def set_advertisement_callback_canceller(
        self, cancel_callback: Callable[[], None]
    ) -> None:
        """Store the targeted Bluetooth advertisement callback cleanup."""
        self._cancel_advertisement_callback = cancel_callback

    @callback
    def async_handle_advertisement(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        """Request recovery when an unavailable panel heater reappears."""
        del service_info, change
        if self.last_update_success:
            return
        self.hass.async_create_task(self.async_request_refresh())

    async def async_shutdown(self) -> None:
        """Stop coordinator refreshes before releasing the panel heater connection."""
        await super().async_shutdown()
        if self._cancel_advertisement_callback is not None:
            self._cancel_advertisement_callback()
            self._cancel_advertisement_callback = None
        shutdown = getattr(self.client, "async_shutdown", None)
        if shutdown is not None:
            await shutdown()

    async def _async_repair_authentication(self) -> bool:
        """Restore a missing proxy bond once with the saved panel heater passkey."""
        passkey = self.config_entry.data.get(CONF_PASSKEY)
        if (
            not isinstance(passkey, str)
            or len(passkey) != 6
            or not all("0" <= digit <= "9" for digit in passkey)
        ):
            return False
        now = time.monotonic()
        if now < self._automatic_repair_until:
            return False
        self._automatic_repair_until = now + _AUTOMATIC_REPAIR_COOLDOWN
        try:
            await async_pair_with_passkey(
                self.hass,
                self.config_entry.data[CONF_PROXY_SOURCE],
                self.config_entry.data[CONF_ADDRESS],
                passkey,
                self.client.async_pair,
                repair_timeout=_AUTOMATIC_REPAIR_TIMEOUT,
            )
        except Exception:
            _LOGGER.debug(
                "Automatic authentication repair failed for %s",
                self.config_entry.title,
                exc_info=True,
            )
            return False
        self._automatic_repair_until = 0.0
        return True

    async def _async_refresh_experimental_fields(self) -> None:
        """Refresh a small raw-field batch without blocking climate recovery."""
        read_raw_fields = getattr(self.client, "async_read_raw_fields", None)
        if read_raw_fields is None:
            return
        field_count = len(EXPERIMENTAL_RAW_FIELDS)
        start = self._raw_field_offset
        batch = tuple(
            EXPERIMENTAL_RAW_FIELDS[(start + index) % field_count]
            for index in range(min(_EXPERIMENTAL_FIELDS_PER_POLL, field_count))
        )
        self._raw_field_offset = (start + len(batch)) % field_count
        try:
            values = await read_raw_fields(batch)
        except DimplexError as err:
            _LOGGER.debug(
                "Optional experimental read failed for %s (%s)",
                self.config_entry.title,
                type(err).__name__,
            )
            return
        self.raw_fields.update(values)

    async def _async_update_data(self) -> HeaterSnapshot:
        """Read the panel heater, retrying one clean authenticated connection."""
        try:
            snapshot = await self.client.async_read_snapshot()
        except DimplexAuthenticationError as err:
            if await self._async_repair_authentication():
                try:
                    snapshot = await self.client.async_read_snapshot()
                except DimplexError as repair_err:
                    err = (
                        repair_err
                        if isinstance(repair_err, DimplexAuthenticationError)
                        else err
                    )
                else:
                    await self._async_refresh_experimental_fields()
                    self._authentication_failures = 0
                    return snapshot
            self._authentication_failures += 1
            if self._authentication_failures >= _AUTHENTICATION_FAILURE_THRESHOLD:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="authentication_failed",
                ) from err
            _LOGGER.debug(
                "Authentication failed for %s (%d/%d); retrying on the next poll",
                self.config_entry.title,
                self._authentication_failures,
                _AUTHENTICATION_FAILURE_THRESHOLD,
            )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from err
        except DimplexConnectionError as err:
            retry = cast(
                Callable[[], Awaitable[HeaterSnapshot]] | None,
                getattr(self.client, "async_retry_connection", None),
            )
            if retry is None:
                self._authentication_failures = 0
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="cannot_connect",
                ) from err
            try:
                snapshot = await retry()
            except DimplexError as retry_err:
                self._authentication_failures = 0
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="cannot_connect",
                ) from retry_err
            await self._async_refresh_experimental_fields()
            self._authentication_failures = 0
            return snapshot
        else:
            await self._async_refresh_experimental_fields()
            self._authentication_failures = 0
            return snapshot
