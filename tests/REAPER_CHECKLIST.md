# REAPER Integration Checklist

Status: **prepared, not executed**. Unchecked entries are proposed checks, not failures.

Obtain permission for the exact checks before starting. Live generation, conversion, or transcription also requires explicit approval for ElevenLabs credit consumption; determine expected usage when possible before sending requests. Do not infer live-call permission from permission for the offline regression suite.

Use a disposable copy of a project and audio. Record REAPER, ReaImGui, SWS, Python, operating-system versions, selected model, and actual outcomes. Never record API keys or private working text in reports. Do not use the live service just to test a validation error.

## Interface and validation without service requests

- [ ] Missing or invalid personal configuration displays an actionable error and blocks operations; the example is not loaded automatically.
- [ ] A successful reload applies presets and clears manual edits; a failed reload retains the last valid in-memory configuration.
- [ ] Switching tabs/models retains independent settings and working text; voice/profile changes and Reset preset behave as documented.
- [ ] Valid TTS text with a trimmed/offset or longer-than-120-second reference item enables generation without offering Glue, provided key/model/settings are valid. Do not click Generate during a validation-only check.
- [ ] Empty/over-limit text, missing key, multiple selection, and an unsaved project with no selected item show the relevant TTS requirement.
- [ ] STS/STT still block long and trimmed/offset audio, and offer Glue where required.
- [ ] Voice search, optional settings, editor resizing, tab navigation, and window resizing remain usable.
- [ ] Collapse/expand Activity log repeatedly without assertions; filters, Clear, and Copy work and do not expose the key or private working text.

## Audio workflows, only with separately approved service requests

- [ ] Selected-item TTS aligns on TTS, saves beside the source, mutes only the source, and leaves only the generated item selected.
- [ ] TTS without selection inserts on TTS at the captured cursor, saves under the saved project's `ElevenReaperLab_Audio`, selects the generated item, and mutes no unrelated item.
- [ ] Voice Conversion imports on VOICE_REGEN, aligns with and mutes the source, and selects only the generated result.
- [ ] With normalization enabled, SWS acts on the generated result and that item remains selected after accepting or cancelling the normalization dialog.
- [ ] With normalization disabled, the generated item still remains selected.
- [ ] Cursor and track selections are preserved; each import/mute and normalization action is coherently undoable. Inspect Undo and Redo in the disposable project.
- [ ] Successful transcription opens TTS with editable text and does not automatically generate audio.
- [ ] Errors release the busy state and provide usable recovery instructions; do not intentionally spend credits to reproduce import failures already covered by simulations.
- [ ] If a genuine import failure occurs, previous item selection is restored and saved audio remains available as documented.

## Reporting

Record which entries were executed, results, skipped items, and reasons in [Progress.md](../docs/development/Progress.md). Distinguish user-reported checks, agent-run offline simulations, and actual REAPER trials. Test Windows and macOS separately before claiming both were verified. A successful request for one model does not verify every model or account.
