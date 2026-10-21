"""Configuration and model-registry regressions; execution requires permission."""

import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from erl_model_registry import ModelRegistry, RegistryError

LUA_VERSION = "54"
V2 = "eleven_multilingual_v2"
STS = "eleven_multilingual_sts_v2"
V4 = "eleven_v4"
CONFIG = json.loads((ROOT / "erl_config.example.json").read_text(encoding="utf-8-sig"))
CATALOG = json.loads((ROOT / "erl_models.json").read_text(encoding="utf-8-sig"))


class RegistryConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = importlib.import_module("lupa.lua" + LUA_VERSION).LuaRuntime(unpack_returned_tuples=True)
        cls.reader = cls.lua.eval("dofile")((ROOT / "erl_json.lua").as_posix())
        cls.lua_module = cls.lua.eval("dofile")((ROOT / "erl_model_registry.lua").as_posix())
        cls.config_module = cls.lua.eval("dofile")((ROOT / "erl_config.lua").as_posix())
        cls.py_registry = ModelRegistry.load()
        cls.lua_registry = cls.lua_module.load()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="erl-config-")
        self.addCleanup(self.temp.cleanup)

    def to_lua(self, value):
        if isinstance(value, (dict, list)):
            return self.reader.decode(json.dumps(value, ensure_ascii=False))[0]
        return value

    def normalize(self, value, expected):
        if isinstance(expected, dict):
            self.assertEqual(set(value.keys()), set(expected.keys()))
            return {key: self.normalize(value[key], entry) for key, entry in expected.items()}
        if isinstance(expected, list):
            self.assertEqual(len(value), len(expected))
            return [self.normalize(value[index], entry) for index, entry in enumerate(expected, 1)]
        return value

    def parity(self, method, *args):
        expected = getattr(self.py_registry, method)(*args)
        actual = getattr(self.lua_registry, method)(self.lua_registry, *(self.to_lua(arg) for arg in args))
        self.assertEqual(self.normalize(actual, expected), expected)
        return expected

    def reject_both(self, method, *args, error_path=None):
        with self.assertRaises(RegistryError) as py_error:
            getattr(self.py_registry, method)(*args)
        with self.assertRaises(Exception) as lua_error:
            getattr(self.lua_registry, method)(self.lua_registry, *(self.to_lua(arg) for arg in args))
        if error_path:
            self.assertIn(error_path + ":", str(py_error.exception))
            self.assertIn(error_path + ":", str(lua_error.exception))

    def write(self, value, name="config.json", raw=False):
        path = Path(self.temp.name) / name
        path.write_text(value if raw else json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path.as_posix()

    def load_config(self, value, registry=None, raw=False):
        path = self.write(value, raw=raw)
        registry_path = self.write(registry, "models.json") if registry is not None else None
        return self.config_module.load(path, registry_path)

    def test_python_and_lua_syntax(self):
        for path in list(ROOT.glob("*.py")) + list((ROOT / "tests").glob("*.py")):
            with self.subTest(file=path.name):
                compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
        for path in list(ROOT.glob("*.lua")) + list((ROOT / "lib").glob("*.lua")):
            with self.subTest(file=path.name):
                result = self.lua.eval("loadfile")(path.as_posix())
                self.assertFalse(isinstance(result, tuple), result)

    def test_missing_or_malformed_personal_configuration_has_no_example_fallback(self):
        missing = (Path(self.temp.name) / "erl_config.json").as_posix()
        loaded, error = self.config_module.load(missing)
        self.assertIsNone(loaded)
        self.assertIsInstance(error, str)
        for raw in ('{"schema_version":', '{"schema_version":1,"schema_version":1}'):
            with self.subTest(raw=raw):
                loaded, error = self.load_config(raw, raw=True)
                self.assertIsNone(loaded)
                self.assertIsInstance(error, str)

    def test_catalog_metadata_matches(self):
        self.assertEqual(self.normalize(self.lua_registry.data, CATALOG), CATALOG)
        for operation in ("tts", "sts", "stt"):
            self.parity("list_models", operation)
        for model in CATALOG["models"]:
            for operation in model["operations"]:
                with self.subTest(model=model["id"]):
                    self.parity("parameters", operation, model["id"])
                    self.parity("operation", operation, model["id"])

    def test_file_transcription_excludes_realtime(self):
        entries = self.parity("list_models", "stt", "audio_file")
        self.assertEqual([entry["id"] for entry in entries], ["scribe_v2", "scribe_v1"])

    def test_status_and_offline_discovery(self):
        for operation, model in (("tts", V2), ("sts", V4), ("stt", "scribe_v2"), ("tts", "future_model")):
            self.parity("status", operation, model)
        for flag in (True, False):
            for operation, model, field in (("tts", V2, "can_do_text_to_speech"), ("sts", STS, "can_do_voice_conversion")):
                result = self.parity("status", operation, model, [{"model_id": model, field: flag}])
                self.assertEqual(result["api_capability"], "supported" if flag else "unsupported")
                self.assertEqual(result["account_availability"], "unknown")
        self.parity("status", "tts", V4, [])
        self.parity("status", "tts", V4, [{"model_id": V4}])
        self.parity("status", "stt", "scribe_v2", [])

    def test_unsupported_operation_and_model(self):
        for operation, model in (("sts", V4), ("tts", "scribe_v2"), ("tts", "future_model"), ("unknown", V2)):
            with self.subTest(operation=operation, model=model):
                self.reject_both("operation", operation, model)

    def test_original_presets_and_four_layer_precedence(self):
        loaded, error, warnings = self.load_config(CONFIG)
        self.assertIsNone(error)
        self.assertEqual(len(warnings), 0)
        normal = {"bedtime": (.91, .85, .8), "meditation": (.9, .85, .8), "course": (.97, .8, .75)}
        special = {"bedtime": (1.08, .7, .75), "meditation": (1., .85, .8), "course": (1.1, .8, .75)}
        for profile in CONFIG["profiles"]:
            for voice in CONFIG["voices"]:
                for operation, model in (("tts", V2), ("sts", STS)):
                    with self.subTest(profile=profile["id"], voice=voice["id"], operation=operation):
                        values = (special if voice["id"] == "wise_man_3" else normal)[profile["id"]]
                        expected = dict(zip(("speed", "stability", "similarity_boost"), values))
                        python = self.py_registry.resolve(CONFIG, profile["id"], voice["id"], operation, model)
                        lua = self.config_module.resolve(loaded, profile["id"], voice["id"], operation, model)
                        self.assertEqual(python, expected)
                        self.assertEqual(self.normalize(lua, expected), expected)
                        manual = {"stability": 0, "use_speaker_boost": False}
                        expected.update(manual)
                        python = self.py_registry.resolve(CONFIG, profile["id"], voice["id"], operation, model, manual)
                        lua = self.config_module.resolve(loaded, profile["id"], voice["id"], operation, model, self.to_lua(manual))
                        self.assertEqual(python, expected)
                        self.assertEqual(self.normalize(lua, expected), expected)

    def test_valid_values_and_destinations(self):
        for operation, model, values in (
            ("tts", V2, {"speed": .25, "stability": 0, "similarity_boost": 1, "style": 0, "use_speaker_boost": False, "seed": 4294967295}),
            ("sts", STS, {"speed": 4, "stability": 1, "similarity_boost": 0, "remove_background_noise": False}),
            ("tts", V4, {"stability": 0, "similarity_boost": 0, "seed": 0}),
            ("tts", "eleven_v3", {"stability": .5, "style": 0}),
            ("stt", "scribe_v2", {"language_code": "it", "tag_audio_events": False, "diarize": True, "diarization_threshold": .22, "seed": 2147483647, "temperature": 0, "timestamps_granularity": "none", "no_verbatim": False}),
        ):
            with self.subTest(operation=operation, model=model):
                self.parity("validate_settings", operation, model, values)
                split = self.parity("split_settings", operation, model, values)
                self.assertEqual(set(split), {"request", "voice_settings"})

    def test_types_ranges_enums_and_required_values(self):
        cases = [
            ("tts", V4, {"stability": .5}),
            ("tts", V4, {"stability": .5, "similarity_boost": .75, "speed": 1}),
            ("tts", "eleven_v3", {"stability": .7}),
            ("tts", "eleven_v3", {"stability": .5, "similarity_boost": .75}),
            ("stt", "scribe_v1", {"no_verbatim": True}),
            ("stt", "scribe_v2", {"num_speakers": 1.5}),
            ("stt", "scribe_v2", {"num_speakers": 0}),
            ("stt", "scribe_v2", {"num_speakers": 33}),
            ("stt", "scribe_v2", {"seed": 2147483648}),
            ("stt", "scribe_v2", {"diarize": 0}),
            ("stt", "scribe_v2", {"timestamps_granularity": "sentence"}),
            ("stt", "scribe_v2", {"language_code": "italian"}),
        ]
        for key, value in (("speed", .24), ("speed", 4.01), ("stability", -.01), ("similarity_boost", 1.01),
                           ("speed", "1.0"), ("stability", False), ("use_speaker_boost", 1), ("seed", -1), ("seed", 1.2)):
            values = {"speed": 1, "stability": .5, "similarity_boost": .75, key: value}
            cases.append(("tts", V2, values))
        for operation, model, values in cases:
            with self.subTest(operation=operation, model=model, values=values):
                self.reject_both("validate_settings", operation, model, values)

    def test_no_suggested_default_injection(self):
        self.assertEqual(self.parity("validate_settings", "stt", "scribe_v2", {}), {})
        self.reject_both("validate_settings", "tts", V4, {})
        self.assertEqual(self.parity("validate_settings", "tts", V4, {}, True), {})

    def test_error_paths_match(self):
        self.reject_both("validate_settings", "tts", V4, {"speed": 1}, True, "/custom", error_path="/custom/speed")
        self.reject_both("compose", "tts", V4, [{"speed": 1}], error_path="/effective_settings/1/speed")

    def test_discovery_rejects_wrong_containers(self):
        self.reject_both("status", "tts", V4, {"invalid": True})
        self.reject_both("status", "tts", V4, ["invalid"])
        self.reject_both("status", "tts", V4, [{"model_id": V4, "can_do_text_to_speech": 0}])

    def test_nonfinite_values_and_unicode_length(self):
        for value in (float("inf"), float("-inf"), float("nan")):
            settings = {"speed": value, "stability": .5, "similarity_boost": .75}
            with self.subTest(value=value):
                with self.assertRaises(RegistryError):
                    self.py_registry.validate_settings("tts", V2, settings)
                with self.assertRaises(Exception):
                    self.lua_registry.validate_settings(self.lua_registry, "tts", V2, self.lua.table_from(settings))
        self.parity("validate_settings", "stt", "scribe_v2", {"language_code": "é😀"})

    def test_returned_metadata_is_independent(self):
        python = self.py_registry.operation("tts", V2)
        lua = self.lua_registry.operation(self.lua_registry, "tts", V2)
        python["parameters"]["speed"]["maximum"] = 99
        lua.parameters.speed.maximum = 99
        self.assertNotIn("maximum", self.py_registry.operation("tts", V2)["parameters"]["speed"])
        self.assertIsNone(self.lua_registry.operation(self.lua_registry, "tts", V2).parameters.speed.maximum)

    def test_constraints_are_checked_after_composition(self):
        self.parity("compose", "stt", "scribe_v2", [{"diarization_threshold": .22}, {"diarize": True}])
        for layers in ([{"diarization_threshold": .22}], [{"diarization_threshold": .22, "diarize": False}],
                       [{"diarization_threshold": .22}, {"diarize": True, "num_speakers": 2}]):
            self.reject_both("compose", "stt", "scribe_v2", layers)
        self.reject_both("compose", "tts", V4, [{"speed": 1}, {"stability": .5, "similarity_boost": .75}])

    def test_unknown_config_is_retained_but_inactive(self):
        candidate = copy.deepcopy(CONFIG)
        candidate["model_defaults"]["tts"]["future_model"] = {"flag": False, "setting": 0}
        loaded, error, warnings = self.load_config(candidate)
        self.assertIsNone(error)
        self.assertIn("unknown model", warnings[1])
        self.assertIs(loaded.model_defaults.tts.future_model.flag, False)
        with self.assertRaises(Exception):
            self.config_module.resolve(loaded, "bedtime", "wise_man_2", "tts", "future_model")

    def test_new_known_model_config_and_supported_parameters(self):
        candidate = copy.deepcopy(CONFIG)
        candidate["model_defaults"]["tts"][V4] = {"stability": .5, "similarity_boost": .75}
        candidate["model_defaults"]["tts"][V2]["use_speaker_boost"] = False
        loaded, error, warnings = self.load_config(candidate)
        self.assertIsNone(error)
        self.assertEqual(len(warnings), 0)
        self.assertIs(loaded.model_defaults.tts[V2].use_speaker_boost, False)
        values = self.config_module.resolve(loaded, "course", "wise_woman", "tts", V4)
        self.assertEqual(self.normalize(values, {"stability": .5, "similarity_boost": .75}), {"stability": .5, "similarity_boost": .75})
        candidate["profiles"][0]["settings"]["tts"][V4] = {"speed": 1}
        loaded, error = self.load_config(candidate)
        self.assertIsNone(loaded)
        self.assertIn("/profiles/1/settings/tts/eleven_v4/speed", error)

    def test_effective_validation_checks_every_profile(self):
        candidate = copy.deepcopy(CONFIG)
        candidate["model_defaults"]["tts"].pop(V4, None)
        candidate["profiles"][0]["settings"]["tts"][V4] = {"stability": .5, "similarity_boost": .75}
        loaded, error = self.load_config(candidate)
        self.assertIsNone(loaded)
        self.assertIn("/profiles/2/effective_settings/", error)

    def test_configuration_errors(self):
        mutations = [
            lambda data: data.update(schema_version=2),
            lambda data: data.update(voices={}),
            lambda data: data.update(voices=[]),
            lambda data: data["voices"].append(copy.deepcopy(data["voices"][0])),
            lambda data: data["profiles"][0]["voice_overrides"].update(missing_voice={}),
            lambda data: data["model_defaults"]["tts"].pop(V2),
            lambda data: data["model_defaults"]["tts"][V2].update(speed=4.1),
            lambda data: data["model_defaults"]["tts"].update(scribe_v2={}),
            lambda data: data["preferences"].update(normalize_audio=1),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                candidate = copy.deepcopy(CONFIG)
                mutate(candidate)
                loaded, error = self.load_config(candidate)
                self.assertIsNone(loaded)
                self.assertIsInstance(error, str)

    def test_strict_json_for_both_catalog_loaders(self):
        original = json.dumps(CATALOG)
        texts = [original.replace('"registry_version": 1', '"registry_version": 1, "registry_version": 1', 1),
                 original[:-1] + ',}', original.replace('"registry_version": 1', '"registry_version": null', 1),
                 original.replace('"registry_version": 1', '"registry_version": 01', 1), original + " trailing"]
        for index, value in enumerate(texts):
            with self.subTest(case=index):
                path = self.write(value, "models.json", raw=True)
                with self.assertRaises(RegistryError):
                    ModelRegistry.load(path)
                with self.assertRaises(Exception):
                    self.lua_module.load(path)
        path = self.write("\ufeff" + original, "models.json", raw=True)
        self.assertEqual(len(ModelRegistry.load(path).data["models"]), len(self.lua_module.load(path).data.models))

    def test_file_failures_and_invalid_utf8(self):
        missing = (Path(self.temp.name) / "missing.json").as_posix()
        with self.assertRaises(RegistryError):
            ModelRegistry.load(missing)
        with self.assertRaises(Exception):
            self.lua_module.load(missing)
        for content in (b"", b" " * (1024 * 1024 + 1), b'{"invalid": "\xff"}'):
            with self.subTest(size=len(content)):
                path = Path(self.temp.name) / "models.json"
                path.write_bytes(content)
                with self.assertRaises(RegistryError):
                    ModelRegistry.load(path)
                with self.assertRaises(Exception):
                    self.lua_module.load(path.as_posix())

    def test_catalog_validation_rejects_bad_metadata(self):
        mutations = [
            lambda data: data.update(registry_version=2),
            lambda data: data["models"].append(copy.deepcopy(data["models"][0])),
            lambda data: data["parameters"]["speed"].update(suggested_default=10),
            lambda data: data["parameters"]["speed"].update(minimum=5),
            lambda data: data["parameters"]["stability"].update(enum=False),
            lambda data: data["parameters"]["stability"].update(enum=[0, 0.0, .5]),
            lambda data: data["parameters"]["stability"].update(source_refs=["missing"]),
            lambda data: data["models"][0]["operations"]["tts"]["parameters"].update(missing={}),
            lambda data: data["models"][0]["operations"]["tts"]["parameters"]["speed"].update(ui=[]),
            lambda data: data["models"][0]["operations"]["tts"].update(input_kind="audio_file"),
            lambda data: data["runtime_defaults"].update(stt="scribe_v2_realtime"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                data = copy.deepcopy(CATALOG)
                mutate(data)
                path = self.write(data, "models.json")
                with self.assertRaises(RegistryError):
                    ModelRegistry.load(path)
                with self.assertRaises(Exception):
                    self.lua_module.load(path)

    def test_migration_and_reordering(self):
        data = copy.deepcopy(CONFIG)
        data["voices"].reverse()
        data["profiles"].reverse()
        loaded, error, _ = self.load_config(data)
        self.assertIsNone(error)
        for states, voice_id, profile_id in (
            ({"selected_voice": "2", "selected_project": "2"}, "wise_man_3", "meditation"),
            ({"selected_voice_id": CONFIG["voices"][2]["voice_id"], "selected_project": "1"}, "wise_woman", "bedtime"),
            ({"selected_voice_key": "wise_man_2", "selected_profile_id": "course"}, "wise_man_2", "course"),
        ):
            voice, profile, messages = self.config_module.restore_selections(loaded, lambda key: states.get(key, ""))
            self.assertEqual(loaded.voices[voice].id, voice_id)
            self.assertEqual(loaded.profiles[profile].id, profile_id)
            self.assertEqual(len(messages), 0)
        voice, profile, messages = self.config_module.restore_selections(loaded, lambda key: {"selected_voice": "999", "selected_project": "999"}.get(key, ""))
        self.assertEqual((voice, profile, len(messages)), (1, 1, 2))

    def test_failed_reload_and_registry_snapshots(self):
        old, error, _ = self.load_config(CONFIG)
        self.assertIsNone(error)
        before = self.config_module.resolve(old, "bedtime", "wise_man_2", "tts", V2)
        candidate = copy.deepcopy(CONFIG)
        candidate["preferences"]["normalize_audio"] = "invalid"
        failed, error = self.load_config(candidate)
        self.assertIsNone(failed)
        self.assertIn("normalize_audio", error)
        changed_catalog = copy.deepcopy(CATALOG)
        changed_catalog["parameters"]["speed"]["maximum"] = 3
        new, error, _ = self.load_config(CONFIG, changed_catalog)
        self.assertIsNone(error)
        self.assertEqual(self.config_module.registry(old).data.parameters.speed.maximum, 4)
        self.assertEqual(self.config_module.registry(new).data.parameters.speed.maximum, 3)
        after = self.config_module.resolve(old, "bedtime", "wise_man_2", "tts", V2)
        self.assertEqual(self.normalize(after, {"speed": .91, "stability": .85, "similarity_boost": .8}),
                         self.normalize(before, {"speed": .91, "stability": .85, "similarity_boost": .8}))
