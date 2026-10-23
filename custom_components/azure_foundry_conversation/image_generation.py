"""Image generation helpers and Home Assistant service for Azure AI Foundry."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import openai
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .client import endpoint_host
from .const import (
    CONF_ENDPOINT,
    CONF_IMAGE_HEIGHT,
    CONF_IMAGE_MODEL,
    CONF_IMAGE_QUALITY,
    CONF_IMAGE_SIZE,
    CONF_IMAGE_WIDTH,
    CUSTOM_IMAGE_SIZE,
    DOMAIN,
    IMAGE_SIZES,
    LOGGER,
    RECOMMENDED_IMAGE_MODEL,
    RECOMMENDED_IMAGE_QUALITY,
    RECOMMENDED_IMAGE_SIZE,
    SUBENTRY_IMAGE,
)

if TYPE_CHECKING:
    from . import AzureFoundryConfigEntry

SERVICE_GENERATE_IMAGE = "generate_image"
CONF_PROMPT = "prompt"
CONF_SIZE = "size"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_IMAGE_SUBENTRY_ID = "image_subentry_id"
IMAGE_MODEL = RECOMMENDED_IMAGE_MODEL
DEFAULT_IMAGE_SIZE = RECOMMENDED_IMAGE_SIZE
IMAGE_DIRECTORY = "azure_foundry_images"
IMAGE_REQUEST_TIMEOUT = 180.0


def _safe_filename() -> str:
    """Return a unique filename safe for use below the image output directory."""
    return f"{uuid4().hex}.png"


def validate_custom_image_dimensions(width: int, height: int) -> None:
    """Validate custom dimensions accepted by Azure image generation."""
    if (
        width % 16
        or height % 16
        or width > 3840
        or height > 3840
        or width < height / 3
        or height < width / 3
        or not 655360 <= width * height <= 8294400
    ):
        raise ValueError("Unsupported custom image dimensions")


def configured_image_size(options: Mapping[str, Any]) -> str:
    """Return an image size from saved image-generation options."""
    size = options.get(CONF_IMAGE_SIZE, DEFAULT_IMAGE_SIZE)
    if size != CUSTOM_IMAGE_SIZE:
        return str(size)

    try:
        width = int(options[CONF_IMAGE_WIDTH])
        height = int(options[CONF_IMAGE_HEIGHT])
        validate_custom_image_dimensions(width, height)
    except (KeyError, TypeError, ValueError) as err:
        raise HomeAssistantError(
            "Custom image dimensions are missing or unsupported"
        ) from err
    return f"{width}x{height}"


async def async_request_image(
    entry: AzureFoundryConfigEntry,
    prompt: str,
    size: str = DEFAULT_IMAGE_SIZE,
    model: str = IMAGE_MODEL,
    quality: str = RECOMMENDED_IMAGE_QUALITY,
) -> bytes:
    """Request a PNG image from Azure and return its bytes."""
    prompt = prompt.strip()
    if not prompt:
        raise HomeAssistantError("An image prompt is required")
    if len(prompt) > 32000:
        raise HomeAssistantError("Image prompts must be 32,000 characters or fewer")
    if size not in IMAGE_SIZES:
        try:
            width, height = (int(dimension) for dimension in size.split("x"))
            validate_custom_image_dimensions(width, height)
        except (ValueError, TypeError) as err:
            raise HomeAssistantError(
                f"Unsupported image size {size!r}; choose one of "
                f"{', '.join(IMAGE_SIZES)} or valid custom dimensions"
            ) from err

    host = endpoint_host(entry.data.get(CONF_ENDPOINT, ""))
    LOGGER.info(
        "Requesting image (entry=%s host=%s model=%s size=%s quality=%s "
        "prompt_chars=%d timeout=%.0fs)",
        entry.entry_id,
        host,
        model,
        size,
        quality,
        len(prompt),
        IMAGE_REQUEST_TIMEOUT,
    )
    started = time.monotonic()
    try:
        response = await entry.runtime_data.with_options(
            timeout=IMAGE_REQUEST_TIMEOUT, max_retries=0
        ).images.generate(
            model=model,
            prompt=prompt,
            size=size,
            n=1,
            quality=quality,
            output_format="png",
        )
    except openai.APITimeoutError as err:
        LOGGER.error(
            "Azure image generation timed out after %.0fs (host=%s model=%s size=%s)",
            time.monotonic() - started,
            host,
            model,
            size,
        )
        raise HomeAssistantError(
            f"Azure image generation timed out after {IMAGE_REQUEST_TIMEOUT:.0f} "
            "seconds"
        ) from err
    except openai.RateLimitError as err:
        LOGGER.warning("Azure image generation rate limited: %s", err)
        raise HomeAssistantError(
            "Azure image generation is rate limited or out of quota"
        ) from err
    except openai.BadRequestError as err:
        LOGGER.warning("Azure image generation rejected the request: %s", err)
        raise HomeAssistantError(
            f"Azure rejected the image generation request: {err}"
        ) from err
    except openai.OpenAIError as err:
        LOGGER.error(
            "Azure image generation failed after %.1fs (host=%s model=%s): %s",
            time.monotonic() - started,
            host,
            model,
            err,
        )
        if getattr(err, "status_code", None) == 404:
            raise HomeAssistantError(
                f"Azure image deployment {model!r} was not found for this endpoint"
            ) from err
        raise HomeAssistantError(
            "Unable to generate an image with Azure AI Foundry"
        ) from err

    LOGGER.info(
        "Azure image generated in %.1fs (model=%s size=%s)",
        time.monotonic() - started,
        model,
        size,
    )

    if not response.data or not response.data[0].b64_json:
        raise HomeAssistantError("Azure returned no image data")

    try:
        return base64.b64decode(response.data[0].b64_json, validate=True)
    except (binascii.Error, ValueError) as err:
        raise HomeAssistantError("Azure returned invalid image data") from err


async def async_generate_image(
    hass: HomeAssistant,
    entry: AzureFoundryConfigEntry,
    prompt: str,
    size: str | None = None,
    image_subentry_id: str | None = None,
) -> dict[str, str]:
    """Generate an image, persist it under /config/www, and return its local URL."""
    prompt = prompt.strip()
    image_subentries = [
        subentry
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_IMAGE
    ]
    if image_subentry_id is not None:
        image_subentry = next(
            (
                subentry
                for subentry in image_subentries
                if subentry.subentry_id == image_subentry_id
            ),
            None,
        )
        if image_subentry is None:
            raise HomeAssistantError(
                "The requested image generation entry was not found"
            )
    elif len(image_subentries) > 1:
        raise HomeAssistantError(
            "Multiple image generation entries are configured; specify "
            "image_subentry_id"
        )
    else:
        image_subentry = image_subentries[0] if image_subentries else None

    image_options = image_subentry.data if image_subentry else {}
    model = image_options.get(CONF_IMAGE_MODEL, IMAGE_MODEL)
    size = size or configured_image_size(image_options)
    quality = image_options.get(CONF_IMAGE_QUALITY, RECOMMENDED_IMAGE_QUALITY)
    image_bytes = await async_request_image(entry, prompt, size, model, quality)

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
        raise HomeAssistantError(
            "Unable to save the generated image to Home Assistant"
        ) from err

    return {
        "image_url": f"/local/{IMAGE_DIRECTORY}/{filename}",
        "path": str(image_path),
        "prompt": prompt,
        "model": model,
        "size": size,
    }


def _select_config_entry(
    entries: list[AzureFoundryConfigEntry],
    config_entry_id: str | None = None,
    image_subentry_id: str | None = None,
) -> AzureFoundryConfigEntry:
    """Select the connection with the requested image-generation configuration."""
    if config_entry_id is not None:
        entries = [entry for entry in entries if entry.entry_id == config_entry_id]
    if not entries:
        raise HomeAssistantError(
            "No loaded Azure AI Foundry integration entry matches this request"
        )

    if image_subentry_id is not None:
        matching_entries = [
            entry
            for entry in entries
            if any(
                subentry.subentry_type == SUBENTRY_IMAGE
                and subentry.subentry_id == image_subentry_id
                for subentry in entry.subentries.values()
            )
        ]
        if len(matching_entries) != 1:
            raise HomeAssistantError(
                "The requested image generation entry was not found"
            )
        return matching_entries[0]

    configured_entries = [
        entry
        for entry in entries
        if any(
            subentry.subentry_type == SUBENTRY_IMAGE
            for subentry in entry.subentries.values()
        )
    ]
    if len(configured_entries) == 1:
        return configured_entries[0]
    if len(configured_entries) > 1:
        raise HomeAssistantError(
            "Multiple image generation entries are configured; specify "
            "config_entry_id or image_subentry_id"
        )
    if len(entries) == 1:
        return entries[0]
    raise HomeAssistantError(
        "Multiple Azure AI Foundry connections are loaded; specify config_entry_id"
    )


def _service_schema() -> vol.Schema:
    """Build the service schema."""
    return vol.Schema(
        {
            vol.Required(CONF_PROMPT): cv.string,
            vol.Optional(CONF_SIZE): vol.In(IMAGE_SIZES),
            vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
            vol.Optional(CONF_IMAGE_SUBENTRY_ID): cv.string,
        }
    )


async def async_setup_image_service(hass: HomeAssistant) -> None:
    """Register the image generation service once for the integration."""

    async def _handle_generate_image(call: ServiceCall) -> dict[str, str]:
        entries: list[AzureFoundryConfigEntry] = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state.value == "loaded"
        ]
        image_subentry_id = call.data.get(CONF_IMAGE_SUBENTRY_ID)
        entry = _select_config_entry(
            entries,
            call.data.get(CONF_CONFIG_ENTRY_ID),
            image_subentry_id,
        )
        result = await async_generate_image(
            hass,
            entry,
            call.data[CONF_PROMPT],
            call.data.get(CONF_SIZE),
            image_subentry_id,
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
