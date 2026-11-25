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
from .const import CONF_PASSKEY, DOMAIN, FIRMWARE_CHECK_INTERVAL
from .coordinator import DimplexConfigEntry
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
    """Offer firmware updates; they need the Dimplex sign-in (Dimplex entry)."""
    async_add_entities(
        [DimplexFirmwareUpdate(entry.runtime_data)], update_before_add=True
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
    _attr_title = "Dimplex"

    @property
    def available(self) -> bool:
        """Available once signed in to Dimplex and the heater id was read."""
        return (
            super().available
            and account(self.hass).signed_in
            and self._label is not None
        )

    @property
    def _label(self) -> cloud.Label | None:
        """The label values, read from the heater: its id and pairing PIN."""
        passkey = self.coordinator.config_entry.data.get(CONF_PASSKEY)
        gdid = self.coordinator.gdid
        if gdid is None or passkey is None:
            return None
        return cloud.Label(gdid, str(passkey).zfill(6))

    @property
    def installed_version(self) -> str | None:
        """Return the display version the heater reports."""
        data = self.coordinator.data
        return None if data is None else data.ui_version

    async def async_update(self) -> None:
        """Ask Dimplex for the latest firmware (no account changes)."""
        if self._attr_in_progress or not self.available:
            return
        try:
            firmware = await self._latest()
        except (cloud.CloudError, ValueError) as err:
            _LOGGER.warning("Firmware check for %s failed: %s", self.name, err)
            return
        self._attr_latest_version = firmware.version

    async def _latest(self) -> cloud.Firmware:
        label = self._label
        assert label is not None  # only called while available, which needs it
        shared = account(self.hass)
        token = await shared.async_access_token()
        return await cloud.async_latest_firmware(shared.session, token, label)

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
            release = await cloud.async_download(account(self.hass).session, firmware)
            image = ota.load_release(release, data.product_code)
            if image.version != firmware.version:
                raise ValueError(
                    f"The download is version {image.version}, "
                    f"Dimplex offers {firmware.version}"
                )
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
