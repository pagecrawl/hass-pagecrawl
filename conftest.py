"""Root conftest making the custom_components package importable in tests."""

from pytest_homeassistant_custom_component.common import (  # noqa: F401
    MockConfigEntry,
)

pytest_plugins = ["pytest_homeassistant_custom_component"]
