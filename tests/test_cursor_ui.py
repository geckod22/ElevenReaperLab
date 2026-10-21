"""Cursor-mode and UI-helper regressions; execution requires permission."""

import copy
import json
from pathlib import Path
import types
from unittest.mock import patch

import test_protocol_worker as regression
from erl_protocol import WorkerError, validate_request
import erl_worker


class CursorUiTests(regression.ProtocolWorkerTests):
    def cursor_request(self, operation="tts"):
        request = self.request(operation)
        project_file = Path(self.fixture_folder.name) / "project.rpp"
        project_file.write_text("fixture", encoding="utf-8")
        request["context"].update(insertion_mode="cursor", project_file=str(project_file))
        request["context"].pop("item_guid")
        request["input"].pop("audio_path")
        request["output"]["directory"] = str(project_file.parent / "ElevenReaperLab_Audio")
        return request

    def test_display_helpers_preserve_selection_and_redact(self):
        helpers = self.lua.eval("dofile")((regression.ROOT / "erl_ui_state.lua").as_posix())
        voices = [{"id": "first", "name": "Narrator", "voice_id": "provider-A"},
                  {"id": "second", "name": "Actor", "voice_id": "provider-B"}]
        table = self.to_lua(voices)
        for query, expected in (("NARR", [1]), ("second", [2]), ("provider-B", [2]), ("missing", [])):
            matches = helpers.filter_voices(table, query)
            self.assertEqual(list(matches.values()), expected)
            self.assertEqual(table[1].id, "first")
        self.assertEqual(helpers.redact("API: key.%[] and private text", self.to_lua(["key.%[]", "private text"])),
                         "API: [redacted] and [redacted]")
        entries = [{"timestamp": "12:00", "level": "info", "message": "Finished"},
                   {"timestamp": "12:01", "level": "error", "message": "Failed"}]
        filtered = helpers.format_logs(self.to_lua(entries), self.to_lua({"info": False, "error": True}), "fail")
        self.assertEqual(filtered, "[12:01] ERROR: Failed")

    def test_cursor_contract_and_operation_restrictions(self):
        request = self.cursor_request()
        validate_request(request, "tts")
        raw = self.protocol.encode_request(self.to_lua(request))
        self.assertNotIn("audio_path", json.loads(raw)["input"])
        for operation in ("sts", "stt"):
            with self.assertRaises(WorkerError):
                validate_request(self.cursor_request(operation), operation)
        for mutate in (lambda data: data["context"].update(project_file=""),
                       lambda data: data["context"].update(item_guid="unrelated"),
                       lambda data: data["output"].update(directory=self.fixture_folder.name)):
            candidate = copy.deepcopy(request)
            mutate(candidate)
            with self.assertRaises(WorkerError):
                validate_request(candidate, "tts")

    def test_cursor_project_guard(self):
        request = self.cursor_request()
        project_file = request["context"]["project_file"]
        fake_reaper = types.SimpleNamespace(
            RPR_GetExtState=lambda section, key: request["request_id"],
            RPR_GetMasterTrack=lambda project: "master",
            RPR_GetSetMediaTrackInfo_String=lambda track, field, value, write:
                (True, track, field, "project-1", write),
            RPR_EnumProjects=lambda index, value, length: ("project", index, project_file, length))
        self.assertIsNone(erl_worker.source_item(fake_reaper, request))
        fake_reaper.RPR_EnumProjects = lambda index, value, length: ("project", index, "different.rpp", length)
        with self.assertRaises(WorkerError) as changed:
            erl_worker.source_item(fake_reaper, request)
        self.assertEqual(changed.exception.code, "PROJECT_CHANGED")

    def test_output_preflight_prevents_http(self):
        request = self.cursor_request()
        directory = Path(request["output"]["directory"])
        directory.write_bytes(b"existing-file")
        result, calls = self.run_simulated(request)
        self.assertEqual(result["error"]["code"], "OUTPUT_UNAVAILABLE")
        self.assertEqual(calls, [])
        self.assertEqual(directory.read_bytes(), b"existing-file")

    def test_cursor_import_preserves_state_and_mutes_nothing(self):
        request = self.cursor_request()
        state = {"cursor": 19.0, "tracks": ["original"], "selected_items": [],
                 "selected_tracks": {"original": True, "master": False},
                 "names": {"original": "Original"}, "undo": []}
        def insert_track(index, defaults):
            state["tracks"].append("target")
            state["selected_tracks"]["target"] = False
        def track_string(track, field, value, write):
            if write:
                state["names"][track] = value
            return True, track, field, state["names"].get(track, ""), write
        def select_only(track):
            for key in state["selected_tracks"]:
                state["selected_tracks"][key] = key == track
        def insert_media(path, mode):
            state["insert_position"] = state["cursor"]
            state["selected_items"] = ["generated"]
        def refuse_muting(*args):
            raise AssertionError("Cursor generation must not mute any existing item")
        fake_reaper = types.SimpleNamespace(
            RPR_CountSelectedMediaItems=lambda project: len(state["selected_items"]),
            RPR_GetSelectedMediaItem=lambda project, index: state["selected_items"][index],
            RPR_GetMasterTrack=lambda project: "master",
            RPR_GetTrack=lambda project, index: state["tracks"][index],
            RPR_CountTracks=lambda project: len(state["tracks"]),
            RPR_IsTrackSelected=lambda track: state["selected_tracks"][track],
            RPR_GetCursorPosition=lambda: state["cursor"],
            RPR_Undo_BeginBlock2=lambda project: state["undo"].append("begin"),
            RPR_Undo_EndBlock2=lambda project, label, flags: state["undo"].append("end"),
            RPR_InsertTrackAtIndex=insert_track,
            RPR_GetSetMediaTrackInfo_String=track_string,
            RPR_Main_OnCommand=lambda command, flags: state.update(selected_items=[]),
            RPR_SetOnlyTrackSelected=select_only,
            RPR_SetEditCurPos=lambda position, move, seek: state.update(cursor=position),
            RPR_InsertMedia=insert_media,
            RPR_GetMediaItemTrack=lambda item: "target",
            RPR_GetSetMediaItemInfo_String=lambda item, field, value, write: (True, item, field, "generated-guid", write),
            RPR_SetMediaItemInfo_Value=refuse_muting,
            RPR_ValidatePtr2=lambda project, pointer, kind: True,
            RPR_SetMediaItemSelected=lambda item, selected: state.update(
                selected_items=state["selected_items"] + [item] if selected else
                [entry for entry in state["selected_items"] if entry != item]),
            RPR_SetTrackSelected=lambda track, selected: state["selected_tracks"].update({track: selected}),
            RPR_UpdateArrange=lambda: None)
        with patch.object(erl_worker, "source_item", return_value=None):
            result = erl_worker.import_audio(fake_reaper, request, "generated.mp3")
        self.assertEqual(result["item_guid"], "generated-guid")
        self.assertEqual(state["insert_position"], request["context"]["position"])
        self.assertEqual(state["cursor"], 19.0)
        self.assertEqual(state["selected_items"], ["generated"])
        self.assertEqual(state["selected_tracks"], {"original": True, "master": False, "target": False})
        self.assertEqual(state["undo"], ["begin", "end"])

    def test_cursor_output_success_and_recovery(self):
        request = self.cursor_request()
        response = types.SimpleNamespace(status_code=200, headers={"Content-Type": "audio/mpeg"},
                                         content=b"simulated-audio", close=lambda: None)
        result, calls = self.run_simulated(request, response)
        self.assertEqual(result["status"], "completed")
        path = Path(result["result"]["audio_path"])
        self.assertEqual(path.parent, Path(request["output"]["directory"]))
        self.assertEqual(path.read_bytes(), b"simulated-audio")
        self.assertEqual(len(calls), 1)
        failed, calls = self.run_simulated(request, response, import_failure=True)
        self.assertIs(failed["error"]["audio_saved"], True)
        recovery_path = Path(failed["error"]["audio_path"])
        self.assertNotEqual(recovery_path, path)
        self.assertEqual(recovery_path.read_bytes(), b"simulated-audio")
