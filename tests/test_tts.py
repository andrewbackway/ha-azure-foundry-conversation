"""Tests for the text-to-speech entity."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.azure_foundry_conversation.const import (
    CONF_TTS_OUTPUT_FORMAT,
    CONF_TTS_STREAMING,
)
from custom_components.azure_foundry_conversation.tts import (
    AzureFoundryTTSEntity,
    _sentence_chunks,
)
from homeassistant.components import tts
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError


def _tts_subentry(entry: MockConfigEntry):
    return next(s for s in entry.subentries.values() if s.subentry_type == "tts")


async def _agen(items: list[str]) -> AsyncGenerator[str]:
    for item in items:
        yield item


class _FakeStream:
    """Minimal async-context-manager stand-in for httpx client.stream."""

    def __init__(
        self, chunks: list[bytes], status_code: int = 200, body: bytes = b""
    ) -> None:
        self._chunks = chunks
        self.status_code = status_code
        self._body = body

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def aiter_bytes(self) -> AsyncGenerator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aread(self) -> bytes:
        return self._body


async def test_tts_synthesizes_audio(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Synthesis posts SSML and returns the audio bytes + extension."""
    entity = AzureFoundryTTSEntity(init_integration, _tts_subentry(init_integration))
    entity.hass = hass

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.content = b"AUDIO"
    client = MagicMock()
    client.post = AsyncMock(return_value=response)

    with patch(
        "custom_components.azure_foundry_conversation.tts.get_async_client",
        return_value=client,
    ):
        extension, audio = await entity.async_get_tts_audio("hello", "en-US", {})

    assert extension == "mp3"
    assert audio == b"AUDIO"
    assert client.post.await_count == 1


async def test_tts_supported_voices(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Voices are loaded from the API and returned per locale."""
    entity = AzureFoundryTTSEntity(init_integration, _tts_subentry(init_integration))
    entity.hass = hass

    voices_payload = [
        {"Locale": "en-US", "ShortName": "en-US-JennyNeural", "DisplayName": "Jenny"},
        {"Locale": "en-GB", "ShortName": "en-GB-SoniaNeural", "DisplayName": "Sonia"},
    ]
    with patch(
        "custom_components.azure_foundry_conversation.tts.async_list_voices",
        AsyncMock(return_value=voices_payload),
    ):
        await entity._async_load_voices()

    voices = entity.async_get_supported_voices("en-US")
    assert voices is not None
    assert voices[0].voice_id == "en-US-JennyNeural"
    assert entity.async_get_supported_voices("xx-XX") is None


async def test_stream_output_single_sentence(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Streaming emits a WAV header then the response PCM chunks."""
    entity = AzureFoundryTTSEntity(init_integration, _tts_subentry(init_integration))
    entity.hass = hass

    client = MagicMock()
    client.stream = MagicMock(return_value=_FakeStream([b"AU", b"DIO"]))
    request = tts.TTSAudioRequest(
        language="en-US", options={}, message_gen=_agen(["Hello world"])
    )

    with patch(
        "custom_components.azure_foundry_conversation.tts.get_async_client",
        return_value=client,
    ):
        response = await entity.async_stream_tts_audio(request)
        chunks = [chunk async for chunk in response.data_gen]

    assert response.extension == "wav"
    audio = b"".join(chunks)
    assert audio.startswith(b"RIFF")
    assert b"AUDIO" in audio
    assert client.stream.call_count == 1


async def test_stream_input_multiple_sentences(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Each sentence is a separate request, concatenated after one WAV header."""
    entity = AzureFoundryTTSEntity(init_integration, _tts_subentry(init_integration))
    entity.hass = hass

    client = MagicMock()
    client.stream = MagicMock(
        side_effect=[_FakeStream([b"one."]), _FakeStream([b"two."])]
    )
    request = tts.TTSAudioRequest(
        language="en-US",
        options={},
        message_gen=_agen(["First sentence. ", "Second sentence."]),
    )

    with patch(
        "custom_components.azure_foundry_conversation.tts.get_async_client",
        return_value=client,
    ):
        response = await entity.async_stream_tts_audio(request)
        chunks = [chunk async for chunk in response.data_gen]

    audio = b"".join(chunks)
    assert audio.startswith(b"RIFF")
    assert audio.index(b"one.") < audio.index(b"two.")
    assert client.stream.call_count == 2


async def test_stream_requests_raw_pcm(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Streaming requests a headerless raw-PCM format regardless of config."""
    subentry = _tts_subentry(init_integration)
    init_integration.subentries[subentry.subentry_id].data = {
        **subentry.data,
        CONF_TTS_OUTPUT_FORMAT: "audio-24khz-48kbitrate-mono-mp3",
    }
    entity = AzureFoundryTTSEntity(init_integration, _tts_subentry(init_integration))
    entity.hass = hass

    client = MagicMock()
    client.stream = MagicMock(return_value=_FakeStream([b"pcm"]))
    request = tts.TTSAudioRequest(
        language="en-US", options={}, message_gen=_agen(["Hello."])
    )

    with patch(
        "custom_components.azure_foundry_conversation.tts.get_async_client",
        return_value=client,
    ):
        response = await entity.async_stream_tts_audio(request)
        _ = [chunk async for chunk in response.data_gen]

    headers = client.stream.call_args.kwargs["headers"]
    assert headers["X-Microsoft-OutputFormat"] == "raw-24khz-16bit-mono-pcm"


async def test_stream_error_raises(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """A non-200 streaming response raises HomeAssistantError."""
    entity = AzureFoundryTTSEntity(init_integration, _tts_subentry(init_integration))
    entity.hass = hass

    client = MagicMock()
    client.stream = MagicMock(
        return_value=_FakeStream([], status_code=401, body=b"denied")
    )
    request = tts.TTSAudioRequest(
        language="en-US", options={}, message_gen=_agen(["Hello."])
    )

    with (
        patch(
            "custom_components.azure_foundry_conversation.tts.get_async_client",
            return_value=client,
        ),
        pytest.raises(HomeAssistantError),
    ):
        response = await entity.async_stream_tts_audio(request)
        _ = [chunk async for chunk in response.data_gen]


async def test_stream_disabled_falls_back(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """With streaming disabled, the full message is synthesized in one post."""
    subentry = _tts_subentry(init_integration)
    init_integration.subentries[subentry.subentry_id].data = {
        **subentry.data,
        CONF_TTS_STREAMING: False,
    }
    entity = AzureFoundryTTSEntity(init_integration, _tts_subentry(init_integration))
    entity.hass = hass

    post_response = MagicMock()
    post_response.raise_for_status = MagicMock()
    post_response.content = b"BLOB"
    client = MagicMock()
    client.post = AsyncMock(return_value=post_response)
    client.stream = MagicMock()
    request = tts.TTSAudioRequest(
        language="en-US", options={}, message_gen=_agen(["a. ", "b."])
    )

    with patch(
        "custom_components.azure_foundry_conversation.tts.get_async_client",
        return_value=client,
    ):
        response = await entity.async_stream_tts_audio(request)
        chunks = [chunk async for chunk in response.data_gen]

    assert response.extension == "mp3"
    assert b"".join(chunks) == b"BLOB"
    assert client.post.await_count == 1
    assert client.stream.call_count == 0


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (["Hello world. How are you?"], ["Hello world.", "How are you?"]),
        (["No punctuation here"], ["No punctuation here"]),
        (["Split ", "across ", "chunks."], ["Split across chunks."]),
        (["Line one\nLine two"], ["Line one", "Line two"]),
        ([""], []),
    ],
)
async def test_sentence_chunks(parts: list[str], expected: list[str]) -> None:
    """The sentence chunker splits on boundaries and flushes the remainder."""
    result = [chunk async for chunk in _sentence_chunks(_agen(parts))]
    assert result == expected
