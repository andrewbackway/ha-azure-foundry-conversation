"""Conversation platform for the Azure AI Foundry Conversation integration."""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AzureFoundryConfigEntry
from .const import DOMAIN, LOGGER
from .entity import AzureFoundryBaseLLMEntity
from .image_generation import async_generate_image


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AzureFoundryConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Publish a conversation entity per conversation subentry."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        async_add_entities(
            [AzureFoundryConversationEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


def _is_image_generation_request(text: str) -> bool:
    """Recognise explicit requests to create a new image, rather than discuss images."""
    lowered = text.casefold().strip()
    actions = ("generate", "create", "draw", "make", "paint", "design")
    subjects = ("image", "picture", "illustration", "artwork", "drawing", "wallpaper")
    if not any(action in lowered for action in actions):
        return False
    if not any(subject in lowered for subject in subjects):
        return False
    # Avoid turning clear refusals or requests not to create an image into API calls.
    if any(
        phrase in lowered
        for phrase in ("don't generate", "do not generate", "don't create", "do not create", "don't draw", "do not draw")
    ):
        return False
    return True

async def _text_result_stream(text: str):
    """Yield a short assistant response through the HA chat log."""
    yield {"role": "assistant"}
    yield {"content": text}


class AzureFoundryConversationEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
    AzureFoundryBaseLLMEntity,
):
    """Azure AI Foundry conversation agent."""

    _attr_supports_streaming = True

    def __init__(
        self, entry: AzureFoundryConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the conversation agent."""
        super().__init__(entry, subentry)
        if subentry.data.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return the supported languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register the agent when added to Home Assistant."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the agent when removed from Home Assistant."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Process a user turn and return the assistant result."""
        if _is_image_generation_request(user_input.text):
            try:
                image = await async_generate_image(
                    self.hass, self.entry, user_input.text
                )
                async for _content in chat_log.async_add_delta_content_stream(
                    self.entity_id, _text_result_stream(f"Your image has been generated: [View image]({image['image_url']})")
                ):
                    pass
            except HomeAssistantError as err:
                async for _content in chat_log.async_add_delta_content_stream(
                    self.entity_id,
                    _text_result_stream(f"Image generation failed: {err}"),
                ):
                    pass
            return conversation.async_get_result_from_chat_log(user_input, chat_log)

        options = self.subentry.data
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                options.get(CONF_LLM_HASS_API),
                options.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        try:
            await self._async_handle_chat_log(chat_log)
        except HomeAssistantError:
            raise
        except Exception:
            LOGGER.exception(
                "Unexpected error while handling conversation for entity %s",
                self.entity_id,
            )
            raise
        return conversation.async_get_result_from_chat_log(user_input, chat_log)
