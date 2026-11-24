"""Every user-visible key the code produces has its text and icon."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.dimplex_ble import config_flow
from custom_components.dimplex_ble.climate import PRESET_MODES
from custom_components.dimplex_ble.protocol import (
    DOCUMENTED_ERROR_CODES,
    HEATING_MODE_KEYS,
    error_code_state,
)
from custom_components.dimplex_ble.select import DISPLAY_UNIT_OPTIONS

ROOT = Path(__file__).parent.parent / "custom_components" / "dimplex_ble"
STRINGS = json.loads((ROOT / "translations" / "en.json").read_text())
ICONS = json.loads((ROOT / "icons.json").read_text())
ENTITY = STRINGS["entity"]


def test_every_preset_has_a_preset_mode_translation_and_icon() -> None:
    """Presets translate under state_attributes, not the climate state."""
    heater = ENTITY["climate"]["heater"]
    texts = heater["state_attributes"]["preset_mode"]["state"]
    icons = ICONS["entity"]["climate"]["heater"]["state_attributes"]["preset_mode"]
    assert set(PRESET_MODES) == set(texts) == set(icons["state"])
    assert "state" not in heater  # would translate the HVAC mode instead
    assert "name" not in heater  # the climate entity is named after the device
    assert "home" not in texts  # HA's "home" means occupied, not a timer


def test_operating_mode_options_are_translated_with_icons() -> None:
    sensor = ENTITY["sensor"]["operating_mode"]
    assert set(sensor["state"]) == set(HEATING_MODE_KEYS)
    assert set(ICONS["entity"]["sensor"]["operating_mode"]["state"]) == set(
        HEATING_MODE_KEYS
    )


def test_only_documented_error_codes_have_texts() -> None:
    states = {error_code_state(code) for code in DOCUMENTED_ERROR_CODES}
    assert set(ENTITY["sensor"]["error_code"]["state"]) == states


def test_display_unit_states_are_translated_with_icons() -> None:
    assert set(ENTITY["select"]["temperature_unit"]["state"]) == set(
        DISPLAY_UNIT_OPTIONS
    )
    assert set(ICONS["entity"]["select"]["temperature_unit"]["state"]) == set(
        DISPLAY_UNIT_OPTIONS
    )


def test_switches_carry_no_explanation_attributes() -> None:
    for switch in ENTITY["switch"].values():
        assert set(switch) == {"name"}


@pytest.mark.parametrize(
    "text",
    [
        *(step.get("description", "") for step in STRINGS["config"]["step"].values()),
        *STRINGS["config"]["error"].values(),
        *STRINGS["config"]["progress"].values(),
    ],
)
def test_flow_texts_are_one_short_sentence(text: str) -> None:
    """Dialogs stay short: details live in the README."""
    text = text.replace("{proxy_notes}", "")
    assert "\n" not in text
    assert "**" not in text
    assert len(text.split()) <= 16


def test_flow_steps_have_texts() -> None:
    steps = set(STRINGS["config"]["step"])
    defined = {
        name.removeprefix("async_step_")
        for name in vars(config_flow.DimplexBleConfigFlow)
        if name.startswith("async_step_")
    }
    # Steps that only route (no form) need no text.
    routing = {"bluetooth", "reauth", "pairing", "paired", "show_pin"}
    assert defined - routing == steps
    assert STRINGS["config"]["flow_title"] == "{name}"


def test_texts_use_no_mac_or_signal_numbers() -> None:
    text = json.dumps(STRINGS)
    assert not re.search(r"dBm|([0-9A-F]{2}:){5}", text)
