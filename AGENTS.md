# Project Instructions

## Action plan

Read [Action_Plan.MD](docs/development/Action_Plan.MD) before starting work on this project. Use it as the reference for the application scope, terminology, behavior requirements, and acceptance criteria. If a task conflicts with the plan or requires clarification, ask the user before proceeding with the affected work.

## Code language

Use English in all scripts unless the user explicitly requests otherwise. This includes comments, function names, variable names, and other code identifiers.

## Clarification and confirmation

When in doubt about an unresolved issue, something that has not been clarified, or a change that would substantially alter the existing behavior, architecture, or agreed scope, ask the user for confirmation before proceeding with the affected work. Do not silently resolve these uncertainties through assumptions.

## Progress tracking

Read [Progress.md](docs/development/Progress.md) at the start of work. After each implementation session, update the current status, completed work, verification actually performed, remaining checks, and next action. Mark work verified only after its acceptance criteria have been checked with the user's permission. Keep documentation focused on the current release rather than development chronology.

## Protecting REAPER work

Preserve original audio files, cursor position, and selections. Make timeline changes undoable through coherent REAPER Undo actions.

## Credentials and API costs

Never put API keys in source code, example configurations, or logs. Prefer simulated API responses for tests once testing has been authorized. Obtain explicit permission before making test calls that consume ElevenLabs credits, and explain the expected credit usage when known.

## Test permission and reporting

Ask for and receive the user's explicit permission before running any type of test. This includes automated tests, simulated or mocked tests, manual tests, REAPER trials, and live API tests, regardless of whether they consume credits or modify data. Describe the proposed tests and wait for approval before executing them. Approval applies only to the tests within the scope authorized by the user; a general implementation request does not authorize testing.

Clearly distinguish automated checks from tests actually performed in REAPER. Report which tests were run, their results, and which checks remain pending. Do not claim that unperformed tests passed.

## Platform compatibility

Maintain Windows and macOS support. Resolve installation paths relative to the script location rather than hardcoding personal paths.

## ElevenLabs models

Check model capabilities and parameters against official ElevenLabs documentation before integrating them. Do not infer compatibility from model names.

## Documentation

Keep project documentation in English unless the user explicitly requests otherwise. Update the README and examples when configuration, requirements, or usage changes. Keep general working rules in this file and detailed implementation requirements in the action plan to avoid duplication.

Internal planning, progress, and test reports belong in `docs/development/`; technical protocol and model-registry guides belong in `docs/technical/`. Keep this file, the main README, and the configuration guide at the project root. Resolve documentation links relative to each Markdown file.
