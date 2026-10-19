#!/bin/bash
# <swiftbar.title>Local LLM Server</swiftbar.title>
# <swiftbar.desc>Monitor, start, and stop Strata and Splash</swiftbar.desc>
# <swiftbar.version>1.0</swiftbar.version>
# <swiftbar.dependencies>uv, Python 3.12+</swiftbar.dependencies>

SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
    DIRECTORY="$(cd -P -- "$(dirname -- "$SOURCE")" && pwd)" || exit 1
    SOURCE="$(readlink "$SOURCE")" || exit 1
    [[ "$SOURCE" = /* ]] || SOURCE="$DIRECTORY/$SOURCE"
done
ROOT="$(cd -P -- "$(dirname -- "$SOURCE")" && pwd)" || exit 1
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
    printf ' | sfimage=exclamationmark.circle.fill\n---\nuv is required. Follow the installation steps in README.\n'
    exit 0
fi
if ! "$UV" run --offline --no-project --no-python-downloads --python '>=3.12' "$ROOT/scripts/app.py" "$@"; then
    if [[ $# -eq 0 ]]; then
        printf ' | sfimage=exclamationmark.circle.fill\n---\nFailed to run the plugin. Check Python 3.12+ and the SwiftBar log.\n'
    fi
fi
