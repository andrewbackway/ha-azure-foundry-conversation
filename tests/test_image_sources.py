"""Tests for resolving image-edit input sources."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.azure_foundry_conversation.image_sources import (
    async_resolve_image,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError


async def test_resolve_allowed_local_png(hass: HomeAssistant) -> None:
    """An allowed local PNG is loaded and normalized."""
    path = Path(hass.config.path("www")) / "source.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    hass.config.allowlist_external_dirs.add(str(path.parent))
    path.write_bytes(b"\x89PNG\r\n\x1a\nsource")

    resolved = await async_resolve_image(hass, str(path))

    assert resolved.data == path.read_bytes()
    assert resolved.mime_type == "image/png"
    assert resolved.extension == ".png"


async def test_resolve_rejects_unallowed_path(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Files outside Home Assistant's allowed directories are rejected."""
    path = tmp_path / "source.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nsource")

    with pytest.raises(HomeAssistantError, match="inside an allowed"):
        await async_resolve_image(hass, str(path))


async def test_resolve_camera_entity(hass: HomeAssistant) -> None:
    """Camera entity snapshots are loaded through Home Assistant."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "custom_components.azure_foundry_conversation.image_sources.camera.async_get_image",
            lambda _hass, _entity_id: _async_image(),
        )

        result = await async_resolve_image(hass, "camera.front_door")

    assert result.data == b"\xff\xd8\xffphoto"
    assert result.mime_type == "image/jpeg"


async def _async_image() -> SimpleNamespace:
    return SimpleNamespace(content=b"\xff\xd8\xffphoto", content_type="image/jpeg")


def test_rejects_mismatched_image_mime_type() -> None:
    """A source's declared MIME type must match its actual file signature."""
    from custom_components.azure_foundry_conversation.image_sources import _image_input

    with pytest.raises(HomeAssistantError, match="MIME type"):
        _image_input(b"\x89PNG\r\n\x1a\nimage", "image/jpeg")
