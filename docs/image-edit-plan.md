# Image Edit Plan

> Status: **IMPLEMENTED - included in the current worktree; not yet released.**
> Source: [Call the image edit API](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/dall-e#call-the-image-edit-api)

## 1. Goal

Add image editing (`POST {endpoint}/openai/deployments/{deployment}/images/edits`) using the
existing image generation deployment (gpt-image series). Edit takes one or more
input images, a prompt, and an optional mask, and returns a PNG.

## 2. Decisions (from Q&A)

| Topic | Decision |
| --- | --- |
| Surfaces | Both a new `edit_image` service **and** the AI Task image entity |
| Input sources | Local file path (allowed dirs), HA media-source URI, `camera`/`image` entity, AI Task attachments |
| Mask | Supported (optional, same sources as input; must match input size, PNG with alpha) |
| Multiple input images | Supported (gpt-image models accept an array) |
| Model/deployment | Reuse the existing image subentry model (no new setting) |
| Extra params | `size`, `quality`, plus `input_fidelity` and `background` |

## 3. Current state

- [image_generation.py](../custom_components/azure_foundry_conversation/image_generation.py):
  `async_request_image()` calls `client.images.generate(...)`; `async_generate_image()`
  saves to `/config/www/azure_foundry_images/<uuid>.png` and returns `/local/...`;
  `generate_image` service with `SupportsResponse.ONLY`.
- [ai_task.py](../custom_components/azure_foundry_conversation/ai_task.py):
  `AzureFoundryImageEntity` supports `GENERATE_IMAGE` and image attachments.
- The integration's primary client uses the Foundry v1 endpoint. The documented
  edit API uses a deployment-scoped route and `api-version`, so edits use a separate
  Azure-specific SDK client while sharing Home Assistant's HTTP session.

## 4. Design

### 4.1 Shared request helper (`image_generation.py`)

Refactor shared validation/error handling so generate and edit don't duplicate it:

- Extract `_validate_prompt()` and `_validate_image_size()` from `async_request_image`.
- Extract the `try/except openai.*` block and base64 decoding into
  `_async_call_image_api(entry, image_api, operation, ...)`.
- New `async_request_image_edit(hass, entry, prompt, images, mask, size, model,
  quality, input_fidelity, background) -> bytes` uses an `AsyncAzureOpenAI` client
  and its `images.edit` method. The client targets
  `/openai/deployments/{deployment}/images/edits?api-version=2025-04-01-preview`,
  matching the linked Microsoft example.
- Same timeout (180s), `max_retries=0`, same error mapping (timeout, rate limit,
  bad request, 404 deployment not found).
- Only pass optional params when set, so deployments are not sent edit options unless
  the user opts in.

### 4.2 Input image resolution (`image_sources.py`)

`async_resolve_image(hass, source) -> ImageInput(data, mime_type, extension)` supporting:

1. **Local path** - must resolve (after `Path.resolve()`) inside
   `hass.config.is_allowed_path()` - reject traversal/symlink escape. Also accept
   `/local/...` URLs mapped to the Home Assistant `www` directory.
2. **Media source URI** (`media-source://...`) via
   `media_source.async_resolve_media`; only media sources with a local resolved path
   are accepted (no arbitrary network URL fetching).
3. **Entity ID** (`camera.*` -> `camera.async_get_image`, `image.*` -> `image.async_get_image`).
4. **AI Task attachments** - Home Assistant 2026.1 resolves these to local paths
   (`task.attachments`); the entity advertises `SUPPORT_ATTACHMENTS`.

Validation common to all: PNG/JPEG/WebP only; max 50 MB per image, 50 MB combined
across inputs and mask, max 16 source images. Non-PNG images are passed through with
their detected MIME type (no Pillow dependency). Mask must be PNG; the size-match check
is delegated to Azure (errors are surfaced as Home Assistant errors). File reads run in
the executor.

### 4.3 `azure_foundry_conversation.edit_image` service

Fields (also added to `services.yaml`, `strings.json`, `translations/en.json`):

| Field | Required | Notes |
| --- | --- | --- |
| `prompt` | yes | Edit instructions |
| `images` | yes | List of sources (path / media-source URI / entity id) |
| `mask` | no | Single source |
| `size` | no | Same options as generate (+ `auto`) |
| `quality` | no | `low`/`medium`/`high`/`auto` |
| `input_fidelity` | no | `low`/`high` |
| `background` | no | `transparent`/`opaque`/`auto` |
| `config_entry_id`, `image_subentry_id` | no | Same selection logic as generate (`_select_config_entry`) |

Response (same shape as generate): `image_url`, `path`, `prompt`, `model`, `size`, plus
`source_count`. Reuses the shared `_async_save_image()` logic.
Note: transparent background requires PNG output (already PNG).

### 4.4 AI Task entity

HA 2026.1's `ai_task.generate_image` service accepts `attachments` (media-source
items), resolves them to `conversation.Attachment` objects, and gates them on
`AITaskEntityFeature.SUPPORT_ATTACHMENTS`. This was verified against Home Assistant
Core tag `2026.1.0` (the integration minimum is 2026.1.0).

- Add `SUPPORT_ATTACHMENTS` to `AzureFoundryImageEntity._attr_supported_features`.
- In `_async_generate_image`: if `task.attachments` is non-empty -> resolve them and call
  `async_request_image_edit` (first attachment(s) = images; no mask via this path);
  otherwise existing generate path.
- Mask for AI Task: no per-call mask; `input_fidelity` and `background` are read from
  the image subentry options (see 4.5).

### 4.5 Config flow (image subentry)

Optional, additive: add `input_fidelity` and `background` defaults to the image subentry
form (`config_flow.py`, `const.py`, `strings.json`, `translations/en.json`), used for AI
Task edits and as service defaults. Existing entries remain valid (all optional).

### 4.6 Files touched

- `image_generation.py` (refactor + edit request/service)
- `image_sources.py` (new)
- `ai_task.py`, `const.py`, `config_flow.py`
- `services.yaml`, `strings.json`, `translations/en.json`
- `README.md` (document `edit_image`, example automation, supported sources)
- Tests: `tests/test_image_generation.py` (extend), `tests/test_image_sources.py` (new),
  `tests/test_ai_task.py`, `tests/test_config_flow.py`

## 5. Security considerations

- Path allowlisting via `hass.config.is_allowed_path`, resolve symlinks before checking.
- Size caps before reading into memory; limit image count.
- Never log image bytes or full paths of non-allowed locations; log counts/sizes only.
- Service stays admin-callable like generate; no new network targets beyond the
  configured Azure endpoint (media-source/camera are local HA resources).

## 6. Testing plan

- Unit: source resolver (allowed/blocked path, traversal, bad type, oversize, entity
  and media-source mocks), request builder (params omitted when unset, multi-image,
  mask), error mapping (timeout/429/400/404), empty/invalid base64 response.
- Service: schema validation, entry/subentry selection, saved file + response shape.
- AI Task: attachments -> edit path; no attachments -> generate path unchanged.
- Config flow: new optional fields round-trip; old entries load.
- Regression: existing generation tests remain in place. Ruff, formatting, Python
  compilation, JSON, and YAML checks pass. Pytest could not execute in this Windows
  environment: installing the repository's test requirements failed while building
  `lru-dict` because Microsoft C++ Build Tools are unavailable.

## 7. Implementation phases

1. Shared request validation, API error mapping, response decoding and save logic -
   implemented.
2. Source resolution, allowlist and size checks - implemented.
3. `async_request_image_edit`, service schema/registration and translations -
   implemented.
4. AI Task attachment edit behavior - implemented against the HA 2026.1 API.
5. Optional `input_fidelity` / `background` configuration - implemented.
6. README and this plan updated. Release version bump is deferred to the normal
   release process; no release or commit was created.

## 8. Implementation notes

- The installed `openai` SDK is 2.45.0; runtime inspection confirmed the Azure
  client's `images.edit` call serializes multipart input and targets the documented
  deployment-scoped route. Its API version is centralized in `const.py`.
- Runtime validation caps each image and combined inputs/mask at 50 MB and limits
  source images to 16.
- The optional subentry fields are omitted from API calls until configured; service
  arguments can override them.
- Mask alpha semantics and model-specific parameter availability should be verified
  with the deployed Azure model; Azure's bad-request response is surfaced if an
  unsupported parameter is supplied.
