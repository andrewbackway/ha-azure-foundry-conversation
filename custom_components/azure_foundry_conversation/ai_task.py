"""AI Task platform for the Azure AI Foundry Conversation integration."""

from __future__ import annotations

import json

from homeassistant.components import ai_task, conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AzureFoundryConfigEntry
from .const import (
    CONF_IMAGE_BACKGROUND,
    CONF_IMAGE_INPUT_FIDELITY,
    CONF_IMAGE_MODEL,
    CONF_IMAGE_QUALITY,
    LOGGER,
    RECOMMENDED_IMAGE_MODEL,
    RECOMMENDED_IMAGE_QUALITY,
    SUBENTRY_IMAGE,
)
from .entity import AzureFoundryBaseLLMEntity
from .image_generation import (
    async_request_image,
    async_request_image_edit,
    configured_image_size,
)
from .image_sources import MAX_IMAGE_BYTES, MAX_IMAGE_COUNT, async_resolve_attachment


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AzureFoundryConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Publish an AI Task entity per ai_task_data and image subentry."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type == "ai_task_data":
            entity: ai_task.AITaskEntity = AzureFoundryAITaskEntity(
                config_entry, subentry
            )
        elif subentry.subentry_type == SUBENTRY_IMAGE:
            entity = AzureFoundryImageEntity(config_entry, subentry)
        else:
            continue
        async_add_entities([entity], config_subentry_id=subentry.subentry_id)


class AzureFoundryImageEntity(
    ai_task.AITaskEntity,
    AzureFoundryBaseLLMEntity,
):
    """Azure AI Foundry image generation entity."""

    _attr_supported_features = (
        ai_task.AITaskEntityFeature.GENERATE_IMAGE
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
    )

    def __init__(self, entry: AzureFoundryConfigEntry, subentry) -> None:
        """Initialize the entity."""
        super().__init__(entry, subentry)
        self._attr_device_info["model"] = subentry.data.get(
            CONF_IMAGE_MODEL, RECOMMENDED_IMAGE_MODEL
        )

    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenImageTaskResult:
        """Handle a generate-image task."""
        options = self.subentry.data
        model = options.get(CONF_IMAGE_MODEL, RECOMMENDED_IMAGE_MODEL)
        size = configured_image_size(options)
        quality = options.get(CONF_IMAGE_QUALITY, RECOMMENDED_IMAGE_QUALITY)
        if task.attachments:
            if len(task.attachments) > MAX_IMAGE_COUNT:
                raise HomeAssistantError(
                    f"No more than {MAX_IMAGE_COUNT} images are supported"
                )
            images = []
            input_bytes = 0
            for attachment in task.attachments:
                image = await async_resolve_attachment(self.hass, attachment)
                input_bytes += len(image.data)
                if input_bytes > MAX_IMAGE_BYTES:
                    raise HomeAssistantError(
                        "Combined source images must be 50 MB or smaller"
                    )
                images.append(image)
            image_bytes = await async_request_image_edit(
                self.hass,
                self.entry,
                task.instructions,
                images,
                None,
                size,
                model,
                quality,
                options.get(CONF_IMAGE_INPUT_FIDELITY),
                options.get(CONF_IMAGE_BACKGROUND),
            )
        else:
            image_bytes = await async_request_image(
                self.entry,
                task.instructions,
                size,
                model,
                quality,
            )
        width, height = (int(part) for part in size.split("x"))
        return ai_task.GenImageTaskResult(
            image_data=image_bytes,
            conversation_id=chat_log.conversation_id,
            mime_type="image/png",
            width=width,
            height=height,
            model=model,
        )


class AzureFoundryAITaskEntity(
    ai_task.AITaskEntity,
    AzureFoundryBaseLLMEntity,
):
    """Azure AI Foundry AI Task entity."""

    _attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_DATA

    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Handle a generate-data task."""
        try:
            await self._async_handle_chat_log(chat_log, task.name, task.structure)
        except HomeAssistantError:
            raise
        except Exception:
            LOGGER.exception(
                "Unexpected error while generating data for AI Task entity %s",
                self.entity_id,
            )
            raise

        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            raise HomeAssistantError(
                "Last content in chat log is not an AssistantContent"
            )

        text = chat_log.content[-1].content or ""
        if not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id,
                data=text,
            )

        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise HomeAssistantError(
                "Error parsing structured data from the model"
            ) from err

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )
