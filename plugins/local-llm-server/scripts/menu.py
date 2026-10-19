import math
import shlex

from .config import ENGINES

STATES = {'stopped': 'Stopped', 'running': 'Running', 'starting': 'Starting',
          'stopping': 'Stopping…', 'stop_failed': 'Stop failed', 'error': 'Error',
          'unknown': 'Ownership unknown', 'external': 'Running externally', 'occupied': 'Port in use',
          'unloaded': 'Running', 'unavailable': 'Running',
          'unconfigured': 'Not configured'}


def safe(value):
    return ''.join(c if ord(c) >= 32 and ord(c) != 127 else ' ' for c in str(value)).replace('|', '¦')


def numeric(value, suffix, divisor=1):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return f'{value / divisor:.1f} {suffix}'
    return '—'


def render(configuration, observations, selections, entry):
    if any(ord(c) < 32 or ord(c) == 127 or c == '|' for c in str(entry)):
        return ' | sfimage=exclamationmark.circle.fill\n---\nPlugin path must not contain newlines, control characters, or pipes\n'
    states = [v.get('state') for v in observations.values()]
    if configuration.errors or any(s in ('error', 'unknown', 'occupied', 'stop_failed') for s in states):
        icon = 'exclamationmark.circle.fill'
    elif any(s in ('running', 'stopping', 'unloaded', 'external', 'unavailable') for s in states):
        icon = 'memorychip.fill'
    else:
        icon = 'memorychip'
    lines = [f' | sfimage={icon}', '---']

    def action(title, verb, engine=None, mid=None, depth=1):
        params = [verb] + ([engine] if engine else []) + ([mid] if mid else [])
        options = ' '.join(f'param{i + 1}={shlex.quote(p)}' for i, p in enumerate(params))
        lines.append(f'{"--" * depth}{title} | bash={shlex.quote(str(entry))} {options} terminal=false refresh=true')

    for name in ENGINES:
        obs = observations.get(name, {})
        cfg = configuration.engines.get(name)
        state = obs.get('state', 'stopped' if cfg else 'unconfigured')
        # SwiftBar 2.1.1 disables submenu headings without an action.
        lines.append(f'{name.capitalize()} — {STATES.get(state, "Unknown")} | refresh=true')
        if name in configuration.errors:
            lines.append(f'--{safe(configuration.errors[name])} | color=red')
        if obs.get('error'):
            lines.append(f'--{safe(obs["error"])} | color=red')
        lines.append(f'--Running model: {safe(obs.get("model_name") or "—")}')
        if state == 'unloaded':
            lines.append('--Model status: Unloaded (idle release)')
        elif state == 'unavailable':
            lines.append('--Monitoring: Unavailable')
        port = obs.get('port') or (cfg or {}).get('port')
        if port:
            lines.append(f'--Endpoint: 127.0.0.1:{port}')
        lines += [f'--Generation speed: {numeric(obs.get("tps"), "tok/s")}',
                  f'--Engine memory: {numeric(obs.get("memory_bytes"), "GB", 1_000_000_000)}',
                  f'--Engine PID: {safe(obs.get("engine_pid") or "—")}',
                  f'--Context limit: {safe(obs.get("context") or "—")}', '-----']
        mutable = state not in ('external', 'occupied', 'unknown', 'starting', 'stopping')
        if obs.get('owned'):
            action('Stop server…', 'stop', name)
        models = cfg.get('models', []) if cfg else []
        selected = selections.get(name)
        chosen = next((m for m in models if m['id'] == selected), None)
        if cfg and mutable and not obs.get('owned') and chosen:
            action('Start server', 'start', name)
        if models:
            lines.append('--Models | refresh=true')
            for model in models:
                checked = ' checked=true' if model['id'] == selected else ''
                if name == 'strata':
                    lines.append(f'----{safe(model["name"])} | refresh=true{checked}')
                    if mutable:
                        action('Select model', 'select', name, model['id'], depth=3)
                    else:
                        lines.append('------Select model | disabled=true')
                    action('Open model config', 'model-config', name, model['id'], depth=3)
                elif mutable:
                    params = f'param1=select param2={name} param3={model["id"]}'
                    lines.append(f'----{safe(model["name"])} | bash={shlex.quote(str(entry))} {params} terminal=false refresh=true{checked}')
                else:
                    lines.append(f'----{safe(model["name"])} | disabled=true{checked}')
            if not chosen:
                lines.append('--Select a model to start')
        if port and state not in ('stopped', 'unconfigured', 'error'):
            action('Open chat', 'chat', name)
        else:
            lines.append('--Open chat | disabled=true')
        action('Open log', 'log', name)
    lines += ['---', f'Open config | bash={shlex.quote(str(entry))} param1=config terminal=false refresh=true']
    return '\n'.join(lines) + '\n'
