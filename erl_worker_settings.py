"""Typed settings and payload preparation for the three REAPER workers.

This module performs no network requests and does not import the REAPER API.
"""

import json
from urllib.parse import quote

from erl_model_registry import ModelRegistry, RegistryError


MODEL_KEYS = {"tts": "model", "sts": "model_sts", "stt": "model_stt"}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError("/settings: duplicate JSON key " + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise RegistryError("/settings: invalid numeric constant " + value)


def load_worker_settings(get_state, operation, catalog=None):
    """Read a complete, operation-specific handoff; never reuse old scalar keys."""
    if operation not in MODEL_KEYS:
        raise RegistryError("/operation: unknown operation")
    if catalog is None:
        raw_catalog = get_state("model_registry_snapshot")
        if not raw_catalog:
            raise RegistryError("/registry: run the updated Eleven Reaper Lab interface first")
        try:
            catalog = ModelRegistry(json.loads(raw_catalog, object_pairs_hook=_unique_pairs,
                                              parse_constant=_reject_constant))
        except (TypeError, ValueError) as error:
            raise RegistryError("/registry: cannot load the interface registry snapshot: " + str(error)) from error
    model_id = get_state(MODEL_KEYS[operation])
    if not model_id:
        raise RegistryError("/model_id: select a model in Eleven Reaper Lab first")
    spec = catalog.operation(operation, model_id)
    expected_input = "text" if operation == "tts" else "audio_file"
    if spec["input_kind"] != expected_input:
        raise RegistryError("/model_id: this worker does not support the selected input kind")
    if catalog.by_id[model_id].get("selection_enabled", True) is not True:
        raise RegistryError("/model_id: this model is currently disabled for selection")
    raw = get_state("settings_" + operation)
    if not raw:
        raise RegistryError("/settings: run the updated Eleven Reaper Lab interface first")
    try:
        settings = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except (TypeError, ValueError) as error:
        raise RegistryError("/settings: cannot decode worker settings: " + str(error)) from error
    settings = catalog.validate_settings(operation, model_id, settings)
    return catalog, model_id, settings


def _parts(catalog, operation, model_id, settings):
    parts = catalog.split_settings(operation, model_id, settings)
    # Integer-valued numeric inputs are accepted by the registry. Encode these
    # fields as JSON integers rather than floating-point values such as 1.0.
    for definition in catalog.parameters(operation, model_id):
        destination = parts[definition["destination"]]
        field = definition["api_field"]
        if definition["type"] == "integer" and field in destination:
            destination[field] = int(destination[field])
    return parts


def create_tts_payload(catalog, model_id, settings, text):
    if not isinstance(text, str) or not text.strip():
        raise RegistryError("/text: enter some text before generating audio")
    spec = catalog.operation("tts", model_id)
    limit = spec.get("limits", {}).get("max_text_characters")
    if limit is not None and len(text) > limit:
        raise RegistryError("/text: exceeds this model's " + str(limit) + " character limit")
    parts = _parts(catalog, "tts", model_id, settings)
    if {"text", "model_id", "voice_settings"}.intersection(parts["request"]):
        raise RegistryError("/settings: parameter collides with a reserved request field")
    result = {"text": text, "model_id": model_id, **parts["request"]}
    if parts["voice_settings"]:
        result["voice_settings"] = parts["voice_settings"]
    return result


def create_multipart_data(catalog, operation, model_id, settings):
    if operation not in ("sts", "stt"):
        raise RegistryError("/operation: multipart settings require sts or stt")
    parts = _parts(catalog, operation, model_id, settings)
    if {"model_id", "voice_settings", "file", "audio"}.intersection(parts["request"]):
        raise RegistryError("/settings: parameter collides with a reserved request field")
    result = {"model_id": model_id}
    for field, value in parts["request"].items():
        result[field] = ("true" if value else "false") if type(value) is bool else str(value)
    if parts["voice_settings"]:
        result["voice_settings"] = json.dumps(parts["voice_settings"], ensure_ascii=False, allow_nan=False)
    return result


def endpoint_url(catalog, operation, model_id, voice_id=None):
    endpoint = catalog.operation(operation, model_id)["endpoint"]
    if "{voice_id}" in endpoint:
        if not isinstance(voice_id, str) or not voice_id.strip():
            raise RegistryError("/voice_id: select a voice before generating audio")
        endpoint = endpoint.replace("{voice_id}", quote(voice_id, safe=""))
    if not endpoint.startswith("/v1/") or "{" in endpoint or "}" in endpoint:
        raise RegistryError("/endpoint: invalid model endpoint")
    return "https://api.elevenlabs.io" + endpoint
