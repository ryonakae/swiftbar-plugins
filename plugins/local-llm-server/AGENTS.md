# Local LLM Server development guide

This directory contains the SwiftBar plugin for Strata and Splash. Read [README.md](README.md) for user-facing behavior first. This guide applies only to this plugin.

## Validation

Run these commands from this directory:

```fish
uv run --offline --no-project --no-python-downloads --python 3.12 -m unittest discover -s tests -v
bash -n local-llm-server.5s.sh
git diff --check
```

These commands require an installed Python 3.12 and `uv`. The plugin supports Python 3.12 or later and uses only the standard library. Run macOS API and process-management tests on macOS. HTTP and lifecycle tests use temporary ports and fake servers.

Run relevant tests before making changes. For bug fixes and behavior changes, confirm a failing test before implementing the change. Do not add a new test framework or shared infrastructure until this plugin needs it.

## Structure and boundaries

- `local-llm-server.5s.sh` is the entry point, with a relative symlink at `../../enabled/local-llm-server.5s.sh`. Preserve direct execution, relative links, chained links, and execution from other working directories. Do not put documentation or helper code in `enabled/`.
- `scripts/app.py` integrates actions and monitoring. `backends.py` handles engine-specific launch commands and HTTP monitoring; `menu.py` renders the menu. `processes.py` and `worker.py` manage ownership and shutdown; `macos.py` identifies processes and measures memory.
- Track only `config/*.example.json`. Do not add local configuration, personal paths, model weights, or logs to Git. The plugin must not register models or rewrite local configuration automatically.
- `Paths` in `app.py` defines state and log locations. Treat these separately from code locations: moving state to match a new code layout can lose ownership of a running server.

## Contracts to preserve

- External servers are monitoring-only. Never choose a shutdown target by port or process name alone. Preserve checks of the worker's launch generation, boot identity, PID start time, and nonce. Insufficient permissions do not mean a process has exited.
- Stop and model changes while running require confirmation. Revalidate state after confirmation, and do not start another server if shutdown fails. Do not add implicit SIGKILL or automatic restart.
- Leave Splash downloads to explicit starts and model switches. Periodic refreshes and model selection while stopped must not download or start anything. Do not overwrite Strata's source configuration JSON or Strata Menubar settings.
- Memory means the physical footprint of the inference engine alone. Show `—` when unavailable; do not substitute RSS, Metal allocations, or system-wide RAM.
- Write fixed UI text, documentation, and code comments in English. Comments should explain why, not describe what the code does. Do not translate user-supplied model display names. Escape them to prevent SwiftBar line or parameter injection. Pass shell arguments as arrays, and never embed user values in AppleScript source.
- SwiftBar 2.1.1 disables submenu headings without actions, so preserve `refresh=true` on those headings. When changing menu hierarchy or navigation, check the real UI if possible.

## Real-device operations

Do not load or download real models, stop running servers, or quit other apps during routine validation. Before real-server operations, large downloads, or permission changes, confirm the targets and impact and stay within the user's approval.

Passing automated tests does not validate untested real-device behavior. In particular, real-engine shutdown and memory comparisons with Activity Monitor require separate checks.

The [design and initial implementation record](docs/plans/2026-10-19-llm-menu.md) and [requirements discussion log](docs/dig/2026-10-19-splash-plugin.md) provide background. They include historical open questions and old paths; consult the README and implementation for current behavior.
