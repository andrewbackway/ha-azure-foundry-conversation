"""The Azure AI Foundry Conversation integration."""

from __future__ import annotations

import openai

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .client import create_client, endpoint_host
from .const import CONF_ENDPOINT, DOMAIN, LOGGER
from .image_generation import SERVICE_GENERATE_IMAGE, async_setup_image_service

PLATFORMS = (
    Platform.AI_TASK,
    Platform.CONVERSATION,
    Platform.STT,
    Platform.TTS,
)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AzureFoundryConfigEntry = ConfigEntry[openai.AsyncOpenAI]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Azure AI Foundry Conversation integration."""
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: AzureFoundryConfigEntry
) -> bool:
    """Set up Azure AI Foundry Conversation from a config entry."""
    endpoint = entry.data[CONF_ENDPOINT]
    client = create_client(hass, entry.data[CONF_API_KEY], endpoint)

    try:
        await client.with_options(timeout=10.0).models.list()
    except openai.AuthenticationError as err:
        LOGGER.error(
            "Authentication failed for Azure Foundry endpoint %s: %s",
            endpoint_host(endpoint),
            err,
        )
        raise ConfigEntryAuthFailed(err) from err
    except openai.OpenAIError as err:
        LOGGER.error(
            "Unable to reach Azure Foundry endpoint %s: %s",
            endpoint_host(endpoint),
            err,
        )
        raise ConfigEntryNotReady(err) from err

    entry.runtime_data = client
    LOGGER.info("Azure Foundry connection ready (endpoint=%s)", endpoint_host(endpoint))

    await async_setup_image_service(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AzureFoundryConfigEntry
) -> bool:
    """Unload the Azure AI Foundry Conversation integration."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not any(
        other.state is ConfigEntryState.LOADED
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        hass.services.async_remove(DOMAIN, SERVICE_GENERATE_IMAGE)
    return unloaded


async def async_update_options(
    hass: HomeAssistant, entry: AzureFoundryConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
