"""Shared fixtures for the Azure AI Foundry Conversation tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.azure_foundry_conversation.const import (
    CONF_CHAT_MODEL,
    CONF_ENDPOINT,
    CONF_STT_API_KEY,
    CONF_STT_ENDPOINT,
    CONF_TTS_API_KEY,
    CONF_TTS_ENDPOINT,
    DOMAIN,
    RECOMMENDED_STT_OPTIONS,
    RECOMMENDED_TTS_OPTIONS,
)
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component

ENDPOINT = "https://res.services.ai.azure.com/"
SPEECH_ENDPOINT = "https://res.cognitiveservices.azure.com/"


def make_auth_error() -> openai.AuthenticationError:
    """Build an openai AuthenticationError for tests."""
    request = httpx.Request("GET", f"{ENDPOINT}openai/v1/models")
    response = httpx.Response(401, request=request)
    return openai.AuthenticationError("unauthorized", response=response, body=None)


def make_connection_error() -> openai.APIConnectionError:
    """Build an openai APIConnectionError for tests."""
    return openai.APIConnectionError(
        request=httpx.Request("GET", f"{ENDPOINT}openai/v1/models")
    )


class FakeStream:
    """A minimal async-iterable stand-in for an openai Responses stream."""

    def __init__(self, events: list) -> None:
        self._events = list(events)
        self._iter: Iterator = iter(())

    def __aiter__(self) -> FakeStream:
        self._iter = iter(self._events)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.fixture(autouse=True)
async def auto_enable_custom_integrations(
    hass: HomeAssistant, enable_custom_integrations
):
    """Initialize Home Assistant and enable loading custom integrations."""
    await async_setup_component(hass, "homeassistant", {})
    yield


@pytest.fixture
def mock_config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry with one of each subentry type."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Azure AI Foundry",
        data={CONF_ENDPOINT: ENDPOINT, CONF_API_KEY: "key"},
        subentries_data=[
            {
                "subentry_type": "conversation",
                "title": "Conversation",
                "unique_id": None,
                "data": {
                    CONF_CHAT_MODEL: "gpt-5.6-sol",
                    CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
                },
            },
            {
                "subentry_type": "ai_task_data",
                "title": "AI Task",
                "unique_id": None,
                "data": {CONF_CHAT_MODEL: "gpt-5.6-sol"},
            },
            {
                "subentry_type": "stt",
                "title": "STT",
                "unique_id": None,
                "data": {
                    CONF_STT_ENDPOINT: SPEECH_ENDPOINT,
                    CONF_STT_API_KEY: "skey",
                    **RECOMMENDED_STT_OPTIONS,
                },
            },
            {
                "subentry_type": "tts",
                "title": "TTS",
                "unique_id": None,
                "data": {
                    CONF_TTS_ENDPOINT: SPEECH_ENDPOINT,
                    CONF_TTS_API_KEY: "skey",
                    **RECOMMENDED_TTS_OPTIONS,
                },
            },
        ],
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_openai_client() -> MagicMock:
    """Return a mock openai client whose connectivity check succeeds."""
    client = MagicMock()
    client.with_options.return_value.models.list = AsyncMock(return_value=MagicMock())
    client.responses.create = AsyncMock()
    return client


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_openai_client
) -> MockConfigEntry:
    """Set up the integration with the network calls mocked out."""
    with (
        patch(
            "custom_components.azure_foundry_conversation.create_client",
            return_value=mock_openai_client,
        ),
        patch(
            "custom_components.azure_foundry_conversation.tts.async_list_voices",
            AsyncMock(return_value=[]),
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    mock_config_entry.runtime_data = mock_openai_client
    return mock_config_entry
