"""Coordinator for a single Dimplex heater."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .client import DimplexBluetoothClient, DimplexConnectionError
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

    async def _async_update_data(self) -> HeaterSnapshot:
        """Read the heater, marking it unavailable when its BLE link fails."""
        try:
            return await self.client.async_read_snapshot()
        except DimplexConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from err
