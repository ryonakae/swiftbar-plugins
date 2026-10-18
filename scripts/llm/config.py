from dataclasses import dataclass, field
import json
from pathlib import Path
import re

ENGINES = ('strata', 'splash')
DEFAULT_PORTS = {'strata': 8080, 'splash': 8000}
ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z')
RESERVED = ('--model', '--host', '--port', '--offline', '--help', '--version')


@dataclass
class Configuration:
    engines: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)


def text(value, label):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError(f'{label}: 空でない文字列が必要です')
    return value


def path_value(value, label):
    value = text(value, label)
    if not (value.startswith('/') or value.startswith('~/')):
        raise ValueError(f'{label}: 絶対パスまたは ~/ を指定してください')
    return str(Path(value).expanduser())


def keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label}: JSONオブジェクトが必要です')
    extra = value.keys() - allowed
    if extra:
        raise ValueError(f'{label}: 不明な項目 {", ".join(sorted(extra))}')


def engine_config(name, source):
    allowed = {'port', 'models', 'directory'} if name == 'strata' else {
        'port', 'models', 'executable', 'modelCacheDir'}
    keys(source, allowed, name)
    result = dict(source)
    port = source.get('port', DEFAULT_PORTS[name])
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(f'{name}.port: 1〜65535の整数が必要です')
    result['port'] = port
    for key in ('directory',) if name == 'strata' else ('executable', 'modelCacheDir'):
        if key in source:
            result[key] = path_value(source[key], key)
    models = source.get('models', [])
    if not isinstance(models, list):
        raise ValueError(f'{name}.models: 配列が必要です')
    if name == 'strata' and models and 'directory' not in result:
        raise ValueError('strata.directory: インストール先を指定してください')
    seen = set()
    result['models'] = []
    for model in models:
        keys(model, {'id', 'name', 'config'} if name == 'strata' else {'id', 'name', 'model', 'args'}, 'model')
        mid = text(model.get('id'), 'id')
        if not ID.fullmatch(mid) or mid in seen:
            raise ValueError('id: 英数字・ハイフン・アンダースコアの一意なIDが必要です')
        seen.add(mid)
        item = {'id': mid, 'name': text(model.get('name'), 'name')}
        if name == 'strata':
            item['config'] = path_value(model.get('config'), 'config')
        else:
            item['model'] = text(model.get('model'), 'model')
            if any(c.isspace() for c in item['model']) or item['model'].startswith('-'):
                raise ValueError('model: Hugging FaceのモデルIDを指定してください')
            args = model.get('args', [])
            if not isinstance(args, list):
                raise ValueError('args: 文字列配列が必要です')
            for arg in args:
                text(arg, 'args')
                flag = arg.split('=', 1)[0]
                if flag in ('--', '-h') or (flag.startswith('--') and any(r.startswith(flag) for r in RESERVED)):
                    raise ValueError(f'args: {flag} はプラグインが管理します')
            item['args'] = list(args)
        result['models'].append(item)
    return result


def load(path):
    result = Configuration()
    try:
        source = json.loads(Path(path).read_text(encoding='utf-8'))
        keys(source, set(ENGINES), '設定')
    except (OSError, ValueError) as error:
        result.errors = {name: f'設定を読み込めません: {error}' for name in ENGINES}
        return result
    for name in ENGINES:
        if name not in source:
            continue
        try:
            result.engines[name] = engine_config(name, source[name])
        except ValueError as error:
            result.errors[name] = str(error)
    if len(result.engines) == 2 and result.engines['strata']['port'] == result.engines['splash']['port']:
        result.errors = {name: 'StrataとSplashには異なるポートを指定してください' for name in ENGINES}
        result.engines.clear()
    return result
