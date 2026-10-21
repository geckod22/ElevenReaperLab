"""Session-state and payload regressions; execution requires permission."""

import copy
import json

import test_registry_configuration as regression
from erl_worker_settings import create_multipart_data, create_tts_payload, endpoint_url
from erl_model_registry import RegistryError


class SessionPayloadTests(regression.RegistryConfigurationTests):
    """Reuses configuration fixtures; the runner selects each test method once."""

    def make_session(self, saved=None, candidate=None):
        loaded, error, _ = self.load_config(candidate or regression.CONFIG)
        self.assertIsNone(error)
        module = self.lua.eval("dofile")((regression.ROOT / "erl_session.lua").as_posix())
        session, _ = module.new(loaded, self.config_module, "bedtime", "wise_man_2",
                                lambda key: (saved or {}).get(key, ""))
        return session

    def test_independent_model_edits_and_reset(self):
        session = self.make_session()
        session.set_value(session, "tts", "stability", 0)
        session.select_model(session, "tts", "eleven_v4")
        values, error = session.settings(session, "tts")
        self.assertIsNone(error)
        self.assertEqual(values.stability, .5)
        session.set_value(session, "tts", "similarity_boost", 0)
        session.select_model(session, "tts", regression.V2)
        values, error = session.settings(session, "tts")
        self.assertIsNone(error)
        self.assertEqual(values.stability, 0)
        sts, _ = session.settings(session, "sts")
        self.assertEqual(sts.stability, .85)
        session.set_context(session, "course", "wise_man_3")
        self.assertFalse(session.is_modified(session, "tts"))
        values, _ = session.settings(session, "tts")
        self.assertEqual(values.speed, 1.1)
        session.select_model(session, "tts", "eleven_v4")
        values, _ = session.settings(session, "tts")
        self.assertEqual(values.similarity_boost, .75)

    def test_optional_omission_false_and_zero(self):
        candidate = copy.deepcopy(regression.CONFIG)
        candidate["model_defaults"]["tts"][regression.V2]["use_speaker_boost"] = True
        session = self.make_session(candidate=candidate)
        session.set_value(session, "tts", "use_speaker_boost", None)
        values, error = session.settings(session, "tts")
        self.assertIsNone(error)
        self.assertIsNone(values.use_speaker_boost)
        session.set_value(session, "tts", "use_speaker_boost", False)
        session.set_value(session, "tts", "seed", 0)
        values, error = session.settings(session, "tts")
        self.assertIsNone(error)
        self.assertIs(values.use_speaker_boost, False)
        self.assertEqual(values.seed, 0)
        session.reset(session)
        values, _ = session.settings(session, "tts")
        self.assertIs(values.use_speaker_boost, True)

    def test_model_restoration_and_operation_filtering(self):
        session = self.make_session({"selected_model_tts": "eleven_v3",
                                     "selected_model_stt": "scribe_v1"})
        self.assertEqual(session.models.tts, "eleven_v3")
        self.assertEqual(session.models.stt, "scribe_v1")
        self.assertIn("unverified", session.catalog.by_id.eleven_v3.selection_note)
        options = session.model_options(session, "stt")
        self.assertEqual([entry.id for entry in options.values()], ["scribe_v2", "scribe_v1"])
        selected, _ = session.select_model(session, "sts", "eleven_v4")
        self.assertIsNone(selected)
        restored = self.make_session({"selected_model_tts": "unknown"})
        self.assertEqual(restored.models.tts, regression.V2)

    def test_incomplete_model_cannot_be_selected(self):
        candidate = copy.deepcopy(regression.CONFIG)
        candidate["model_defaults"]["tts"].pop("eleven_v4")
        session = self.make_session(candidate=candidate)
        selected, reason = session.select_model(session, "tts", "eleven_v4")
        self.assertIsNone(selected)
        self.assertIn("defaults", reason)

    def test_temporary_invalid_constraint_is_editable(self):
        session = self.make_session()
        session.set_value(session, "stt", "diarization_threshold", .3)
        values, error = session.settings(session, "stt")
        self.assertEqual(values.diarization_threshold, .3)
        self.assertIn("diarize", error)
        session.set_value(session, "stt", "diarize", True)
        _, error = session.settings(session, "stt")
        self.assertIsNone(error)

    def test_unicode_text_and_limits(self):
        session = self.make_session({"selected_model_tts": "eleven_v3"})
        text = "Caffè | 漢字\n🎙️"
        count, limit, error = session.text_status(session, text)
        self.assertEqual(count, len(text))
        self.assertEqual(limit, 5000)
        self.assertIsNone(error)
        self.assertIsNone(session.text_status(session, "é" * 5000)[2])
        self.assertIsNotNone(session.text_status(session, "é" * 5001)[2])
        self.assertIsNotNone(session.text_status(session, " \n")[2])

    def test_payloads_do_not_leak_incompatible_fields(self):
        catalog = self.py_registry
        text = "Caffè | 漢字\n🎙️"
        for model, defaults in regression.CONFIG["model_defaults"]["tts"].items():
            with self.subTest(model=model):
                values = dict(defaults, seed=0)
                payload = create_tts_payload(catalog, model, values, text)
                self.assertEqual(payload["text"], text)
                self.assertEqual(payload["model_id"], model)
                self.assertEqual(payload["seed"], 0)
                self.assertIs(type(payload["seed"]), int)
                self.assertNotIn("seed", payload["voice_settings"])
                if model in ("eleven_v3", "eleven_v4", "eleven_v4_turbo"):
                    self.assertNotIn("speed", payload["voice_settings"])
        with self.assertRaises(RegistryError):
            create_tts_payload(catalog, "eleven_v4", {"speed": 1, "stability": .5,
                                                     "similarity_boost": .75}, text)

    def test_multipart_types_and_empty_transcription(self):
        values = {"speed": 1, "stability": 0, "similarity_boost": .75,
                  "use_speaker_boost": False, "seed": 0, "remove_background_noise": False}
        data = create_multipart_data(self.py_registry, "sts", regression.STS, values)
        self.assertEqual(data["remove_background_noise"], "false")
        self.assertEqual(data["seed"], "0")
        self.assertIs(json.loads(data["voice_settings"])["use_speaker_boost"], False)
        data = create_multipart_data(self.py_registry, "stt", "scribe_v1", {})
        self.assertEqual(data, {"model_id": "scribe_v1"})
        data = create_multipart_data(self.py_registry, "stt", "scribe_v2",
                                     {"diarize": False, "temperature": 0})
        self.assertEqual(data["diarize"], "false")
        self.assertEqual(data["temperature"], "0")

    def test_endpoint_escapes_voice_id(self):
        self.assertEqual(endpoint_url(self.py_registry, "tts", "eleven_v4", "voice/id"),
                         "https://api.elevenlabs.io/v1/text-to-speech/voice%2Fid")
