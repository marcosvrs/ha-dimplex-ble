"""Base entity shared by the Dimplex platforms."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import DimplexBluetoothClient
from .const import DOMAIN, MANUFACTURER
from .coordinator import DimplexCoordinator


class DimplexEntity(CoordinatorEntity[DimplexCoordinator]):
    """Base for entities attached to one physical Dimplex heater."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DimplexCoordinator) -> None:
        """Initialize an entity with a stable address-based identity."""
        super().__init__(coordinator)
        self._client: DimplexBluetoothClient = coordinator.client
        self._attr_unique_id = f"{format_mac(coordinator.client.address)}_{self._attr_translation_key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the heater identity with its generic Home Assistant name."""
        data = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, format_mac(self._client.address))},
            connections={(dr.CONNECTION_BLUETOOTH, format_mac(self._client.address))},
            manufacturer=MANUFACTURER,
            model=data.model if data is not None else None,
            name=self.coordinator.config_entry.title,
        )
