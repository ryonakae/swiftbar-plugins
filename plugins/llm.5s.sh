#!/bin/bash
# <swiftbar.title>Local LLM</swiftbar.title>
# <swiftbar.desc>Monitor, start, and stop Strata and Splash</swiftbar.desc>
# <swiftbar.version>1.0</swiftbar.version>
# <swiftbar.dependencies>uv, Python 3.12+</swiftbar.dependencies>

ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)" || exit 1
UV="$(command -v uv || true)"
if [[ -z "$UV" ]]; then
    for candidate in /opt/homebrew/bin/uv /usr/local/bin/uv; do
        if [[ -x "$candidate" ]]; then
            UV="$candidate"
            break
        fi
    done
fi
if [[ -z "$UV" ]]; then
    printf ' | sfimage=exclamationmark.triangle\n---\nuv is required. Follow the installation steps in README.\n'
    exit 0
fi
if ! "$UV" run --offline --no-project --no-python-downloads --python '>=3.12' "$ROOT/scripts/llm/app.py" "$@"; then
    if [[ $# -eq 0 ]]; then
        printf ' | sfimage=exclamationmark.triangle\n---\nFailed to run the plugin. Check Python 3.12+ and the SwiftBar log.\n'
    fi
fi
