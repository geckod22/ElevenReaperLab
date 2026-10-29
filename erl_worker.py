"""Synchronous REAPER adapter for protocol v2; external execution remains on hold."""

import mimetypes
import os
import re
import uuid

from erl_protocol import (WorkerError, decode_json, terminal_response, validate_request)
from erl_model_registry import RegistryError
from erl_worker_settings import create_multipart_data, create_tts_payload, endpoint_url

SECTION = "eleven_reaper_lab"


def item_guid(reaper, item):
    return reaper.RPR_GetSetMediaItemInfo_String(item, "GUID", "", False)[3]


def project_token(reaper):
    # Python's GetTrackGUID returns a GUID* pointer, unlike Lua's GUID text.
    # Use the string API to compare the same value across both languages.
    result = reaper.RPR_GetSetMediaTrackInfo_String(reaper.RPR_GetMasterTrack(0), "GUID", "", False)
    if not result[0] or not isinstance(result[3], str) or not result[3]:
        raise WorkerError("PROJECT_CONTEXT_UNAVAILABLE", "Cannot read the active project's master-track GUID.")
    return result[3]


def source_item(reaper, request):
    context = request["context"]
    if reaper.RPR_GetExtState(SECTION, "active_request_id") != request["request_id"]:
        raise WorkerError("LOCALLY_CANCELLED", "This request is no longer active; its result will not be imported.")
    if project_token(reaper) != context["project_master_guid"]:
        raise WorkerError("PROJECT_CHANGED", "The original REAPER project is no longer active.")
    if context["insertion_mode"] == "cursor":
        project_file = reaper.RPR_EnumProjects(-1, "", 32768)[2]
        if not project_file or os.path.normcase(os.path.abspath(project_file)) != os.path.normcase(os.path.abspath(context["project_file"])):
            raise WorkerError("PROJECT_CHANGED", "The saved project path changed after the request was prepared.")
        if not os.path.isfile(project_file):
            raise WorkerError("PROJECT_UNSAVED", "Save the project before generating audio without a selected item.")
        return None
    for index in range(reaper.RPR_CountMediaItems(0)):
        item = reaper.RPR_GetMediaItem(0, index)
        if item_guid(reaper, item) != context["item_guid"]:
            continue
        take = reaper.RPR_GetActiveTake(item)
        if not reaper.RPR_ValidatePtr2(0, take, "MediaItem_Take*") or reaper.RPR_TakeIsMIDI(take):
            break
        path = reaper.RPR_GetMediaSourceFileName(reaper.RPR_GetMediaItemTake_Source(take), "", 32768)[1]
        position = reaper.RPR_GetMediaItemInfo_Value(item, "D_POSITION")
        if os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(request["input"]["audio_path"])):
            raise WorkerError("SOURCE_CHANGED", "The source audio changed after the request was prepared.")
        if abs(position - context["position"]) > .000001:
            raise WorkerError("SOURCE_CHANGED", "The source item moved after the request was prepared.")
        if not os.path.isfile(path):
            raise WorkerError("SOURCE_MISSING", "The source audio file is unavailable.")
        return item
    raise WorkerError("SOURCE_MISSING", "The original audio item is unavailable.")


def import_audio(reaper, request, audio_path):
    item = source_item(reaper, request)
    items = [reaper.RPR_GetSelectedMediaItem(0, index)
             for index in range(reaper.RPR_CountSelectedMediaItems(0))]
    tracks = [reaper.RPR_GetMasterTrack(0)] + [reaper.RPR_GetTrack(0, index)
              for index in range(reaper.RPR_CountTracks(0))]
    selected_tracks = [track for track in tracks if reaper.RPR_IsTrackSelected(track)]
    cursor = reaper.RPR_GetCursorPosition()
    track_name = request["output"]["track_name"]
    imported_item = None
    reaper.RPR_Undo_BeginBlock2(0)
    try:
        target = None
        for index in range(reaper.RPR_CountTracks(0)):
            track = reaper.RPR_GetTrack(0, index)
            if reaper.RPR_GetSetMediaTrackInfo_String(track, "P_NAME", "", False)[3] == track_name:
                target = track
                break
        if target is None:
            index = reaper.RPR_CountTracks(0)
            reaper.RPR_InsertTrackAtIndex(index, True)
            target = reaper.RPR_GetTrack(0, index)
            reaper.RPR_GetSetMediaTrackInfo_String(target, "P_NAME", track_name, True)
        reaper.RPR_Main_OnCommand(40289, 0)  # Unselect all items for controlled import.
        reaper.RPR_SetOnlyTrackSelected(target)
        reaper.RPR_SetEditCurPos(request["context"]["position"], False, False)
        reaper.RPR_InsertMedia(audio_path, 0)
        if reaper.RPR_CountSelectedMediaItems(0) != 1:
            raise WorkerError("IMPORT_FAILED", "REAPER did not select exactly one imported item.")
        generated = reaper.RPR_GetSelectedMediaItem(0, 0)
        if reaper.RPR_GetMediaItemTrack(generated) != target or generated == item:
            raise WorkerError("IMPORT_FAILED", "REAPER did not import audio on the requested track.")
        result = {"kind": "audio", "audio_path": audio_path,
                  "item_guid": item_guid(reaper, generated),
                  "project_master_guid": request["context"]["project_master_guid"]}
        if not result["item_guid"]:
            raise WorkerError("IMPORT_FAILED", "Cannot identify the imported audio item.")
        if item is not None and not reaper.RPR_SetMediaItemInfo_Value(item, "B_MUTE", 1):
            raise WorkerError("IMPORT_FAILED", "The imported audio is available but the source could not be muted.")
        imported_item = generated
        return result
    finally:
        try:
            reaper.RPR_SetEditCurPos(cursor, False, False)
            reaper.RPR_Main_OnCommand(40289, 0)
            # Keep successful output selected; restore the input selection on failure.
            for selected in ([imported_item] if imported_item is not None else items):
                if reaper.RPR_ValidatePtr2(0, selected, "MediaItem*"):
                    reaper.RPR_SetMediaItemSelected(selected, True)
            for index in range(reaper.RPR_CountTracks(0)):
                reaper.RPR_SetTrackSelected(reaper.RPR_GetTrack(0, index), False)
            reaper.RPR_SetTrackSelected(reaper.RPR_GetMasterTrack(0), False)
            for track in selected_tracks:
                if reaper.RPR_ValidatePtr2(0, track, "MediaTrack*"):
                    reaper.RPR_SetTrackSelected(track, True)
        finally:
            reaper.RPR_Undo_EndBlock2(0, "Eleven Reaper Lab: " + request["operation"].upper(), -1)
            reaper.RPR_UpdateArrange()


def perform_request(reaper, request, catalog, api_key, logs, state):
    # Import inside the guarded action so a missing dependency produces a terminal error.
    try:
        import requests
    except ImportError:
        raise WorkerError("DEPENDENCY_MISSING", "Install requests in REAPER's Python environment.") from None
    source_item(reaper, request)
    operation, model = request["operation"], request["model_id"]
    url = endpoint_url(catalog, operation, model, request.get("voice_id"))
    headers = {"xi-api-key": api_key}
    timeout = (request["timeouts"]["connect"], request["timeouts"]["read"])
    response = None
    audio_output = None
    try:
        if operation != "stt":
            if request["context"]["insertion_mode"] == "cursor":
                directory = request["output"]["directory"]
                base_path = os.path.join(directory, "TTS")
            else:
                base_path = os.path.splitext(request["input"]["audio_path"])[0] + "_" + operation.upper()
                directory = os.path.dirname(base_path)
            output_path = base_path + "_" + uuid.uuid4().hex + ".mp3"
            try:
                if request["context"]["insertion_mode"] == "cursor":
                    os.makedirs(directory, exist_ok=True)
                # Reserve this unique output before spending credits. Hold the
                # file handle until the result is written; never overwrite input.
                audio_output = open(output_path, "xb")
            except OSError:
                raise WorkerError("OUTPUT_UNAVAILABLE", "Cannot create the generated audio file in the displayed output folder.") from None
            state["audio_path"] = output_path
        logs.append({"level": "info", "message": "Sending " + operation.upper() + " request using " + model + "."})
        # Explicitly disable redirects and retries: a generation must never be replayed.
        if operation == "tts":
            payload = create_tts_payload(catalog, model, request["settings"], request["input"]["text"])
            state["api_attempted"] = True
            response = requests.post(url, headers=headers, json=payload, timeout=timeout, allow_redirects=False)
        else:
            data = create_multipart_data(catalog, operation, model, request["settings"])
            source_path = request["input"]["audio_path"]
            mime = mimetypes.guess_type(source_path)[0] or "application/octet-stream"
            with open(source_path, "rb") as source:
                files = {"file" if operation == "stt" else "audio": (os.path.basename(source_path), source, mime)}
                state["api_attempted"] = True
                response = requests.post(url, headers=headers, data=data, files=files,
                                         timeout=timeout, allow_redirects=False)
        state["http_received"] = True
        status = response.status_code
        if status != 200:
            code = {401: "AUTHENTICATION_FAILED", 403: "ACCESS_DENIED", 404: "MODEL_OR_VOICE_UNAVAILABLE",
                    422: "SERVICE_VALIDATION_FAILED", 429: "SERVICE_LIMIT"}.get(status, "HTTP_ERROR")
            raise WorkerError(code, "ElevenLabs returned HTTP " + str(status) + ". No automatic retry was made.",
                              status >= 500, http_status=status)
        if operation == "tts":
            # Billing metadata belongs to the service request, even if import later fails.
            credit_cost = response.headers.get("character-cost")
            if (isinstance(credit_cost, str) and len(credit_cost) <= 64
                    and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", credit_cost.strip())):
                logs.append({"level": "info", "message": "Text to Speech credits used: " + credit_cost.strip() + "."})
            else:
                logs.append({"level": "info", "message": "Text to Speech credit usage unavailable."})
        if operation == "stt":
            try:
                data = response.json()
            except ValueError:
                raise WorkerError("INVALID_RESPONSE", "ElevenLabs returned invalid transcription JSON.", True) from None
            if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                raise WorkerError("INVALID_RESPONSE", "ElevenLabs returned no transcription text.", True)
            source_item(reaper, request)
            return {"kind": "text", "text": data["text"]}
        content_type = response.headers.get("Content-Type", "").lower().split(";", 1)[0]
        if not response.content or not (content_type.startswith("audio/") or content_type == "application/octet-stream"):
            raise WorkerError("INVALID_RESPONSE", "ElevenLabs returned no valid audio response.", True)
        audio_output.write(response.content)
        audio_output.close()
        audio_output = None
        state["audio_saved"] = True
        logs.append({"level": "info", "message": "Audio saved; importing into REAPER."})
        return import_audio(reaper, request, state["audio_path"])
    except requests.exceptions.Timeout:
        raise WorkerError("NETWORK_TIMEOUT", "Network timeout. ElevenLabs may have processed the request; do not retry without checking.", True) from None
    except requests.exceptions.RequestException:
        raise WorkerError("NETWORK_ERROR", "Network communication failed. The server outcome may be uncertain; no automatic retry was made.", True) from None
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            try:
                if audio_output is not None:
                    audio_output.close()
            finally:
                # Remove only our own incomplete reservation. Completed audio
                # remains recoverable when REAPER import fails.
                if state.get("audio_path") and not state.get("audio_saved"):
                    try:
                        os.remove(state["audio_path"])
                    except OSError:
                        pass


def run_worker(operation, reaper):
    request, logs, state = {}, [], {}
    status, result, error = "failed", None, None
    try:
        raw = reaper.RPR_GetExtState(SECTION, "request_json")
        request = decode_json(raw)
        if reaper.RPR_GetExtState(SECTION, "active_request_id") != request.get("request_id"):
            return  # Stale invocations must not overwrite another request's response.
        reaper.RPR_DeleteExtState(SECTION, "request_json", False)
        catalog = validate_request(request, operation)
        api_key = reaper.RPR_GetExtState(SECTION, "request_api_key")
        reaper.RPR_DeleteExtState(SECTION, "request_api_key", False)
        if not api_key.strip():
            raise WorkerError("AUTHENTICATION_MISSING", "Enter an ElevenLabs API key.")
        result = perform_request(reaper, request, catalog, api_key, logs, state)
        status = "completed"
        logs.append({"level": "info", "message": operation.upper() + " completed."})
    except WorkerError as failure:
        error = failure
        if failure.code == "LOCALLY_CANCELLED":
            status = "cancelled"
    except RegistryError:
        error = WorkerError("INVALID_SETTINGS", "The model settings or text do not satisfy the request rules.",
                            bool(state.get("api_attempted")))
    except (KeyboardInterrupt, SystemExit):
        status = "cancelled"
        error = WorkerError("LOCALLY_CANCELLED", "The local action was interrupted; this does not cancel a server-side generation.",
                            bool(state.get("api_attempted")))
    except Exception:
        # Exception strings and response bodies may contain credentials or input text.
        error = WorkerError("WORKER_ERROR", "The worker could not complete the operation. No automatic retry was made.",
                            bool(state.get("api_attempted")))
    finally:
        active_id = reaper.RPR_GetExtState(SECTION, "active_request_id")
        if request.get("request_id") == active_id and active_id:
            reaper.RPR_DeleteExtState(SECTION, "request_api_key", False)
            if error and state.get("audio_saved"):
                error.details["audio_path"] = state["audio_path"]
                error.details["audio_saved"] = True
                error.message += " Generated audio was saved and can be recovered from the reported path."
            reaper.RPR_SetExtState(SECTION, "response_json",
                terminal_response(request, status, result=result, error=error, logs=logs), False)
