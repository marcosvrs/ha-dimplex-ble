"""Integration errors and the single translation point from native BLE errors."""

from __future__ import annotations

import re

from bleak.exc import BleakError
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN
from .protocol import DimplexProtocolError

# SMP failure reasons that mean "the heater rejected the PIN". Only 81
# (CONFIRM_VALUE_FAIL) is hardware-confirmed: a wrong PIN produced it twice
# (ledger H8, docs/investigation-2026-11-24/ledger.md, 2026-11-24 16:36:40 and
# 16:50:59). 78 PASSKEY_FAIL, 80 PAIR_AUTH_FAIL and 88 DHKEY_CHK_FAIL were never
# observed, so they stay transient (no reauth), like every other code including
# 99 (SMP timeout) and 102 (link lost).
CREDENTIAL_REJECTED_CODES = frozenset({81})

# bleak-esphome 4.0.0 flattens the native pairing response into this message
# (backend/client.py:596); it is the only string the integration parses.
_PAIR_ERROR = re.compile(r"^Pairing failed due to error: (-?\d+)$")


class DimplexError(Exception):
    """Base class for panel-heater operation failures."""

    translation_key = "cannot_connect"


class HeaterUnavailableError(DimplexError):
    """The heater or its proxy route is temporarily unavailable."""


class ResponderMissingError(HeaterUnavailableError):
    """The pinned proxy does not expose the Dimplex PIN responder actions."""

    translation_key = "responder_missing"


class HeaterAuthError(DimplexError):
    """The heater rejected the stored PIN during pairing."""

    translation_key = "reauth_required"


class CharacteristicError(DimplexError):
    """A GATT characteristic is absent or lacks the requested property."""

    translation_key = "characteristic_unavailable"


def pair_error_code(err: BaseException) -> int | None:
    """Return the native SMP failure reason of a failed ``pair()``."""
    if not isinstance(err, BleakError):
        return None
    match = _PAIR_ERROR.match(str(err))
    return int(match.group(1)) if match else None


def att_error_code(err: BaseException) -> int | None:
    """Return the ATT/GATT status kept by bleak-esphome in ``__cause__``."""
    status = getattr(getattr(err.__cause__, "error", None), "error", None)
    return status if isinstance(status, int) else None


def classify_pair_error(err: BaseException) -> DimplexError:
    """Map a failed ``pair()`` to reauth (PIN rejected) or transient."""
    if pair_error_code(err) in CREDENTIAL_REJECTED_CODES:
        return HeaterAuthError("The heater rejected the PIN")
    return HeaterUnavailableError("Pairing did not complete")


def to_homeassistant_error(
    err: DimplexError | DimplexProtocolError,
) -> HomeAssistantError:
    """Return the translated Home Assistant error for a failed operation."""
    if isinstance(err, DimplexProtocolError):
        key = "invalid_gatt_request"
    else:
        key = err.translation_key
    error_type = (
        ServiceValidationError
        if isinstance(err, (DimplexProtocolError, CharacteristicError))
        else HomeAssistantError
    )
    return error_type(translation_domain=DOMAIN, translation_key=key)
