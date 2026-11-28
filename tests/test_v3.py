"""Contracts for the standard-library default path."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from runner.cli import parser, prepare
from runner import evidence


class V3Defaults(unittest.TestCase):
    def test_plain_cli_defaults(self):
        req, prompt, schema = prepare(parser().parse_args([
            '--task', '中文任务\n', '--workdir', str(ROOT), '--codex-path', sys.executable,
            '--state-dir', str(ROOT / 'tests' / '.work'), '--task-id', 'job-1',
        ]))
        self.assertEqual((req['backend'], req['model'], req['effort']), ('cli', '', ''))
        self.assertEqual(prompt, '中文任务\n')
        self.assertIsNone(schema)
        self.assertEqual(Path(req['run_dir']).parent, ROOT / 'tests' / '.work' / 'runs')

    def test_effort_controls_runtime_budget(self):
        req, _, _ = prepare(parser().parse_args([
            '--task', 'fixture', '--codex-path', sys.executable, '--effort', 'xhigh',
        ]))
        self.assertEqual(req['timeout_s'], 2700)

    def test_evidence_fingerprints_changes_to_already_dirty_file(self):
        scratch=ROOT/'tests'/'.work'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as dirname:
            root=Path(dirname).resolve()
            run=root/'runner-output'
            file=root/'dirty.txt'
            file.write_text('existing edit',encoding='utf-8')

            def git_fixture(exe, workdir, *args):
                if '--show-toplevel' in args:
                    return str(root).encode()
                if args[0]=='rev-parse':
                    return b'base\n'
                if args[0]=='status':
                    return b' M dirty.txt\x00?? runner-output/log\x00'
                return b''

            with patch.object(evidence.shutil,'which',return_value='git'), \
                 patch.object(evidence,'git',side_effect=git_fixture):
                before=evidence.snapshot(root,run)
                file.write_text('second edit',encoding='utf-8')
                result=evidence.collect_changes(root,run,before,{})
            self.assertEqual(result['changed_files'],['dirty.txt'])
            self.assertEqual(result['diff_stat']['source'],'git_snapshots')
