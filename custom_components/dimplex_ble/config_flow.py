"""Config flow for model-neutral Dimplex BLE panel heater discovery and pairing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, cast

import probatio
from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
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
from .commissioning import async_start_pairing
from .const import (
    CONF_ADDRESS,
    CONF_ADDRESS_TYPE,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    DOMAIN,
    PENDING_DEVICES,
)
from .discovery import advertisement_label, is_dimplex_advertisement
from .esphome_proxy import (
    DEBUG_LOG_PREFIX,
    DimplexPairingError,
    DimplexPairingInProgressError,
    async_send_passkey,
)
from .pairing import ble_device_from_source, select_proxy_source

_LOGGER = logging.getLogger(__name__)
# ConfigR waits roughly ten seconds for the panel heater to enter standby before
# retrying a later connection.
PAIRING_RETRY_DELAY = 10.0
PAIRING_MAX_AUTOMATIC_RETRIES = 3


class DimplexBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Discover a panel heater and complete first-time pairing through its proxy."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow-local discovery and pairing state."""
        self._devices: dict[str, tuple[str, int, str | None]] = {}
        self._device_by_address: dict[str, BLEDevice] = {}
        self._ble_device: BLEDevice | None = None
        self._address: str | None = None
        self._proxy_source: str | None = None
        self._advertised_name: str | None = None
        self._pairing_task: asyncio.Task[None] | None = None
        self._pairing_retry_task: asyncio.Task[None] | None = None
        self._pairing_lock = asyncio.Lock()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a lost BLE authentication bond."""
        address = entry_data.get(CONF_ADDRESS)
        proxy_source = entry_data.get(CONF_PROXY_SOURCE)
        if not isinstance(address, str) or not isinstance(proxy_source, str):
            return self.async_abort(reason="proxy_unavailable")
        error = await self._async_prepare_repair(entry_data, start_pairing=False)
        if error is not None:
            return self._show_reauth_form(error)
        # Do not occupy a proxy slot while an automatically-created reauth
        # flow is waiting for the user. Pairing starts when the user opens the
        # repair step and submits this transition.
        return self._show_pair_start_form()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a user-requested authentication repair."""
        if user_input is not None:
            return await self.async_step_reauth_confirm(user_input)
        entry = self._get_reconfigure_entry()
        error = await self._async_prepare_repair(entry.data)
        if error is not None:
            return self._show_reauth_form(error)
        return self._show_reauth_form()

    async def _async_prepare_repair(
        self, entry_data: Mapping[str, Any], *, start_pairing: bool = True
    ) -> str | None:
        """Prepare a repair flow from an existing entry identity."""
        address = entry_data.get(CONF_ADDRESS)
        proxy_source = entry_data.get(CONF_PROXY_SOURCE)
        if not isinstance(address, str) or not isinstance(proxy_source, str):
            return "proxy_unavailable"
        self._address = address
        self._proxy_source = proxy_source
        entry = self._get_repair_entry()
        self._advertised_name = entry.title
        for prefix in ("Dimplex Panel Heater ", "Dimplex Heater "):
            if entry.title.startswith(prefix):
                self._advertised_name = entry.title.removeprefix(prefix)
                break
        self._ble_device = ble_device_from_source(self.hass, address, proxy_source)
        if self._ble_device is None:
            # A bonded panel heater can stop advertising after authentication is lost.
            # Keep the configured proxy route usable from its stored identity so
            # the connection attempt can trigger the panel heater's PIN display.
            address_type = entry_data.get(CONF_ADDRESS_TYPE, 0)
            if not isinstance(address_type, int):
                address_type = 0
            self._ble_device = BLEDevice(
                address,
                self._advertised_name,
                {"source": proxy_source, "address_type": address_type},
            )
        if not start_pairing:
            return None
        async with self._pairing_lock:
            error = await self._async_start_pairing(address, proxy_source)
            if error is None or error == "pairing_in_progress":
                self._async_ensure_pairing_retry()
        return error

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the panel heater PIN and restore its BLE authentication bond."""
        if user_input is None:
            address = self._address
            proxy_source = self._proxy_source
            if address is None or proxy_source is None:
                return self.async_abort(reason="proxy_unavailable")
            async with self._pairing_lock:
                error = await self._async_start_pairing(address, proxy_source)
                if error is None:
                    self._async_ensure_pairing_retry()
            if error is not None:
                return self._show_reauth_form(error)
            return self._show_reauth_form()

        passkey = user_input.get(CONF_PASSKEY)
        if not isinstance(passkey, str) or not self._valid_passkey(passkey):
            return self._show_reauth_form("invalid_passkey")

        address = self._address
        proxy_source = self._proxy_source
        if address is None or proxy_source is None:
            return self.async_abort(reason="proxy_unavailable")

        try:
            async with self._pairing_lock:
                error = await self._async_start_pairing(address, proxy_source)
                if error is not None:
                    return self._show_reauth_form(error)
                task = self._pairing_task
                if task is None:
                    return self._show_reauth_form("pairing_failed")
                await async_send_passkey(self.hass, proxy_source, address, passkey)
            await task
        except (DimplexPairingError, DimplexConnectionError, HomeAssistantError):
            return self._show_reauth_form("pairing_failed")

        retry_task = self._pairing_retry_task
        if retry_task is not None and not retry_task.done():
            retry_task.cancel()
        self._pairing_retry_task = None
        return self.async_update_reload_and_abort(
            self._get_repair_entry(),
            data_updates={CONF_PASSKEY: passkey},
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Start setup for a panel heater found by Home Assistant Bluetooth."""
        name = discovery_info.name or discovery_info.device.name or ""
        if not is_dimplex_advertisement(name):
            _LOGGER.debug("Rejected a Bluetooth discovery that is not Dimplex")
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
            _LOGGER.debug(
                "Rejected a Dimplex discovery because no passkey-capable route was found"
            )
            return self.async_abort(reason="proxy_passkey_support_missing")

        selected_route = max(
            (
                info
                for info in service_infos
                if info.connectable
                and info.source == proxy_source
                and format_mac(info.device.address) == address
            ),
            key=lambda info: info.rssi,
            default=None,
        )
        if selected_route is not None:
            self._ble_device = selected_route.device
        else:
            try:
                self._ble_device = ble_device_from_source(
                    self.hass, address, proxy_source
                )
            except RuntimeError:
                self._ble_device = None
        self._address = address
        self._proxy_source = proxy_source
        return await self.async_step_passkey()

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start pairing after the user explicitly continues."""
        if self._address is None or self._proxy_source is None:
            return self.async_abort(reason="proxy_unavailable")
        if user_input is None:
            return self._show_pair_start_form()
        if self.context.get("source") == SOURCE_REAUTH:
            return await self.async_step_reauth_confirm()
        return await self.async_step_passkey()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List nearby panel heaters that a passkey-enabled ESPHome proxy can reach."""
        if not self._devices:
            await bluetooth.async_request_active_scan(self.hass, duration=5.0)
            service_infos = list(
                bluetooth.async_discovered_service_info(self.hass, connectable=True)
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
                    default=None,
                )
                if selected_route is not None:
                    selected_name = (
                        selected_route.name
                        or selected_route.device.name
                        or advertised_name
                    )
                    selected_rssi = selected_route.rssi
                    self._device_by_address[address] = selected_route.device
                else:
                    cached_device = ble_device_from_source(
                        self.hass, address, proxy_source
                    )
                    if cached_device is None:
                        unsupported_heater_seen = True
                        continue
                    selected_name = advertised_name
                    selected_rssi = max(
                        (
                            info.rssi
                            for info in routes_by_address[address]
                            if info.connectable
                        ),
                        default=-127,
                    )
                    self._device_by_address[address] = cached_device
                if not is_dimplex_advertisement(selected_name):
                    selected_name = advertised_name
                self._devices[address] = (
                    proxy_source,
                    selected_rssi,
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
            self._ble_device = self._device_by_address.get(address)
            self._address = address
            self._proxy_source = selected[0]
            self._advertised_name = selected[2]
            if self._advertised_name is not None:
                self.context["title_placeholders"] = {"name": self._advertised_name}
            return await self.async_step_passkey()
        return self._show_user_form()

    async def async_step_passkey(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start pairing on entry, then submit the PIN from the panel-heater display."""
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
                    await async_send_passkey(self.hass, proxy_source, address, passkey)

            if not pairing_complete:
                await cast(asyncio.Task[None], task)
        except DimplexPairingError:
            return self._show_passkey_form("proxy_passkey_support_missing")
        except (DimplexConnectionError, HomeAssistantError, TimeoutError):
            return self._show_passkey_form("pairing_failed")
        finally:
            user_input.pop(CONF_PASSKEY, None)

        retry_task = self._pairing_retry_task
        if retry_task is not None and not retry_task.done():
            retry_task.cancel()
        self._pairing_retry_task = None
        if self._ble_device is not None:
            pending_devices = self.hass.data.setdefault(DOMAIN, {}).setdefault(
                PENDING_DEVICES, {}
            )
            pending_devices[address] = self._ble_device
        title = self._next_device_title()
        entry_data: dict[str, Any] = {
            CONF_ADDRESS: address,
            CONF_PROXY_SOURCE: proxy_source,
        }
        entry_data[CONF_PASSKEY] = passkey
        details = getattr(self._ble_device, "details", None)
        address_type = (
            details.get("address_type") if isinstance(details, dict) else None
        )
        if type(address_type) is int:
            entry_data[CONF_ADDRESS_TYPE] = address_type
        return self.async_create_entry(
            title=title,
            data=entry_data,
        )

    async def _async_start_pairing(self, address: str, proxy_source: str) -> str | None:
        """Start one managed pairing session for the selected panel heater."""
        if self._pairing_task is not None and not self._pairing_task.done():
            return None

        client: DimplexBluetoothClient | None = None
        if self.context.get("source") in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            runtime_data = getattr(self._get_repair_entry(), "runtime_data", None)
            client = getattr(runtime_data, "client", None)
        if client is None:
            client = DimplexBluetoothClient(
                self.hass, address, proxy_source, self._ble_device
            )
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
        """Retry pairing a bounded number of times while the flow is open."""
        address = self._address
        proxy_source = self._proxy_source
        if address is None or proxy_source is None:
            return

        attempts = 1 if self._pairing_task is not None else 0
        while True:
            task = self._pairing_task
            if task is None:
                if attempts >= PAIRING_MAX_AUTOMATIC_RETRIES:
                    _LOGGER.debug(
                        "%s Automatic pairing retry limit reached",
                        DEBUG_LOG_PREFIX,
                    )
                    return
                await asyncio.sleep(PAIRING_RETRY_DELAY)
                async with self._pairing_lock:
                    if self._pairing_task is not None:
                        continue
                    attempts += 1
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
                if attempts >= PAIRING_MAX_AUTOMATIC_RETRIES:
                    _LOGGER.debug(
                        "%s Automatic pairing retry limit reached",
                        DEBUG_LOG_PREFIX,
                    )
                    return
                await asyncio.sleep(PAIRING_RETRY_DELAY)
                async with self._pairing_lock:
                    if self._pairing_task is task:
                        self._pairing_task = None
                        attempts += 1
                        error = await self._async_start_pairing(address, proxy_source)
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

    def _show_pair_start_form(self, error: str | None = None) -> ConfigFlowResult:
        """Offer a retry after pairing could not start."""
        return self.async_show_form(
            step_id="pair",
            data_schema=probatio.Schema({}),
            description_placeholders={
                "name": self._advertised_name or "the selected panel heater",
            },
            errors={"base": error} if error else {},
        )

    def _show_user_form(self, error: str | None = None) -> ConfigFlowResult:
        """Show every eligible panel heater using its advertised local name."""
        ordered_devices = sorted(
            self._devices.items(), key=lambda item: item[1][1], reverse=True
        )
        options: list[SelectOptionDict] = []
        for index, (address, (_, rssi, advertised_name)) in enumerate(
            ordered_devices, start=1
        ):
            label = advertised_name or str(index)
            options.append(
                {
                    "value": address,
                    "label": f"Dimplex Panel Heater {label} ({rssi} dBm)",
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

    def _get_repair_entry(self) -> ConfigEntry:
        """Return the entry being repaired or manually reconfigured."""
        if self.context.get("source") == SOURCE_RECONFIGURE:
            return self._get_reconfigure_entry()
        return self._get_reauth_entry()

    def _show_reauth_form(self, error: str | None = None) -> ConfigFlowResult:
        """Show the panel-heater PIN form for authentication repair."""
        step_id = (
            "reconfigure"
            if self.context.get("source") == SOURCE_RECONFIGURE
            else "reauth_confirm"
        )
        return self.async_show_form(
            step_id=step_id,
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
                "name": self._advertised_name or "the selected panel heater",
            },
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
                "name": self._advertised_name or "the selected panel heater",
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
        def _normalize_title(title: str) -> str:
            for prefix in ("Dimplex Panel Heater ", "Dimplex Heater "):
                if title.startswith(prefix):
                    return title.removeprefix(prefix).casefold()
            return title.casefold()

        normalized_titles = {_normalize_title(title) for title in titles}

        if self._advertised_name is not None:
            base_title = f"Dimplex Panel Heater {self._advertised_name}"
            title = base_title
            suffix = 2
            while _normalize_title(title) in normalized_titles:
                title = f"{base_title} ({suffix})"
                suffix += 1
        else:
            index = 1
            title = f"Dimplex Panel Heater {index}"
            while _normalize_title(title) in normalized_titles:
                index += 1
                title = f"Dimplex Panel Heater {index}"

        reserved_titles.add(title)
        return title

    @staticmethod
    def _valid_passkey(passkey: str) -> bool:
        """Accept exactly six ASCII decimal digits."""
        return len(passkey) == 6 and all("0" <= digit <= "9" for digit in passkey)
