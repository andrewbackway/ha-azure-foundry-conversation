"""Config flow for the Azure AI Foundry Conversation integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import openai
import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    CONF_API_KEY,
    CONF_LLM_HASS_API,
    CONF_NAME,
    CONF_PROMPT,
)
from homeassistant.core import callback
from homeassistant.helpers import llm
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import create_client, endpoint_host
from .const import (
    CONF_CHAT_MODEL,
    CONF_ENDPOINT,
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_RECOMMENDED,
    CONF_STT_API_KEY,
    CONF_STT_ENDPOINT,
    CONF_STT_FORMAT,
    CONF_STT_LANGUAGE,
    CONF_STT_PROFANITY,
    CONF_TTS_API_KEY,
    CONF_TTS_ENDPOINT,
    CONF_TTS_LANGUAGE,
    CONF_TTS_OUTPUT_FORMAT,
    CONF_TTS_PITCH,
    CONF_TTS_RATE,
    CONF_TTS_ROLE,
    CONF_TTS_STREAMING,
    CONF_TTS_STYLE,
    CONF_TTS_STYLE_DEGREE,
    CONF_TTS_VOICE,
    CONF_TTS_VOLUME,
    CONF_VERBOSITY,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_STT_FORMAT,
    DEFAULT_STT_LANGUAGE,
    DEFAULT_STT_NAME,
    DEFAULT_STT_PROFANITY,
    DEFAULT_TTS_NAME,
    DEFAULT_TTS_OUTPUT_FORMAT,
    DEFAULT_TTS_STREAMING,
    DEFAULT_TTS_VOICE,
    DOMAIN,
    LOGGER,
    RECOMMENDED_AI_TASK_OPTIONS,
    RECOMMENDED_CHAT_MODEL,
    RECOMMENDED_CONVERSATION_OPTIONS,
    RECOMMENDED_MAX_TOKENS,
    RECOMMENDED_REASONING_EFFORT,
    RECOMMENDED_STT_OPTIONS,
    RECOMMENDED_TTS_OPTIONS,
    RECOMMENDED_VERBOSITY,
    STT_FORMATS,
    STT_PROFANITY_OPTIONS,
    TTS_OUTPUT_FORMATS,
    VERBOSITY_OPTIONS,
    reasoning_effort_options,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENDPOINT): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


async def validate_input(hass, data: dict[str, Any]) -> None:
    """Validate the connection to the Azure Foundry endpoint."""
    client = create_client(hass, data[CONF_API_KEY], data[CONF_ENDPOINT])
    await client.with_options(timeout=10.0).models.list()


class AzureFoundryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Azure AI Foundry Conversation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial connection step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._async_abort_entries_match({CONF_ENDPOINT: user_input[CONF_ENDPOINT]})
            try:
                await validate_input(self.hass, user_input)
            except openai.AuthenticationError:
                errors["base"] = "invalid_auth"
            except openai.APIConnectionError:
                errors["base"] = "cannot_connect"
            except openai.OpenAIError as err:
                LOGGER.error(
                    "Validation error for endpoint %s: %s",
                    endpoint_host(user_input[CONF_ENDPOINT]),
                    err,
                )
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected exception during config validation")
                errors["base"] = "unknown"
            else:
                if self.source == SOURCE_REAUTH:
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data_updates=user_input
                    )
                return self.async_create_entry(
                    title="Azure AI Foundry",
                    data=user_input,
                    subentries=[
                        {
                            "subentry_type": "conversation",
                            "data": RECOMMENDED_CONVERSATION_OPTIONS,
                            "title": DEFAULT_CONVERSATION_NAME,
                            "unique_id": None,
                        },
                        {
                            "subentry_type": "ai_task_data",
                            "data": RECOMMENDED_AI_TASK_OPTIONS,
                            "title": DEFAULT_AI_TASK_NAME,
                            "unique_id": None,
                        },
                        {
                            "subentry_type": "stt",
                            "data": RECOMMENDED_STT_OPTIONS,
                            "title": DEFAULT_STT_NAME,
                            "unique_id": None,
                        },
                        {
                            "subentry_type": "tts",
                            "data": RECOMMENDED_TTS_OPTIONS,
                            "title": DEFAULT_TTS_NAME,
                            "unique_id": None,
                        },
                    ],
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input or {}
            ),
            errors=errors,
            description_placeholders={
                "endpoint_example": "https://your-resource.services.ai.azure.com/"
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow editing the endpoint and API key of an existing entry."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
            except openai.AuthenticationError:
                errors["base"] = "invalid_auth"
            except openai.APIConnectionError:
                errors["base"] = "cannot_connect"
            except openai.OpenAIError as err:
                LOGGER.error(
                    "Validation error for endpoint %s: %s",
                    endpoint_host(user_input[CONF_ENDPOINT]),
                    err,
                )
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected exception during config validation")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reconfigure_entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input or dict(reconfigure_entry.data)
            ),
            errors=errors,
            description_placeholders={
                "endpoint_example": "https://your-resource.services.ai.azure.com/"
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth on an authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth by re-collecting the connection details."""
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm", data_schema=STEP_USER_DATA_SCHEMA
            )
        return await self.async_step_user(user_input)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this integration supports."""
        return {
            "conversation": LLMSubentryFlowHandler,
            "ai_task_data": LLMSubentryFlowHandler,
            "stt": STTSubentryFlowHandler,
            "tts": TTSSubentryFlowHandler,
        }


class LLMSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for conversation and AI Task subentries."""

    options: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    @property
    def _is_conversation(self) -> bool:
        return self._subentry_type == "conversation"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a new subentry."""
        if self._subentry_type == "ai_task_data":
            self.options = dict(RECOMMENDED_AI_TASK_OPTIONS)
        else:
            self.options = dict(RECOMMENDED_CONVERSATION_OPTIONS)
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing subentry."""
        self.options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect the primary options."""
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        options = self.options
        hass_apis = [
            SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]

        schema: dict[Any, Any] = {}
        if self._is_new:
            default_name = (
                DEFAULT_CONVERSATION_NAME
                if self._is_conversation
                else DEFAULT_AI_TASK_NAME
            )
            schema[vol.Required(CONF_NAME, default=default_name)] = str

        if self._is_conversation:
            schema[
                vol.Optional(
                    CONF_PROMPT,
                    description={
                        "suggested_value": options.get(
                            CONF_PROMPT, llm.DEFAULT_INSTRUCTIONS_PROMPT
                        )
                    },
                )
            ] = TemplateSelector()
            schema[vol.Optional(CONF_LLM_HASS_API)] = SelectSelector(
                SelectSelectorConfig(options=hass_apis, multiple=True)
            )

        schema[
            vol.Required(
                CONF_CHAT_MODEL,
                default=options.get(CONF_CHAT_MODEL, RECOMMENDED_CHAT_MODEL),
            )
        ] = str
        schema[
            vol.Required(CONF_RECOMMENDED, default=options.get(CONF_RECOMMENDED, False))
        ] = bool

        if user_input is not None:
            if not user_input.get(CONF_LLM_HASS_API):
                user_input.pop(CONF_LLM_HASS_API, None)
            options.update(user_input)
            if user_input.get(CONF_RECOMMENDED):
                return self._async_finish()
            return await self.async_step_advanced()

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), options
            ),
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Collect advanced/model-specific options."""
        options = self.options
        model = options.get(CONF_CHAT_MODEL, RECOMMENDED_CHAT_MODEL)

        schema: dict[Any, Any] = {
            vol.Optional(
                CONF_MAX_TOKENS,
                default=options.get(CONF_MAX_TOKENS, RECOMMENDED_MAX_TOKENS),
            ): int,
        }

        effort_opts = reasoning_effort_options(model)
        if effort_opts:
            default_effort = (
                RECOMMENDED_REASONING_EFFORT
                if RECOMMENDED_REASONING_EFFORT in effort_opts
                else effort_opts[0]
            )
            schema[
                vol.Optional(
                    CONF_REASONING_EFFORT,
                    default=options.get(CONF_REASONING_EFFORT, default_effort),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=effort_opts,
                    translation_key=CONF_REASONING_EFFORT,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        if model.lower().startswith(("gpt-5", "gpt-6")):
            schema[
                vol.Optional(
                    CONF_VERBOSITY,
                    default=options.get(CONF_VERBOSITY, RECOMMENDED_VERBOSITY),
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=VERBOSITY_OPTIONS,
                    translation_key=CONF_VERBOSITY,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )

        if user_input is not None:
            options.update(user_input)
            return self._async_finish()

        return self.async_show_form(
            step_id="advanced",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), options
            ),
        )

    def _async_finish(self) -> SubentryFlowResult:
        options = self.options
        name = options.pop(CONF_NAME, None)
        if self._is_new:
            return self.async_create_entry(title=name or "", data=options)
        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            data=options,
        )


class STTSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for Azure AI Speech STT subentries."""

    options: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self.options = dict(RECOMMENDED_STT_OPTIONS)
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self.options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        options = self.options
        schema: dict[Any, Any] = {}
        if self._is_new:
            schema[vol.Required(CONF_NAME, default=DEFAULT_STT_NAME)] = str
        schema.update(
            {
                vol.Optional(CONF_STT_ENDPOINT): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Optional(CONF_STT_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_STT_LANGUAGE, default=DEFAULT_STT_LANGUAGE): str,
                vol.Optional(
                    CONF_STT_FORMAT, default=DEFAULT_STT_FORMAT
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=STT_FORMATS,
                        translation_key=CONF_STT_FORMAT,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_STT_PROFANITY, default=DEFAULT_STT_PROFANITY
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=STT_PROFANITY_OPTIONS,
                        translation_key=CONF_STT_PROFANITY,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        if user_input is not None:
            options.update(user_input)
            name = options.pop(CONF_NAME, None)
            if self._is_new:
                return self.async_create_entry(title=name or "", data=options)
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=options,
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), options
            ),
        )


class TTSSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for Azure AI Speech TTS subentries."""

    options: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self.options = dict(RECOMMENDED_TTS_OPTIONS)
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self.options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if self._get_entry().state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        options = self.options
        schema: dict[Any, Any] = {}
        if self._is_new:
            schema[vol.Required(CONF_NAME, default=DEFAULT_TTS_NAME)] = str
        schema.update(
            {
                vol.Optional(CONF_TTS_ENDPOINT): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Optional(CONF_TTS_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_TTS_VOICE, default=DEFAULT_TTS_VOICE): str,
                vol.Optional(CONF_TTS_LANGUAGE): str,
                vol.Optional(
                    CONF_TTS_OUTPUT_FORMAT, default=DEFAULT_TTS_OUTPUT_FORMAT
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(TTS_OUTPUT_FORMATS),
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_TTS_STREAMING, default=DEFAULT_TTS_STREAMING): bool,
                vol.Optional(CONF_TTS_RATE): str,
                vol.Optional(CONF_TTS_PITCH): str,
                vol.Optional(CONF_TTS_VOLUME): str,
                vol.Optional(CONF_TTS_STYLE): str,
                vol.Optional(CONF_TTS_STYLE_DEGREE): str,
                vol.Optional(CONF_TTS_ROLE): str,
            }
        )

        if user_input is not None:
            options.update(user_input)
            name = options.pop(CONF_NAME, None)
            if self._is_new:
                return self.async_create_entry(title=name or "", data=options)
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=options,
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), options
            ),
        )
