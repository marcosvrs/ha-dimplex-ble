from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def enable_local_custom_integrations(enable_custom_integrations: None) -> None:
    """Make the local integration available to Home Assistant's loader."""
