import errno
import http.client
import json
import math
import os
from pathlib import Path
import shutil
import time
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def executable(path):
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        raise ValueError('実行可能ファイルが見つかりません。設定とインストール先を確認してください')
    return str(path)


def launch_plan(engine, cfg, model, online=False, environment=None):
    env = dict(os.environ if environment is None else environment)
    common = {'model_id': model['id'], 'model_name': model['name'], 'port': cfg['port']}
    if engine == 'strata':
        if online:
            raise ValueError('Strataの取得・準備は既存のセットアップで行ってください')
        root = Path(cfg['directory'])
        python = executable(root / '.venv/bin/python')
        server = root / 'serve/server.py'
        if not server.is_file():
            raise ValueError('Strataのserve/server.pyが見つかりません')
        source = json.loads(Path(model['config']).read_text(encoding='utf-8-sig'))
        if not isinstance(source, dict) or not isinstance(source.get('args'), list) or not isinstance(source.get('exe'), str):
            raise ValueError('Strataの設定JSONにはexeとargsが必要です')
        base = Path(source.get('cwd') or str(root))
        if not base.is_absolute():
            base = root / base
        native = Path(source['exe'])
        native = (base / native).resolve() if not native.is_absolute() else native.resolve()
        env['PYTHONUNBUFFERED'] = '1'
        return {**common, 'command': [python, str(server), '--engine', 'strata', '--config', model['config'],
                                     '--host', '127.0.0.1', '--port', str(cfg['port'])],
                'cwd': str(root), 'env': env, 'engine_path': str(native)}
    if engine != 'splash':
        raise ValueError('不明なエンジンです')
    binary = cfg.get('executable') or shutil.which('splash')
    if not binary:
        binary = next((p for p in ('/opt/homebrew/bin/splash', '/usr/local/bin/splash') if os.path.isfile(p)), None)
    binary = executable(binary)
    command = [binary, 'serve', '--model', model['model'], '--host', '127.0.0.1', '--port', str(cfg['port'])]
    command.extend(model.get('args', []))
    if online:
        env.pop('HF_HUB_OFFLINE', None)
        env.pop('TRANSFORMERS_OFFLINE', None)
    else:
        command.append('--offline')
        env['HF_HUB_OFFLINE'] = '1'
    if 'modelCacheDir' in cfg:
        env['HF_HUB_CACHE'] = cfg['modelCacheDir']
    native = Path(binary).resolve().parent.parent / 'libexec/engine/splash'
    return {**common, 'command': command, 'cwd': str(Path.home()), 'env': env,
            'engine_path': str(native.resolve()) if native.is_file() else None}


def request_json(port, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(f'http://127.0.0.1:{port}{path}', timeout=1.5) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('監視APIの応答が大きすぎます')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('監視APIの応答形式が異なります')
        return value


def positive_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def observe(engine, port, previous=None, now=None, wake=''):
    now = time.monotonic() if now is None else now
    result = {'port': port, 'state': 'occupied', 'tps': None, 'context': None, 'sample': None}
    try:
        if engine == 'strata':
            health = request_json(port, '/health')
            if health.get('service') != 'strata':
                raise ValueError('このポートの応答はStrataではありません')
            result.update(state='running' if health.get('loaded') else 'unloaded',
                          model_name=health.get('model'), context=health.get('max_context'))
            try:
                metrics = request_json(port, '/metrics')
                live = metrics.get('live', {})
                result['context'] = metrics.get('engine', {}).get('max_context') or result['context']
                rate = live.get('tok_s')
                if live.get('state') == 'generating' and positive_number(rate):
                    result['tps'] = rate
            except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException, AttributeError, TypeError) as error:
                if isinstance(error, urllib.error.HTTPError):
                    error.close()
                result['error'] = '生成速度の監視不可'
            return result
        data = request_json(port, '/status')
        instance = data.get('instance', {})
        if (type(data.get('schema_version')) is not int or data['schema_version'] < 1
                or not isinstance(instance.get('id'), str) or not instance['id']
                or type(data.get('ready')) is not bool or not isinstance(data.get('metrics'), dict)):
            raise ValueError('このポートの応答はSplashではありません')
        released = data.get('weights', {}).get('released', False)
        result.update(state=('unloaded' if released else 'running') if data.get('ready') else 'starting',
                      model_name=instance.get('model'), context=data.get('maximum_context_tokens'),
                      server_pid=instance.get('pid'))
        count = data['metrics'].get('decode_output_tokens')
        generation = data.get('transport', {}).get('restarts', 0)
        if positive_number(count):
            sample = {'instance': instance['id'], 'generation': generation, 'count': count, 'time': now, 'wake': wake}
            result['sample'] = sample
            if previous and all(previous.get(k) == sample[k] for k in ('instance', 'generation', 'wake')):
                elapsed = now - previous.get('time', now)
                delta = count - previous.get('count', count)
                generating = data.get('scheduler', {}).get('decoding', 0) > 0
                if generating and 0 < elapsed <= 15 and delta > 0:
                    result['tps'] = delta / elapsed
        if data.get('transport', {}).get('status_stale') or data.get('transport', {}).get('recovering'):
            result.update(state='unavailable', tps=None, sample=None)
        return result
    except urllib.error.HTTPError as error:
        result['error'] = '監視APIに認証が必要です' if error.code in (401, 403) else f'監視API: HTTP {error.code}'
        error.close()
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError) or getattr(error.reason, 'errno', None) == errno.ECONNREFUSED:
            result['state'] = 'stopped'
        else:
            result['error'] = '監視APIから応答がありません'
    except (OSError, ValueError, http.client.HTTPException, AttributeError, TypeError, OverflowError):
        result['error'] = '監視APIの形式が異なるか、応答を取得できません'
    return result
