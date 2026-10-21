"""REAPER action entry point for the versioned Eleven Reaper Lab protocol."""

import json
import os
import sys
import reaper_python as reaper

if __name__ == "__main__":
    try:
        # REAPER may execute actions without defining __file__. Lua supplies the
        # installation directory before dispatch; ordinary Python can use __file__.
        script_folder = reaper.RPR_GetExtState("eleven_reaper_lab", "worker_script_folder")
        if not script_folder:
            script_path = globals().get("__file__")
            if script_path:
                script_folder = os.path.dirname(os.path.abspath(script_path))
        if not script_folder or not os.path.isabs(script_folder):
            raise RuntimeError("Cannot resolve the worker installation directory")
        if script_folder not in sys.path:
            sys.path.insert(0, script_folder)
        from erl_worker import run_worker
        run_worker("stt", reaper)
    except Exception:
        # Even a dependency/bootstrap failure must release the matching UI request.
        section = "eleven_reaper_lab"
        active_id = reaper.RPR_GetExtState(section, "active_request_id")
        if active_id:
            response = {"protocol_version": 2, "request_id": active_id,
                        "operation": "stt", "status": "failed", "logs": [],
                        "error": {"code": "WORKER_BOOTSTRAP_FAILED",
                                  "message": "Cannot start the worker. Verify that all application modules are installed.",
                                  "outcome_uncertain": True}}
            reaper.RPR_DeleteExtState(section, "request_api_key", False)
            reaper.RPR_SetExtState(section, "response_json", json.dumps(response), False)
