"""Offline model metadata; no REAPER dependency, network calls, or import-time I/O."""

import copy
import json
import math
from pathlib import Path


class RegistryError(ValueError):
    """Invalid catalog, model selection, or settings."""


def _fail(path, message):
    raise RegistryError(f"{path or '/'}: {message}")


def _child(path, key):
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _finite(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _text(value, path):
    if not isinstance(value, str) or not value.strip(" \t\r\n\v\f"):
        _fail(path, "expected a non-empty string")


def _object(value, path):
    if not isinstance(value, dict):
        _fail(path, "expected a JSON object")


def _fields(value, path, allowed):
    _object(value, path)
    for key in value:
        if key not in allowed:
            _fail(_child(path, key), "unknown registry field")


def _strings(value, path, nonempty=False):
    if not isinstance(value, list):
        _fail(path, "expected a JSON array")
    if nonempty and not value:
        _fail(path, "must not be empty")
    seen = set()
    for index, entry in enumerate(value, 1):
        _text(entry, _child(path, index))
        if entry in seen:
            _fail(_child(path, index), "duplicate entry")
        seen.add(entry)


def _check_value(definition, value, path):
    kind = definition["type"]
    if kind in ("number", "integer"):
        if not _finite(value):
            _fail(path, "expected a finite " + kind)
        if kind == "integer" and value % 1 != 0:
            _fail(path, "expected an integer")
        if "minimum" in definition and value < definition["minimum"]:
            _fail(path, "value is below minimum " + str(definition["minimum"]))
        if "maximum" in definition and value > definition["maximum"]:
            _fail(path, "value exceeds maximum " + str(definition["maximum"]))
    elif kind == "boolean" and type(value) is not bool:
        _fail(path, "expected a boolean")
    elif kind == "string" and not isinstance(value, str):
        _fail(path, "expected a string")
    if kind == "string":
        if "min_length" in definition and len(value) < definition["min_length"]:
            _fail(path, "string is too short")
        if "max_length" in definition and len(value) > definition["max_length"]:
            _fail(path, "string is too long")
    if "enum" in definition:
        if not any(value == allowed and (type(value) is type(allowed) or
                   (_finite(value) and _finite(allowed))) for allowed in definition["enum"]):
            _fail(path, "value is not in the allowed set")


def _merge_definition(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key == "ui":
            _object(value, "/parameter_override/ui")
            result["ui"].update(copy.deepcopy(value))
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_json(path):
    def pairs_hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail(_child("", key), "duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        _fail("", "invalid JSON numeric constant " + value)

    def inspect(value, path="", depth=0):
        if depth > 64:
            _fail(path, "configuration nesting is too deep")
        if value is None:
            _fail(path, "null is not supported; omit optional fields instead")
        if isinstance(value, dict):
            for key, entry in value.items():
                inspect(entry, _child(path, key), depth + 1)
        elif isinstance(value, list):
            for index, entry in enumerate(value, 1):
                inspect(entry, _child(path, index), depth + 1)

    try:
        with Path(path).open("rb") as source:
            content = source.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            _fail("", "JSON file exceeds the 1 MiB size limit")
        value = json.loads(content.decode("utf-8-sig"), object_pairs_hook=pairs_hook,
                           parse_constant=reject_constant)
        inspect(value)
        return value
    except RegistryError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise RegistryError("Cannot read registry JSON: " + str(error)) from error


class ModelRegistry:
    OPERATIONS = {"tts", "sts", "stt"}
    DEFINITION_FIELDS = {"label", "type", "api_field", "destination", "api_required", "minimum",
                         "maximum", "min_length", "max_length", "enum", "suggested_default",
                         "description", "ui", "source_refs"}

    def __init__(self, data):
        self.data = copy.deepcopy(data)
        self.by_id = {}
        try:
            self._validate_catalog()
        except (KeyError, TypeError, OverflowError) as error:
            raise RegistryError("/: malformed registry metadata: " + str(error)) from error

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path is not None else Path(__file__).resolve().with_name("erl_models.json")
        return cls(_read_json(path))

    def _references(self, value, path):
        _strings(value, path, True)
        for index, key in enumerate(value, 1):
            if key not in self.data["sources"]:
                _fail(_child(path, index), "unknown source reference")

    def _definition(self, value, path, partial=False):
        _fields(value, path, self.DEFINITION_FIELDS)
        if partial:
            return
        for field in ("label", "api_field", "description"):
            _text(value.get(field), path + "/" + field)
        if value.get("type") not in {"number", "integer", "boolean", "string"}:
            _fail(path + "/type", "unknown parameter type")
        if value.get("destination") not in {"request", "voice_settings"}:
            _fail(path + "/destination", "expected request or voice_settings")
        if type(value.get("api_required")) is not bool:
            _fail(path + "/api_required", "expected a boolean")
        for field in ("minimum", "maximum", "min_length", "max_length"):
            if field in value and not _finite(value[field]):
                _fail(path + "/" + field, "expected a finite number")
        if "minimum" in value and "maximum" in value and value["minimum"] > value["maximum"]:
            _fail(path, "invalid numeric interval")
        for field in ("min_length", "max_length"):
            if field in value and (value[field] < 0 or value[field] % 1 != 0):
                _fail(path + "/" + field, "expected a non-negative integer")
        if "min_length" in value and "max_length" in value and value["min_length"] > value["max_length"]:
            _fail(path, "invalid string length interval")
        if "enum" in value:
            if not isinstance(value["enum"], list) or not value["enum"]:
                _fail(path + "/enum", "expected a non-empty JSON array")
            without_enum = {key: entry for key, entry in value.items() if key != "enum"}
            seen = set()
            for index, entry in enumerate(value["enum"], 1):
                _check_value(without_enum, entry, _child(path + "/enum", index))
                key = ("number" if _finite(entry) else type(entry).__name__, entry)
                if key in seen:
                    _fail(path + "/enum", "duplicate allowed value")
                seen.add(key)
        ui = value.get("ui")
        _fields(ui, path + "/ui", {"control", "step", "minimum", "maximum", "labels"})
        if ui.get("control") not in {"slider", "integer", "checkbox", "select", "text"}:
            _fail(path + "/ui/control", "unknown UI control")
        for field in ("minimum", "maximum", "step"):
            if field in ui and not _finite(ui[field]):
                _fail(path + "/ui/" + field, "expected a finite number")
        if "step" in ui and ui["step"] <= 0:
            _fail(path + "/ui/step", "expected a positive step")
        ui_min, ui_max = ui.get("minimum", value.get("minimum")), ui.get("maximum", value.get("maximum"))
        if ui_min is not None and ui_max is not None and ui_min > ui_max:
            _fail(path + "/ui", "invalid numeric interval")
        if "minimum" in value and ui_min is not None and ui_min < value["minimum"]:
            _fail(path + "/ui/minimum", "outside API interval")
        if "maximum" in value and ui_max is not None and ui_max > value["maximum"]:
            _fail(path + "/ui/maximum", "outside API interval")
        if "labels" in ui:
            _strings(ui["labels"], path + "/ui/labels", True)
            if "enum" not in value or len(ui["labels"]) != len(value["enum"]):
                _fail(path + "/ui/labels", "must match enum length")
        self._references(value.get("source_refs"), path + "/source_refs")
        if "suggested_default" in value:
            _check_value(value, value["suggested_default"], path + "/suggested_default")

    def _validate_catalog(self):
        data = self.data
        _fields(data, "", {"registry_version", "reviewed_on", "sources", "notes", "runtime_defaults",
                            "legacy_worker_parameters", "parameters", "models"})
        if type(data.get("registry_version")) not in (int, float) or data["registry_version"] != 1:
            _fail("/registry_version", "unsupported registry version")
        _text(data.get("reviewed_on"), "/reviewed_on")
        _object(data.get("sources"), "/sources")
        for key, url in data["sources"].items():
            _text(url, _child("/sources", key))
            if not url.startswith("https://"):
                _fail(_child("/sources", key), "expected an HTTPS source URL")
        _strings(data.get("notes"), "/notes")
        _object(data.get("parameters"), "/parameters")
        for key, value in data["parameters"].items():
            self._definition(value, _child("/parameters", key))
        if not isinstance(data.get("models"), list) or not data["models"]:
            _fail("/models", "expected a non-empty model array")
        for index, model in enumerate(data["models"], 1):
            path = _child("/models", index)
            _fields(model, path, {"id", "name", "reviewed_on", "source_refs", "notes", "operations",
                                  "selection_enabled", "selection_note"})
            for field in ("id", "name", "reviewed_on"):
                _text(model.get(field), path + "/" + field)
            if "selection_enabled" in model and type(model["selection_enabled"]) is not bool:
                _fail(path + "/selection_enabled", "expected a boolean")
            if "selection_note" in model:
                _text(model["selection_note"], path + "/selection_note")
            if model["id"] in self.by_id:
                _fail(path + "/id", "duplicate model ID")
            self._references(model.get("source_refs"), path + "/source_refs")
            _strings(model.get("notes"), path + "/notes")
            _object(model.get("operations"), path + "/operations")
            if not model["operations"]:
                _fail(path + "/operations", "at least one operation is required")
            for operation, spec in model["operations"].items():
                spec_path = _child(path + "/operations", operation)
                if operation not in self.OPERATIONS:
                    _fail(spec_path, "unknown operation")
                _fields(spec, spec_path, {"input_kind", "transport", "endpoint", "parameters",
                                          "required_settings", "constraints", "limits"})
                expected_input = "text" if operation == "tts" else "audio_file"
                if spec.get("input_kind") != expected_input and not (
                        operation == "stt" and spec.get("input_kind") == "audio_stream"):
                    _fail(spec_path + "/input_kind", "incompatible input kind")
                transport = {"text": "json", "audio_file": "multipart", "audio_stream": "websocket"}[spec["input_kind"]]
                if spec.get("transport") != transport:
                    _fail(spec_path + "/transport", "incompatible transport")
                _text(spec.get("endpoint"), spec_path + "/endpoint")
                _object(spec.get("parameters"), spec_path + "/parameters")
                destinations = set()
                for key, override in spec["parameters"].items():
                    parameter_path = _child(spec_path + "/parameters", key)
                    if key not in data["parameters"]:
                        _fail(parameter_path, "unknown parameter reference")
                    self._definition(override, parameter_path, True)
                    merged = _merge_definition(data["parameters"][key], override)
                    self._definition(merged, parameter_path)
                    destination = (merged["destination"], merged["api_field"])
                    if destination in destinations:
                        _fail(parameter_path, "duplicate API destination")
                    destinations.add(destination)
                _strings(spec.get("required_settings"), spec_path + "/required_settings")
                for key in spec["required_settings"]:
                    if key not in spec["parameters"]:
                        _fail(spec_path + "/required_settings", "unknown parameter reference")
                if "limits" in spec:
                    _fields(spec["limits"], spec_path + "/limits", {"max_text_characters"})
                    limit = spec["limits"].get("max_text_characters")
                    if not _finite(limit) or limit <= 0 or limit % 1:
                        _fail(spec_path + "/limits", "expected a positive character limit")
                if not isinstance(spec.get("constraints"), list):
                    _fail(spec_path + "/constraints", "expected a JSON array")
                for rule_index, rule in enumerate(spec["constraints"], 1):
                    rule_path = _child(spec_path + "/constraints", rule_index)
                    _fields(rule, rule_path, {"setting", "requires", "conflicts"})
                    if rule.get("setting") not in spec["parameters"]:
                        _fail(rule_path + "/setting", "unknown parameter reference")
                    _object(rule.get("requires"), rule_path + "/requires")
                    for key, expected in rule["requires"].items():
                        if key not in spec["parameters"]:
                            _fail(rule_path + "/requires", "unknown parameter reference")
                        _check_value(_merge_definition(data["parameters"][key], spec["parameters"][key]),
                                     expected, _child(rule_path + "/requires", key))
                    _strings(rule.get("conflicts"), rule_path + "/conflicts")
                    if any(key not in spec["parameters"] for key in rule["conflicts"]):
                        _fail(rule_path + "/conflicts", "unknown parameter reference")
            self.by_id[model["id"]] = model
        _fields(data.get("runtime_defaults"), "/runtime_defaults", self.OPERATIONS)
        _fields(data.get("legacy_worker_parameters"), "/legacy_worker_parameters", self.OPERATIONS)
        for operation in sorted(self.OPERATIONS):
            model_id = data["runtime_defaults"].get(operation)
            _text(model_id, "/runtime_defaults/" + operation)
            spec = self.operation(operation, model_id)
            if spec["input_kind"] == "audio_stream":
                _fail("/runtime_defaults/" + operation, "current runtime requires file input")
            path = "/legacy_worker_parameters/" + operation
            values = data["legacy_worker_parameters"].get(operation)
            _strings(values, path)
            if any(key not in spec["parameters"] for key in values):
                _fail(path, "unknown runtime parameter")

    def operation(self, operation, model_id):
        if operation not in self.OPERATIONS:
            _fail("/operation", "unknown operation")
        if model_id not in self.by_id:
            _fail("/model_id", "unknown model; its configuration is inactive")
        if operation not in self.by_id[model_id]["operations"]:
            _fail("/model_id", "model is incompatible with operation " + operation)
        return copy.deepcopy(self.by_id[model_id]["operations"][operation])

    def status(self, operation, model_id, discovered_models=None):
        """Inspect optional discovery data without fetching it or assuming account access."""
        model = self.by_id.get(model_id)
        result = {"known": model is not None, "compatible": model is not None and operation in model["operations"],
                  "account_availability": "unknown", "discovery_status": "not_checked", "api_capability": "unknown"}
        if discovered_models is None:
            return result
        if not isinstance(discovered_models, list):
            _fail("/discovered_models", "expected a model list")
        if operation == "stt":
            result["discovery_status"] = "not_applicable"
            return result
        if operation not in self.OPERATIONS:
            _fail("/operation", "unknown operation")
        result["discovery_status"] = "not_listed"
        for entry in discovered_models:
            _object(entry, "/discovered_models")
            if entry.get("model_id") == model_id:
                result["discovery_status"] = "listed"
                key = "can_do_text_to_speech" if operation == "tts" else "can_do_voice_conversion"
                if entry.get(key) is not None:
                    if type(entry[key]) is not bool:
                        _fail("/discovered_models", "capability flag must be boolean")
                    result["api_capability"] = "supported" if entry[key] else "unsupported"
                break
        return result

    def list_models(self, operation, input_kind=None):
        if operation not in self.OPERATIONS:
            _fail("/operation", "unknown operation")
        return [copy.deepcopy(model) for model in self.data["models"] if operation in model["operations"]
                and (input_kind is None or model["operations"][operation]["input_kind"] == input_kind)]

    def parameters(self, operation, model_id):
        spec = self.operation(operation, model_id)
        return [dict(_merge_definition(self.data["parameters"][key], spec["parameters"][key]), id=key)
                for key in sorted(spec["parameters"])]

    def validate_settings(self, operation, model_id, settings, partial=False, path="/settings"):
        spec = self.operation(operation, model_id)
        _object(settings, path)
        for key in sorted(settings, key=str):
            if key not in spec["parameters"]:
                _fail(_child(path, key), "parameter is not registered for this model and operation")
            _check_value(_merge_definition(self.data["parameters"][key], spec["parameters"][key]),
                         settings[key], _child(path, key))
        if not partial:
            for key in spec["required_settings"]:
                if key not in settings:
                    _fail(_child(path, key), "required application setting is missing")
            for rule in spec["constraints"]:
                if rule["setting"] in settings:
                    for key, expected in rule["requires"].items():
                        actual = settings.get(key)
                        if type(actual) is not type(expected) or actual != expected:
                            _fail(_child(path, rule["setting"]), "requires " + key + " = " + str(expected).lower())
                    for key in rule["conflicts"]:
                        if key in settings:
                            _fail(_child(path, rule["setting"]), "cannot be combined with " + key)
        return copy.deepcopy(settings)

    def compose(self, operation, model_id, layers, path="/effective_settings"):
        result = {}
        for index, layer in enumerate(layers, 1):
            self.validate_settings(operation, model_id, layer, True, _child(path, index))
            result.update(layer)
        return self.validate_settings(operation, model_id, result, False, path)

    def resolve(self, config, profile_id, voice_id, operation, model_id, manual=None):
        if not any(voice["id"] == voice_id for voice in config["voices"]):
            _fail("/voice_id", "unknown voice identifier")
        profile = next((entry for entry in config["profiles"] if entry["id"] == profile_id), None)
        if profile is None:
            _fail("/profile_id", "unknown profile identifier")

        def values(settings):
            return settings.get(operation, {}).get(model_id, {})

        return self.compose(operation, model_id, [values(config["model_defaults"]),
                            values(profile.get("settings", {})),
                            values(profile.get("voice_overrides", {}).get(voice_id, {})),
                            {} if manual is None else manual])

    def split_settings(self, operation, model_id, settings):
        values = self.validate_settings(operation, model_id, settings)
        spec = self.operation(operation, model_id)
        result = {"request": {}, "voice_settings": {}}
        for key, value in values.items():
            definition = _merge_definition(self.data["parameters"][key], spec["parameters"][key])
            result[definition["destination"]][definition["api_field"]] = value
        return result
