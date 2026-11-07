"""The Eni Plenitude integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import PlenitudeCoordinator

PLATFORMS = [Platform.SENSOR]

type PlenitudeConfigEntry = ConfigEntry[PlenitudeCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PlenitudeConfigEntry) -> bool:
    """Set up a POD from a config entry."""
    coordinator = PlenitudeCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Price changed in the options: reload so cost uses it.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: PlenitudeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PlenitudeConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
