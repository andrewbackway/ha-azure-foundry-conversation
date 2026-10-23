"""Tests for Azure AI Foundry image generation."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.azure_foundry_conversation.const import (
    CONF_IMAGE_HEIGHT,
    CONF_IMAGE_MODEL,
    CONF_IMAGE_QUALITY,
    CONF_IMAGE_SIZE,
    CONF_IMAGE_WIDTH,
    DOMAIN,
    SUBENTRY_IMAGE,
)
from custom_components.azure_foundry_conversation.conversation import (
    _is_image_generation_request,
)
from custom_components.azure_foundry_conversation.image_generation import (
    DEFAULT_IMAGE_SIZE,
    IMAGE_MODEL,
    SERVICE_GENERATE_IMAGE,
    _select_config_entry,
    async_generate_image,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError


def _entry_with_image_subentry(
    entry: MockConfigEntry, options: dict[str, Any]
) -> SimpleNamespace:
    """Build an entry-like object with the supplied image subentry options."""
    return SimpleNamespace(
        entry_id=entry.entry_id,
        data=entry.data,
        runtime_data=entry.runtime_data,
        subentries={
            "image-subentry": SimpleNamespace(
                subentry_type=SUBENTRY_IMAGE,
                subentry_id="image-subentry",
                data=options,
            )
        },
    )


def test_service_selects_connection_with_image_subentry() -> None:
    """The service prefers the connection that has image configuration."""
    first = SimpleNamespace(entry_id="first", subentries={})
    image_subentry = SimpleNamespace(
        subentry_type=SUBENTRY_IMAGE,
        subentry_id="image-id",
    )
    second = SimpleNamespace(
        entry_id="second",
        subentries={"image-id": image_subentry},
    )

    assert _select_config_entry([first, second]) is second
    assert _select_config_entry([first, second], image_subentry_id="image-id") is second


def test_service_requires_connection_when_image_config_is_ambiguous() -> None:
    """The service avoids silently selecting the wrong Azure connection."""
    first = SimpleNamespace(entry_id="first", subentries={})
    second = SimpleNamespace(entry_id="second", subentries={})

    with pytest.raises(HomeAssistantError, match="specify config_entry_id"):
        _select_config_entry([first, second])


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


async def test_generate_image_uses_configured_image_subentry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Image generation uses the deployment and defaults from its subentry."""
    entry = _entry_with_image_subentry(
        init_integration,
        {
            CONF_IMAGE_MODEL: "my-image-deployment",
            CONF_IMAGE_SIZE: "1536x1024",
            CONF_IMAGE_QUALITY: "medium",
        },
    )
    entry.runtime_data.images.generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode("ascii"))]
        )
    )

    result = await async_generate_image(hass, entry, "A red house")

    assert result["model"] == "my-image-deployment"
    assert result["size"] == "1536x1024"
    entry.runtime_data.images.generate.assert_awaited_once_with(
        model="my-image-deployment",
        prompt="A red house",
        size="1536x1024",
        n=1,
        quality="medium",
        output_format="png",
    )


async def test_generate_image_uses_custom_dimensions(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Custom dimensions from the image subentry are passed to Azure."""
    entry = _entry_with_image_subentry(
        init_integration,
        {
            CONF_IMAGE_SIZE: "custom",
            CONF_IMAGE_WIDTH: 1536,
            CONF_IMAGE_HEIGHT: 864,
        },
    )
    entry.runtime_data.images.generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode("ascii"))]
        )
    )
    result = await async_generate_image(hass, entry, "A red house")

    assert result["size"] == "1536x864"
    entry.runtime_data.images.generate.assert_awaited_once_with(
        model=IMAGE_MODEL,
        prompt="A red house",
        size="1536x864",
        n=1,
        quality="high",
        output_format="png",
    )


async def test_generate_image_rejects_unsupported_size(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Only image sizes supported by gpt-image-1 are accepted."""
    with pytest.raises(HomeAssistantError, match="Unsupported image size"):
        await async_generate_image(hass, init_integration, "A red house", "512x512")


async def test_generate_image_rejects_invalid_base64(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Invalid image data is reported as a Home Assistant error."""
    init_integration.runtime_data.images.generate = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(b64_json="not base64!")])
    )

    with pytest.raises(HomeAssistantError, match="invalid image data"):
        await async_generate_image(hass, init_integration, "A red house")


async def test_generate_image_service_returns_image(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The service generates an image and returns its local URL."""
    init_integration.runtime_data.images.generate = AsyncMock(
        return_value=SimpleNamespace(
            data=[
                SimpleNamespace(
                    b64_json=base64.b64encode(b"fake-png-data").decode("ascii")
                )
            ]
        )
    )

    result = await hass.services.async_call(
        DOMAIN,
        SERVICE_GENERATE_IMAGE,
        {"prompt": "A red house"},
        blocking=True,
        return_response=True,
    )

    assert result["model"] == IMAGE_MODEL
    assert result["prompt"] == "A red house"
    assert result["image_url"].startswith("/local/azure_foundry_images/")
    assert Path(result["path"]).read_bytes() == b"fake-png-data"


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
