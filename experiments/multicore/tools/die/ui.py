#!/usr/bin/env python3
"""Local, single-job dashboard for the real four-Coral RTL simulator."""
import argparse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import signal
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import webbrowser

from operators import CATALOG, ROOT, UPSTREAM, execute_operator, load_bundle, positive_clock, validate_profile

ARTIFACTS = {'report.html': 'text/html; charset=utf-8', 'profile.json': 'application/json',
             'execution.json': 'application/json', 'run.log': 'text/plain; charset=utf-8'}
TERMINAL = {'PASS', 'FAILED', 'CANCELLED'}


def options(data):
    """Only bounded simulator settings are accepted, never paths or commands."""
    defaults = dict(operator='add', rows=7, elements=37, rounds=1, schedule='parallel',
                    stall_period=3, run_timeout=420, clock_mhz=500)
    if not isinstance(data, dict) or set(data)-set(defaults):
        raise ValueError('未知运行参数')
    result = dict(defaults, **data)
    if (not isinstance(result['operator'], str) or result['operator'] not in CATALOG or
            result['schedule'] not in ('serial', 'parallel')):
        raise ValueError('无效的算子或调度方式')
    for name, lo, hi in [('rows', 1, 8), ('elements', 1, 128), ('rounds', 1, 2),
                         ('stall_period', 0, 1000), ('run_timeout', 1, 3600)]:
        value = result[name]
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(f'{name} 必须是 {lo}–{hi} 内的整数')
    try:
        if type(result['clock_mhz']) not in (int, float):
            raise ValueError('invalid clock type')
        result['clock_mhz'] = positive_clock(result['clock_mhz'])
    except (ValueError, TypeError, argparse.ArgumentTypeError):
        raise ValueError('假设频率必须为有限正数且不超过 1000000 MHz') from None
    if result['operator'] == 'add':
        result['rows'] = 7
    else:
        result['elements'], result['rounds'] = 37, 1
    if result['operator'] != 'pair':
        result['schedule'] = 'parallel'
    return result


class Dashboard:
    def __init__(self, firmware, results):
        self.firmware, self.results = firmware.resolve(), results.resolve()
        self.results.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.active = None
        self.worker = None
        self.cancel = threading.Event()
        self.closed = False
        self.token = secrets.token_urlsafe(32)
        self.ready = self.readiness()
        self.ready_checked = time.monotonic()

    def readiness(self):
        binary = UPSTREAM/'bazel-bin/experiments/multicore/smoke_die'
        status = dict(simulator=binary.is_file(), firmware=False,
                      firmware_path=str(self.firmware), simulator_path=str(binary))
        try:
            report = load_bundle(self.firmware)
            status.update(firmware=True, firmware_sha256=report['outputs_sha256']['controller.bin'])
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            status['error'] = str(error)
        status['ready'] = status['simulator'] and status['firmware']
        return status

    def path(self, name):
        if not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,160}', name) or name in ('.', '..'):
            raise ValueError('无效的运行编号')
        path = (self.results/name).resolve()
        if path.parent != self.results or not path.is_dir():
            raise ValueError('运行目录不存在')
        return path

    def persist(self):
        # Called under lock. Replace so readers never see partially written JSON.
        directory = self.path(self.active['id'])
        temporary = directory/'job.tmp'
        temporary.write_text(json.dumps(self.active, ensure_ascii=False, indent=2)+'\n')
        temporary.replace(directory/'job.json')

    def launch(self, data):
        settings = options(data)
        with self.lock:
            if self.closed:
                raise ValueError('服务正在关闭')
            if self.active and self.active['status'] not in TERMINAL:
                raise ValueError('已有任务运行中，请等待或取消')
            self.ready = self.readiness()
            if not self.ready['ready']:
                raise ValueError('请先在终端执行 ncoral.py build，完成后刷新页面')
            name = 'ui_'+settings['operator']+'_'+str(time.time_ns())
            # execute_operator creates this directory exclusively.
            self.active = dict(id=name, status='STARTING', settings=settings,
                               submitted=datetime.now(timezone.utc).isoformat())
            self.cancel = threading.Event()
            self.worker = threading.Thread(target=self.run, args=(settings, name, self.cancel), daemon=True)
            self.worker.start()
            return dict(self.active)

    def run(self, settings, name, cancel):
        def started(phase):
            with self.lock:
                self.active.update(phase, status='CANCELLING' if cancel.is_set() else 'RUNNING')
                self.persist()
        try:
            args = SimpleNamespace(**settings, firmware=self.firmware, output=self.results/name)
            profile, _, _ = execute_operator(args, cancel_event=cancel, on_started=started)
            final = dict(status='PASS', operators=profile['operators'],
                         host_wall_seconds=profile['host_wall_seconds'])
        except Exception as error:
            final = dict(status='CANCELLED' if cancel.is_set() else 'FAILED', error=str(error))
        with self.lock:
            self.active.update(final, finished=datetime.now(timezone.utc).isoformat())
            if (self.results/name).is_dir():
                self.persist()

    def stop_job(self):
        with self.lock:
            if self.active and self.active['status'] not in TERMINAL:
                self.cancel.set()
                self.active['status'] = 'CANCELLING'
            return dict(self.active) if self.active else None

    def close(self):
        with self.lock:
            self.closed = True
        self.stop_job()
        if self.worker:
            self.worker.join(timeout=15)

    def snapshot(self):
        with self.lock:
            active = dict(self.active) if self.active else None
        if not self.ready['ready'] and time.monotonic()-self.ready_checked > 5:
            self.ready = self.readiness()
            self.ready_checked = time.monotonic()
        history = []
        # Bound the history work to the latest 100 directories.
        for directory in sorted((p for p in self.results.iterdir() if p.is_dir() and not p.is_symlink()),
                                key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                directory = self.path(directory.name)
                job_file, profile_file = directory/'job.json', directory/'profile.json'
                item = json.loads(job_file.read_text()) if job_file.is_file() else dict(id=directory.name)
                if not isinstance(item, dict):
                    continue
                if profile_file.is_file():
                    profile = json.loads(profile_file.read_text())
                    validate_profile(profile)
                    item.update(status='PASS', operators=profile['operators'],
                                host_wall_seconds=profile.get('host_wall_seconds'),
                                finished=profile.get('created', item.get('finished', '')))
                elif not job_file.is_file():
                    if not (directory/'execution.json').is_file():
                        continue
                    item.update(status='FAILED', finished='')
                elif item.get('status') not in TERMINAL and (not active or active['id'] != directory.name):
                    item.update(status='FAILED', error='上次服务已停止；无已验证的成功报告')
                item['id'] = directory.name
                item['report'] = item.get('status') == 'PASS' and (directory/'report.html').is_file()
                history.append(item)
            except (OSError, ValueError, KeyError, RuntimeError):
                continue
        history.sort(key=lambda item: item.get('finished', item.get('submitted', '')), reverse=True)
        return dict(active=active, history=history, environment=self.ready)


def make_server(app, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, data, kind='application/json; charset=utf-8', status=200):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'SAMEORIGIN')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def trusted(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.trusted():
                return self.send(dict(error='invalid host'), status=403)
            path = urlsplit(self.path).path
            try:
                if path in ('/', '/architecture'):
                    html = Path(__file__).with_name('dashboard.html').read_text()
                    if path == '/architecture':
                        html = html.replace('<body>', '<body class="arch-expanded arch-only">')
                    return self.send(html.replace('__NCORAL_TOKEN__', app.token).encode(), 'text/html; charset=utf-8')
                if path == '/assets/architecture.js':
                    return self.send(Path(__file__).with_name('architecture.js').read_bytes(),
                                     'text/javascript; charset=utf-8')
                if path == '/api/state':
                    return self.send(app.snapshot())
                match = re.fullmatch(r'/runs/([^/]+)/(report.html|profile.json|execution.json|run.log)', path)
                if match:
                    directory = app.path(match[1])
                    artifact = (directory/match[2]).resolve()
                    if artifact.parent != directory:
                        raise ValueError('无效的文件路径')
                    if match[2] == 'report.html':
                        validate_profile(json.loads((directory/'profile.json').read_text()))
                    if match[2] == 'run.log':
                        # The dashboard only needs a bounded tail, not multi-MB logs.
                        with artifact.open('rb') as stream:
                            stream.seek(max(0, artifact.stat().st_size-65536))
                            content = stream.read(65536)
                    else:
                        content = artifact.read_bytes()
                    return self.send(content, ARTIFACTS[match[2]])
                return self.send(dict(error='not found'), status=404)
            except (OSError, ValueError, KeyError, RuntimeError) as error:
                return self.send(dict(error=str(error)), status=404)

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if (not self.trusted() or self.headers.get('Origin') not in (None, origin) or
                    self.headers.get('X-NCoral-Token') != app.token or
                    self.headers.get('Content-Type') != 'application/json'):
                return self.send(dict(error='invalid local session'), status=403)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('无效请求长度')
                data = json.loads(self.rfile.read(length))
                if self.path == '/api/run':
                    return self.send(app.launch(data), status=202)
                if self.path == '/api/cancel' and data == {}:
                    return self.send(app.stop_job())
                return self.send(dict(error='not found'), status=404)
            except (OSError, ValueError, TypeError, KeyError) as error:
                return self.send(dict(error=str(error)), status=400)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765, help='localhost port; 0 chooses a free port')
    parser.add_argument('--firmware', type=Path, default=ROOT/'die/logs/firmware/controller.bin')
    parser.add_argument('--results', type=Path, default=ROOT/'die/logs/operators')
    parser.add_argument('--session-timeout', type=int, default=7200, help='server lifetime in seconds (default two hours)')
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or not 1 <= args.session_timeout <= 86400:
        parser.error('port must be 0..65535, session timeout 1..86400 seconds')
    app = Dashboard(args.firmware, args.results)
    server = make_server(app, args.port)
    url = f'http://127.0.0.1:{server.server_port}'
    deadline = datetime.now(timezone.utc)+timedelta(seconds=args.session_timeout)
    print(f'NCoral UI: {url}\nServer deadline: {deadline.isoformat()} ({args.session_timeout}s)\n'
          f'Results: {app.results}\nCtrl+C stops the server and active simulation.', flush=True)
    timer = threading.Timer(args.session_timeout, server.shutdown)
    timer.daemon = True
    timer.start()
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    previous = signal.signal(signal.SIGTERM, shutdown)
    try:
        if args.open:
            webbrowser.open(url)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        app.close()
        server.server_close()
        signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
