# Configuration Guide

Before first launch, copy `erl_config.example.json` to `erl_config.json` beside `ERL_App.lua` and customize the copy. The example is a template; the application reads only your personal `erl_config.json`. The file controls voices, model defaults, usage profiles, and the default normalization preference. The application does not write to this file.

The interface offers a model selector and dynamic parameters within each operation tab. The shared local capability registry determines supported fields; it does not fetch models or check account access. See [MODEL_REGISTRY.md](docs/technical/MODEL_REGISTRY.md) for registered models and documented limitations.

## Editing and reloading

1. Back up `erl_config.json` before editing it.
2. Save your changes as UTF-8 JSON. A UTF-8 byte order mark is accepted.
3. Expand **Settings** and click **Reload configuration** in the application.

If configuration is missing or invalid at startup, the application displays the error and blocks all operational controls. Correct the file and reload it. There is no automatic fallback configuration and no automatic overwrite of your file. You can manually copy `erl_config.example.json` to `erl_config.json` to restore the distributed settings.

If a later reload fails, the application displays the error and retains the last valid configuration and current settings in memory. They remain usable for that session. If you restart with an invalid file, startup is blocked.

A successful reload retains the selected voice, profile, and models by ID when available, reapplies their presets, clears manual changes across all operations/models, and restores `preferences.normalize_audio`. Reloading is disabled while an operation is running. Registry and configuration are loaded together; a failed candidate retains the previous registry snapshot as well as the previous configuration.

## File structure

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | Number | Must be `1`. |
| `voices` | Non-empty array | Available voices in display order. |
| `model_defaults` | Object | Default settings grouped by operation and model ID. |
| `profiles` | Non-empty array | Usage profiles in display order. |
| `preferences` | Object | Application defaults; currently requires `normalize_audio`. |

All five root fields are required. Unknown structural fields are rejected to catch spelling mistakes. JSON comments, trailing commas, duplicate object keys, and `null` are rejected. Omit optional fields instead of setting them to `null`. Use `{}` for an empty object and `[]` for an array. The maximum configuration size is 1 MiB; nesting is limited to 64 levels.

Errors use field paths such as `/profiles/1/settings/tts/eleven_multilingual_v2/speed`. Array positions in these messages start at 1.

## Voices

Each voice requires:

- `id`: a unique, stable internal identifier, such as `narrator`. Use lowercase English identifiers starting with a letter; subsequent characters may also be digits, underscores, or hyphens.
- `name`: the display name; it may use any language.
- `voice_id`: the actual ElevenLabs voice ID. Each provider ID must be unique in the list.
- `description`: an optional non-empty description.

For example, an additional voice entry would be:

```json
{
  "id": "narrator",
  "name": "My Narrator",
  "voice_id": "REPLACE_WITH_YOUR_ELEVENLABS_VOICE_ID",
  "description": "Narration voice"
}
```

Replace the placeholder with an accessible ElevenLabs voice ID. Loading configuration checks structure, not whether a voice is available to your account. Loading and reloading do not call ElevenLabs.

Keep `id` unchanged when renaming or reordering a voice. Profile overrides refer to this internal ID, not to the display name or provider ID. If you remove a voice, also remove references to it from every profile's `voice_overrides`.

## Model defaults and supported settings

Operation keys are `tts`, `sts`, and `stt`. Within each operation, settings are keyed by the exact model ID.

Defaults for the fallback models (`eleven_multilingual_v2` and `eleven_multilingual_sts_v2`) are required by the configuration schema and must contain all three parameters:

| Parameter | Type | Registered REST range | Current UI range |
| --- | --- | --- | --- |
| `speed` | Number | 0.25–4.0 | 0.5–2.0 |
| `stability` | Number | 0.0–1.0 | 0.0–1.0 |
| `similarity_boost` | Number | 0.0–1.0 | 0.0–1.0 |

REST and UI limits are separate fields in `erl_models.json`. Configuration is checked against the REST rules; editing through the current controls uses the UI range. These are model-specific registered rules, not universal limits for all ElevenLabs models. Registry suggestions are not inserted automatically into configuration.

Additional model entries can be stored as objects with scalar number, boolean, or string settings. Known models are checked for operation compatibility, supported parameters, types, ranges, and allowed values. Unknown models are retained as inactive entries with warnings; they cannot be resolved through the registry. All numbers must be finite.

The Model selector and controls follow the registry for each operation. Python validates the complete settings again, sends only supported fields, and separates voice settings from request-level fields such as seed. STT settings never inherit TTS/STS voice parameters. See [MODEL_REGISTRY.md](docs/technical/MODEL_REGISTRY.md) for the registered settings and documentation limitations.

Partial profile and voice overrides are allowed. After composition, required application settings and cross-parameter constraints are checked for every profile/voice combination for each referenced known model. For example, introducing v4 settings requires enough defaults or overrides to provide both stability and similarity for every profile. Parameters such as speed are rejected under v4 rather than silently removed.

An additional v4 defaults entry within `model_defaults.tts` can be prepared as follows, alongside the required existing v2 entry:

```json
"eleven_v4": {
  "stability": 0.5,
  "similarity_boost": 0.75
}
```

The example configuration includes this entry. With complete effective settings, a registered model becomes selectable for its compatible operation. Models without the required effective settings are disabled with a configuration explanation. Eleven v3 is enabled with a visible warning: its provisional Stability values 0, 0.5, and 1 have not been verified through the API.

The example model defaults are v3 stability 0.5; v4 and v4 Turbo stability 0.5 and similarity 0.75; Flash v2.5 and English STS v2 speed 1.0, stability 0.5, and similarity 0.75. Scribe v1/v2 have empty settings objects, leaving optional fields to the service. Installation and reload never automatically rewrite your JSON.

## Profiles and precedence

Each profile requires a unique stable `id` and a non-empty `name`. Identifier rules are the same as for voices. Optional `settings` and `voice_overrides` objects can be omitted when defaults are sufficient.

`settings` has the same operation → model → parameters structure as `model_defaults`. Partial overrides are allowed. `voice_overrides` maps internal voice IDs to another operation → model → parameters object.

Values are composed in this order, with later layers overriding earlier ones:

1. `model_defaults` for the operation and model.
2. The selected profile's `settings` for the operation and model.
3. The selected profile's `voice_overrides` for the selected voice, operation, and model.
4. Manual parameter changes in the interface.

The example configuration supplies these profile values for the fallback TTS and STS models:

| Profile | Other voices: speed / stability / similarity | Vecchio Saggio 3 |
| --- | --- | --- |
| Bedtime | 0.91 / 0.85 / 0.80 | 1.08 / 0.70 / 0.75 |
| Meditation | 0.90 / 0.85 / 0.80 | 1.00 / 0.85 / 0.80 |
| Course | 0.97 / 0.80 / 0.75 | 1.10 / 0.80 / 0.75 |

“Profile” means a usage preset, not a REAPER `.rpp` project.

## Manual settings and preferences

Choose the **Text to Speech**, **Voice Conversion**, or **Speech to Text** tab, then **Model**. Each action uses its own selected model and values. Switching tabs or models preserves manual changes separately for each operation/model pair. Voice and profile selections remain shared; voice-generation controls are omitted from the transcription tab.

**Preset modified** indicates that the displayed operation/model has manual overrides. **Reset preset**, changing the voice or profile, or successfully reloading configuration clears manual overrides for every operation and model. Manual overrides are session-only and never update the JSON. Startup applies the configured presets.

Required settings appear directly. **Optional settings** contains a **Use ...** checkbox for each additional supported parameter. Checking it initializes a custom value from registry suggestions; suggestions are used only for this explicit action. Unchecking omits the field even if configuration supplied it. Values `false` and `0` remain real values, distinct from omission. Reset restores the configured preset. Temporary invalid combinations display an error and block dispatch until corrected.

`preferences.normalize_audio` must be a JSON boolean (`true` or `false`). The checkbox may override it during the session. Successful reload and application restart restore the configured default.

With no selected item, TTS requires a saved REAPER project and writes to `ElevenReaperLab_Audio` in that project's directory, inserting at the captured edit cursor. This destination is displayed in the TTS tab and cannot currently be overridden in JSON. Selected-item generation retains its source-based destination. STS/STT still require a selected audio item. No configuration or example file is rewritten automatically.

## Saved selections

The application saves selections in REAPER ExtState under `eleven_reaper_lab`:

- `selected_voice_key`: the internal voice `id`.
- `selected_profile_id`: the internal profile `id`.
- `selected_model_tts`, `selected_model_sts`, and `selected_model_stt`: selected model IDs per operation.

After a successful load, selection IDs are saved. If a saved selection is unavailable, the first configured entry is selected and a message is added to the log.

## API key and bundled dependency

Do not add an API key to configuration or examples. The API key field uses REAPER ExtState storage, separate from the shareable JSON. This is not encrypted secret storage.

JSON support is bundled in `lib/json.lua`, from [rxi/json.lua](https://github.com/rxi/json.lua), with its MIT license in `lib/json.LICENSE`. See `lib/README.md` for the exact upstream revision. No additional JSON package installation is needed.
