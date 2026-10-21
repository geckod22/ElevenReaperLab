# Regression Tests

## Python files

| File | Role |
| --- | --- |
| `run_regression.py` | Runs the suite, blocks network connections, selects the Lua runtime, and avoids repeating inherited tests. |
| `test_registry_configuration.py` | Configuration validation, preset precedence, model catalog, Lua/Python parity, and source syntax. |
| `test_session_payloads.py` | Model selections, manual settings, resets, text limits, and API payload construction. |
| `test_protocol_worker.py` | Lua/Python messages, request identity, action startup, simulated HTTP errors/timeouts, and audio recovery. |
| `test_cursor_ui.py` | Cursor insertion, output-folder checks, voice search, and log filtering/redaction. |
| `test_audio_workflows.py` | TTS input rules, generated-item selection, import failures, and normalization state preservation. |

## Requirements

Python and **Lupa** with the Lua 5.3 runtime; Lua 5.4 is also selectable. Lupa is needed only for tests. Fixtures use `erl_config.example.json`, temporary files, and simulated REAPER/HTTP bindings; they do not require personal configuration, real credentials, or live ElevenLabs calls.

## Running

Obtain explicit permission before running tests, as required by [AGENTS.md](../AGENTS.md). From the project root:

```text
python -B tests/run_regression.py --lua-version 53
```

To run a specific class or method, filter its name:

```text
python -B tests/run_regression.py --lua-version 53 --match AudioWorkflowTests
```

If Lupa is installed separately, append `--lua-module-dir "PATH_TO_LUPA_PARENT"`. For Lua 5.4, use `--lua-version 54`.

Use the runner rather than `unittest discover`: the runner installs network guards and prevents inherited test cases from running more than once.

These are offline tests; they do not verify actual REAPER/ReaImGui/SWS behavior or service acceptance. The optional [REAPER checklist](REAPER_CHECKLIST.md) covers manual integration checks and is not used by the runner. Record execution results in [Progress.md](../docs/development/Progress.md); the current suite has not yet been executed.
