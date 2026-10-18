import math
import shlex

from .config import ENGINES

STATES = {'stopped': '停止中', 'running': '稼働中', 'starting': '起動中',
          'stopping': '停止中…', 'stop_failed': '停止失敗', 'error': 'エラー',
          'unknown': '所有権不明', 'external': '外部で稼働中', 'occupied': 'ポート使用中',
          'unloaded': '稼働中・モデル未ロード', 'unavailable': '稼働中・監視不可',
          'unconfigured': '未設定'}


def safe(value):
    return ''.join(c if ord(c) >= 32 and ord(c) != 127 else ' ' for c in str(value)).replace('|', '¦')


def numeric(value, suffix, divisor=1):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return f'{value / divisor:.1f} {suffix}'
    return '—'


def render(configuration, observations, selections, entry):
    if any(ord(c) < 32 or ord(c) == 127 or c == '|' for c in str(entry)):
        return ' | sfimage=exclamationmark.triangle\n---\n配置パスに改行・制御文字・パイプは使用できません\n'
    states = [v.get('state') for v in observations.values()]
    if configuration.errors or any(s in ('error', 'unknown', 'occupied', 'stop_failed') for s in states):
        icon = 'exclamationmark.triangle'
    elif any(s in ('starting', 'stopping') for s in states):
        icon = 'hourglass'
    elif any(s in ('running', 'unloaded', 'external', 'unavailable') for s in states):
        icon = 'server.rack'
    else:
        icon = 'power'
    lines = [f' | sfimage={icon}', '---']

    def action(title, verb, engine=None, mid=None):
        params = [verb] + ([engine] if engine else []) + ([mid] if mid else [])
        options = ' '.join(f'param{i + 1}={shlex.quote(p)}' for i, p in enumerate(params))
        lines.append(f'--{title} | bash={shlex.quote(str(entry))} {options} terminal=false refresh=true')

    for name in ENGINES:
        obs = observations.get(name, {})
        cfg = configuration.engines.get(name)
        state = obs.get('state', 'stopped' if cfg else 'unconfigured')
        lines.append(f'{name.capitalize()} — {STATES.get(state, "不明")}')
        if name in configuration.errors:
            lines.append(f'--{safe(configuration.errors[name])} | color=red')
        if obs.get('error'):
            lines.append(f'--{safe(obs["error"])} | color=red')
        lines.append(f'--稼働モデル: {safe(obs.get("model_name") or "—")}')
        port = obs.get('port') or (cfg or {}).get('port')
        if port:
            lines.append(f'--接続先: 127.0.0.1:{port}')
        lines += [f'--生成速度: {numeric(obs.get("tps"), "tok/s")}',
                  f'--エンジンメモリ: {numeric(obs.get("memory_bytes"), "GB", 1_000_000_000)}',
                  f'--エンジンPID: {safe(obs.get("engine_pid") or "—")}',
                  f'--コンテキスト: {safe(obs.get("context") or "—")}']
        mutable = state not in ('external', 'occupied', 'unknown', 'starting', 'stopping')
        if obs.get('owned'):
            action('停止…', 'stop', name)
        models = cfg.get('models', []) if cfg else []
        selected = selections.get(name)
        chosen = next((m for m in models if m['id'] == selected), None)
        if cfg and mutable and not obs.get('owned') and chosen:
            action('起動', 'start', name)
        if cfg and mutable and chosen and name == 'splash':
            action('ダウンロード／更新して起動…', 'download', name)
        if models:
            lines.append('--モデル')
            for model in models:
                checked = ' checked=true' if model['id'] == selected else ''
                if mutable:
                    params = f'param1=select param2={name} param3={model["id"]}'
                    lines.append(f'----{safe(model["name"])} | bash={shlex.quote(str(entry))} {params} terminal=false refresh=true{checked}')
                else:
                    lines.append(f'----{safe(model["name"])} | disabled=true{checked}')
            if not chosen:
                lines.append('--起動するモデルを選択してください')
        if port:
            action('チャットを開く', 'chat', name)
        action('ログを開く', 'log', name)
        if name == 'strata' and chosen:
            action('モデル設定を開く', 'model-config', name, chosen['id'])
    lines += ['---', f'設定を開く | bash={shlex.quote(str(entry))} param1=config terminal=false refresh=true']
    return '\n'.join(lines) + '\n'
