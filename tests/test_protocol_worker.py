"""Protocol, action-bootstrap, and simulated HTTP regressions; execution requires permission."""

import copy
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from erl_protocol import VERSION, WorkerError, decode_json, terminal_response, validate_request
import erl_worker

LUA_VERSION = "53"


class ProtocolWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = importlib.import_module("lupa.lua" + LUA_VERSION).LuaRuntime(unpack_returned_tuples=True)
        cls.reader = cls.lua.eval("dofile")((ROOT / "erl_json.lua").as_posix())
        cls.protocol = cls.lua.eval("dofile")((ROOT / "erl_protocol.lua").as_posix())

    def setUp(self):
        self.fixture_folder = tempfile.TemporaryDirectory(prefix="erl-protocol-")
        self.addCleanup(self.fixture_folder.cleanup)

    def request(self, operation="stt"):
        model = {"tts": "eleven_v4", "sts": "eleven_multilingual_sts_v2", "stt": "scribe_v2"}[operation]
        config = json.loads((ROOT / "erl_config.example.json").read_text(encoding="utf-8-sig"))
        return {"protocol_version": VERSION, "request_id": "request-1", "operation": operation,
                "model_id": model, "voice_id": "voice-test" if operation != "stt" else "",
                "settings": config["model_defaults"][operation][model],
                "registry_json": (ROOT / "erl_models.json").read_text(encoding="utf-8-sig"),
                "input": {"audio_path": "input.wav", "text": "Caffè | 漢字\n🎙"},
                "context": {"project_master_guid": "project-1", "item_guid": "item-1", "position": 3.5, "insertion_mode": "item"},
                "output": {"track_name": {"tts": "TTS", "sts": "VOICE_REGEN", "stt": ""}[operation],
                           "normalize": operation != "stt"},
                "timeouts": {"connect": 10, "read": 300}}

    def to_lua(self, value):
        return self.reader.decode(json.dumps(value, ensure_ascii=False))[0]

    def test_action_bootstrap_without_file(self):
        for filename, operation in (("erl_text_to_speech.py", "tts"),
                                    ("erl_speech_to_speech.py", "sts"),
                                    ("erl_speech_to_text.py", "stt")):
            with self.subTest(action=filename):
                calls = []
                fake_reaper = types.ModuleType("reaper_python")
                fake_reaper.RPR_GetExtState = lambda section, key: str(ROOT) if key == "worker_script_folder" else ""
                fake_worker = types.ModuleType("erl_worker")
                fake_worker.run_worker = lambda selected, api: calls.append((selected, api))
                original_path = list(sys.path)
                try:
                    sys.path[:] = [entry for entry in sys.path if entry != str(ROOT)]
                    with patch.dict(sys.modules, {"reaper_python": fake_reaper, "erl_worker": fake_worker}):
                        # Match REAPER's action environment: __name__ exists, __file__ does not.
                        exec(compile((ROOT / filename).read_text(encoding="utf-8-sig"), filename, "exec"),
                             {"__name__": "__main__"})
                    self.assertEqual(calls, [(operation, fake_reaper)])
                    self.assertIn(str(ROOT), sys.path)
                finally:
                    sys.path[:] = original_path

    def test_project_identity_uses_guid_text(self):
        guid = "{12345678-1234-5678-90AB-1234567890AB}"
        fake_reaper = types.SimpleNamespace(
            RPR_GetMasterTrack=lambda project: "master-track",
            RPR_GetSetMediaTrackInfo_String=lambda track, field, value, write: (True, track, field, guid, write),
            RPR_GetExtState=lambda section, key: "request-1",
            RPR_CountMediaItems=lambda project: 0)
        self.assertEqual(erl_worker.project_token(fake_reaper), guid)
        request = self.request()
        request["context"]["project_master_guid"] = guid
        with self.assertRaises(WorkerError) as missing:
            erl_worker.source_item(fake_reaper, request)
        self.assertEqual(missing.exception.code, "SOURCE_MISSING")
        request["context"]["project_master_guid"] = "another-project"
        with self.assertRaises(WorkerError) as changed:
            erl_worker.source_item(fake_reaper, request)
        self.assertEqual(changed.exception.code, "PROJECT_CHANGED")

    def test_request_roundtrip_and_rejection(self):
        for operation in ("tts", "sts", "stt"):
            request = self.request(operation)
            raw = self.protocol.encode_request(self.to_lua(request))
            decoded = decode_json(raw)
            self.assertEqual(decoded, request)
            validate_request(decoded, operation)
            self.assertIsInstance(decoded["settings"], dict)
        request = self.request()
        for mutate in (lambda data: data.update(protocol_version=1),
                       lambda data: data.update(settings=[]),
                       lambda data: data["timeouts"].update(read=0),
                       lambda data: data.update(operation="tts")):
            candidate = copy.deepcopy(request)
            mutate(candidate)
            with self.assertRaises(WorkerError):
                validate_request(candidate, "stt")
        for raw in ('[]', '{"request_id":"a","request_id":"b"}', '{"a":NaN}'):
            with self.assertRaises(WorkerError):
                decode_json(raw)

    def test_terminal_responses_and_stale_identity(self):
        request = self.request()
        text = "Success | ERROR: still ordinary text\nCaffè 漢字 🎙"
        raw = terminal_response(request, "completed", result={"kind": "text", "text": text})
        response = self.protocol.decode_response(raw, self.to_lua(request))
        self.assertEqual(response.result.text, text)
        stale = dict(request, request_id="obsolete")
        result, error, ignored = self.protocol.decode_response(raw, self.to_lua(stale))
        self.assertIsNone(result)
        self.assertIsNone(error)
        self.assertIs(ignored, True)
        for status in ("failed", "cancelled"):
            raw = terminal_response(request, status, error=WorkerError("NETWORK_TIMEOUT", "Timed out", True))
            response = self.protocol.decode_response(raw, self.to_lua(request))
            self.assertEqual(response.status, status)
            self.assertIs(response.error.outcome_uncertain, True)
        audio_request = self.request("tts")
        audio_result = {"kind": "audio", "audio_path": "generated.mp3", "item_guid": "generated-1",
                        "project_master_guid": "project-1"}
        audio_raw = terminal_response(audio_request, "completed", result=audio_result)
        audio_response = self.protocol.decode_response(audio_raw, self.to_lua(audio_request))
        self.assertEqual(audio_response.result.item_guid, "generated-1")
        bad = json.loads(raw)
        bad["status"] = "Success"
        result, error = self.protocol.decode_response(json.dumps(bad), self.to_lua(request))
        self.assertIsNone(result)
        self.assertIsNotNone(error)

    def test_request_uses_snapshot_and_rejects_disabled_models(self):
        request = self.request("tts")
        with patch("erl_model_registry.ModelRegistry.load", side_effect=AssertionError("No disk reload")):
            catalog = validate_request(request, "tts")
        self.assertIn(request["model_id"], catalog.by_id)
        snapshot = json.loads(request["registry_json"])
        next(model for model in snapshot["models"] if model["id"] == request["model_id"])["selection_enabled"] = False
        request["registry_json"] = json.dumps(snapshot)
        with self.assertRaises(WorkerError) as disabled:
            validate_request(request, "tts")
        self.assertEqual(disabled.exception.code, "INVALID_SETTINGS")
        request = self.request("tts")
        request["settings"]["speed"] = 1
        with self.assertRaises(WorkerError) as invalid:
            validate_request(request, "tts")
        self.assertEqual(invalid.exception.code, "INVALID_SETTINGS")

    def run_simulated(self, request, response=None, failure=None, import_failure=False):
        request = copy.deepcopy(request)
        source_path = request["input"].get("audio_path")
        if source_path and not Path(source_path).is_absolute():
            source = Path(self.fixture_folder.name) / "input.wav"
            source.write_bytes(b"fixture")
            request["input"]["audio_path"] = str(source)
        states = {"active_request_id": request["request_id"], "request_json": json.dumps(request),
                  "request_api_key": "FAKE-TEST-KEY"}
        reaper = types.SimpleNamespace(
            RPR_GetExtState=lambda section, key: states.get(key, ""),
            RPR_DeleteExtState=lambda section, key, persist: states.pop(key, None),
            RPR_SetExtState=lambda section, key, value, persist: states.update({key: value}))
        class Timeout(Exception):
            pass
        class RequestException(Exception):
            pass
        calls = []
        def post(*args, **kwargs):
            calls.append(kwargs)
            if failure == "timeout":
                raise Timeout()
            if failure == "network":
                raise RequestException()
            return response
        fake_requests = types.SimpleNamespace(post=post,
            exceptions=types.SimpleNamespace(Timeout=Timeout, RequestException=RequestException))
        imported = {"kind": "audio", "audio_path": "generated.mp3", "item_guid": "generated-1",
                    "project_master_guid": "project-1"}
        with patch.dict(sys.modules, {"requests": fake_requests}), \
             patch.object(erl_worker, "source_item", return_value=object()), \
             patch.object(erl_worker, "import_audio", side_effect=WorkerError("IMPORT_FAILED", "Import failed")
                          if import_failure else lambda reaper, request, path: dict(imported, audio_path=path)):
            erl_worker.run_worker(request["operation"], reaper)
        self.assertNotIn("request_api_key", states)
        self.assertNotIn("request_json", states)
        raw = states["response_json"]
        self.assertNotIn("FAKE-TEST-KEY", raw)
        return json.loads(raw), calls

    def test_simulated_success_and_timeout(self):
        response = types.SimpleNamespace(status_code=200, json=lambda: {"text": "Caffè | 漢字\n🎙"}, close=lambda: None)
        # A fixture file supports the multipart upload without using real audio.
        with tempfile.TemporaryDirectory(prefix="erl-worker-") as folder:
            path = Path(folder) / "input.wav"
            path.write_bytes(b"fixture")
            request = self.request()
            request["input"]["audio_path"] = str(path)
            result, calls = self.run_simulated(request, response)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["result"]["text"], "Caffè | 漢字\n🎙")
            self.assertEqual(calls[0]["timeout"], (10, 300))
            self.assertIs(calls[0]["allow_redirects"], False)
            self.assertIn(calls[0]["files"]["file"][2], ("audio/wav", "audio/x-wav", "audio/vnd.wave"))
        for failure in ("timeout", "network"):
            result, calls = self.run_simulated(self.request("tts"), failure=failure)
            self.assertEqual(result["status"], "failed")
            self.assertIs(result["error"]["outcome_uncertain"], True)
            self.assertEqual(len(calls), 1)

    def test_http_failure_and_audio_recovery(self):
        for status, expected in ((401, "AUTHENTICATION_FAILED"), (429, "SERVICE_LIMIT"), (503, "HTTP_ERROR")):
            response = types.SimpleNamespace(status_code=status, close=lambda: None)
            result, calls = self.run_simulated(self.request("tts"), response)
            self.assertEqual(result["error"]["code"], expected)
            self.assertEqual(len(calls), 1)
        with tempfile.TemporaryDirectory(prefix="erl-worker-") as folder:
            response = types.SimpleNamespace(status_code=200, headers={"Content-Type": "audio/mpeg"},
                                             content=b"simulated-audio", close=lambda: None)
            request = self.request("tts")
            request["input"]["audio_path"] = str(Path(folder) / "input.wav")
            result, calls = self.run_simulated(request, response, import_failure=True)
            self.assertEqual(result["error"]["code"], "IMPORT_FAILED")
            self.assertIs(result["error"]["audio_saved"], True)
            self.assertEqual(Path(result["error"]["audio_path"]).read_bytes(), b"simulated-audio")
            self.assertEqual(len(calls), 1)
            Path(request["input"]["audio_path"]).write_bytes(b"fixture")
            for operation in ("tts", "sts"):
                request = self.request(operation)
                request["input"]["audio_path"] = str(Path(folder) / "input.wav")
                result, calls = self.run_simulated(request, response)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["result"]["kind"], "audio")
                self.assertEqual(Path(result["result"]["audio_path"]).read_bytes(), b"simulated-audio")
                self.assertEqual(len(calls), 1)
