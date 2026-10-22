# Azure AI Foundry Conversation for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/andrewbackway/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.1%2B-blue.svg)](https://www.home-assistant.io/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Validate](https://github.com/andrewbackway/ha-azure-foundry-conversation/actions/workflows/validate.yaml/badge.svg)](https://github.com/andrewbackway/ha-azure-foundry-conversation/actions/workflows/validate.yaml)
[![Lint](https://github.com/andrewbackway/ha-azure-foundry-conversation/actions/workflows/lint.yaml/badge.svg)](https://github.com/andrewbackway/ha-azure-foundry-conversation/actions/workflows/lint.yaml)
[![Test](https://github.com/andrewbackway/ha-azure-foundry-conversation/actions/workflows/test.yaml/badge.svg)](https://github.com/andrewbackway/ha-azure-foundry-conversation/actions/workflows/test.yaml)

A custom [Home Assistant](https://www.home-assistant.io/) integration that adds an
**Azure AI Foundry**-backed conversation agent (plus AI Task, Speech-to-Text and
Text-to-Speech entities) so you can talk to your home and control it with natural
language.

It targets the **Azure AI Foundry v1 Responses API**, which is required for
reasoning models such as `gpt-5.6-sol` to use Home Assistant's Assist tools. (The
classic Chat Completions API rejects tools + reasoning for these models — see
[Troubleshooting](#troubleshooting).)

## Features

- **Conversation agent** — Responses API, tool calling (device control through
  HA Assist), streaming, and configurable reasoning effort.
- **AI Task** — `ai_task.generate_data` with optional structured (JSON) output.
- **Image generation** — create images with the Azure `gpt-image-1` deployment from conversation requests or the `azure_foundry_conversation.generate_image` service. Generated PNGs are saved locally under `/config/www/azure_foundry_images/` and exposed through Home Assistant's `/local/` URL.
- **Speech-to-Text** — Azure AI Speech short-audio recognition.
- **Text-to-Speech** — Azure AI Speech neural voices via SSML, with low-latency
  streaming that plays audio as it is synthesized (sentence-by-sentence as the
  model generates text).

## Prerequisites

- An **Azure AI Foundry** (AI Services) resource.
- A **chat model deployment** that supports the Responses API with tools
  (e.g. `gpt-5.6-sol`). These models generally require Tier 5/6 quota.
- For image generation: deploy an image model with the deployment name `gpt-image-1` on the same Azure endpoint and ensure your resource has access/quota for it. Image generation is separate from the chat deployment.
- For STT/TTS: **Azure AI Speech** access. A multi-service Azure AI Foundry
  resource exposes a single key that works for both the LLM and Speech — only the
  Speech endpoint URL differs from the OpenAI endpoint.

## Sign up for Azure free credits (optional)

[Create an Azure free account](https://azure.microsoft.com/free/). Eligible new
customers receive $200 in Azure credit for the first 30 days, along with limited
quantities of some free services for 12 months. Microsoft’s offer eligibility
and terms apply.

Use that Azure account to open [Azure AI Foundry](https://ai.azure.com) and create
your resource and model deployment as described below. Credits can help pay for
eligible usage, but do not guarantee access to a model or the quota it requires.
Check the model’s regional availability, quota, and pricing before deploying.
Credits expire after 30 days or when used up; continuing to use Azure afterward
may require upgrading to pay-as-you-go pricing, where usage can incur charges.

## Set up in Azure AI Foundry

1. Open your Foundry resource (or create one at `https://ai.azure.com`) and copy the **endpoint**
   (`https://<your-resource>.services.ai.azure.com/`) and a **key** from
   *Keys and Endpoint*.
2. **Deploy** the chat model and note the **deployment name** — this is the
   "model name" the integration asks for (not the base model id).
3. Confirm the deployment supports the **Responses API** (required for tools +
   reasoning).
4. For STT/TTS, find the **Speech endpoint** URL — either the custom-subdomain
   form (`https://<your-resource>.cognitiveservices.azure.com/`) or a regional
   host (`https://<region>.stt.speech.microsoft.com/` /
   `https://<region>.tts.speech.microsoft.com/`). You can reuse the resource key.

> Never commit keys. The endpoint is per-install.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=andrewbackway&repository=ha-azure-foundry-conversation&category=integration)

1. In HACS, add this repository as a custom repository (category: **Integration**):
  `https://github.com/andrewbackway/ha-azure-foundry-conversation`.
2. Install **Azure AI Foundry Conversation**.
3. Restart Home Assistant.

After installing and restarting, add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=azure_foundry_conversation)

### Manual

Copy `custom_components/azure_foundry_conversation/` into your Home Assistant
`config/custom_components/` directory and restart.

## Configure in Home Assistant

1. *Settings → Devices & Services → Add Integration →* **Azure AI Foundry
   Conversation**.
2. Enter the **Endpoint URL** and **API key**. The flow validates the credentials.
3. Four entities are created as subentries: **Conversation**, **AI Task**, **STT**
   and **TTS**. Configure each via **Configure**:
   - **Conversation / AI Task** — set **Model** to your Azure deployment name;
     optionally set instructions (prompt), *Control Home Assistant* (LLM/Assist
     API), reasoning effort, max output tokens and verbosity.
   - **STT** — Speech endpoint URL, API key, language, result format, profanity.
   - **TTS** — Speech endpoint URL, API key, voice, output format, streaming
     toggle, and optional rate / pitch / volume / style / style degree / role.
4. Assign the entities to an **Assist pipeline**
   (*Settings → Voice assistants*): conversation agent + STT + TTS.

Add another agent at any time by adding another subentry — one connection can host
several agents with different deployments or prompts.

## Configuration reference

### Connection (config entry)

| Option | Notes |
|--------|-------|
| Endpoint URL | Base resource URL; `/openai/v1/` is appended automatically. |
| API key | Sent to the Azure Foundry v1 endpoint. |

### Conversation / AI Task (per agent)

| Option | Default | Notes |
|--------|---------|-------|
| Model (deployment name) | `gpt-5.6-sol` | Your Azure **deployment** name. |
| Instructions (prompt) | HA default | Template-capable system prompt. |
| Control Home Assistant | Assist | Enables device control via HA's LLM API. |
| Reasoning effort | `low` | `none`/`low`/`medium`/`high`/`xhigh`/`max` (per model). |
| Maximum output tokens | `3000` | Maps to `max_output_tokens`. |
| Verbosity | `medium` | `low`/`medium`/`high` (gpt-5/6). |

> `temperature` and `max_tokens` are intentionally **not** exposed — reasoning
> models reject them. Only `max_output_tokens` is used.

### Speech-to-Text

| Option | Default | Notes |
|--------|---------|-------|
| Speech endpoint URL | — | Custom-subdomain or regional Speech host. |
| API key | — | Azure Speech key. |
| Language | `en-US` | Recognition language. |
| Result format | `simple` | `simple` or `detailed`. |
| Profanity | `masked` | `masked` / `removed` / `raw`. |

### Text-to-Speech

| Option | Default | Notes |
|--------|---------|-------|
| Speech endpoint URL | — | Custom-subdomain or regional Speech host. |
| API key | — | Azure Speech key. |
| Voice | `en-US-JennyNeural` | Voice short name. |
| Language/locale | from voice | SSML `xml:lang`. |
| Output format | `audio-24khz-48kbitrate-mono-mp3` | `X-Microsoft-OutputFormat` (used when streaming is off). |
| Stream audio | `on` | Streams seamless PCM/WAV as it's synthesized for lower latency; sample rate follows the output format. |
| Rate / Pitch / Volume | — | SSML prosody (e.g. `+10%`, `+2st`, `+6dB`). |
| Style / Style degree / Role | — | `mstts:express-as` (voice-dependent). |

## Usage examples

- "Turn on the kitchen light." (device control via Assist)
- "What's the weather like?" (chat)
- `ai_task.generate_data` with a `structure` to get JSON back from the model.
- Ask the conversation agent directly, for example: `Create an image of a red campervan beside an Australian beach at sunset`. Explicit image-creation requests are sent to `gpt-image-1`; other messages continue through the chat model.

### Image generation service

The integration registers `azure_foundry_conversation.generate_image` as a Home Assistant service. It supports service responses, so use `return_response: true` from a script or automation that calls the service. Required input is `prompt`; optional inputs are `size` and `config_entry_id`. The supported sizes are `1024x1024`, `1536x1024` (landscape), and `1024x1536` (portrait). The default size is `1024x1024` and quality is set to high.

Example service call:

```yaml
action: azure_foundry_conversation.generate_image
data:
  prompt: A photorealistic red campervan beside an Australian beach at sunset
  size: 1536x1024
response_variable: generated_image
```

The service response includes `image_url` (a Home Assistant `/local/...` URL), `path` (the saved file path), `prompt`, `model`, and `size`. With multiple Azure connections, set `config_entry_id` to the desired config entry ID; otherwise the first loaded integration entry is used. The generated image is stored in Home Assistant's `www` directory, so it persists across restarts. Make sure your Home Assistant backup and storage policies cover this directory.

## Troubleshooting

- **`invalid_auth`** — wrong or expired API key.
- **`model_not_found` / 404** — the Model field must be your **deployment name**,
  not the base model id.
- **429** — quota/tier limit for the deployment (not an integration bug).
- **Content filter** — Azure guardrails blocked the prompt or response.
- **"Function tools with reasoning_effort are not supported … in
  /v1/chat/completions"** — this is exactly why the integration uses the Responses
  API. If you see it, you are (or a dependency is) calling Chat Completions.
- **Enable debug logging:**

  ```yaml
  logger:
    logs:
      custom_components.azure_foundry_conversation: debug
  ```

  Error logs include the HTTP status, Azure error code, and request id for Azure
  support correlation. Secrets are never logged.

## Credits

Based on the Home Assistant core
[`openai_conversation`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/openai_conversation)
integration and inspired by
[olohmann/home-assistant-aoai-conversation](https://github.com/olohmann/home-assistant-aoai-conversation).

## License

[Apache-2.0](LICENSE).