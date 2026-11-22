"""Own one Dimplex pairing attempt from relay arm through cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import esphome_proxy
from .const import DOMAIN
from .esphome_proxy import (
    DEBUG_LOG_PREFIX,
    DimplexPairingError,
    DimplexPairingInProgressError,
    async_arm_pairing,
    async_cancel_pairing,
)

_PAIRING_LOCKS = "proxy_pairing_locks"


def proxy_pairing_lock(hass: HomeAssistant, source: str) -> asyncio.Lock:
    """Return one lock per proxy so concurrent PIN flows cannot collide."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks: dict[str, asyncio.Lock] = domain_data.setdefault(_PAIRING_LOCKS, {})
    key = source.casefold()
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


_LOGGER = logging.getLogger(__name__)


class DimplexPairingSession:
    """Manage proxy relay state, one pairing task, and its cleanup invariant."""

    def __init__(
        self,
        hass: HomeAssistant,
        source: str,
        address: str,
        pair: Callable[[], Awaitable[None]],
        *,
        timeout: float | None = None,
    ) -> None:
        """Initialize one address-scoped pairing attempt."""
        self._hass = hass
        self._source = source
        self._address = address
        self._pair = pair
        self._timeout = esphome_proxy.PAIRING_TIMEOUT if timeout is None else timeout
        self._lock = proxy_pairing_lock(hass, source)
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        """Return the background pairing task, if the attempt was started."""
        return self._task

    async def async_start(self) -> asyncio.Task[None]:
        """Arm the proxy and start the pairing task."""
        if self._task is not None and not self._task.done():
            return self._task
        if self._lock.locked():
            raise DimplexPairingInProgressError(
                "The selected ESPHome proxy is already pairing with another panel heater"
            )

        await self._lock.acquire()
        try:
            await async_arm_pairing(self._hass, self._source, self._address)
            self._task = self._hass.async_create_task(self._async_run())
        except BaseException:
            self._lock.release()
            raise
        return self._task

    async def async_wait(self) -> None:
        """Wait for pairing to complete and propagate its result."""
        task = self._task
        if task is None:
            raise DimplexPairingError("The pairing attempt has not been started")
        await task

    def async_cancel(self) -> None:
        """Cancel the background task; its finally path owns relay cleanup."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()

    async def _async_run(self) -> None:
        """Run pairing with bounded wait and unconditional relay cleanup."""
        try:
            await asyncio.wait_for(self._pair(), timeout=self._timeout)
        except asyncio.CancelledError:
            _LOGGER.warning("%s BLE pairing task cancelled", DEBUG_LOG_PREFIX)
            raise
        except TimeoutError:
            _LOGGER.warning(
                "%s BLE pairing attempt timed out after %.0f seconds",
                DEBUG_LOG_PREFIX,
                self._timeout,
            )
            raise
        except Exception as err:
            _LOGGER.warning(
                "%s BLE pairing attempt failed (%s)",
                DEBUG_LOG_PREFIX,
                type(err).__name__,
                exc_info=True,
            )
            raise
        finally:
            try:
                await async_cancel_pairing(self._hass, self._source)
            except (DimplexPairingError, HomeAssistantError):
                _LOGGER.debug(
                    "%s Could not clear the ESPHome pairing relay",
                    DEBUG_LOG_PREFIX,
                    exc_info=True,
                )
            finally:
                self._lock.release()


async def async_start_pairing(
    hass: HomeAssistant,
    source: str,
    address: str,
    pair: Callable[[], Awaitable[None]],
    *,
    pairing_timeout: float | None = None,
) -> asyncio.Task[None]:
    """Start one managed pairing session and return its task interface."""
    session = DimplexPairingSession(
        hass,
        source,
        address,
        pair,
        timeout=pairing_timeout,
    )
    return await session.async_start()


async def async_pair_with_passkey(
    hass: HomeAssistant,
    source: str,
    address: str,
    passkey: str,
    pair: Callable[[], Awaitable[None]],
    *,
    repair_timeout: float,
) -> None:
    """Repair one lost bond with a stored passkey and a bounded timeout."""

    async def pair_with_passkey() -> None:
        await esphome_proxy.async_send_passkey(hass, source, address, passkey)
        await pair()

    task = await async_start_pairing(
        hass,
        source,
        address,
        pair_with_passkey,
        pairing_timeout=repair_timeout,
    )
    await task
