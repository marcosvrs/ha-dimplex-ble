"""Native firmware update entity: Dimplex's latest image, flashed over BLE."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import cloud, ota
from .const import CONF_QR_LABEL, DOMAIN, FIRMWARE_CHECK_INTERVAL
from .coordinator import DimplexConfigEntry, DimplexCoordinator
from .entity import DimplexEntity
from .firmware import account

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1
SCAN_INTERVAL = FIRMWARE_CHECK_INTERVAL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Offer firmware updates for heaters whose QR label was entered."""
    if label := entry.options.get(CONF_QR_LABEL):
        async_add_entities(
            [DimplexFirmwareUpdate(entry.runtime_data, cloud.Label.parse(label))],
            update_before_add=True,
        )


class DimplexFirmwareUpdate(DimplexEntity, UpdateEntity):
    """The heater's display firmware against Dimplex's latest release."""

    _attr_translation_key = "firmware"
    _unique_suffix = "firmware"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = True
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    def __init__(self, coordinator: DimplexCoordinator, label: cloud.Label) -> None:
        """Bind the heater to its QR label."""
        super().__init__(coordinator)
        self._label = label
        self._attr_title = "Dimplex"

    @property
    def installed_version(self) -> str | None:
        """Return the display version the heater reports."""
        data = self.coordinator.data
        return None if data is None else data.ui_version

    async def async_update(self) -> None:
        """Ask Dimplex for the latest firmware (no account changes)."""
        if self._attr_in_progress:
            return
        try:
            firmware = await self._latest()
        except (cloud.CloudError, ValueError) as err:
            _LOGGER.warning("Firmware check for %s failed: %s", self.name, err)
            return
        self._attr_latest_version = firmware.version

    async def _latest(self) -> cloud.Firmware:
        shared = account(self.hass)
        token = await shared.async_access_token()
        return await cloud.async_latest_firmware(shared.session, token, self._label)

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Download, check and flash the latest image; then re-read the heater."""
        data = self.coordinator.data
        if data is None or not data.product_code:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="firmware_unknown_heater"
            )
        try:
            firmware = await self._latest()
            if version is not None and version != firmware.version:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="firmware_version_gone",
                    translation_placeholders={"version": version},
                )
            release = await cloud.async_download(account(self.hass).session, firmware)
            image = ota.load_release(release, data.product_code)
        except (cloud.CloudError, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="firmware_download_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self._attr_in_progress = True
        self._attr_update_percentage = 0
        self.async_write_ha_state()
        try:
            await self.coordinator.link.async_flash(image, self._on_progress)
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="firmware_update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            self._attr_in_progress = False
            self._attr_update_percentage = None
            self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _on_progress(self, fraction: float) -> None:
        percentage = int(fraction * 100)
        if percentage != self._attr_update_percentage:
            self._attr_update_percentage = percentage
            self.async_write_ha_state()
