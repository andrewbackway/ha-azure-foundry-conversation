"""Image generation helpers and Home Assistant service for Azure AI Foundry."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import openai
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from . import AzureFoundryConfigEntry

SERVICE_GENERATE_IMAGE = "generate_image"
CONF_PROMPT = "prompt"
CONF_SIZE = "size"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
IMAGE_MODEL = "gpt-image-1"
DEFAULT_IMAGE_SIZE = "1024x1024"
IMAGE_SIZES = ("1024x1024", "1536x1024", "1024x1536")
IMAGE_DIRECTORY = "azure_foundry_images"


def _safe_filename() -> str:
    """Return a unique filename safe for use below the image output directory."""
    return f"{uuid4().hex}.png"


async def async_generate_image(
    hass: HomeAssistant,
    entry: AzureFoundryConfigEntry,
    prompt: str,
    size: str = DEFAULT_IMAGE_SIZE,
) -> dict[str, str]:
    """Generate an image, persist it under /config/www, and return its local URL."""
    prompt = prompt.strip()
    if not prompt:
        raise HomeAssistantError("An image prompt is required")
    if len(prompt) > 32000:
        raise HomeAssistantError("Image prompts must be 32,000 characters or fewer")
    if size not in IMAGE_SIZES:
        raise HomeAssistantError(
            f"Unsupported image size {size!r}; choose one of {', '.join(IMAGE_SIZES)}"
        )

    try:
        response = await entry.runtime_data.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt,
            size=size,
            n=1,
            quality="high",
            output_format="png",
        )
    except openai.RateLimitError as err:
        LOGGER.warning("Azure image generation rate limited: %s", err)
        raise HomeAssistantError("Azure image generation is rate limited or out of quota") from err
    except openai.BadRequestError as err:
        LOGGER.warning("Azure image generation rejected the request: %s", err)
        raise HomeAssistantError("Azure rejected the image generation request") from err
    except openai.OpenAIError as err:
        LOGGER.error("Azure image generation failed: %s", err)
        raise HomeAssistantError("Unable to generate an image with Azure AI Foundry") from err

    if not response.data or not response.data[0].b64_json:
        raise HomeAssistantError("Azure returned no image data")

    try:
        image_bytes = base64.b64decode(response.data[0].b64_json, validate=True)
    except (binascii.Error, ValueError) as err:
        raise HomeAssistantError("Azure returned invalid image data") from err

    filename = _safe_filename()
    image_dir = Path(hass.config.path("www")) / IMAGE_DIRECTORY
    image_path = image_dir / filename

    def _write_image() -> None:
        image_dir.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(image_bytes)

    try:
        await hass.async_add_executor_job(_write_image)
    except OSError as err:
        LOGGER.error("Unable to save generated image: %s", err)
        raise HomeAssistantError("Unable to save the generated image to Home Assistant") from err

    return {
        "image_url": f"/local/{IMAGE_DIRECTORY}/{filename}",
        "path": str(image_path),
        "prompt": prompt,
        "model": IMAGE_MODEL,
        "size": size,
    }


def _service_schema() -> vol.Schema:
    """Build the service schema."""
    return vol.Schema(
        {
            vol.Required(CONF_PROMPT): cv.string,
            vol.Optional(CONF_SIZE, default=DEFAULT_IMAGE_SIZE): vol.In(IMAGE_SIZES),
            vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
        }
    )


async def async_setup_image_service(hass: HomeAssistant) -> None:
    """Register the image generation service once for the integration."""

    async def _handle_generate_image(call: ServiceCall) -> dict[str, str]:
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state.value == "loaded"
        ]
        requested_id = call.data.get(CONF_CONFIG_ENTRY_ID)
        if requested_id:
            entries = [entry for entry in entries if entry.entry_id == requested_id]
        if not entries:
            raise HomeAssistantError(
                "No loaded Azure AI Foundry integration entry matches this request"
            )
        result = await async_generate_image(
            hass,
            entries[0],
            call.data[CONF_PROMPT],
            call.data[CONF_SIZE],
        )
        return result

    if not hass.services.has_service(DOMAIN, SERVICE_GENERATE_IMAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GENERATE_IMAGE,
            _handle_generate_image,
            schema=_service_schema(),
            supports_response=SupportsResponse.ONLY,
        )
