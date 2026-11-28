import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from runner.backends import cli_backend, cli_command, consume_item, empty_execution, progress, resolve_runtime


class BackendV3Tests(unittest.TestCase):
    def test_plain_cli_preserves_model_and_effort_inheritance(self):
        request = {'run_dir': 'run', 'workdir': '.', 'approval': 'never',
                   'sandbox': 'workspace-write', 'add_dirs': ['one', 'two']}
        with patch('runner.backends.find_codex', return_value=Path(sys.executable)):
            argv = cli_command(request)
            self.assertEqual(argv.count('--add-dir'), 2)
            self.assertNotIn('--output-schema', argv)
            self.assertNotIn('-m', argv)
            self.assertFalse(any('model_reasoning_effort' in value for value in argv))
            argv = cli_command({**request, 'resume': 'thread'})
            self.assertIn('sandbox_workspace_write.writable_roots=["one", "two"]', argv)
            self.assertNotIn('--add-dir', argv)

    def test_legacy_store_pin_is_automatically_rediscovered(self):
        with patch('runner.backends.read_json', return_value={
                'mode': 'existing-cli', 'codex_path': r'C:\Program Files\WindowsApps\OpenAI.Codex_1\app\resources\codex.exe'}):
            with patch('runner.backends.find_codex') as discover:
                resolve_runtime('', ROOT / 'config' / 'runtime.local.json')
                discover.assert_called_once_with('')

    def test_progress_exposes_category_and_event_clock(self):
        scratch = ROOT / 'tests' / '.work'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            run = Path(tmp)
            state = empty_execution()
            consume_item({'type': 'file_change', 'changes': [{'path': 'a.txt', 'kind': 'update'}]}, state)
            progress(run, state, 'running', 'file_change', event=True)
            data = json.loads((run / 'progress.json').read_text(encoding='utf-8'))
            self.assertGreater(data['last_event_at'], 0)
            self.assertEqual(data['completed_tool_count'], 1)
            self.assertEqual(data['recent_action'], 'file_change')
            self.assertEqual(state['changed_files'], ['a.txt'])

    def test_retry_only_when_resume_reports_active_writer(self):
        scratch = ROOT / 'tests' / '.work'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            run = Path(tmp)
            with patch('runner.backends.cli_command', return_value=['codex', '-']), \
                 patch('runner.backends.cli_attempt', side_effect=[
                     {**empty_execution(), 'status': 'thread_busy'},
                     {**empty_execution(), 'status': 'completed'}]) as invoke, \
                 patch('runner.backends.time.sleep'):
                result = cli_backend({'resume': 'thread-id'}, run, prompt='果')
                self.assertEqual(invoke.call_count, 2)
                self.assertEqual(result['status'], 'completed')

    @unittest.skipUnless(os.name == 'nt', 'private console relay is Windows-specific')
    def test_windows_private_console_delivers_ctrl_c(self):
        relay = ROOT / 'scripts/runner/processes.py'
        program = ('import signal, sys, time\n'
                   'signal.signal(signal.SIGINT, lambda *_: sys.exit(8))\n'
                   'print(sys.stdin.readline().strip(), flush=True)\n'
                   'for _ in range(500): time.sleep(.01)\n')
        info = subprocess.STARTUPINFO()
        info.dwFlags = subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        argv = [sys.executable, '-B', str(relay), '_console_cli', sys.executable, '-B', '-c', program]
        with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              startupinfo=info, creationflags=subprocess.CREATE_NEW_CONSOLE) as proc:
            proc.stdin.write('就绪\n'.encode('utf-8'))
            proc.stdin.flush()
            try:
                # The child can use a host-dependent stdio encoding.
                self.assertTrue(proc.stdout.readline().strip())
                sender = ('import sys; from types import SimpleNamespace; '
                          'from runner.backends import soft_interrupt; '
                          'sys.exit(0 if soft_interrupt(SimpleNamespace(pid=int(sys.argv[1]), poll=lambda: None)) else 1)')
                subprocess.run([sys.executable, '-B', '-c', sender, str(proc.pid)], cwd=ROOT/'scripts',
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=3, check=True)
                self.assertEqual(proc.wait(timeout=3), 8)
            finally:
                if proc.poll() is None:
                    proc.kill()
