from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'scripts'

from . import backends, config, dialogs, macos, menu
from .processes import Manager


@dataclass
class Paths:
    home: Path
    root: Path = Path(__file__).resolve().parents[1]

    @property
    def config(self):
        return self.root / 'config/local-llm-server.json'

    @property
    def state(self):
        return self.home / 'Library/Application Support/swiftbar-plugins/llm'

    @property
    def logs(self):
        return self.home / 'Library/Logs/swiftbar-plugins/llm'


def read_json(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.write-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def connection_port(status, settings):
    port = status.get('port') if status.get('owned') or status.get('state') == 'unknown' else None
    return port or (settings or {}).get('port')


class Controller:
    def __init__(self, paths, confirm=dialogs.confirm):
        self.paths = paths
        self.manager = Manager(paths.state, paths.logs)
        self.confirm = confirm

    def selection(self, engine):
        return read_json(self.paths.state / f'{engine}-selection.json').get('id')

    def choose(self, engine, mid):
        save_json(self.paths.state / f'{engine}-selection.json', {'id': mid})

    def fingerprint(self, engine):
        try:
            digest = hashlib.sha256(self.paths.config.read_bytes()).hexdigest()
        except OSError:
            digest = None
        status = self.manager.status(engine)
        return digest, status.get('nonce'), status.get('owned'), self.selection(engine)

    def action(self, verb, engine, mid=None):
        if engine not in config.ENGINES or verb not in ('start', 'stop', 'select'):
            raise ValueError('Unknown action.')
        error_path = self.paths.state / f'{engine}-action-error.json'
        try:
            result = self._action(verb, engine, mid)
        except (OSError, ValueError, RuntimeError) as error:
            save_json(error_path, {'error': str(error)})
            raise
        if not result.get('cancelled'):
            save_json(error_path, {})
        return result

    def _action(self, verb, engine, mid):
        operation_lock = self.paths.state / f'{engine}-action.lock'
        with lock(operation_lock):
            before = self.fingerprint(engine)
            current = self.manager.status(engine)
            cfg = config.load(self.paths.config)
            if verb != 'stop' and engine in cfg.errors:
                raise ValueError(cfg.errors[engine])
            settings = cfg.engines.get(engine)
            if verb != 'stop':
                if settings is None:
                    raise ValueError('Engine is not configured.')
                wanted = mid if verb == 'select' else self.selection(engine)
                model = next((m for m in settings['models'] if m['id'] == wanted), None)
                if model is None:
                    raise ValueError('Select a model registered in the configuration.')
            if current.get('state') == 'unknown':
                raise ValueError('Ownership cannot be verified. Check the existing server manually.')
            if verb == 'stop' and not current.get('owned'):
                raise ValueError('No managed server is running. External servers will not be stopped.')
            if verb == 'start' and current.get('owned'):
                raise ValueError('A managed server is already running.')
            if not current.get('owned') and verb != 'stop':
                external = backends.observe(engine, settings['port'])
                if external['state'] != 'stopped':
                    raise ValueError('The port is in use or cannot be checked. Stop external servers manually.')
            if verb == 'select' and self.selection(engine) == mid:
                return current
            needs_confirmation = verb == 'stop' or (verb == 'select' and current.get('owned'))
        if needs_confirmation:
            if verb == 'stop':
                message = f'Stop {engine.capitalize()}? Active requests will be interrupted.'
            else:
                message = f'Change the {engine.capitalize()} model? The current server will be stopped and active requests interrupted.'
            if not self.confirm(message):
                return {**current, 'cancelled': True}
        with lock(operation_lock):
            if self.fingerprint(engine) != before:
                raise ValueError('Configuration, selection, or server changed while confirming. Refresh the menu and try again.')
            current = self.manager.status(engine)
            if verb == 'stop':
                return self.manager.stop(engine, current['nonce'])
            if current.get('owned'):
                stopped = self.manager.stop(engine, current['nonce'])
                if stopped.get('state') != 'stopped':
                    raise ValueError('Stop did not complete. The next model will not be started.')
            if verb == 'select':
                self.choose(engine, mid)
                if not current.get('owned'):
                    return current
            launch = backends.launch_plan(engine, settings, model)
            return self.manager.start(engine, launch)

    def observations(self):
        cfg = config.load(self.paths.config)
        views = {}
        for engine in config.ENGINES:
            current = self.manager.status(engine)
            settings = cfg.engines.get(engine)
            port = connection_port(current, settings)
            if not port:
                views[engine] = current if current.get('state') != 'stopped' else {'state': 'unconfigured'}
                continue
            sample_path = self.paths.state / f'{engine}-sample.json'
            with lock(self.paths.state / f'{engine}-sample.lock'):
                saved = read_json(sample_path)
                clock = time.monotonic()
                wall = time.time()
                previous = saved.get('sample')
                if previous and abs((wall - saved.get('wall', wall)) - (clock - previous.get('time', clock))) > 2:
                    previous = None
                observed = backends.observe(engine, port, previous, now=clock)
                sample = observed.pop('sample', None)
                memory = self.memory(engine, current, observed)
                if sample:
                    sample['engine_generation'] = memory.get('engine_generation')
                    if previous and previous.get('engine_generation') != sample['engine_generation']:
                        observed['tps'] = None
                save_json(sample_path, {'sample': sample, 'wall': wall})
            view = {**current, **observed, **memory}
            view['model_name'] = (current.get('model_name') or observed.get('model_name')) if current.get('owned') else observed.get('model_name')
            if current.get('owned'):
                if current['state'] in ('stopping', 'stop_failed', 'error'):
                    view['state'] = current['state']
                elif observed['state'] == 'stopped':
                    view['state'] = 'starting'
                elif observed['state'] == 'occupied':
                    view['state'] = 'unavailable'
            elif current.get('state') == 'unknown':
                view['state'] = 'unknown'
            elif observed['state'] in ('running', 'starting', 'unloaded', 'unavailable'):
                view['state'] = 'external'
            elif observed['state'] == 'stopped' and current.get('state') == 'error':
                view.update(state='error', error=current.get('error'))
            failure = read_json(self.paths.state / f'{engine}-action-error.json').get('error')
            if failure:
                view['error'] = failure
                if view['state'] == 'stopped':
                    view['state'] = 'error'
            views[engine] = view
        return cfg, views

    def memory(self, engine, current, observed):
        root = current.get('root_pid') if current.get('owned') else observed.get('server_pid')
        if type(root) is not int or root <= 0:
            return {}
        try:
            children = macos.descendants(root)
            native = current.get('engine_path')
            if native:
                candidates = macos.match_executable(children, native)
            elif engine == 'splash':
                candidates = [p for p in children if Path(p.path).name == 'splash' and Path(p.path).parent.name == 'engine']
            else:
                return {}
            candidates = [p for p in candidates if p.uid == os.getuid()]
            if len(candidates) != 1:
                return {}
            process = candidates[0]
            value = macos.footprint(process.pid, process.start_time)
            return {'engine_pid': process.pid, 'memory_bytes': value,
                    'engine_generation': f'{process.pid}:{process.start_time}'}
        except OSError:
            return {}

    def render(self, entry):
        cfg, views = self.observations()
        return menu.render(cfg, views, {e: self.selection(e) for e in config.ENGINES}, entry)

    def open_target(self, verb, engine=None, mid=None):
        cfg = config.load(self.paths.config)
        if verb == 'config':
            target = self.paths.config if self.paths.config.exists() else self.paths.config.parent
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.mkdir()
        elif engine in config.ENGINES and verb == 'log':
            status = self.manager.status(engine)
            target = status.get('log') or self.paths.logs
            self.paths.logs.mkdir(parents=True, exist_ok=True)
        elif engine in config.ENGINES and verb == 'chat':
            status = self.manager.status(engine)
            port = connection_port(status, cfg.engines.get(engine))
            if not port:
                raise ValueError('Endpoint is not configured.')
            if (not status.get('owned') and status.get('state') != 'unknown'
                    and backends.observe(engine, port)['state'] == 'stopped'):
                raise ValueError('Server is not running.')
            target = f'http://127.0.0.1:{port}/'
        elif engine == 'strata' and verb == 'model-config':
            target = next((m['config'] for m in cfg.engines.get(engine, {}).get('models', []) if m['id'] == mid), None)
            if not target:
                raise ValueError('Model config was not found.')
        else:
            raise ValueError('Unknown action.')
        subprocess.run(['/usr/bin/open', str(target)], check=True)


def main():
    os.umask(0o077)
    controller = Controller(Paths(Path.home()))
    args = sys.argv[1:]
    entry = Path(__file__).resolve().parents[1] / 'local-llm-server.5s.sh'
    try:
        if not args:
            print(controller.render(entry), end='')
        elif args[0] in ('start', 'stop', 'select') and len(args) in (2, 3):
            result = controller.action(*args)
            if result.get('state') in ('error', 'stop_failed', 'unknown'):
                dialogs.notice(result.get('error') or 'The action could not be completed. Check the log.')
        elif args[0] in ('config', 'log', 'chat', 'model-config') and len(args) <= 3:
            controller.open_target(*args)
        else:
            raise ValueError('Invalid arguments.')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if args:
            dialogs.notice(str(error))
        else:
            print(f' | sfimage=exclamationmark.triangle\n---\n{menu.safe(error)}')
        return 1 if args else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
