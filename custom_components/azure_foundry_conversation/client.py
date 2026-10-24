"""Client helpers for the Azure AI Foundry Conversation integration.

This module centralizes:

- Building the OpenAI-compatible client pointed at the Azure AI Foundry
  ``/openai/v1/`` endpoint (used by the conversation and AI Task entities).
- Building the versioned Azure client used by the deployment-scoped image edit API.
- Small helpers for the Azure AI Speech REST endpoints (used by STT and TTS),
  including detecting the endpoint form and building the correct REST paths.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import openai

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from .const import IMAGE_EDIT_API_VERSION

# REST paths for the Azure AI Speech short-audio / synthesis endpoints.
# Custom-subdomain hosts (``*.cognitiveservices.azure.com``) route via /stt and
# /tts prefixes; regional Speech hosts do not.
_STT_PATH_CUSTOM = "/stt/speech/recognition/conversation/cognitiveservices/v1"
_STT_PATH_REGIONAL = "/speech/recognition/conversation/cognitiveservices/v1"
_TTS_PATH_CUSTOM = "/tts/cognitiveservices/v1"
_TTS_PATH_REGIONAL = "/cognitiveservices/v1"
_TTS_VOICES_PATH_CUSTOM = "/tts/cognitiveservices/voices/list"
_TTS_VOICES_PATH_REGIONAL = "/cognitiveservices/voices/list"


def normalize_foundry_base_url(endpoint: str) -> str:
    """Return the Azure Foundry v1 base URL for the OpenAI SDK.

    Accepts the bare resource endpoint and appends ``/openai/v1/`` if needed.
    """
    endpoint = (endpoint or "").strip()
    trimmed = endpoint.rstrip("/")
    if trimmed.endswith("/openai/v1"):
        return trimmed + "/"
    return trimmed + "/openai/v1/"


def create_client(
    hass: HomeAssistant, api_key: str, endpoint: str
) -> openai.AsyncOpenAI:
    """Create an async OpenAI client bound to the Azure Foundry v1 endpoint."""
    return openai.AsyncOpenAI(
        api_key=api_key,
        base_url=normalize_foundry_base_url(endpoint),
        http_client=get_async_client(hass),
    )


def create_image_edit_client(
    hass: HomeAssistant,
    api_key: str,
    endpoint: str,
    timeout: float,
) -> openai.AsyncAzureOpenAI:
    """Create an Azure deployment-scoped client for image edit requests."""
    azure_endpoint = (endpoint or "").strip().rstrip("/")
    if azure_endpoint.endswith("/openai/v1"):
        azure_endpoint = azure_endpoint.removesuffix("/openai/v1")

    return openai.AsyncAzureOpenAI(
        api_key=api_key,
        azure_endpoint=azure_endpoint,
        api_version=IMAGE_EDIT_API_VERSION,
        http_client=get_async_client(hass),
        timeout=timeout,
        max_retries=0,
    )


def endpoint_host(endpoint: str) -> str:
    """Return only the host of an endpoint, for safe logging (no key)."""
    try:
        return urlsplit(endpoint).netloc or endpoint
    except ValueError:
        return "<invalid-endpoint>"


def _base_speech_url(endpoint: str) -> tuple[str, str, str]:
    """Split a Speech endpoint into (scheme, netloc, lowercased-host).

    Azure AI Foundry hosts (``*.services.ai.azure.com``) don't serve the Speech
    REST endpoints, so they're rewritten to the equivalent Cognitive Services
    host (``*.cognitiveservices.azure.com``) exposed by the same resource. This
    lets STT/TTS fall back to the main Foundry endpoint and still resolve.
    """
    parts = urlsplit((endpoint or "").strip())
    scheme = parts.scheme or "https"
    netloc = parts.netloc or parts.path
    lowered = netloc.lower()
    marker = ".services.ai.azure.com"
    if marker in lowered:
        netloc = netloc[: lowered.index(marker)] + ".cognitiveservices.azure.com"
        lowered = netloc.lower()
    return scheme, netloc, lowered


def build_stt_url(
    endpoint: str, language: str, result_format: str, profanity: str
) -> str:
    """Build the Azure Speech short-audio recognition URL for the endpoint form."""
    scheme, netloc, host = _base_speech_url(endpoint)
    path = _STT_PATH_CUSTOM if "cognitiveservices" in host else _STT_PATH_REGIONAL
    query = f"language={language}&format={result_format}&profanity={profanity}"
    return urlunsplit((scheme, netloc, path, query, ""))


def build_tts_url(endpoint: str) -> str:
    """Build the Azure Speech synthesis URL for the endpoint form."""
    scheme, netloc, host = _base_speech_url(endpoint)
    path = _TTS_PATH_CUSTOM if "cognitiveservices" in host else _TTS_PATH_REGIONAL
    return urlunsplit((scheme, netloc, path, "", ""))


def build_tts_voices_url(endpoint: str) -> str:
    """Build the Azure Speech voices/list URL for the endpoint form."""
    scheme, netloc, host = _base_speech_url(endpoint)
    path = (
        _TTS_VOICES_PATH_CUSTOM
        if "cognitiveservices" in host
        else _TTS_VOICES_PATH_REGIONAL
    )
    return urlunsplit((scheme, netloc, path, "", ""))


async def async_list_voices(
    hass: HomeAssistant, endpoint: str, api_key: str
) -> list[dict[str, Any]]:
    """Fetch the list of available Azure Speech voices for a resource."""
    client = get_async_client(hass)
    response = await client.get(
        build_tts_voices_url(endpoint),
        headers={"Ocp-Apim-Subscription-Key": api_key},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()
