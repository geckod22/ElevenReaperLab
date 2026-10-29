# Lua/Python Protocol v2

The application uses protocol-v2 JSON messages with explicit item and cursor insertion modes in nonpersistent REAPER ExtState under `eleven_reaper_lab`. The three registered Python actions are entry points for the shared `erl_worker.py` adapter. `erl_protocol.lua` and `erl_protocol.py` define message handling; model metadata defines payload compatibility. Execution is synchronous.

## Transport and request lifecycle

1. Lua validates selection and effective settings, snapshots the request, and generates a GUID `request_id`.
2. Lua writes `active_request_id`, `request_api_key`, and `request_json`. Authentication is separate from the envelope and ordinary logs.

   Lua also supplies nonpersistent `worker_script_folder` from its own script location. Python actions use this directory to import local modules because REAPER may execute them without defining `__file__`. Ordinary Python execution retains a guarded `__file__` fallback. Bootstrap failures are caught and reported through the protocol.
3. The synchronous action claims and removes `request_json`, validates it, reads and removes temporary authentication, and makes at most one HTTP request.
4. The worker writes `response_json` only while its request still owns `active_request_id`.
5. Lua validates version, identity, operation, terminal status, logs, and result/error. Stale responses cannot update the editor or trigger normalization.
6. Lua clears temporary state and busy status before post-processing. A synchronous action returning without a valid response produces `MISSING_RESPONSE` or `ACTION_FAILED` instead of leaving the interface busy.

Install/update the complete application folder and restart REAPER to unload cached Python modules. The interface and workers must use the same protocol version; requests with unsupported versions are rejected before networking. Worker actions require a fresh request from the interface; invoking one directly does not replay a consumed request. The configuration schema version is 1.

## Request fields

| Field | Meaning |
| --- | --- |
| `protocol_version` | Integer `2`. |
| `request_id` | Unique GUID for this attempt. |
| `operation` | `tts`, `sts`, or `stt`. |
| `model_id`, `voice_id` | Selected model and provider voice; voice is empty for STT. |
| `settings` | Flat typed object, including `{}` for default-only transcription. |
| `registry_json` | Original JSON string of the interface's validated catalog snapshot. |
| `input` | TTS `text`; source `audio_path` only in item mode. |
| `context` | `project_master_guid`, `insertion_mode`, captured timeline `position`; `item_guid` in item mode, absolute saved `project_file` in cursor mode. |
| `output` | Destination `track_name`, snapshotted `normalize` boolean; absolute `directory` in cursor mode. |
| `timeouts` | Connection and read-inactivity seconds. |

The native master-track GUID provides a project token without writing project metadata, including for unsaved projects. Source item GUID, path, and position are checked before networking and again before importing or accepting transcription. Lua also retains the actual project reference and verifies it before dispatch and normalization.

`insertion_mode: "item"` references a selected audio item. TTS uses it for placement, output location, and source muting; only STS/STT upload source audio. `insertion_mode: "cursor"` is allowed only for TTS with no selected item: the request omits `item_guid` and `audio_path`, includes the saved `.rpp` path and the captured edit cursor, and requires `output.directory` to be `ElevenReaperLab_Audio` inside the saved project's directory. Python verifies the current project identity and saved path before the call and import. Unsaved projects and cursor-mode STS/STT are rejected. Multiple selected items are rejected by Lua instead of silently choosing an insertion mode.

Python reads the master GUID through `GetSetMediaTrackInfo_String` with the `GUID` attribute. Its `GetTrackGUID` binding returns a native pointer representation and must not be compared with Lua's GUID text. After updating imported Python modules, restart REAPER to avoid retaining a cached earlier module version.

The API-key field uses a persistent REAPER preference. `request_api_key` is a temporary request copy and is removed on consumption and terminal cleanup. The protocol is local inter-script transport, not encrypted credential storage.

## Terminal responses

Every response includes version, request ID, operation, `status`, and a `logs` array of `{level, message}` objects. Logs do not determine success.

- `completed` has a `result`: either `{kind: "text", text: ...}` or `{kind: "audio", audio_path: ..., item_guid: ..., project_master_guid: ...}`.
- `failed` or `cancelled` has an `error` with `code`, readable `message`, and boolean `outcome_uncertain`. HTTP errors may include `http_status`. An import failure after saving audio includes `audio_saved: true` and `audio_path` for manual recovery.

Fields are omitted when inapplicable; JSON null is not used. Unicode, newlines, and literal `|` characters remain data rather than message delimiters. Full input text, API keys, raw HTTP bodies, and exception strings are excluded from ordinary logs.

After a successful TTS HTTP response, the worker reads the optional `character-cost` response header and records a nonnegative integer or decimal value as `Text to Speech credits used: N.` in the existing log array. Missing or invalid metadata produces `Text to Speech credit usage unavailable.` without failing generation. No text-length estimate, account-balance comparison, history lookup, or additional API request is used. Billing is logged before audio saving/import, so a later local failure does not hide the reported charge. HTTP failures and timeouts do not imply zero cost. STS/STT do not emit billing entries. See ElevenLabs' [generation-cost documentation](https://elevenlabs.io/docs/api-reference/introduction/).

Error codes distinguish invalid requests/settings, unsupported protocol versions, operation mismatch, missing authentication/dependencies/source, changed project/source, HTTP authentication/access/model/validation/limit failures, network timeout/error, invalid service response, import failure, bootstrap failure, missing terminal response, and local interruption.

## Timeout and uncertain outcomes

Requests use **10 seconds for connection** and **300 seconds for read inactivity**. These are passed as a Requests timeout tuple. As explained in the [Requests documentation](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts), read inactivity is not a total download deadline. DNS, uploading, slow trickling responses, and local processing are not bounded by a single overall application deadline.

Automatic retries and redirects are disabled. On network timeout or interruption after submission, the server may have processed the request and consumed credits. The UI reports this uncertainty and does not retry. Saved audio is retained after import errors; retrying generation is not required to recover that file.

Networking still executes synchronously inside the REAPER action. The UI cannot offer responsive cancellation during a blocked call. Caught local interruptions have a `cancelled` terminal state, but cannot guarantee server-side cancellation. External execution, polling, process monitoring, and responsive cancellation are outside the current implementation. Python owns audio importing; request correlation and error handling protect the synchronous workflow. Forcefully terminating REAPER cannot guarantee a final response.

## REAPER changes and normalization

In item mode, audio is saved beside the original source with a unique UUID filename. In cursor mode, audio is saved in the project's `ElevenReaperLab_Audio` folder and inserted on `TTS` at the captured cursor, without muting an existing item. Before an audio API call, Python creates the cursor-mode directory if needed and exclusively reserves a unique output file to check destination availability. Failed or incomplete reservations are removed; completed audio remains recoverable after import failure. Upload MIME types follow the source extension, with an octet-stream fallback. Item-mode TTS uses `TTS`, and voice conversion uses `VOICE_REGEN`; both mute the source item after successful import.

Importing and muting form one Undo block. A successful import leaves only the generated item selected; an import failure restores the previous item selection. Cursor and selected tracks are restored in both cases. Partial timeline changes are left undoable; the app does not automatically undo unrelated work. A saved audio file is not removed if import fails.

Normalization occurs only after a matching completed audio result and only when the request's captured preference enables it. Lua locates the returned item GUID in the original project, verifies its audio path, temporarily selects that item for the existing SWS action, and restores cursor and selections. Missing SWS or changed result references produce a warning without changing successful generation into failure. Normalization has its own Undo block.

Native API signatures were consulted in the [official REAPER API reference](https://www.reaper.fm/sdk/reascript/reascripthelp.html). Actual REAPER behavior, SWS dialogs, Undo, macOS, and live HTTP behavior require separately authorized integration checks.

