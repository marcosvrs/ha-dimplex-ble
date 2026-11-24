"""Base entity shared by the Dimplex platforms."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, UNIQUE_ID_SUFFIXES
from .coordinator import DimplexCoordinator
from .protocol import parse_model


def unique_id(address: str, suffix: str) -> str:
    """Return the entity unique_id for one panel heater and entity suffix."""
    return f"{format_mac(address)}_{suffix}"


def device_info_for_coordinator(coordinator: DimplexCoordinator) -> DeviceInfo:
    """Return the stable device metadata shared by all panel heater entities."""
    data = coordinator.data
    software_versions: list[str] = []
    if data is not None:
        if data.ui_version is not None:
            software_versions.append(f"UI {data.ui_version}")
        if data.rf_firmware_revision is not None:
            software_versions.append(f"RF {data.rf_firmware_revision}")
    return DeviceInfo(
        identifiers={(DOMAIN, format_mac(coordinator.link.address))},
        connections={(dr.CONNECTION_BLUETOOTH, format_mac(coordinator.link.address))},
        manufacturer=MANUFACTURER,
        model=parse_model(data.model) if data is not None else None,
        model_id=(data.model.split(";")[0].strip() or None)
        if data is not None and data.model
        else None,
        name=coordinator.config_entry.title,
        sw_version=" / ".join(software_versions) or None,
    )


class DimplexEntity(CoordinatorEntity[DimplexCoordinator]):
    """Base for entities attached to one physical Dimplex panel heater."""

    _attr_has_entity_name = True
    # One of UNIQUE_ID_SUFFIXES; never derived from the translation key.
    _unique_suffix: ClassVar[str]

    def __init__(self, coordinator: DimplexCoordinator) -> None:
        """Initialize an entity with a stable address-based identity."""
        super().__init__(coordinator)
        assert self._unique_suffix in UNIQUE_ID_SUFFIXES
        self._attr_unique_id = unique_id(coordinator.link.address, self._unique_suffix)

    @property
    def device_info(self) -> DeviceInfo:
        """Return the panel heater identity with its current metadata."""
        return device_info_for_coordinator(self.coordinator)
