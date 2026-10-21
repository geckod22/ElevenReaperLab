"""Current selection, TTS validation, and normalization regressions. Not live REAPER tests."""

import types
from unittest.mock import patch

import test_protocol_worker as fixtures
from erl_protocol import WorkerError
import erl_worker


def app_function(lua, name, bindings):
    """Load one real local function without starting the app or registering actions.

    This deliberately depends on top-level local-function boundaries. Fail visibly
    if the app is reorganized instead of silently testing copied implementation.
    """
    source = (fixtures.ROOT / "ERL_App.lua").read_text(encoding="utf-8-sig")
    marker = "local function " + name + "("
    if source.count(marker) != 1:
        raise AssertionError("Expected one app function: " + name)
    start = source.index(marker)
    end = source.index("\nlocal function ", start + len(marker))
    globals_table = lua.globals()
    for key, value in bindings.items():
        globals_table[key] = value
    return lua.execute(source[start:end] + "\nreturn " + name)


class AudioWorkflowTests(fixtures.ProtocolWorkerTests):
    def test_dispatch_waits_for_busy_feedback_and_consumes_command_once(self):
        state = {"calls": [], "errors": []}
        globals_table = self.lua.globals()

        def complete():
            globals_table.is_processing = False

        def fail(code, message):
            state["errors"].append(code)
            globals_table.is_processing = False

        reaper = self.lua.table_from({
            "ValidatePtr": lambda *args: True,
            "EnumProjects": lambda index: "project",
            "Main_OnCommand": lambda command, flags: state["calls"].append(command),
        })
        dispatch = app_function(self.lua, "dispatch_pending", {
            "reaper": reaper, "pending_command": 123,
            "pending_feedback_drawn": False, "active_project": "project",
            "is_processing": True, "HandleScriptMessages": complete, "fail_active": fail,
        })
        dispatch()
        self.assertEqual(state["calls"], [])
        self.assertEqual(globals_table.pending_command, 123)
        globals_table.pending_feedback_drawn = True
        dispatch()
        dispatch()
        self.assertEqual(state["calls"], [123])
        self.assertEqual(state["errors"], [])
        self.assertEqual(globals_table.pending_command, "")
        self.assertIs(globals_table.pending_feedback_drawn, False)

        globals_table.pending_command = 456
        globals_table.pending_feedback_drawn = True
        globals_table.is_processing = True
        globals_table.active_project = "different-project"
        dispatch()
        self.assertEqual(state["calls"], [123])
        self.assertEqual(state["errors"], ["PROJECT_CHANGED"])

    def input_fixture(self, count=1, duration=4, offset=0, source_length=4,
                      project_file="/fixture/project.rpp", midi=False):
        state = {"duration": duration, "offset": offset, "source_length": source_length}
        reaper = self.lua.table_from({
            "EnumProjects": lambda index: ("project", project_file),
            "CountSelectedMediaItems": lambda project: count,
            "GetCursorPosition": lambda: 19,
            "GetSelectedMediaItem": lambda project, index: "source",
            "GetActiveTake": lambda item: "take",
            "TakeIsMIDI": lambda take: midi,
            "GetMediaItemTake_Source": lambda take: "media",
            "GetMediaSourceFileName": lambda source: "/fixture/input.wav",
            "GetSetMediaItemInfo_String": lambda *args: (True, "source-guid"),
            "GetMediaItemInfo_Value": lambda item, field: duration if field == "D_LENGTH" else 3.5,
            "GetMediaItemTakeInfo_Value": lambda take, field: offset,
            "GetMediaSourceLength": lambda source: (source_length, False),
        })
        bindings = {"reaper": reaper, "MAX_DURATION_SEC": 120}
        bindings["CheckItemLength"] = app_function(self.lua, "CheckItemLength", bindings)
        bindings["NeedsGlue"] = app_function(self.lua, "NeedsGlue", bindings)
        return app_function(self.lua, "input_context", bindings), state

    def test_tts_accepts_trimmed_offset_and_long_reference_items(self):
        for duration, offset, source_length in ((4, 2, 10), (180, 0, 180), (180, 2, 200)):
            with self.subTest(duration=duration, offset=offset):
                context, _ = self.input_fixture(duration=duration, offset=offset, source_length=source_length)
                result = context("tts")
                self.assertIsNone(result.error)
                self.assertFalse(result.needs_glue)
                self.assertEqual(result.position, 3.5)
                self.assertEqual(result.insertion_mode, "item")
                self.assertEqual(result.path, "/fixture/input.wav")

    def test_sts_stt_keep_duration_and_glue_guards(self):
        for operation in ("sts", "stt"):
            for duration, offset, source_length, problem in (
                (180, 0, 180, "120-second"), (4, 2, 10, "Glue")):
                with self.subTest(operation=operation, problem=problem):
                    context, _ = self.input_fixture(duration=duration, offset=offset, source_length=source_length)
                    self.assertIn(problem, context(operation).error)
            context, _ = self.input_fixture()
            self.assertIsNone(context(operation).error)

    def test_tts_cursor_requires_saved_project_and_rejects_multiple_selection(self):
        context, _ = self.input_fixture(count=0)
        result = context("tts")
        self.assertIsNone(result.error)
        self.assertEqual(result.insertion_mode, "cursor")
        self.assertEqual(result.position, 19)
        self.assertTrue(result.directory.endswith("ElevenReaperLab_Audio"))
        context, _ = self.input_fixture(count=0, project_file="")
        self.assertIn("Save the project", context("tts").error)
        for operation in ("tts", "sts", "stt"):
            context, _ = self.input_fixture(count=2)
            self.assertIsNotNone(context(operation).error)
        context, _ = self.input_fixture(midi=True)
        self.assertIn("audio take", context("tts").error)

    def import_fixture(self, operation="tts", cursor_mode=False, failure=None):
        request = self.request(operation)
        if cursor_mode:
            request["context"]["insertion_mode"] = "cursor"
        state = {"items": ["source"], "tracks": {"master": False, "original": True, "target": False},
                 "cursor": 19, "undo": [], "muted": [], "arranged": False}

        def select(item, selected):
            if selected and item not in state["items"]:
                state["items"].append(item)
            elif not selected and item in state["items"]:
                state["items"].remove(item)

        def only_track(track):
            for entry in state["tracks"]:
                state["tracks"][entry] = entry == track

        def insert(path, mode):
            state["insert_position"] = state["cursor"]
            state["items"] = [] if failure == "insert" else ["generated"]

        def mute(item, field, value):
            if failure == "mute":
                return False
            state["muted"].append(item)
            return True

        fake = types.SimpleNamespace(
            RPR_CountSelectedMediaItems=lambda project: len(state["items"]),
            RPR_GetSelectedMediaItem=lambda project, index: state["items"][index],
            RPR_GetMasterTrack=lambda project: "master",
            RPR_CountTracks=lambda project: 2,
            RPR_GetTrack=lambda project, index: ("original", "target")[index],
            RPR_IsTrackSelected=lambda track: state["tracks"][track],
            RPR_GetCursorPosition=lambda: state["cursor"],
            RPR_Undo_BeginBlock2=lambda project: state["undo"].append("begin"),
            RPR_Undo_EndBlock2=lambda project, label, flags: state["undo"].append("end"),
            RPR_GetSetMediaTrackInfo_String=lambda track, field, value, write:
                (True, track, field, request["output"]["track_name"] if track == "target" else "Original", write),
            RPR_Main_OnCommand=lambda command, flags: state.update(items=[]),
            RPR_SetOnlyTrackSelected=only_track,
            RPR_SetEditCurPos=lambda position, move, seek: state.update(cursor=position),
            RPR_InsertMedia=insert,
            RPR_GetMediaItemTrack=lambda item: "target",
            RPR_GetSetMediaItemInfo_String=lambda item, field, value, write: (True, item, field, "generated-guid", write),
            RPR_SetMediaItemInfo_Value=mute,
            RPR_ValidatePtr2=lambda *args: True,
            RPR_SetMediaItemSelected=select,
            RPR_SetTrackSelected=lambda track, selected: state["tracks"].update({track: selected}),
            RPR_UpdateArrange=lambda: state.update(arranged=True))
        return request, fake, state

    def assert_import_state(self, state, selected):
        self.assertEqual(state["items"], selected)
        self.assertEqual(state["cursor"], 19)
        self.assertEqual(state["tracks"], {"master": False, "original": True, "target": False})
        self.assertEqual(state["undo"], ["begin", "end"])
        self.assertTrue(state["arranged"])

    def test_success_selects_only_generated_item_for_tts_and_sts(self):
        for operation in ("tts", "sts"):
            with self.subTest(operation=operation):
                request, fake, state = self.import_fixture(operation)
                with patch.object(erl_worker, "source_item", return_value="source"):
                    result = erl_worker.import_audio(fake, request, "generated.mp3")
                self.assertEqual(result["item_guid"], "generated-guid")
                self.assertEqual(state["insert_position"], request["context"]["position"])
                self.assertEqual(state["muted"], ["source"])
                self.assert_import_state(state, ["generated"])

    def test_cursor_import_selects_generated_item_without_muting(self):
        request, fake, state = self.import_fixture(cursor_mode=True)
        state["items"] = []
        with patch.object(erl_worker, "source_item", return_value=None):
            erl_worker.import_audio(fake, request, "generated.mp3")
        self.assertEqual(state["muted"], [])
        self.assert_import_state(state, ["generated"])

    def test_import_failures_restore_previous_selection_and_close_undo(self):
        for failure in ("insert", "mute"):
            with self.subTest(failure=failure):
                request, fake, state = self.import_fixture(failure=failure)
                with patch.object(erl_worker, "source_item", return_value="source"), self.assertRaises(WorkerError):
                    erl_worker.import_audio(fake, request, "generated.mp3")
                self.assert_import_state(state, ["source"])

    def normalization_fixture(self, enabled=True, command=42, failure=False, changed_path=False):
        state = {"selected": {"generated": True, "source": False}, "cursor": 19,
                 "tracks": {"master": False, "original": True}, "calls": [], "undo": [], "logs": []}
        def action(command_id, flags):
            state["calls"].append([item for item, selected in state["selected"].items() if selected])
            # Simulate action side effects, including a failing SWS action.
            state["selected"] = {"generated": False, "source": True}
            state["cursor"] = 99
            state["tracks"]["original"] = False
            if failure:
                raise RuntimeError("Simulated normalization failure")
        api = {
            "ValidatePtr": lambda *args: True,
            "ValidatePtr2": lambda *args: True,
            "EnumProjects": lambda index: "project",
            "CountMediaItems": lambda project: 2,
            "GetMediaItem": lambda project, index: ("source", "generated")[index],
            "GetSetMediaItemInfo_String": lambda item, *args: (True, "generated-guid" if item == "generated" else "source-guid"),
            "GetActiveTake": lambda item: "take",
            "GetMediaItemTake_Source": lambda take: "media",
            "GetMediaSourceFileName": lambda media: "changed.mp3" if changed_path else "generated.mp3",
            "NamedCommandLookup": lambda name: command,
            "CountSelectedMediaItems": lambda project: sum(state["selected"].values()),
            "GetSelectedMediaItem": lambda project, index: [item for item, selected in state["selected"].items() if selected][index],
            "GetMasterTrack": lambda project: "master",
            "IsTrackSelected": lambda track: state["tracks"][track],
            "CountTracks": lambda project: 1,
            "GetTrack": lambda project, index: "original",
            "GetCursorPosition": lambda: state["cursor"],
            "Undo_BeginBlock2": lambda project: state["undo"].append("begin"),
            "Undo_EndBlock2": lambda *args: state["undo"].append("end"),
            "SelectAllMediaItems": lambda project, selected: state["selected"].update({item: selected for item in state["selected"]}),
            "SetMediaItemSelected": lambda item, selected: state["selected"].update({item: selected}),
            "SetTrackSelected": lambda track, selected: state["tracks"].update({track: selected}),
            "SetEditCurPos": lambda position, *args: state.update(cursor=position),
            "Main_OnCommand": action,
        }
        normalize = app_function(self.lua, "normalize_result", {
            "reaper": self.lua.table_from(api), "AddLog": lambda message: state["logs"].append(message)})
        request = self.to_lua({"output": {"normalize": enabled}})
        result = self.to_lua({"kind": "audio", "item_guid": "generated-guid", "audio_path": "generated.mp3"})
        return normalize, request, result, state

    def test_normalization_retains_generated_selection_on_success_or_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                normalize, request, result, state = self.normalization_fixture(failure=failure)
                normalize(request, result, "project")
                self.assertEqual(state["calls"], [["generated"]])
                self.assertEqual(state["selected"], {"generated": True, "source": False})
                self.assertEqual(state["cursor"], 19)
                self.assertEqual(state["tracks"], {"master": False, "original": True})
                self.assertEqual(state["undo"], ["begin", "end"])
                self.assertEqual(bool(state["logs"]), failure)

    def test_normalization_disabled_missing_sws_or_changed_audio_does_not_touch_selection(self):
        for options in ({"enabled": False}, {"command": 0}, {"changed_path": True}):
            with self.subTest(options=options):
                normalize, request, result, state = self.normalization_fixture(**options)
                normalize(request, result, "project")
                self.assertEqual(state["calls"], [])
                self.assertEqual(state["undo"], [])
                self.assertEqual(state["selected"], {"generated": True, "source": False})
