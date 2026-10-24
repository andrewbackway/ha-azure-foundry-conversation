"""Resolve Home Assistant image sources for Azure image editing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components import camera, image, media_source
from homeassistant.exceptions import HomeAssistantError

if TYPE_CHECKING:
    from homeassistant.components.conversation import Attachment
    from homeassistant.core import HomeAssistant

MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_IMAGE_COUNT = 16
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
}


@dataclass(frozen=True, slots=True)
class ImageInput:
    """An image source ready to send to the OpenAI-compatible SDK."""

    data: bytes
    mime_type: str
    extension: str


def _image_input(data: bytes, mime_type: str | None = None) -> ImageInput:
    """Validate image format and size, and normalize its MIME type."""
    if not data:
        raise HomeAssistantError("An image source is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HomeAssistantError("Each image source must be 50 MB or smaller")

    normalized_mime_type = (mime_type or "").lower().split(";", maxsplit=1)[0]
    for supported_type, (signature, extension) in SUPPORTED_IMAGE_TYPES.items():
        if data.startswith(signature):
            if normalized_mime_type and normalized_mime_type not in (
                supported_type,
                "image/jpg" if supported_type == "image/jpeg" else supported_type,
                "application/octet-stream",
            ):
                raise HomeAssistantError("The image MIME type does not match its data")
            return ImageInput(data, supported_type, extension)

    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if normalized_mime_type and normalized_mime_type not in (
            "image/webp",
            "application/octet-stream",
        ):
            raise HomeAssistantError("The image MIME type does not match its data")
        return ImageInput(data, "image/webp", ".webp")

    raise HomeAssistantError("Image sources must be JPEG, PNG, or WebP files")


async def _async_read_path(
    hass: HomeAssistant,
    path: Path,
    mime_type: str | None = None,
    *,
    require_allowed_path: bool,
) -> ImageInput:
    """Read a validated image path without blocking the event loop."""
    try:
        resolved_path = await hass.async_add_executor_job(path.resolve, True)
        if require_allowed_path and not hass.config.is_allowed_path(str(resolved_path)):
            raise HomeAssistantError(
                "Local image paths must be inside an allowed Home Assistant directory"
            )

        def _read_file() -> bytes:
            if not resolved_path.is_file():
                raise HomeAssistantError("The image source is not a regular file")
            if resolved_path.stat().st_size > MAX_IMAGE_BYTES:
                raise HomeAssistantError("Each image source must be 50 MB or smaller")
            return resolved_path.read_bytes()

        data = await hass.async_add_executor_job(_read_file)
    except OSError as err:
        raise HomeAssistantError("Unable to read the image source") from err
    return _image_input(data, mime_type)


async def async_resolve_image(hass: HomeAssistant, source: str) -> ImageInput:
    """Resolve a local path, media-source URI, or camera/image entity."""
    source = source.strip()
    if not source:
        raise HomeAssistantError("An image source is required")

    if source.startswith("media-source://"):
        resolved = await media_source.async_resolve_media(hass, source, None)
        if resolved.path is None:
            raise HomeAssistantError(
                "Only local Home Assistant media sources can be used for image edits"
            )
        return await _async_read_path(
            hass,
            Path(resolved.path),
            resolved.mime_type,
            require_allowed_path=False,
        )

    if "." in source and "/" not in source and "\\" not in source:
        domain, _ = source.split(".", maxsplit=1)
        if domain in (camera.DOMAIN, image.DOMAIN):
            camera_image = await (
                camera.async_get_image(hass, source)
                if domain == camera.DOMAIN
                else image.async_get_image(hass, source)
            )
            return _image_input(camera_image.content, camera_image.content_type)

    path = Path(source)
    if not path.is_absolute() and not source.startswith("/local/"):
        raise HomeAssistantError(
            "Image sources must be absolute local paths, media-source URIs, "
            "or camera/image entity IDs"
        )
    if source.startswith("/local/"):
        path = Path(hass.config.path("www")) / source.removeprefix("/local/")
    return await _async_read_path(hass, path, require_allowed_path=True)


async def async_resolve_attachment(
    hass: HomeAssistant, attachment: Attachment
) -> ImageInput:
    """Read an image attachment already resolved by Home Assistant AI Task."""
    return await _async_read_path(
        hass,
        Path(attachment.path),
        attachment.mime_type,
        require_allowed_path=False,
    )
