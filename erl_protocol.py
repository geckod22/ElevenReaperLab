"""Versioned request/response contracts; no REAPER dependency or networking."""

import json
import math
import os

from erl_model_registry import ModelRegistry, RegistryError
from erl_worker_settings import _reject_constant, _unique_pairs

VERSION = 2
OPERATIONS = {"tts", "sts", "stt"}


class WorkerError(Exception):
    def __init__(self, code, message, outcome_uncertain=False, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.outcome_uncertain = outcome_uncertain
        self.details = details


def decode_json(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4 * 1024 * 1024:
        raise WorkerError("INVALID_REQUEST", "Request JSON is missing or exceeds the size limit.")
    try:
        data = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except (TypeError, ValueError):
        raise WorkerError("INVALID_REQUEST", "Request JSON is malformed.") from None
    if not isinstance(data, dict):
        raise WorkerError("INVALID_REQUEST", "Request must be a JSON object.")
    return data


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_request(data, expected_operation):
    if type(data.get("protocol_version")) is not int or data["protocol_version"] != VERSION:
        raise WorkerError("PROTOCOL_VERSION", "Unsupported request protocol version.")
    if not _text(data.get("request_id")) or len(data["request_id"]) > 128:
        raise WorkerError("INVALID_REQUEST", "Request ID is missing or invalid.")
    if not isinstance(data.get("operation"), str) or data["operation"] not in OPERATIONS or data["operation"] != expected_operation:
        raise WorkerError("OPERATION_MISMATCH", "Request does not match this worker action.")
    for name in ("settings", "input", "context", "output", "timeouts"):
        if not isinstance(data.get(name), dict):
            raise WorkerError("INVALID_REQUEST", "Request field must be an object: " + name)
    context, source, output = data["context"], data["input"], data["output"]
    if not _text(context.get("project_master_guid")):
        raise WorkerError("INVALID_CONTEXT", "The project reference is missing.")
    mode = context.get("insertion_mode")
    if mode not in ("item", "cursor"):
        raise WorkerError("INVALID_CONTEXT", "An explicit item or cursor insertion mode is required.")
    position = context.get("position")
    if type(position) not in (int, float) or not math.isfinite(position):
        raise WorkerError("INVALID_CONTEXT", "Source position must be a finite number.")
    if mode == "item":
        if not _text(context.get("item_guid")) or not _text(source.get("audio_path")):
            raise WorkerError("INVALID_INPUT", "The source audio path or item reference is missing.")
    else:
        if expected_operation != "tts":
            raise WorkerError("INVALID_INPUT", "This operation requires a selected source audio item.")
        project_file = context.get("project_file")
        if not _text(project_file) or not os.path.isabs(project_file):
            raise WorkerError("PROJECT_UNSAVED", "Save the project before generating audio without a selected item.")
        expected_directory = os.path.join(os.path.dirname(project_file), "ElevenReaperLab_Audio")
        directory = output.get("directory")
        if not _text(directory) or os.path.normcase(os.path.abspath(directory)) != os.path.normcase(os.path.abspath(expected_directory)):
            raise WorkerError("INVALID_OUTPUT", "The output folder must be ElevenReaperLab_Audio inside the saved project directory.")
        if "item_guid" in context or "audio_path" in source:
            raise WorkerError("INVALID_CONTEXT", "Cursor generation must not reference a source audio item.")
    if expected_operation == "tts" and not _text(source.get("text")):
        raise WorkerError("INVALID_INPUT", "Enter text before generating audio.")
    if expected_operation != "stt" and not _text(data.get("voice_id")):
        raise WorkerError("INVALID_INPUT", "A voice is required for audio generation.")
    if type(output.get("normalize")) is not bool:
        raise WorkerError("INVALID_REQUEST", "Normalization preference must be a boolean.")
    expected_track = {"tts": "TTS", "sts": "VOICE_REGEN", "stt": ""}[expected_operation]
    if output.get("track_name") != expected_track:
        raise WorkerError("INVALID_REQUEST", "Unexpected output track for this operation.")
    for field in ("connect", "read"):
        value = data["timeouts"].get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 600:
            raise WorkerError("INVALID_REQUEST", "Invalid network timeout: " + field)
    try:
        catalog = ModelRegistry(decode_json(data.get("registry_json")))
        model = data.get("model_id")
        spec = catalog.operation(expected_operation, model)
        if spec["input_kind"] != ("text" if expected_operation == "tts" else "audio_file"):
            raise RegistryError("Unsupported model input kind")
        if catalog.by_id[model].get("selection_enabled", True) is not True:
            raise RegistryError("Model selection is disabled")
        data["settings"] = catalog.validate_settings(expected_operation, model, data["settings"])
    except (RegistryError, TypeError):
        raise WorkerError("INVALID_SETTINGS", "Model, registry snapshot, or settings are invalid.") from None
    return catalog


def terminal_response(identity, status, *, result=None, error=None, logs=()):
    response = {"protocol_version": VERSION, "request_id": identity.get("request_id", ""),
                "operation": identity.get("operation", ""), "status": status, "logs": list(logs)}
    if status == "completed":
        response["result"] = result
    else:
        response["error"] = {"code": error.code, "message": error.message,
                             "outcome_uncertain": error.outcome_uncertain, **error.details}
    return json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
