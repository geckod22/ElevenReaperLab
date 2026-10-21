"""Offline regression entry point. Run only after explicit user authorization."""

import argparse
import importlib
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lua-version", choices=("53", "54"), default="53")
    parser.add_argument("--lua-module-dir", help="Parent directory of an optional Lupa installation")
    parser.add_argument("--match", default="", help="Run only test IDs containing this text")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if args.lua_module_dir:
        sys.path.insert(0, args.lua_module_dir)
    modules = (
        ("test_registry_configuration", "RegistryConfigurationTests"),
        ("test_session_payloads", "SessionPayloadTests"),
        ("test_protocol_worker", "ProtocolWorkerTests"),
        ("test_cursor_ui", "CursorUiTests"),
        ("test_audio_workflows", "AudioWorkflowTests"),
    )
    # Apply guards before importing any tests, fixtures, or production helpers.
    with patch.object(socket, "socket", side_effect=AssertionError("Network access is forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("Network access is forbidden")):
        suite = unittest.TestSuite()
        for name, class_name in modules:
            module = importlib.import_module(name)
            if hasattr(module, "LUA_VERSION"):
                module.LUA_VERSION = args.lua_version
            cls = getattr(module, class_name)
            # Include inherited helpers, but do not repeat inherited test methods.
            for method in sorted(cls.__dict__):
                if method.startswith("test_") and callable(getattr(cls, method)):
                    case = cls(method)
                    if args.match in case.id():
                        suite.addTest(case)
        if suite.countTestCases() == 0:
            parser.error("No tests matched; nothing was executed")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
