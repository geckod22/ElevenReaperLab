# Eleven Reaper Lab

**Eleven Reaper Lab** connects [REAPER](https://www.reaper.fm/) with [ElevenLabs](https://elevenlabs.io/), bringing **Text to Speech**, **Voice Conversion**, and **Speech to Text** into your workflow.

Generate audio from text, convert a selected voice, or transcribe audio and edit the text before generating a new take. The ReaImGui interface provides model-specific controls, searchable voices, configurable profiles, and an activity log.

Developed by [Armando Liccardo (geckod22)](https://github.com/geckod22).

This is an independent project, not affiliated with or endorsed by Cockos or ElevenLabs.

## Requirements

- **REAPER** with Python enabled for ReaScript.
- **[Python](https://www.python.org/downloads/)** with the `requests` package installed in the environment used by REAPER.
- **[SWS / S&M Extensions](https://www.sws-extension.org/)** for item management and loudness operations.
- **ReaImGui**, available through **[ReaPack](https://reapack.com/)**.
- An **ElevenLabs API key** with access to the voices and models you want to use.

## Installation

1. Install Python, then open **Options > Preferences > Plug-ins > ReaScript** in REAPER. Enable Python for ReaScript and configure the Python shared library path for your platform.

    To locate the Python executable, use `where python3` on Windows or `ls -l $(which python3)` on macOS.

2. Install the remaining dependencies listed above.
3. Download and extract the project. Place the entire project folder inside your REAPER Scripts directory:

    - Windows: `%AppData%\REAPER\Scripts`
    - macOS: `~/Library/Application Support/REAPER/Scripts`

4. Before the first launch, copy `erl_config.example.json` to `erl_config.json` in the same folder as `ERL_App.lua`. Customize the copy with the ElevenLabs voice IDs you can access, your profiles, and your preferred model defaults. Keep the example file as a reference.
5. In REAPER, open **Actions > Show action list**, choose **New Action > Load ReaScript...**, and select `ERL_App.lua` from the extracted folder.
6. Run `Script: ERL_App.lua` and enter your API key under **Settings**.

The application registers its Python actions automatically on first launch. Keep all supplied scripts, JSON files, and the `lib` folder together.

**Updating:** back up and preserve your customized `erl_config.json`, update the application files together, and restart REAPER to unload cached Python modules. Review changes to `erl_config.example.json` before manually incorporating any new settings into your configuration.

## Usage

Choose an operation tab and a model. For speech generation and conversion, select a voice and profile, then adjust the model's settings as needed. **Reset preset** restores the configured values. Expand **Settings** to change the API key or audio normalization preference.

### Text to Speech

Enter your text in the editor and generate audio:

- **With one audio item selected:** the result is aligned with the source on the `TTS` track, saved beside the source file, and the original item is muted.
- **With no items selected:** save the REAPER project first. The result is inserted at the edit cursor on the `TTS` track and saved in `ElevenReaperLab_Audio` beside the `.rpp` file.

The destination is shown before generation. Selecting multiple items is not supported.

Text to Speech does not upload the selected item's audio. The item only provides alignment and the output location, so it does not need Glue and its duration does not limit text generation. With no item selected, generation uses the saved project and edit cursor as described above.

After a successful audio import, only the newly generated item remains selected, including after optional normalization. The edit cursor and track selection are preserved. If importing fails, the previous item selection is restored.

### Voice Conversion

Select one audio item and use **Voice Conversion** to convert it to the chosen voice. The result is inserted on the `VOICE_REGEN` track and the original item is muted. This operation uses the source audio directly; it does not use the text editor.

### Speech to Text

Select one audio item and use **Speech to Text** to transcribe it. After a successful transcription, the application opens **Text to Speech** with the transcribed text ready to edit. Audio is generated only when you request it.

The **Activity log** shows operation results and errors. Expand it to filter, copy, or clear entries. You can also assign a keyboard shortcut to the application in REAPER's Action List.

## Configuration and profiles

`erl_config.example.json` is the distributed template; `erl_config.json` is your personal configuration. Create the latter by copying the example, then edit it to manage voices, model defaults, usage profiles, and the default normalization preference. The example profiles are **Bedtime**, **Meditation**, and **Course**. Keep your API key in the application's **Settings**, outside the JSON file.

After editing, click **Reload configuration** under **Settings**. A successful reload reapplies presets and clears manual setting changes. Invalid configuration at startup blocks operations until corrected; a failed reload retains the last valid configuration for the current session.

The application requires a valid `erl_config.json`; it does not create it automatically or load the example as a fallback. To restore the example settings, back up your personal file before replacing it with a copy of [erl_config.example.json](erl_config.example.json). See the [Configuration Guide](CONFIGURATION.md) for examples, setting precedence, and validation rules.

## Limitations

- REAPER remains blocked during service requests. Live progress and responsive cancellation are unavailable while a request is running.
- Voice Conversion and Speech to Text require one valid audio item, with a maximum duration of 120 seconds. Follow the application's Glue requirements before processing. These Glue and duration restrictions do not apply to Text to Speech.
- Available controls come from the bundled model catalog. Opening or reloading the application does not check which models or voices your ElevenLabs account can access. Realtime transcription is not supported.
- Eleven v3 Stability choices are provisional and have not been verified through the API; the interface displays a compatibility warning.
- Requests use a 10-second connection timeout and a 300-second read-inactivity timeout. They are not retried automatically. If the outcome is uncertain, check the service before repeating a generation to avoid duplicate requests and charges.

## Documentation and third-party library

- [Configuration Guide](CONFIGURATION.md): voices, profiles, defaults, and configuration errors.
- [Model Registry](docs/technical/MODEL_REGISTRY.md): registered models, parameter capabilities, and references.
- [Protocol Guide](docs/technical/PROTOCOL.md): request handling, errors, and recovery behavior.

The bundled [rxi/json.lua](https://github.com/rxi/json.lua) library requires no separate installation. Its MIT license is included in [lib/json.LICENSE](lib/json.LICENSE).
