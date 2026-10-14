"""Tests for Azure AI Foundry image generation."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.azure_foundry_conversation.conversation import _is_image_generation_request
from custom_components.azure_foundry_conversation.image_generation import (
    DEFAULT_IMAGE_SIZE,
    IMAGE_MODEL,
    SERVICE_GENERATE_IMAGE,
    async_generate_image,
)
from custom_components.azure_foundry_conversation.const import DOMAIN


async def test_generate_image_saves_png(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Image generation decodes Azure's base64 response and saves a local PNG."""
    png_bytes = b"fake-png-data"
    init_integration.runtime_data.images.generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes).decode("ascii"))]
        )
    )

    result = await async_generate_image(
        hass, init_integration, "A red house", DEFAULT_IMAGE_SIZE
    )

    assert result["model"] == IMAGE_MODEL
    assert result["prompt"] == "A red house"
    assert result["image_url"].startswith("/local/azure_foundry_images/")
    assert Path(result["path"]).read_bytes() == png_bytes
    init_integration.runtime_data.images.generate.assert_awaited_once_with(
        model=IMAGE_MODEL,
        prompt="A red house",
        size=DEFAULT_IMAGE_SIZE,
        n=1,
        quality="high",
        output_format="png",
    )


async def test_generate_image_rejects_empty_prompt(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """An empty prompt is rejected before calling Azure."""
    with pytest.raises(HomeAssistantError, match="prompt is required"):
        await async_generate_image(hass, init_integration, "   ")

    init_integration.runtime_data.images.generate.assert_not_called()


async def test_generate_image_rejects_unsupported_size(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Only image sizes supported by gpt-image-1 are accepted."""
    with pytest.raises(HomeAssistantError, match="Unsupported image size"):
        await async_generate_image(
            hass, init_integration, "A red house", "512x512"
        )


async def test_generate_image_rejects_invalid_base64(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Invalid image data is reported as a Home Assistant error."""
    init_integration.runtime_data.images.generate = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(b64_json="not base64!")])
    )

    with pytest.raises(HomeAssistantError, match="invalid image data"):
        await async_generate_image(hass, init_integration, "A red house")


async def test_generate_image_service_is_registered(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The service is available after the integration is loaded."""
    assert hass.services.has_service(DOMAIN, SERVICE_GENERATE_IMAGE)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Create an image of a red campervan", True),
        ("Can you draw a picture of a kookaburra?", True),
        ("What is image generation?", False),
        ("Turn on the kitchen light", False),
        ("Do not create an image", False),
    ],
)
def test_image_generation_request_detection(message: str, expected: bool) -> None:
    """Only explicit creation requests bypass the normal conversation model."""
    assert _is_image_generation_request(message) is expected
