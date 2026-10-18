"""Local API validation, job exclusivity, cancellation and artifact isolation."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ui import Dashboard, make_server, options
from operators import bounded


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(Dashboard, 'readiness', return_value=dict(ready=True)))
        self.app = Dashboard(self.directory/'controller.bin', self.directory/'runs')
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.app.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.stack.close()

    def request(self, path, data=None, **headers):
        if data is not None:
            headers = {'Content-Type':'application/json', 'X-NCoral-Token':self.app.token, **headers}
        request = Request(self.url+path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()

    def test_rejects_cross_origin_and_unauthenticated_launches(self):
        self.assertEqual(self.request('/api/run', {}, Origin='https://example.invalid')[0], 403)
        self.assertEqual(self.request('/api/run', {}, **{'X-NCoral-Token':'wrong'})[0], 403)
        self.assertEqual(self.request('/api/run', {}, Host='example.invalid')[0], 403)
        self.assertEqual(self.request('/api/run', {}, **{'Content-Type':'text/plain'})[0], 403)
        self.assertIsNone(self.app.active)

    def test_serves_page_and_blocks_arbitrary_files(self):
        code, page = self.request('/')
        self.assertEqual(code, 200)
        self.assertIn('一个控制核，四个独立执行核'.encode(), page)
        self.assertNotIn(b'__NCORAL_TOKEN__', page)
        code, architecture = self.request('/architecture')
        self.assertEqual(code, 200)
        self.assertIn(b'arch-expanded arch-only', architecture)
        self.assertIn('播放数据流'.encode(), architecture)
        self.assertIn('显示矩阵扩展'.encode(), architecture)
        code, script = self.request('/assets/architecture.js')
        self.assertEqual(code, 200)
        self.assertIn(b'function setupArchitecture()', script)
        job = self.app.results/'example'; job.mkdir()
        (job/'run.log').write_text('local log')
        self.assertEqual(self.request('/runs/example/run.log'), (200, b'local log'))
        secret = self.directory/'secret'; secret.write_text('private')
        (job/'profile.json').symlink_to(secret)
        self.assertEqual(self.request('/runs/example/profile.json')[0], 404)
        self.assertEqual(self.request('/runs/../secret/run.log')[0], 404)
        self.assertEqual(self.request('/runs/example/job.json')[0], 404)
        self.assertEqual(self.request('/api/state')[0], 200)

    def test_exclusive_job_and_cancel_persist_failure_without_report(self):
        entered = threading.Event()
        def fake_run(args, *, cancel_event, on_started):
            args.output.mkdir()
            on_started(dict(started='2026-10-18T00:00:00+00:00', deadline='2026-10-18T00:01:00+00:00', deadline_seconds=60))
            entered.set()
            if not cancel_event.wait(3):
                raise AssertionError('cancel was not delivered')
            raise RuntimeError('cancelled by test')
        with patch('ui.execute_operator', side_effect=fake_run):
            self.assertEqual(self.request('/api/run', dict(operator='pair',schedule='serial'))[0], 202)
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request('/api/run', dict(operator='add'))[0], 400)
            self.assertEqual(self.request('/api/cancel', {})[0], 200)
            self.app.worker.join(timeout=3)
            snapshot = self.app.snapshot()
            self.assertEqual(snapshot['active']['status'], 'CANCELLED')
            self.assertFalse(snapshot['history'][0]['report'])
            self.assertEqual(snapshot['active']['settings']['schedule'], 'serial')
            self.assertEqual(snapshot['active']['deadline_seconds'], 60)
            self.assertEqual(self.request('/runs/'+snapshot['active']['id']+'/report.html')[0], 404)

    def test_worker_failure_preserves_error_and_allows_next_run(self):
        def fail(args, **kwargs):
            args.output.mkdir()
            raise RuntimeError('simulator failed')
        with patch('ui.execute_operator', side_effect=fail):
            self.assertEqual(self.request('/api/run', dict(operator='conv'))[0], 202)
            self.app.worker.join(timeout=2)
            state = self.app.snapshot()
            self.assertEqual(state['active']['status'], 'FAILED')
            self.assertIn('simulator failed', state['active']['error'])
            self.assertFalse(state['history'][0]['report'])
            self.assertEqual(self.request('/api/run', dict(operator='add'))[0], 202)
            self.app.worker.join(timeout=2)

    def test_restart_marks_interrupted_jobs_failed(self):
        folder = self.app.results/'interrupted'; folder.mkdir()
        (folder/'job.json').write_text(json.dumps(dict(id='interrupted',status='RUNNING')))
        state = self.app.snapshot()
        self.assertEqual(state['history'][0]['status'], 'FAILED')
        self.assertFalse(state['history'][0]['report'])


class SettingsTests(unittest.TestCase):
    def test_rejects_paths_commands_and_unbounded_settings(self):
        for data in [[], {'firmware':'/bin/sh'}, {'operator':'shell'}, {'operator':[]},
                     {'elements':True}, {'rows':9}, {'run_timeout':3601}, {'rounds':3},
                     {'stall_period':-1}, {'clock_mhz':float('nan')}, {'clock_mhz':True}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                options(data)
        self.assertEqual(options(dict(operator='conv',elements=80,rounds=2))['rounds'], 1)
        self.assertEqual(options(dict(operator='pair',schedule='serial'))['schedule'], 'serial')

    def test_bounded_cancel_reaps_simulator_process(self):
        event = threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder)/'run.log'
            timer = threading.Timer(.3, event.set)
            timer.start()
            try:
                result = bounded([sys.executable, '-c', 'import os,time;print(os.getpid(),flush=True);time.sleep(20)'],
                                 3, log, Path(folder), cancel_event=event)
            finally:
                timer.cancel()
            self.assertEqual(result['exit_code'], 130)
            pid = int(log.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_bounded_timeout_never_becomes_success(self):
        with tempfile.TemporaryDirectory() as folder:
            result = bounded([sys.executable,'-c','import time;time.sleep(20)'], .1,
                             Path(folder)/'timeout.log', Path(folder))
            self.assertEqual(result['exit_code'], 124)
            self.assertIn('deadline', result)


if __name__ == '__main__':
    unittest.main()
