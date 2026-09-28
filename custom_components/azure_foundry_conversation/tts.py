"""Text-to-speech platform (Azure AI Speech) for Azure AI Foundry."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
import struct
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import httpx

from homeassistant.components import tts
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.httpx_client import get_async_client

from . import AzureFoundryConfigEntry
from .client import async_list_voices, build_tts_url
from .const import (
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
    DEFAULT_TTS_OUTPUT_FORMAT,
    DEFAULT_TTS_STREAMING,
    DEFAULT_TTS_VOICE,
    LOGGER,
    SPEECH_LANGUAGES,
    TTS_OUTPUT_FORMATS,
)
from .entity import AzureFoundrySpeechEntity

_DEFAULT_LANGUAGE = "en-US"

# Force a synthesis boundary on long run-on text with no punctuation so the
# first audio chunk isn't held back by an unbounded buffer.
_MAX_CHUNK_CHARS = 200
_SENTENCE_BOUNDARY_CHARS = ".!?\n"

# Streaming input synthesizes each sentence as a separate request. Concatenating
# independent MP3/OGG payloads clips words at the seams (codec priming/padding),
# so streaming mode requests headerless raw PCM and wraps it in one WAV stream,
# which concatenates sample-accurately. Rate is derived from the chosen format.
_RAW_PCM_RATES = {"8khz": 8000, "16khz": 16000, "24khz": 24000, "48khz": 48000}
_DEFAULT_PCM_RATE = 24000

# Silence padding (raw PCM zeros) around streamed segments. Lead silence covers
# the player's start-of-playback ramp so the first word isn't clipped; gap
# silence restores the natural pause Azure trims between per-sentence requests;
# tail silence keeps the last word from being cut when playback stops.
_LEAD_SILENCE_MS = 250
_GAP_SILENCE_MS = 60
_TAIL_SILENCE_MS = 150


def _streaming_pcm_format(output_format: str) -> tuple[str, int]:
    """Return the raw-PCM Azure format + sample rate for streaming synthesis."""
    for token, rate in _RAW_PCM_RATES.items():
        if token in output_format:
            return f"raw-{token}-16bit-mono-pcm", rate
    return f"raw-{_DEFAULT_PCM_RATE // 1000}khz-16bit-mono-pcm", _DEFAULT_PCM_RATE


def _silence(sample_rate: int, milliseconds: int, *, bits: int = 16) -> bytes:
    """Return mono PCM silence of the given duration."""
    return b"\x00" * (sample_rate * milliseconds // 1000 * (bits // 8))


def _wav_header(sample_rate: int, *, bits: int = 16, channels: int = 1) -> bytes:
    """Build a streaming WAV header for 16-bit mono PCM of unknown length."""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    # Length is unknown while streaming; 0xFFFFFFFF tells players to read to EOF.
    streaming_size = 0xFFFFFFFF
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", streaming_size),
            b"WAVE",
            b"fmt ",
            struct.pack(
                "<IHHIIHH",
                16,
                1,
                channels,
                sample_rate,
                byte_rate,
                block_align,
                bits,
            ),
            b"data",
            struct.pack("<I", streaming_size),
        )
    )


def _find_boundary(buffer: str) -> int | None:
    """Return the split index (exclusive) for the first sentence in buffer.

    Splits just after a sentence-ending character; otherwise forces a split
    once the buffer exceeds the max length, preferring the last space.
    """
    for index, char in enumerate(buffer):
        if char in _SENTENCE_BOUNDARY_CHARS:
            return index + 1
    if len(buffer) >= _MAX_CHUNK_CHARS:
        cut = buffer.rfind(" ", 0, _MAX_CHUNK_CHARS)
        return cut + 1 if cut > 0 else _MAX_CHUNK_CHARS
    return None


async def _sentence_chunks(message_gen: AsyncGenerator[str]) -> AsyncGenerator[str]:
    """Yield sentence-sized chunks from an incremental text generator."""
    buffer = ""
    async for text in message_gen:
        buffer += text
        while (split_at := _find_boundary(buffer)) is not None:
            sentence = buffer[:split_at].strip()
            buffer = buffer[split_at:].lstrip()
            if sentence:
                yield sentence
    tail = buffer.strip()
    if tail:
        yield tail


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: AzureFoundryConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Publish a text-to-speech entity per tts subentry."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "tts":
            continue
        async_add_entities(
            [AzureFoundryTTSEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


def _build_ssml(
    text: str,
    language: str,
    voice: str,
    *,
    rate: str | None,
    pitch: str | None,
    volume: str | None,
    style: str | None,
    style_degree: str | None,
    role: str | None,
) -> str:
    """Build an SSML document for the Azure Speech synthesis endpoint."""
    inner = escape(text)

    if style or role:
        attrs = ""
        if style:
            attrs += f" style={quoteattr(style)}"
        if style_degree:
            attrs += f" styledegree={quoteattr(style_degree)}"
        if role:
            attrs += f" role={quoteattr(role)}"
        inner = f"<mstts:express-as{attrs}>{inner}</mstts:express-as>"

    prosody_attrs = ""
    if rate:
        prosody_attrs += f" rate={quoteattr(rate)}"
    if pitch:
        prosody_attrs += f" pitch={quoteattr(pitch)}"
    if volume:
        prosody_attrs += f" volume={quoteattr(volume)}"
    if prosody_attrs:
        inner = f"<prosody{prosody_attrs}>{inner}</prosody>"

    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" '
        f"xml:lang={quoteattr(language)}>"
        f"<voice name={quoteattr(voice)}>{inner}</voice></speak>"
    )


class AzureFoundryTTSEntity(tts.TextToSpeechEntity, AzureFoundrySpeechEntity):
    """Azure AI Speech neural text-to-speech entity."""

    _attr_name = "Text-to-speech"

    # Populated per instance from the Speech voices/list endpoint.
    _voices: dict[str, list[tts.Voice]] = {}

    @property
    def default_language(self) -> str:
        """Return the default language."""
        return self.subentry.data.get(CONF_TTS_LANGUAGE) or _DEFAULT_LANGUAGE

    @property
    def supported_languages(self) -> list[str]:
        """Return the supported languages."""
        return SPEECH_LANGUAGES

    @property
    def supported_options(self) -> list[str]:
        """Return the supported per-call options."""
        return [tts.ATTR_VOICE]

    async def async_added_to_hass(self) -> None:
        """Load the available voices when the entity is added."""
        await super().async_added_to_hass()
        await self._async_load_voices()

    async def _async_load_voices(self) -> None:
        """Fetch and cache the available voices grouped by locale."""
        endpoint, api_key = self._resolve_speech_credentials(
            CONF_TTS_ENDPOINT, CONF_TTS_API_KEY
        )
        if not endpoint or not api_key:
            LOGGER.warning(
                "Azure Speech TTS endpoint or API key not configured; "
                "skipping voice list. Reconfigure the TTS entry to enable it"
            )
            return
        try:
            raw = await async_list_voices(self.hass, endpoint, api_key)
        except httpx.HTTPError as err:
            LOGGER.warning("Could not fetch Azure Speech voices: %s", err)
            return

        voices: dict[str, list[tts.Voice]] = {}
        for item in raw:
            locale = item.get("Locale")
            short_name = item.get("ShortName")
            if not locale or not short_name:
                continue
            display = item.get("DisplayName") or short_name
            voices.setdefault(locale, []).append(tts.Voice(short_name, display))
        self._voices = voices
        LOGGER.debug("Loaded Azure Speech voices for %d locales", len(voices))

    @callback
    def async_get_supported_voices(self, language: str) -> list[tts.Voice] | None:
        """Return the voices available for a locale, if known."""
        return self._voices.get(language)

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> tts.TtsAudioType:
        """Synthesize speech via the Azure Speech REST API (single request)."""
        build_ssml, headers, url, extension = self._resolve_request(language, options)
        client = get_async_client(self.hass)
        try:
            response = await client.post(
                url,
                headers=headers,
                content=build_ssml(message).encode("utf-8"),
                timeout=30.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            LOGGER.error(
                "Azure Speech TTS failed (status=%s): %s",
                err.response.status_code,
                err,
            )
            raise HomeAssistantError("Azure Speech TTS request failed") from err
        except httpx.HTTPError as err:
            LOGGER.error("Azure Speech TTS connection error: %s", err)
            raise HomeAssistantError("Azure Speech TTS request failed") from err

        return extension, response.content

    async def async_stream_tts_audio(
        self, request: tts.TTSAudioRequest
    ) -> tts.TTSAudioResponse:
        """Stream synthesized audio as the text is generated.

        Streaming mode requests headerless raw PCM and wraps all sentences in a
        single WAV stream so per-sentence segments concatenate without clipping.
        """
        streaming = self.subentry.data.get(CONF_TTS_STREAMING, DEFAULT_TTS_STREAMING)

        if not streaming:
            # Buffer the whole message and synthesize in one non-streaming call.
            message = "".join([chunk async for chunk in request.message_gen])
            extension, audio = await self.async_get_tts_audio(
                message, request.language, request.options
            )

            async def _single_blob() -> AsyncGenerator[bytes]:
                yield audio

            return tts.TTSAudioResponse(extension, _single_blob())

        pcm_format, sample_rate = _streaming_pcm_format(
            self.subentry.data.get(CONF_TTS_OUTPUT_FORMAT, DEFAULT_TTS_OUTPUT_FORMAT)
        )
        build_ssml, headers, url, _extension = self._resolve_request(
            request.language, request.options, output_format=pcm_format
        )

        async def data_gen() -> AsyncGenerator[bytes]:
            yield _wav_header(sample_rate)
            yield _silence(sample_rate, _LEAD_SILENCE_MS)
            first = True
            async for sentence in _sentence_chunks(request.message_gen):
                if not first:
                    yield _silence(sample_rate, _GAP_SILENCE_MS)
                first = False
                async for chunk in self._post_stream(
                    build_ssml(sentence), headers, url
                ):
                    yield chunk
            yield _silence(sample_rate, _TAIL_SILENCE_MS)

        return tts.TTSAudioResponse("wav", data_gen())

    def _resolve_request(
        self,
        language: str,
        options: dict[str, Any],
        output_format: str | None = None,
    ) -> tuple[Callable[[str], str], dict[str, str], str, str]:
        """Resolve the reusable synthesis context for a request.

        Returns an SSML builder plus the shared headers, URL, and audio file
        extension. ``output_format`` overrides the configured format (used to
        request raw PCM for seamless streaming).
        """
        data = self.subentry.data
        endpoint, api_key = self._resolve_speech_credentials(
            CONF_TTS_ENDPOINT, CONF_TTS_API_KEY
        )
        if not endpoint or not api_key:
            raise HomeAssistantError(
                "Azure Speech TTS is not configured; "
                "reconfigure the TTS entry with an endpoint and API key"
            )
        voice = options.get(tts.ATTR_VOICE) or data.get(
            CONF_TTS_VOICE, DEFAULT_TTS_VOICE
        )
        resolved_format = output_format or data.get(
            CONF_TTS_OUTPUT_FORMAT, DEFAULT_TTS_OUTPUT_FORMAT
        )
        extension, _content_type = TTS_OUTPUT_FORMATS.get(
            resolved_format, ("mp3", "audio/mpeg")
        )
        resolved_language = language or data.get(CONF_TTS_LANGUAGE) or _DEFAULT_LANGUAGE

        def build_ssml(text: str) -> str:
            return _build_ssml(
                text,
                resolved_language,
                voice,
                rate=data.get(CONF_TTS_RATE),
                pitch=data.get(CONF_TTS_PITCH),
                volume=data.get(CONF_TTS_VOLUME),
                style=data.get(CONF_TTS_STYLE),
                style_degree=data.get(CONF_TTS_STYLE_DEGREE),
                role=data.get(CONF_TTS_ROLE),
            )

        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": resolved_format,
            "User-Agent": "home-assistant-azure-foundry",
        }
        return build_ssml, headers, build_tts_url(endpoint), extension

    async def _post_stream(
        self, ssml: str, headers: dict[str, str], url: str
    ) -> AsyncGenerator[bytes]:
        """Stream the audio body of one Azure Speech synthesis request."""
        client = get_async_client(self.hass)
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                content=ssml.encode("utf-8"),
                timeout=30.0,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    LOGGER.error(
                        "Azure Speech TTS failed (status=%s): %s",
                        response.status_code,
                        body[:200],
                    )
                    raise HomeAssistantError("Azure Speech TTS request failed")
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.HTTPError as err:
            LOGGER.error("Azure Speech TTS connection error: %s", err)
            raise HomeAssistantError("Azure Speech TTS request failed") from err
