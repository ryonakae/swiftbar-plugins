#!/bin/bash
# <swiftbar.title>Local LLM</swiftbar.title>
# <swiftbar.desc>StrataとSplashの状態・起動停止</swiftbar.desc>
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
    printf ' | sfimage=exclamationmark.triangle\n---\nuvが必要です。READMEの導入手順を確認してください\n'
    exit 0
fi
if ! "$UV" run --offline --no-project --no-python-downloads --python '>=3.12' "$ROOT/scripts/llm/app.py" "$@"; then
    if [[ $# -eq 0 ]]; then
        printf ' | sfimage=exclamationmark.triangle\n---\n実行に失敗しました。Python 3.12以上とSwiftBarのログを確認してください\n'
    fi
fi
