"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.azure_foundry_conversation.config_flow import (
    _update_speech_options,
)
from custom_components.azure_foundry_conversation.const import (
    CONF_ENDPOINT,
    CONF_STT_API_KEY,
    CONF_STT_ENDPOINT,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import ENDPOINT, make_auth_error, make_connection_error


def test_update_speech_options_removes_cleared_credentials() -> None:
    """Cleared optional speech credentials are removed from saved options."""
    options = {
        CONF_STT_ENDPOINT: "https://speech.example",
        CONF_STT_API_KEY: "speech-key",
        "language": "en-US",
    }

    _update_speech_options(
        options, {"language": "en-GB"}, CONF_STT_ENDPOINT, CONF_STT_API_KEY
    )

    assert options == {"language": "en-GB"}


async def test_user_flow_creates_entry_with_subentries(hass: HomeAssistant) -> None:
    """A successful flow creates the entry plus four subentries."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with (
        patch(
            "custom_components.azure_foundry_conversation.config_flow.validate_input",
            AsyncMock(),
        ),
        patch(
            "custom_components.azure_foundry_conversation.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENDPOINT: ENDPOINT, CONF_API_KEY: "key"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ENDPOINT: ENDPOINT, CONF_API_KEY: "key"}
    subentry_types = sorted(sub["subentry_type"] for sub in result["subentries"])
    assert subentry_types == ["ai_task_data", "conversation", "stt", "tts"]


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    """An auth failure surfaces invalid_auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(
        "custom_components.azure_foundry_conversation.config_flow.validate_input",
        AsyncMock(side_effect=make_auth_error()),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENDPOINT: ENDPOINT, CONF_API_KEY: "bad"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """A connection failure surfaces cannot_connect."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(
        "custom_components.azure_foundry_conversation.config_flow.validate_input",
        AsyncMock(side_effect=make_connection_error()),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_ENDPOINT: ENDPOINT, CONF_API_KEY: "key"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
