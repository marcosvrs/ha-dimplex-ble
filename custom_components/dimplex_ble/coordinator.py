"""Coordinator for a single Dimplex heater."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .client import (
    DimplexAuthenticationError,
    DimplexBluetoothClient,
    DimplexConnectionError,
)
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN
from .protocol import HeaterSnapshot

_LOGGER = logging.getLogger(__name__)


type DimplexConfigEntry = ConfigEntry[DimplexCoordinator]


class DimplexCoordinator(DataUpdateCoordinator[HeaterSnapshot]):
    """Fetch and share the latest mapped heater values."""

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

    async def async_shutdown(self) -> None:
        """Release the persistent BLE connection during entry unload."""
        await self.client.async_shutdown()

    async def _async_update_data(self) -> HeaterSnapshot:
        """Read the heater, retrying one clean authenticated connection."""
        try:
            return await self.client.async_read_snapshot()
        except DimplexAuthenticationError:
            try:
                return await self.client.async_retry_authenticated_connection()
            except DimplexAuthenticationError as retry_err:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="authentication_failed",
                ) from retry_err
            except DimplexConnectionError as retry_err:
                raise UpdateFailed(
                    translation_domain=DOMAIN,
                    translation_key="cannot_connect",
                ) from retry_err
        except DimplexConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from err
