"""Config flow for model-neutral Dimplex BLE discovery and pairing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import probatio
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import DimplexBluetoothClient, DimplexConnectionError
from .const import CONF_ADDRESS, CONF_PASSKEY, CONF_PROXY_SOURCE, DOMAIN
from .discovery import advertisement_label, is_dimplex_advertisement
from .pairing import (
    DEBUG_LOG_PREFIX,
    DimplexPairingError,
    DimplexPairingInProgressError,
    async_send_passkey,
    async_start_pairing,
    select_proxy_source,
)

_LOGGER = logging.getLogger(__name__)
PAIRING_RETRY_DELAY = 1.0


class DimplexBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover a heater and complete first-time pairing through its proxy."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow-local discovery and pairing state."""
        self._devices: dict[str, tuple[str, int, str | None]] = {}
        self._address: str | None = None
        self._proxy_source: str | None = None
        self._advertised_name: str | None = None
        self._pairing_task: asyncio.Task[None] | None = None
        self._pairing_retry_task: asyncio.Task[None] | None = None
        self._pairing_lock = asyncio.Lock()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Start setup for a heater found by Home Assistant Bluetooth."""
        name = discovery_info.name or discovery_info.device.name or ""
        if not is_dimplex_advertisement(name):
            return self.async_abort(reason="not_dimplex")
        self._advertised_name = advertisement_label(name)
        if self._advertised_name is not None:
            self.context["title_placeholders"] = {"name": self._advertised_name}

        address = format_mac(discovery_info.device.address)
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        service_infos = list(
            bluetooth.async_discovered_service_info(self.hass, connectable=True)
        )
        if not any(info is discovery_info for info in service_infos):
            service_infos.append(discovery_info)
        proxy_source = select_proxy_source(self.hass, address, service_infos)
        if proxy_source is None:
            return self.async_abort(reason="proxy_passkey_support_missing")

        self._address = address
        self._proxy_source = proxy_source
        return await self.async_step_passkey()

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retry setup after a pairing request could not start."""
        if self._address is None or self._proxy_source is None:
            return self.async_abort(reason="proxy_unavailable")
        if user_input is None:
            return self._show_pair_start_form()
        return await self.async_step_passkey()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List nearby heaters that a passkey-enabled ESPHome proxy can reach."""
        if not self._devices:
            service_infos = list(
                bluetooth.async_discovered_service_info(
                    self.hass, connectable=True
                )
            )
            if not service_infos:
                await bluetooth.async_request_active_scan(self.hass, duration=5.0)
                service_infos = list(
                    bluetooth.async_discovered_service_info(
                        self.hass, connectable=True
                    )
                )

            heater_names: dict[str, str] = {}
            for service_info in service_infos:
                name = service_info.name or service_info.device.name or ""
                if is_dimplex_advertisement(name):
                    address = format_mac(service_info.device.address)
                    heater_names.setdefault(address, name)

            routes_by_address: dict[str, list[BluetoothServiceInfoBleak]] = {
                address: [] for address in heater_names
            }
            for service_info in service_infos:
                address = format_mac(service_info.device.address)
                routes = routes_by_address.get(address)
                if routes is not None:
                    routes.append(service_info)

            unsupported_heater_seen = False
            for address, advertised_name in heater_names.items():
                proxy_source = select_proxy_source(self.hass, address, service_infos)
                if proxy_source is None:
                    unsupported_heater_seen = True
                    continue

                selected_route = max(
                    (
                        info
                        for info in routes_by_address[address]
                        if info.connectable and info.source == proxy_source
                    ),
                    key=lambda info: info.rssi,
                )
                selected_name = (
                    selected_route.name
                    or selected_route.device.name
                    or advertised_name
                )
                if not is_dimplex_advertisement(selected_name):
                    selected_name = advertised_name
                self._devices[address] = (
                    proxy_source,
                    selected_route.rssi,
                    advertisement_label(selected_name),
                )
            if not self._devices:
                reason = (
                    "proxy_passkey_support_missing"
                    if unsupported_heater_seen
                    else "no_heaters_found"
                )
                return self.async_abort(reason=reason)

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            selected = self._devices.get(address)
            if selected is None:
                return self._show_user_form("device_not_found")
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            self._address = address
            self._proxy_source = selected[0]
            self._advertised_name = selected[2]
            if self._advertised_name is not None:
                self.context["title_placeholders"] = {
                    "name": self._advertised_name
                }
            return await self.async_step_passkey()

        return self._show_user_form()

    async def async_step_passkey(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start pairing on entry, then submit the PIN from the heater display."""
        address = self._address
        proxy_source = self._proxy_source
        if address is None or proxy_source is None:
            return self.async_abort(reason="proxy_unavailable")

        if user_input is None:
            async with self._pairing_lock:
                error = await self._async_start_pairing(address, proxy_source)
                if error is None:
                    self._async_ensure_pairing_retry()
            if error is not None:
                return self._show_pair_start_form(error)
            return self._show_passkey_form()

        passkey = user_input.get(CONF_PASSKEY)
        if not isinstance(passkey, str) or not self._valid_passkey(passkey):
            user_input.pop(CONF_PASSKEY, None)
            return self._show_passkey_form("invalid_passkey")

        pairing_complete = False
        try:
            async with self._pairing_lock:
                task = self._pairing_task
                if task is not None and task.done():
                    if task.cancelled():
                        self._pairing_task = None
                        task = None
                    else:
                        try:
                            task.result()
                        except Exception:
                            self._pairing_task = None
                            task = None
                        else:
                            pairing_complete = True

                if not pairing_complete:
                    if task is None:
                        error = await self._async_start_pairing(address, proxy_source)
                        if error is not None:
                            return self._show_pair_start_form(error)
                        task = self._pairing_task
                        self._async_ensure_pairing_retry()
                    if task is None:
                        return self._show_pair_start_form("pairing_failed")
                    _LOGGER.debug(
                        "%s Submitting PIN to the selected proxy; value redacted",
                        DEBUG_LOG_PREFIX,
                    )
                    await async_send_passkey(
                        self.hass, proxy_source, address, passkey
                    )

            if not pairing_complete:
                assert task is not None
                await task
        except DimplexPairingError:
            return self._show_passkey_form("proxy_passkey_support_missing")
        except (DimplexConnectionError, HomeAssistantError, TimeoutError):
            return self._show_passkey_form("pairing_failed")
        finally:
            user_input.pop(CONF_PASSKEY, None)

        title = self._next_device_title()
        return self.async_create_entry(
            title=title,
            data={
                CONF_ADDRESS: address,
                CONF_PROXY_SOURCE: proxy_source,
            },
        )

    async def _async_start_pairing(
        self, address: str, proxy_source: str
    ) -> str | None:
        """Arm the selected proxy and start its connection attempt."""
        if self._pairing_task is not None and not self._pairing_task.done():
            return None

        client = DimplexBluetoothClient(self.hass, address, proxy_source)
        try:
            self._pairing_task = await async_start_pairing(
                self.hass, proxy_source, address, client.async_pair
            )
        except DimplexPairingInProgressError:
            return "pairing_in_progress"
        except DimplexPairingError:
            return "proxy_passkey_support_missing"
        except (DimplexConnectionError, HomeAssistantError):
            return "pairing_failed"
        return None

    def _async_ensure_pairing_retry(self) -> None:
        """Keep the PIN prompt available while this config flow remains open."""
        task = self._pairing_retry_task
        if task is None or task.done():
            self._pairing_retry_task = self.hass.async_create_task(
                self._async_retry_pairing_while_flow_active()
            )

    async def _async_retry_pairing_while_flow_active(self) -> None:
        """Restart an expired pairing request until the user closes the flow."""
        address = self._address
        proxy_source = self._proxy_source
        if address is None or proxy_source is None:
            return

        while True:
            task = self._pairing_task
            if task is None:
                await asyncio.sleep(PAIRING_RETRY_DELAY)
                async with self._pairing_lock:
                    error = await self._async_start_pairing(address, proxy_source)
                if error is not None:
                    _LOGGER.debug(
                        "%s Pairing retry could not start (%s)",
                        DEBUG_LOG_PREFIX,
                        error,
                    )
                continue

            try:
                await task
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug(
                    "%s Pairing attempt ended; retrying while the flow is active (%s)",
                    DEBUG_LOG_PREFIX,
                    type(err).__name__,
                )
                await asyncio.sleep(PAIRING_RETRY_DELAY)
                async with self._pairing_lock:
                    if self._pairing_task is task:
                        self._pairing_task = None
                        error = await self._async_start_pairing(
                            address, proxy_source
                        )
                    else:
                        error = None
                if error is not None:
                    _LOGGER.debug(
                        "%s Pairing retry could not start (%s)",
                        DEBUG_LOG_PREFIX,
                        error,
                    )
            else:
                return

    def async_remove(self) -> None:
        """Stop pairing and retries when Home Assistant removes this flow."""
        for task in (self._pairing_task, self._pairing_retry_task):
            if task is not None and not task.done():
                task.cancel()
        super().async_remove()

    def _show_pair_start_form(
        self, error: str | None = None
    ) -> ConfigFlowResult:
        """Offer a retry after pairing could not start."""
        return self.async_show_form(
            step_id="pair",
            data_schema=probatio.Schema({}),
            description_placeholders={
                "name": self._advertised_name or "the selected heater",
            },
            errors={"base": error} if error else {},
        )

    def _show_user_form(self, error: str | None = None) -> ConfigFlowResult:
        """Show every eligible heater using its advertised local name."""
        ordered_devices = sorted(
            self._devices.items(), key=lambda item: item[1][1], reverse=True
        )
        options: list[SelectOptionDict] = []
        for index, (address, (_, rssi, advertised_name)) in enumerate(
            ordered_devices, start=1
        ):
            label = advertised_name or f"heater {index}"
            options.append(
                {
                    "value": address,
                    "label": f"Dimplex {label} ({rssi} dBm)",
                }
            )
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
            errors={"base": error} if error else {},
        )

    def _show_passkey_form(self, error: str | None = None) -> ConfigFlowResult:
        """Show a password-style PIN field without persisting the value."""
        return self.async_show_form(
            step_id="passkey",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_PASSKEY): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="off",
                        )
                    )
                }
            ),
            description_placeholders={
                "name": self._advertised_name or "the selected heater",
            },
            errors={"base": error} if error else {},
        )

    def _next_device_title(self) -> str:
        """Allocate a non-conflicting title, preferring the advertised name."""
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        titles = {
            entry.title for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        reserved_titles: set[str] = domain_data.setdefault(
            "reserved_device_titles", set()
        )
        titles.update(reserved_titles)
        normalized_titles = {title.casefold() for title in titles}

        if self._advertised_name is not None:
            base_title = f"Dimplex Heater {self._advertised_name}"
            title = base_title
            suffix = 2
            while title.casefold() in normalized_titles:
                title = f"{base_title} ({suffix})"
                suffix += 1
        else:
            index = 1
            title = f"Dimplex Heater {index}"
            while title.casefold() in normalized_titles:
                index += 1
                title = f"Dimplex Heater {index}"

        reserved_titles.add(title)
        return title

    @staticmethod
    def _valid_passkey(passkey: str) -> bool:
        """Accept exactly six ASCII decimal digits."""
        return len(passkey) == 6 and all("0" <= digit <= "9" for digit in passkey)
