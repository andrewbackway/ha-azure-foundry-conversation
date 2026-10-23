"""Constants for the Azure AI Foundry Conversation integration."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.helpers import llm

DOMAIN = "azure_foundry_conversation"
LOGGER: logging.Logger = logging.getLogger(__package__)

# Default entity/subentry names.
DEFAULT_CONVERSATION_NAME = "Azure Foundry Conversation"
DEFAULT_AI_TASK_NAME = "Azure Foundry AI Task"
DEFAULT_IMAGE_NAME = "Azure Foundry Image Generation"
DEFAULT_STT_NAME = "Azure Speech STT"
DEFAULT_TTS_NAME = "Azure Speech TTS"

# Connection (config entry) keys.
CONF_ENDPOINT = "endpoint"

# Conversation / AI Task (subentry) option keys.
CONF_CHAT_MODEL = "chat_model"
CONF_MAX_TOKENS = "max_tokens"
CONF_REASONING_EFFORT = "reasoning_effort"
CONF_RECOMMENDED = "recommended"
CONF_VERBOSITY = "verbosity"

# Image generation (subentry) option keys.
CONF_IMAGE_MODEL = "image_model"
CONF_IMAGE_SIZE = "image_size"
CONF_IMAGE_WIDTH = "image_width"
CONF_IMAGE_HEIGHT = "image_height"
CONF_IMAGE_QUALITY = "image_quality"
SUBENTRY_IMAGE = "image_generation"
CUSTOM_IMAGE_SIZE = "custom"
RECOMMENDED_IMAGE_MODEL = "gpt-image-1"
RECOMMENDED_IMAGE_SIZE = "1024x1024"
RECOMMENDED_IMAGE_QUALITY = "high"
IMAGE_SIZES: list[str] = ["1024x1024", "1536x1024", "1024x1536"]
IMAGE_SIZE_OPTIONS: list[str] = [*IMAGE_SIZES, CUSTOM_IMAGE_SIZE]
IMAGE_QUALITIES: list[str] = ["low", "medium", "high"]

# Speech-to-text (subentry) option keys.
CONF_STT_ENDPOINT = "stt_endpoint"
CONF_STT_API_KEY = "stt_api_key"
CONF_STT_LANGUAGE = "stt_language"
CONF_STT_FORMAT = "stt_format"
CONF_STT_PROFANITY = "stt_profanity"

# Text-to-speech (subentry) option keys.
CONF_TTS_ENDPOINT = "tts_endpoint"
CONF_TTS_API_KEY = "tts_api_key"
CONF_TTS_VOICE = "tts_voice"
CONF_TTS_LANGUAGE = "tts_language"
CONF_TTS_OUTPUT_FORMAT = "tts_output_format"
CONF_TTS_RATE = "tts_rate"
CONF_TTS_PITCH = "tts_pitch"
CONF_TTS_VOLUME = "tts_volume"
CONF_TTS_STYLE = "tts_style"
CONF_TTS_STYLE_DEGREE = "tts_style_degree"
CONF_TTS_ROLE = "tts_role"
CONF_TTS_STREAMING = "tts_streaming"

# Recommended defaults (conversation / AI Task).
RECOMMENDED_CHAT_MODEL = "gpt-5.6-sol"
RECOMMENDED_MAX_TOKENS = 3000
RECOMMENDED_REASONING_EFFORT = "low"
RECOMMENDED_VERBOSITY = "medium"

# Recommended defaults (speech).
DEFAULT_STT_LANGUAGE = "en-US"
DEFAULT_STT_FORMAT = "simple"
DEFAULT_STT_PROFANITY = "masked"
DEFAULT_TTS_VOICE = "en-US-JennyNeural"
DEFAULT_TTS_OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
DEFAULT_TTS_STREAMING = True

STT_FORMATS: list[str] = ["simple", "detailed"]
STT_PROFANITY_OPTIONS: list[str] = ["masked", "removed", "raw"]
VERBOSITY_OPTIONS: list[str] = ["low", "medium", "high"]

# A representative set of Azure AI Speech locales for STT/TTS entity matching.
SPEECH_LANGUAGES: list[str] = [
    "ar-EG",
    "ca-ES",
    "cs-CZ",
    "da-DK",
    "de-AT",
    "de-CH",
    "de-DE",
    "el-GR",
    "en-AU",
    "en-CA",
    "en-GB",
    "en-IE",
    "en-IN",
    "en-NZ",
    "en-US",
    "es-ES",
    "es-MX",
    "fi-FI",
    "fr-CA",
    "fr-FR",
    "he-IL",
    "hi-IN",
    "hu-HU",
    "id-ID",
    "it-IT",
    "ja-JP",
    "ko-KR",
    "nb-NO",
    "nl-NL",
    "pl-PL",
    "pt-BR",
    "pt-PT",
    "ro-RO",
    "ru-RU",
    "sv-SE",
    "th-TH",
    "tr-TR",
    "uk-UA",
    "vi-VN",
    "zh-CN",
    "zh-HK",
    "zh-TW",
]


# X-Microsoft-OutputFormat -> (file extension, HA-facing content type).
TTS_OUTPUT_FORMATS: dict[str, tuple[str, str]] = {
    "audio-16khz-32kbitrate-mono-mp3": ("mp3", "audio/mpeg"),
    "audio-16khz-64kbitrate-mono-mp3": ("mp3", "audio/mpeg"),
    "audio-16khz-128kbitrate-mono-mp3": ("mp3", "audio/mpeg"),
    "audio-24khz-48kbitrate-mono-mp3": ("mp3", "audio/mpeg"),
    "audio-24khz-96kbitrate-mono-mp3": ("mp3", "audio/mpeg"),
    "audio-24khz-160kbitrate-mono-mp3": ("mp3", "audio/mpeg"),
    "audio-48khz-96kbitrate-mono-mp3": ("mp3", "audio/mpeg"),
    "audio-48khz-192kbitrate-mono-mp3": ("mp3", "audio/mpeg"),
    "ogg-24khz-16bit-mono-opus": ("ogg", "audio/ogg"),
    "ogg-48khz-16bit-mono-opus": ("ogg", "audio/ogg"),
    "webm-24khz-16bit-mono-opus": ("webm", "audio/webm"),
    "riff-16khz-16bit-mono-pcm": ("wav", "audio/wav"),
    "riff-24khz-16bit-mono-pcm": ("wav", "audio/wav"),
    "riff-48khz-16bit-mono-pcm": ("wav", "audio/wav"),
}

# Seed options applied to freshly created subentries.
RECOMMENDED_CONVERSATION_OPTIONS = {
    CONF_RECOMMENDED: True,
    CONF_CHAT_MODEL: RECOMMENDED_CHAT_MODEL,
    CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
    CONF_PROMPT: llm.DEFAULT_INSTRUCTIONS_PROMPT,
}
RECOMMENDED_AI_TASK_OPTIONS = {
    CONF_RECOMMENDED: True,
    CONF_CHAT_MODEL: RECOMMENDED_CHAT_MODEL,
}
RECOMMENDED_IMAGE_OPTIONS = {
    CONF_IMAGE_MODEL: RECOMMENDED_IMAGE_MODEL,
    CONF_IMAGE_SIZE: RECOMMENDED_IMAGE_SIZE,
    CONF_IMAGE_QUALITY: RECOMMENDED_IMAGE_QUALITY,
}
RECOMMENDED_STT_OPTIONS = {
    CONF_STT_LANGUAGE: DEFAULT_STT_LANGUAGE,
    CONF_STT_FORMAT: DEFAULT_STT_FORMAT,
    CONF_STT_PROFANITY: DEFAULT_STT_PROFANITY,
}
RECOMMENDED_TTS_OPTIONS = {
    CONF_TTS_VOICE: DEFAULT_TTS_VOICE,
    CONF_TTS_OUTPUT_FORMAT: DEFAULT_TTS_OUTPUT_FORMAT,
    CONF_TTS_STREAMING: DEFAULT_TTS_STREAMING,
}


def reasoning_effort_options(model: str) -> list[str] | None:
    """Return the reasoning-effort values a model deployment supports.

    Returns None when the model is not a reasoning model.
    """
    name = (model or "").lower()
    if name.startswith(("gpt-6", "gpt-5.6")):
        return ["none", "low", "medium", "high", "xhigh", "max"]
    if name.startswith(("gpt-5.2", "gpt-5.3", "gpt-5.4", "gpt-5.5")):
        return ["none", "low", "medium", "high", "xhigh"]
    if name.startswith("gpt-5.1"):
        return ["none", "low", "medium", "high"]
    if name.startswith("gpt-5"):
        return ["minimal", "low", "medium", "high"]
    if name.startswith("o"):
        return ["low", "medium", "high"]
    return None
