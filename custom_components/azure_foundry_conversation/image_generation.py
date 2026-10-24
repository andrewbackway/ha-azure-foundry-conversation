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

from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .client import create_image_edit_client, endpoint_host
from .const import (
    CONF_ENDPOINT,
    CONF_IMAGE_BACKGROUND,
    CONF_IMAGE_HEIGHT,
    CONF_IMAGE_INPUT_FIDELITY,
    CONF_IMAGE_MODEL,
    CONF_IMAGE_QUALITY,
    CONF_IMAGE_SIZE,
    CONF_IMAGE_WIDTH,
    CUSTOM_IMAGE_SIZE,
    DOMAIN,
    IMAGE_BACKGROUNDS,
    IMAGE_EDIT_QUALITIES,
    IMAGE_INPUT_FIDELITIES,
    IMAGE_SIZES,
    LOGGER,
    RECOMMENDED_IMAGE_MODEL,
    RECOMMENDED_IMAGE_QUALITY,
    RECOMMENDED_IMAGE_SIZE,
    SUBENTRY_IMAGE,
)
from .image_sources import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_COUNT,
    ImageInput,
    async_resolve_image,
)

if TYPE_CHECKING:
    from . import AzureFoundryConfigEntry

SERVICE_GENERATE_IMAGE = "generate_image"
SERVICE_EDIT_IMAGE = "edit_image"
CONF_PROMPT = "prompt"
CONF_IMAGES = "images"
CONF_MASK = "mask"
CONF_SIZE = "size"
CONF_QUALITY = "quality"
CONF_INPUT_FIDELITY = "input_fidelity"
CONF_BACKGROUND = "background"
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
    prompt = _validate_prompt(prompt)
    _validate_image_size(size)
    image_api = entry.runtime_data.with_options(
        timeout=IMAGE_REQUEST_TIMEOUT, max_retries=0
    ).images
    return await _async_call_image_api(
        entry,
        image_api,
        "generate",
        prompt=prompt,
        size=size,
        model=model,
        quality=quality,
        n=1,
    )


def _validate_prompt(prompt: str) -> str:
    """Validate and normalize an image prompt."""
    prompt = prompt.strip()
    if not prompt:
        raise HomeAssistantError("An image prompt is required")
    if len(prompt) > 32000:
        raise HomeAssistantError("Image prompts must be 32,000 characters or fewer")
    return prompt


def _validate_image_size(size: str) -> None:
    """Validate supported fixed or custom image dimensions."""
    if size in IMAGE_SIZES or size == "auto":
        return
    try:
        width, height = (int(dimension) for dimension in size.split("x"))
        validate_custom_image_dimensions(width, height)
    except (ValueError, TypeError) as err:
        raise HomeAssistantError(
            f"Unsupported image size {size!r}; choose one of "
            f"{', '.join(IMAGE_SIZES)} or valid custom dimensions"
        ) from err


async def _async_call_image_api(
    entry: AzureFoundryConfigEntry,
    image_api: Any,
    operation: str,
    *,
    model: str,
    prompt: str,
    size: str,
    **params: Any,
) -> bytes:
    """Call an Azure image endpoint and decode its PNG response."""
    host = endpoint_host(entry.data.get(CONF_ENDPOINT, ""))
    LOGGER.info(
        "Requesting image (operation=%s entry=%s host=%s model=%s size=%s "
        "prompt_chars=%d timeout=%.0fs)",
        operation,
        entry.entry_id,
        host,
        model,
        size,
        len(prompt),
        IMAGE_REQUEST_TIMEOUT,
    )
    started = time.monotonic()
    try:
        response = await getattr(image_api, operation)(
            model=model,
            prompt=prompt,
            size=size,
            output_format="png",
            **params,
        )
    except openai.APITimeoutError as err:
        LOGGER.error(
            "Azure image %s timed out after %.0fs (host=%s model=%s size=%s)",
            operation,
            time.monotonic() - started,
            host,
            model,
            size,
        )
        raise HomeAssistantError(
            f"Azure image {operation} timed out after {IMAGE_REQUEST_TIMEOUT:.0f} "
            "seconds"
        ) from err
    except openai.RateLimitError as err:
        LOGGER.warning("Azure image %s rate limited: %s", operation, err)
        raise HomeAssistantError(
            f"Azure image {operation} is rate limited or out of quota"
        ) from err
    except openai.BadRequestError as err:
        LOGGER.warning("Azure image %s rejected the request: %s", operation, err)
        raise HomeAssistantError(
            f"Azure rejected the image {operation} request: {err}"
        ) from err
    except openai.OpenAIError as err:
        LOGGER.error(
            "Azure image %s failed after %.1fs (host=%s model=%s): %s",
            operation,
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
            f"Unable to {operation} an image with Azure AI Foundry"
        ) from err

    LOGGER.info(
        "Azure image %s completed in %.1fs (model=%s size=%s)",
        operation,
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


async def async_request_image_edit(
    hass: HomeAssistant,
    entry: AzureFoundryConfigEntry,
    prompt: str,
    images: list[ImageInput],
    mask: ImageInput | None,
    size: str,
    model: str,
    quality: str,
    input_fidelity: str | None = None,
    background: str | None = None,
) -> bytes:
    """Submit one or more images for editing and return the generated PNG."""
    prompt = _validate_prompt(prompt)
    _validate_image_size(size)
    if not images:
        raise HomeAssistantError("At least one source image is required")
    if len(images) > MAX_IMAGE_COUNT:
        raise HomeAssistantError(f"No more than {MAX_IMAGE_COUNT} images are supported")
    if (
        sum(len(image.data) for image in images)
        + (len(mask.data) if mask is not None else 0)
        > MAX_IMAGE_BYTES
    ):
        raise HomeAssistantError(
            "Combined source images and mask must be 50 MB or smaller"
        )
    if mask is not None and mask.mime_type != "image/png":
        raise HomeAssistantError("The edit mask must be a PNG image")
    if quality not in IMAGE_EDIT_QUALITIES:
        raise HomeAssistantError("Unsupported image quality")
    if input_fidelity is not None and input_fidelity not in IMAGE_INPUT_FIDELITIES:
        raise HomeAssistantError("Unsupported image input_fidelity")
    if background is not None and background not in IMAGE_BACKGROUNDS:
        raise HomeAssistantError("Unsupported image background")

    params: dict[str, Any] = {
        "image": [
            (f"input{index}{image.extension}", image.data, image.mime_type)
            for index, image in enumerate(images)
        ],
        "quality": quality,
        "n": 1,
    }
    if mask is not None:
        params["mask"] = ("mask.png", mask.data, mask.mime_type)
    if input_fidelity is not None:
        params["input_fidelity"] = input_fidelity
    if background is not None:
        params["background"] = background

    image_client = create_image_edit_client(
        hass,
        entry.data[CONF_API_KEY],
        entry.data[CONF_ENDPOINT],
        IMAGE_REQUEST_TIMEOUT,
    )
    return await _async_call_image_api(
        entry,
        image_client.images,
        "edit",
        model=model,
        prompt=prompt,
        size=size,
        **params,
    )


async def async_generate_image(
    hass: HomeAssistant,
    entry: AzureFoundryConfigEntry,
    prompt: str,
    size: str | None = None,
    image_subentry_id: str | None = None,
) -> dict[str, str | int]:
    """Generate an image, persist it under /config/www, and return its local URL."""
    prompt = _validate_prompt(prompt)
    image_options = _image_options(entry, image_subentry_id)
    model = image_options.get(CONF_IMAGE_MODEL, IMAGE_MODEL)
    size = size or configured_image_size(image_options)
    quality = image_options.get(CONF_IMAGE_QUALITY, RECOMMENDED_IMAGE_QUALITY)
    image_bytes = await async_request_image(entry, prompt, size, model, quality)

    return await _async_save_image(hass, image_bytes, prompt, model, size)


async def async_edit_image(
    hass: HomeAssistant,
    entry: AzureFoundryConfigEntry,
    prompt: str,
    image_sources: list[str],
    mask_source: str | None = None,
    size: str | None = None,
    quality: str | None = None,
    input_fidelity: str | None = None,
    background: str | None = None,
    image_subentry_id: str | None = None,
) -> dict[str, str | int]:
    """Edit one or more images, save the result, and return its local URL."""
    prompt = _validate_prompt(prompt)
    if not image_sources:
        raise HomeAssistantError("At least one source image is required")
    if len(image_sources) > MAX_IMAGE_COUNT:
        raise HomeAssistantError(f"No more than {MAX_IMAGE_COUNT} images are supported")

    options = _image_options(entry, image_subentry_id)
    model = options.get(CONF_IMAGE_MODEL, IMAGE_MODEL)
    size = size or configured_image_size(options)
    quality = quality or options.get(CONF_IMAGE_QUALITY, RECOMMENDED_IMAGE_QUALITY)
    input_fidelity = input_fidelity or options.get(CONF_IMAGE_INPUT_FIDELITY)
    background = background or options.get(CONF_IMAGE_BACKGROUND)
    if quality not in IMAGE_EDIT_QUALITIES:
        raise HomeAssistantError("Unsupported image quality")

    images: list[ImageInput] = []
    input_bytes = 0
    for source in image_sources:
        image = await async_resolve_image(hass, source)
        input_bytes += len(image.data)
        if input_bytes > MAX_IMAGE_BYTES:
            raise HomeAssistantError("Combined source images must be 50 MB or smaller")
        images.append(image)
    mask = (
        await async_resolve_image(hass, mask_source)
        if mask_source is not None
        else None
    )
    if mask is not None and input_bytes + len(mask.data) > MAX_IMAGE_BYTES:
        raise HomeAssistantError(
            "Combined source images and mask must be 50 MB or smaller"
        )
    image_bytes = await async_request_image_edit(
        hass,
        entry,
        prompt,
        images,
        mask,
        size,
        model,
        quality,
        input_fidelity,
        background,
    )
    return await _async_save_image(
        hass, image_bytes, prompt, model, size, source_count=len(images)
    )


async def _async_save_image(
    hass: HomeAssistant,
    image_bytes: bytes,
    prompt: str,
    model: str,
    size: str,
    **metadata: str | int,
) -> dict[str, str | int]:
    """Save an image under /config/www and return its local URL and metadata."""
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
        **metadata,
    }


def _image_options(
    entry: AzureFoundryConfigEntry, image_subentry_id: str | None
) -> Mapping[str, Any]:
    """Return the selected image subentry options."""
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
    return image_subentry.data if image_subentry else {}


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
    """Build the image generation service schema."""
    return vol.Schema(
        {
            vol.Required(CONF_PROMPT): cv.string,
            vol.Optional(CONF_SIZE): vol.In(IMAGE_SIZES),
            vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
            vol.Optional(CONF_IMAGE_SUBENTRY_ID): cv.string,
        }
    )


def _edit_service_schema() -> vol.Schema:
    """Build the image editing service schema."""
    return vol.Schema(
        {
            vol.Required(CONF_PROMPT): cv.string,
            vol.Required(CONF_IMAGES): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(CONF_MASK): cv.string,
            vol.Optional(CONF_SIZE): cv.string,
            vol.Optional(CONF_QUALITY): vol.In(IMAGE_EDIT_QUALITIES),
            vol.Optional(CONF_INPUT_FIDELITY): vol.In(IMAGE_INPUT_FIDELITIES),
            vol.Optional(CONF_BACKGROUND): vol.In(IMAGE_BACKGROUNDS),
            vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
            vol.Optional(CONF_IMAGE_SUBENTRY_ID): cv.string,
        }
    )


async def async_setup_image_service(hass: HomeAssistant) -> None:
    """Register the image generation service once for the integration."""

    async def _handle_generate_image(call: ServiceCall) -> dict[str, str | int]:
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

    async def _handle_edit_image(call: ServiceCall) -> dict[str, str | int]:
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
        return await async_edit_image(
            hass,
            entry,
            call.data[CONF_PROMPT],
            call.data[CONF_IMAGES],
            call.data.get(CONF_MASK),
            call.data.get(CONF_SIZE),
            call.data.get(CONF_QUALITY),
            call.data.get(CONF_INPUT_FIDELITY),
            call.data.get(CONF_BACKGROUND),
            image_subentry_id,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_GENERATE_IMAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GENERATE_IMAGE,
            _handle_generate_image,
            schema=_service_schema(),
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_EDIT_IMAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_EDIT_IMAGE,
            _handle_edit_image,
            schema=_edit_service_schema(),
            supports_response=SupportsResponse.ONLY,
        )
