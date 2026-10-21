# Model Registry

`erl_models.json` is a local catalog shared by Lua and Python. It describes registered capabilities and validation rules. It is application metadata, separate from the user-editable voices, profiles, and defaults in `erl_config.json`.

The registry does not call ElevenLabs or prove account access. The application uses it for model selectors, dynamic controls, and worker payload preparation. File transcription excludes realtime models. A model must have valid effective settings to be selectable.

## Files

| File | Responsibility |
| --- | --- |
| `erl_models.json` | Model IDs, operation definitions, parameter rules, sources, and review date. |
| `erl_model_registry.lua` | Catalog loading, lookup, validation, composition, and payload grouping for Lua. |
| `erl_model_registry.py` | Matching capabilities for Python, using only the standard library. |
| `erl_json.lua` | Shared strict JSON reading for the Lua configuration and registry loaders. |
| `erl_config.lua` | Configuration structure, saved selections, registry-backed validation, and profile resolution. |
| `erl_session.lua` | Operation/model selection, temporary per-model edits, optional-field omission, and text limits. |
| `erl_worker_settings.py` | Settings validation and typed JSON/multipart payload preparation. |

JSON decoding still uses the unmodified bundled rxi/json.lua library and its MIT license. The shared reader applies structural checks and accepts UTF-8 with an optional byte order mark.

## Registered models

| Model ID | Operation | Input | Registered settings |
| --- | --- | --- | --- |
| `eleven_multilingual_v2` | TTS | Text | Speed, stability, similarity, style, speaker boost, seed. |
| `eleven_v3` | TTS | Text | Stability, style, seed; see the documentation notes below. |
| `eleven_v4` | TTS | Text | Stability, similarity, seed. |
| `eleven_v4_turbo` | TTS | Text | Stability, similarity, seed. |
| `eleven_flash_v2_5` | TTS | Text | Speed, stability, similarity, seed; conservative subset. |
| `eleven_multilingual_sts_v2` | STS | Audio file | Speed, stability, similarity, style, speaker boost, seed, noise removal. |
| `eleven_english_sts_v2` | STS | Audio file | Speed, stability, similarity, style, speaker boost, seed, noise removal. |
| `scribe_v2` | STT | Audio file | Language, event tags, diarization, speaker count, timestamp detail, temperature, seed, speaker separation threshold, filler removal. |
| `scribe_v1` | STT | Audio file | The registered Scribe v2 set excluding filler removal. |
| `scribe_v2_realtime` | STT | Audio stream | Recognition entry only; no realtime controls or transport integration. |

This is a deliberately scoped catalog, not a complete ElevenLabs API schema. A setting absent from a known model's entry is not accepted by this application, even if the service might support it.

File-transcription callers must filter models by `input_kind = audio_file`; the realtime recognition entry is excluded by that filter.

## Catalog structure

- `registry_version`: currently `1`.
- `reviewed_on`: date on which the documentation was consulted; not a live API test date.
- `sources`: named official documentation URLs, referenced by parameter and model entries.
- `notes`: catalog-wide scope and limitations.
- `runtime_defaults`: fallback model IDs when no usable saved selection exists.
- `legacy_worker_parameters`: required fallback-model defaults for configuration validation; dispatched fields follow each model's parameter definitions.
- `parameters`: reusable parameter definitions.
- `models`: ordered model entries with operation-specific parameter references and overrides.

Model entries may include `selection_enabled` (boolean, default true) and `selection_note` (visible explanatory text). Keep application compatibility rules in the registry and user defaults in configuration. Adding configuration for an unknown model cannot enable it.

Each operation records its input kind, transport, endpoint, registered parameters, required application settings, optional constraints, and documented limits. Endpoint metadata is descriptive; the registry does not perform requests.

Each parameter records its API field, destination (`voice_settings` or `request`), type, label, description, optional numeric bounds or allowed values, optional string-length bounds, UI metadata, sources, and an optional suggested default. Model-specific overrides are merged into these definitions; UI fields are merged by key.

`api_required` describes endpoint optionality. `required_settings` is a separate application policy for a complete effective setting set. The application requires explicit values for its core voice settings so it can validate a reproducible configuration, even when the service permits omission.

Suggested defaults are metadata only. Neither language implementation inserts them into user settings. Settings precedence is:

1. User-configured model defaults.
2. Profile settings for the operation and model.
3. Profile overrides for the selected voice, operation, and model.
4. Manual settings for that operation and model.

## Validation behavior

- Validate every configured layer for type, supported parameters, numeric range, string length, and allowed values before composing it.
- Validate required application settings and cross-parameter constraints on the final effective values.
- Preserve `false` and `0` as supplied values. Do not coerce strings into numbers or booleans.
- Reject a known model under an incompatible operation.
- Retain settings for unknown models as inactive and return a warning. They cannot be resolved or dispatched through the registry.
- Check all profile/voice combinations for each referenced known model during configuration loading. Supply shared defaults when some profiles do not define their own required values.
- Report configuration paths in errors. Effective-value errors use synthetic paths such as `/profiles/1/effective_settings/wise_man_2/tts/eleven_v4/stability`.

The Scribe speaker separation threshold requires an explicitly configured `diarize: true` and cannot coexist with `num_speakers`. Requirements are checked after composition, allowing different layers to contribute settings.

Workers dispatch every supplied registered setting to its declared API destination. Unsupported fields are rejected. Unchecked optional controls omit fields; explicit false and zero values are preserved. Empty STT settings are encoded as an object (`{}`), not an array.

Each validated configuration retains its registry snapshot. Configuration reload reads a candidate catalog and candidate configuration together; failure retains the previous configuration, registry, and manual values. At startup, failure blocks operational controls.

The interface includes the original validated catalog JSON as `registry_json` in the versioned request envelope, alongside the selected `model_id` and flat typed `settings` object. Python validates that snapshot instead of rereading a possibly changed file. The protocol-v2 envelope supports item and cursor insertion modes. Workers consume the validated request snapshot rather than standalone scalar settings. See [PROTOCOL.md](PROTOCOL.md) for correlation, errors, timeouts, and terminal handling.

## API and UI limits

The registry stores REST speed bounds of 0.25–4.0 from the official REST settings reference. The speed control uses a 0.5–2.0 range in separate UI metadata. Loaded configuration is checked against REST bounds; editing through the current control uses UI bounds.

Documented text limits are stored where available: 10,000 characters for Multilingual v2 and v4, 5,000 for v3, and 40,000 for Flash v2.5. No unverified v4 Turbo text limit is invented. The application counts Unicode code points in the editor and rejects over-limit text both before handoff and during Python payload preparation.

## Compatibility limitations

- The model-specific product guide excludes speed, similarity, and speaker boost for v3, although the generic official settings reference discusses v2/v3 more broadly. The catalog follows the specific exclusions and registers style based on the explicit official settings reference.
- The v3 stability choices 0, 0.5, and 1 are a provisional application policy for the Creative/Natural/Robust selector. The selector displays a compatibility warning. Numeric mode mapping remains unverified and needs separately authorized API verification.
- Flash v2.5 registers a subset of settings. Style and speaker boost are not enabled without model-specific confirmation.
- STS speed is registered from the generic REST voice-settings definition; its per-model effect has not been independently tested.
- Catalog membership and documentation do not establish account access or server acceptance. The application does not query live model availability.

## Lua and Python interfaces

Both registry implementations expose equivalent operations:

| Method | Purpose |
| --- | --- |
| `load(path)` | Read and validate a catalog; use the bundled file when path is omitted. |
| `operation(operation, model_id)` | Get a compatible operation definition or raise an error. |
| `list_models(operation, input_kind)` | List known models in catalog order, optionally filtering input kind. |
| `status(operation, model_id, discovered_models)` | Report local recognition, local compatibility, optional API listing flags, and unknown account access. |
| `parameters(operation, model_id)` | Return merged parameter definitions, sorted by parameter ID. |
| `validate_settings(operation, model_id, settings, partial, path)` | Validate one layer or a complete set; return a copy. |
| `compose(operation, model_id, layers, path)` | Merge ordered layers and validate the result. |
| `split_settings(operation, model_id, settings)` | Group validated settings by API destination without encoding or sending them. |

Lua instance methods use `:`. Lua `registry.load` is a module function; Python loading uses `ModelRegistry.load`. Failures raise Lua errors or Python `RegistryError`. Configuration loading catches errors and returns them for display in the GUI.

Python additionally exposes `resolve(config, profile_id, voice_id, operation, model_id, manual)` for the same four-layer composition used by Lua `config.resolve`. The Python method expects an already structurally validated configuration; it does not replace the Lua configuration validator.

The optional `discovered_models` argument accepts an already fetched `/v1/models` list. It never fetches data. Listing state and API capability flags are reported separately from local rules; they neither expand the catalog nor establish account access. STT discovery is marked not applicable to this TTS/STS list. Network fetching, persistence, and cache behavior remain future integration work.

