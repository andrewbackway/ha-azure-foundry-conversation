# TTS Streaming Plan — Options 1 & 2

> Status: **DRAFT — awaiting approval. No code until approved.**
> Scope: reduce TTS latency by (1) streaming audio output as it arrives and
> (2) streaming text input so synthesis starts before the LLM finishes.
> File touched primarily: `custom_components/azure_foundry_conversation/tts.py`
> (plus a small helper in `client.py`).

---

## 1. Goal

Cut *time-to-first-audio* in the voice pipeline.

- **Option 1 — Output streaming:** stream the Azure Speech HTTP response body to
  the media player chunk-by-chunk instead of buffering the whole file.
- **Option 2 — Input streaming:** consume Home Assistant's text generator
  sentence-by-sentence and synthesize each sentence as it becomes available, so
  audio starts while the LLM is still generating tokens.

Both are additive; Option 2 builds on the plumbing introduced by Option 1.

---

## 2. Current behavior (baseline)

`AzureFoundryTTSEntity.async_get_tts_audio()` in
[tts.py](../custom_components/azure_foundry_conversation/tts.py) builds one SSML
document from the **full** message, does a single blocking
`client.post(...)`, and returns `response.content` (the entire audio blob) only
after synthesis + download complete. Nothing plays until then.

The base class default `async_stream_tts_audio` simply joins every text chunk
and calls our `async_get_tts_audio`, so today we get **zero** streaming benefit.

---

## 3. Home Assistant streaming API (verified against HA `dev`)

From `homeassistant/components/tts/entity.py`:

```python
@dataclass
class TTSAudioRequest:
    language: str
    options: dict[str, Any]
    message_gen: AsyncGenerator[str]


@dataclass
class TTSAudioResponse:
    extension: str
    data_gen: AsyncGenerator[bytes]


async def async_stream_tts_audio(
    self, request: TTSAudioRequest
) -> TTSAudioResponse: ...
```

Key facts:
- Overriding `async_stream_tts_audio` automatically flips
  `async_supports_streaming_input()` to `True` (HA detects the override).
- We return a `TTSAudioResponse` whose `data_gen` is an async generator of
  audio `bytes`. HA pumps those chunks to the player as they yield.
- `request.message_gen` yields text as the LLM produces it (used by Option 2).

---

## 4. Option 1 — Output streaming (HTTP chunked)

### Approach
Override `async_stream_tts_audio`. Collect the full text from `message_gen`
(join, same as today), build the SSML once, then open an httpx **streaming**
request and forward body chunks.

### Sketch
```python
async def async_stream_tts_audio(self, request):
    message = "".join([chunk async for chunk in request.message_gen])
    ssml, headers, url, extension = self._prepare_synthesis(
        message, request.language, request.options
    )

    async def data_gen():
        client = get_async_client(self.hass)
        async with client.stream(
            "POST",
            url,
            headers=headers,
            content=ssml.encode("utf-8"),
            timeout=30.0,
        ) as response:
            if response.status_code != 200:
                await response.aread()
                _log_and_raise(response)
            async for chunk in response.aiter_bytes():
                yield chunk

    return tts.TTSAudioResponse(extension, data_gen())
```

### Refactor
Extract the shared SSML/headers/URL/format prep from the current
`async_get_tts_audio` into a private `_prepare_synthesis()` helper so both the
legacy path and the streaming path use identical logic. Keep
`async_get_tts_audio` working (backwards compatibility + non-streaming callers).

### Notes
- Status/error must be checked *before* yielding audio; on error read the body
  (`await response.aread()`), log status + snippet, raise `HomeAssistantError`.
- Once bytes have been yielded we can no longer cleanly signal an error — accept
  that a mid-stream network failure truncates audio (log it).
- `get_async_client(self.hass)` (shared pooled httpx client) already gives
  connection reuse/keep-alive.

---

## 5. Option 2 — Input streaming (sentence chunking)

### Approach
Instead of joining the whole message, buffer `message_gen` until a sentence
boundary, synthesize that sentence, stream its audio, then continue. Requests
run **sequentially** so audio ordering is preserved; the next sentence can be
synthesized while the previous one is still playing.

### Sentence chunking
- Accumulate text until a boundary: `.`, `!`, `?`, newline, or a max-length
  guard (e.g. ~200 chars) to bound latency on run-on text.
- Guard against splitting mid-number/abbreviation minimally (keep it simple;
  perfect segmentation is out of scope).
- Flush any remainder when `message_gen` ends.

### Sketch
```python
async def data_gen():
    async for sentence in _sentence_chunks(request.message_gen):
        ssml, headers, url, _ = self._prepare_synthesis(
            sentence, request.language, request.options
        )
        async with client.stream("POST", url, ...) as response:
            ...  # yield response.aiter_bytes()
```

### Format constraint (important)
Option 2 concatenates the output of **multiple independent synthesis calls**
into one stream. That only plays cleanly for frame-based/streamable codecs:
- **Safe:** `audio-*-mp3` (default), `ogg/webm opus`.
- **Not safe:** `riff-*-pcm` (WAV) — each response carries its own RIFF header,
  so concatenation produces one valid header followed by garbage.

Mitigation: when input-streaming is active and the configured output format is a
`riff-*` WAV, either (a) fall back to a single-shot request (Option 1 behavior)
for that call, or (b) log a warning and coerce to mp3. Decide during review —
**(a) is safer.**

---

## 6. Configuration

Add an opt-in toggle so behavior is predictable and reversible:

- `CONF_TTS_STREAMING` (bool, default `True`) — master switch. When off, keep
  today's single-shot `async_get_tts_audio` path.
- Option 2 activates only when streaming is on **and** the format is
  concatenation-safe (see §5); otherwise it degrades to Option 1.

Per repo convention (repo memory), any new `CONF_` key requires updating:
1. the `RECOMMENDED_TTS_OPTIONS` seed dict in `const.py`,
2. the TTS subentry form schema in `config_flow.py`,
3. runtime read via `.get()` with a default.
Also add the label/description to `strings.json` and `translations/en.json`.

---

## 7. Error handling & logging

- Reuse the existing error mapping: log status + short body snippet, raise
  `HomeAssistantError("Azure Speech TTS request failed")`.
- Check status before the first audio chunk yields (per §4).
- Log at debug: chosen path (single-shot / output-stream / input-stream),
  sentence count, and total bytes streamed.

---

## 8. Testing (`tests/test_tts.py`)

Per repo memory: **do not** install HA/test deps here; validate via `get_errors`
and add tests for CI to run elsewhere.

- Mock httpx streaming (`aiter_bytes`) → assert `data_gen` yields chunks in order
  and `extension` is correct.
- Non-200 streaming response → asserts `HomeAssistantError` before any yield.
- Sentence chunker unit tests: boundaries, max-length guard, trailing flush,
  empty input.
- Format guard: `riff-*` format with input-streaming falls back to single-shot.
- Streaming toggle off → falls back to `async_get_tts_audio`.

---

## 9. Phased rollout

1. **Phase A (Option 1):** add `_prepare_synthesis()` refactor +
   `async_stream_tts_audio` output streaming + `CONF_TTS_STREAMING` toggle.
   Ship and validate latency improvement.
2. **Phase B (Option 2):** add sentence chunker + per-sentence synthesis + format
   safety guard. Gate behind the same toggle.

---

## 10. Open questions / risks

- **mp3 concatenation seams:** independent per-sentence mp3 payloads may have a
  tiny silence/artifact at joins. Usually acceptable for assistant speech;
  confirm on a real device during review.
- **First-sentence latency vs. quality:** very short first sentences start audio
  sooner but can sound choppy; the max-length guard balances this.
- **WAV/PCM users:** must fall back (§5). Confirm choice (a) vs (b).
- **HA version floor:** `async_stream_tts_audio` must exist in the minimum
  supported HA release; confirm `manifest.json` / `hacs.json` target.
