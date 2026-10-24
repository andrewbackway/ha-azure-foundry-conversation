"""Tests for the AI Task entity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.azure_foundry_conversation.ai_task import (
    AzureFoundryImageEntity,
)
from custom_components.azure_foundry_conversation.const import SUBENTRY_IMAGE
from custom_components.azure_foundry_conversation.image_sources import ImageInput
from homeassistant.components import ai_task
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import FakeStream


def test_image_task_entity_supports_attachments() -> None:
    """Image Task entity declares its support for edit attachments."""
    entity = AzureFoundryImageEntity(
        SimpleNamespace(),
        SimpleNamespace(
            subentry_id="image-entry",
            title="Image generation",
            data={},
        ),
    )
    assert ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS in entity.supported_features


async def test_generate_image_edits_when_attachments_are_provided(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Image Task attachments are passed to Azure's edit API."""
    entity = AzureFoundryImageEntity(
        init_integration,
        SimpleNamespace(
            subentry_id="image-entry",
            subentry_type=SUBENTRY_IMAGE,
            title="Image generation",
            data={},
        ),
    )
    entity.hass = hass
    attachment = SimpleNamespace(path="/config/source.png", mime_type="image/png")
    image = ImageInput(b"\x89PNG\r\n\x1a\nsource", "image/png", ".png")
    with (
        patch(
            "custom_components.azure_foundry_conversation.ai_task.async_resolve_attachment",
            AsyncMock(return_value=image),
        ),
        patch(
            "custom_components.azure_foundry_conversation.ai_task.async_request_image_edit",
            AsyncMock(return_value=b"edited"),
        ) as request_edit,
        patch(
            "custom_components.azure_foundry_conversation.ai_task.async_request_image",
            AsyncMock(),
        ) as request_generate,
    ):
        result = await entity._async_generate_image(
            SimpleNamespace(instructions="Edit the image", attachments=[attachment]),
            SimpleNamespace(conversation_id="conversation-id"),
        )

    request_edit.assert_awaited_once_with(
        hass,
        init_integration,
        "Edit the image",
        [image],
        None,
        "1024x1024",
        "gpt-image-1",
        "high",
        None,
        None,
    )
    request_generate.assert_not_awaited()
    assert result.image_data == b"edited"


def _ai_task_entity_id(hass: HomeAssistant) -> str:
    ent_reg = er.async_get(hass)
    return next(
        entry.entity_id
        for entry in ent_reg.entities.values()
        if entry.domain == "ai_task"
    )


async def test_generate_data_returns_text(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """generate_data returns the model's text when no structure is requested."""
    client = init_integration.runtime_data
    client.responses.create = AsyncMock(
        return_value=FakeStream(
            [
                SimpleNamespace(type="response.output_text.delta", delta="the answer"),
                SimpleNamespace(type="response.completed"),
            ]
        )
    )

    result = await ai_task.async_generate_data(
        hass,
        task_name="test",
        entity_id=_ai_task_entity_id(hass),
        instructions="give me an answer",
    )

    assert result.data == "the answer"
