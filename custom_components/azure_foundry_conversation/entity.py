"""Shared base entity that talks to the Azure Foundry Responses API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json
from typing import TYPE_CHECKING, Any

import openai
from voluptuous_openapi import UNSUPPORTED, convert

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import (
    CONF_CHAT_MODEL,
    CONF_ENDPOINT,
    CONF_MAX_TOKENS,
    CONF_REASONING_EFFORT,
    CONF_VERBOSITY,
    DOMAIN,
    LOGGER,
    RECOMMENDED_CHAT_MODEL,
    RECOMMENDED_MAX_TOKENS,
    reasoning_effort_options,
)

if TYPE_CHECKING:
    from . import AzureFoundryConfigEntry

# Maximum number of tool-call round trips before we stop asking the model.
MAX_TOOL_ITERATIONS = 10


def _log_error(
    operation: str, model: str, err: Exception, *, exc_info: bool = False
) -> None:
    """Log an Azure Foundry error with correlation detail (no secrets)."""
    LOGGER.error(
        "Azure Foundry error in %s (model=%s status=%s code=%s req_id=%s): %s",
        operation,
        model,
        getattr(err, "status_code", None),
        getattr(err, "code", None),
        getattr(err, "request_id", None),
        err,
        exc_info=exc_info,
    )


def _is_unsupported(value: Any) -> bool:
    """Return True for a voluptuous_openapi UNSUPPORTED sentinel."""
    return value is UNSUPPORTED or type(value).__name__ == "_Unsupported"


def _strip_unsupported(obj: Any) -> Any:
    """Recursively drop voluptuous_openapi UNSUPPORTED sentinels from a schema.

    These are not JSON serializable; also prune any object ``required`` entries
    that reference properties we had to remove.
    """
    if isinstance(obj, dict):
        cleaned = {
            key: _strip_unsupported(val)
            for key, val in obj.items()
            if not _is_unsupported(val)
        }
        properties = cleaned.get("properties")
        required = cleaned.get("required")
        if isinstance(properties, dict) and isinstance(required, list):
            cleaned["required"] = [name for name in required if name in properties]
        return cleaned
    if isinstance(obj, list):
        return [_strip_unsupported(item) for item in obj if not _is_unsupported(item)]
    return obj


def _as_object_schema(schema: Any) -> dict[str, Any]:
    """Coerce a converted schema into a JSON-serializable object schema."""
    if not isinstance(schema, dict) or _is_unsupported(schema):
        return {"type": "object", "properties": {}}
    return schema


def _format_tool(tool: llm.Tool, custom_serializer: Any) -> dict[str, Any]:
    """Convert a Home Assistant LLM tool into a Responses function tool."""
    raw = convert(tool.parameters, custom_serializer=custom_serializer)
    parameters = _as_object_schema(_strip_unsupported(raw))
    if parameters != raw:
        LOGGER.warning(
            "Tool %r has parameters that could not be represented as JSON "
            "schema; dropped the unsupported parts. Raw conversion: %r",
            tool.name,
            raw,
        )
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description or "",
        "parameters": parameters,
        "strict": False,
    }


def _adjust_schema(schema: dict[str, Any]) -> None:
    """Make a JSON schema compatible with strict structured outputs."""
    if schema.get("type") == "object":
        if "properties" not in schema:
            return
        schema["additionalProperties"] = False
        schema.setdefault("required", [])
        for key, val in schema["properties"].items():
            _adjust_schema(val)
            if key not in schema["required"]:
                schema["required"].append(key)
    elif schema.get("type") == "array":
        if "items" in schema:
            _adjust_schema(schema["items"])


def _format_structure(structure: Any) -> dict[str, Any]:
    """Convert a voluptuous structure to a strict JSON schema."""
    result = _as_object_schema(_strip_unsupported(convert(structure)))
    _adjust_schema(result)
    return result


def _convert_content_to_param(
    content: conversation.Content,
) -> list[dict[str, Any]]:
    """Convert one chat-log content item into Responses input items."""
    if isinstance(content, conversation.ToolResultContent):
        return [
            {
                "type": "function_call_output",
                "call_id": content.tool_call_id,
                "output": json.dumps(content.tool_result, default=str),
            }
        ]

    items: list[dict[str, Any]] = []
    if content.content:
        role = content.role
        if role == "system":
            role = "developer"
        items.append({"type": "message", "role": role, "content": content.content})

    if isinstance(content, conversation.AssistantContent) and content.tool_calls:
        items.extend(
            {
                "type": "function_call",
                "name": tool_call.tool_name,
                "arguments": json.dumps(tool_call.tool_args),
                "call_id": tool_call.id,
            }
            for tool_call in content.tool_calls
        )
    return items


async def _transform_stream(
    result: Any,
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Transform an openai Responses event stream into HA content deltas."""
    yield {"role": "assistant"}
    async for event in result:
        etype = getattr(event, "type", None)
        if etype == "response.output_text.delta":
            yield {"content": event.delta}
        elif etype == "response.output_item.done":
            item = event.item
            if getattr(item, "type", None) == "function_call":
                yield {
                    "tool_calls": [
                        llm.ToolInput(
                            id=item.call_id,
                            tool_name=item.name,
                            tool_args=json.loads(item.arguments or "{}"),
                        )
                    ]
                }
        elif etype == "response.incomplete":
            details = getattr(event.response, "incomplete_details", None)
            LOGGER.warning(
                "Azure Foundry response incomplete: %s",
                getattr(details, "reason", "unknown"),
            )
        elif etype == "error":
            raise HomeAssistantError(
                f"Azure Foundry stream error: {getattr(event, 'message', 'unknown')}"
            )


class AzureFoundryBaseLLMEntity(Entity):
    """Base entity for conversation and AI Task using the Responses API."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, entry: AzureFoundryConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the entity."""
        self.entry = entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Azure AI Foundry",
            model=subentry.data.get(CONF_CHAT_MODEL, "Azure AI Foundry"),
            entry_type=DeviceEntryType.SERVICE,
        )

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        structure_name: str | None = None,
        structure: Any | None = None,
    ) -> None:
        """Drive the model + tool-call loop, streaming into the chat log."""
        options = self.subentry.data
        client = self.entry.runtime_data
        model: str = options.get(CONF_CHAT_MODEL, RECOMMENDED_CHAT_MODEL)

        model_args: dict[str, Any] = {
            "model": model,
            "store": False,
            "stream": True,
            "max_output_tokens": options.get(CONF_MAX_TOKENS, RECOMMENDED_MAX_TOKENS),
        }

        tools: list[dict[str, Any]] | None = None
        if chat_log.llm_api:
            tools = [
                _format_tool(tool, chat_log.llm_api.custom_serializer)
                for tool in chat_log.llm_api.tools
            ]
        if tools:
            model_args["tools"] = tools

        effort = options.get(CONF_REASONING_EFFORT)
        if effort and reasoning_effort_options(model):
            model_args["reasoning"] = {"effort": effort}

        text_args: dict[str, Any] = {}
        verbosity = options.get(CONF_VERBOSITY)
        if verbosity and model.lower().startswith(("gpt-5", "gpt-6")):
            text_args["verbosity"] = verbosity
        if structure is not None and structure_name:
            text_args["format"] = {
                "type": "json_schema",
                "name": slugify(structure_name),
                "schema": _format_structure(structure),
                "strict": True,
            }
        if text_args:
            model_args["text"] = text_args

        LOGGER.debug(
            "Azure Foundry request: model=%s, tools=%d, reasoning=%s",
            model,
            len(tools) if tools else 0,
            effort or "default",
        )

        for _iteration in range(MAX_TOOL_ITERATIONS):
            model_args["input"] = [
                param
                for content in chat_log.content
                for param in _convert_content_to_param(content)
            ]

            try:
                result = await client.responses.create(**model_args)
            except openai.RateLimitError as err:
                _log_error("responses.create", model, err)
                raise HomeAssistantError(
                    "Rate limited or out of quota on Azure Foundry"
                ) from err
            except openai.BadRequestError as err:
                _log_error("responses.create", model, err, exc_info=True)
                if getattr(err, "code", None) == "content_filter":
                    raise HomeAssistantError(
                        "The request was blocked by a content filter"
                    ) from err
                raise HomeAssistantError("Invalid request to Azure Foundry") from err
            except openai.OpenAIError as err:
                _log_error("responses.create", model, err, exc_info=True)
                raise HomeAssistantError("Error talking to Azure Foundry") from err

            async for _content in chat_log.async_add_delta_content_stream(
                self.entity_id, _transform_stream(result)
            ):
                pass

            if not chat_log.unresponded_tool_results:
                break


class AzureFoundrySpeechEntity(Entity):
    """Base entity for the Azure AI Speech STT/TTS platforms."""

    # Subclasses must set a non-None _attr_name; the TTS component rejects a
    # None engine name ("TTS engine name is not set").
    _attr_has_entity_name = True

    def __init__(
        self, entry: AzureFoundryConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the speech entity."""
        self.entry = entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Azure AI Foundry",
            model="Azure AI Speech",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _resolve_speech_credentials(
        self, endpoint_key: str, api_key_key: str
    ) -> tuple[str | None, str | None]:
        """Return the speech endpoint/key, falling back to the main entry."""
        data = self.subentry.data
        endpoint = data.get(endpoint_key) or self.entry.data.get(CONF_ENDPOINT)
        api_key = data.get(api_key_key) or self.entry.data.get(CONF_API_KEY)
        return endpoint, api_key
