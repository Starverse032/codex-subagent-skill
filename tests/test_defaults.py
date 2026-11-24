"""Observable model/effort selection, including continuation and CLI transport."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from runner import cli
from runner.backends import cli_command
from runner.store import read_json,write_json


class DefaultPolicyTests(unittest.TestCase):
    def setUp(self):
        scratch=ROOT/'tests/.work'
        scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=scratch)
        self.base=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self,*flags):
        args=cli.parser().parse_args(['--task','bounded backend task','--workdir',str(self.base),
                                     '--backend','cli','--codex-path',sys.executable,*map(str,flags)])
        return cli.prepare(args)[0]

    def continuation(self,*flags,model='custom-model',effort='xhigh',preset='deep'):
        prior=self.base/'previous'
        prior.mkdir(exist_ok=True)
        write_json(prior/'request.json',{'workdir':str(self.base),'backend':'cli','model':model,
                                       'effort':effort,'preset':preset})
        args=cli.parser().parse_args(['resume',str(prior),'--task','bounded follow-up',
                                     '--codex-path',sys.executable,*map(str,flags)])
        with patch.object(cli,'read_result',return_value={'status':'ok',
                'thread_id':'12345678-1234-1234-1234-123456789abc'}):
            return cli.prepare(args)[0]

    def test_effort_presets_keep_sol(self):
        for preset,effort in [('standard','medium'),('deep','high'),('max','max'),('image','medium')]:
            with self.subTest(preset=preset):
                req=self.prepare('--preset',preset)
                self.assertEqual((req['model'],req['effort']),('gpt-6.2-sol',effort))
        for effort in ['high','xhigh','max']:
            with self.subTest(effort=effort):
                req=self.prepare('--effort',effort)
                self.assertEqual((req['model'],req['effort']),('gpt-6.2-sol',effort))

    def test_configured_default_is_used(self):
        settings=read_json(ROOT/'config/presets.json')
        settings['default']='deep'
        original=cli.read_json
        def configured(path,*args,**kwargs):
            return settings if Path(path)==ROOT/'config/presets.json' else original(path,*args,**kwargs)
        with patch.object(cli,'read_json',side_effect=configured):
            req=self.prepare()
        self.assertEqual((req['preset'],req['model'],req['effort']),('deep','gpt-6.2-sol','high'))

    def test_inherit_is_explicit_and_clears_cli_overrides(self):
        req=self.prepare('--preset','inherit')
        self.assertEqual((req['model'],req['effort']),('',''))
        command=cli_command(req)
        self.assertNotIn('-m',command)
        self.assertFalse(any('model_reasoning_effort=' in item for item in command))

    def test_cli_passes_default_and_explicit_overrides(self):
        req=self.prepare()
        command=cli_command(req)
        self.assertEqual(command[command.index('-m')+1],'gpt-6.2-sol')
        self.assertIn('model_reasoning_effort="medium"',command)
        req=self.prepare('--preset','max','--model','chosen-model','--effort','high')
        command=cli_command(req)
        self.assertEqual(command[command.index('-m')+1],'chosen-model')
        self.assertIn('model_reasoning_effort="high"',command)

    def test_resume_preserves_model_effort_and_preset(self):
        req=self.continuation()
        self.assertEqual((req['preset'],req['model'],req['effort']),('deep','custom-model','xhigh'))

    def test_resume_effort_preset_does_not_switch_model(self):
        req=self.continuation('--preset','max')
        self.assertEqual((req['preset'],req['model'],req['effort']),('max','custom-model','max'))
        req=self.continuation('--effort','high')
        self.assertEqual((req['model'],req['effort']),('custom-model','high'))

    def test_resume_explicit_inherit_and_model_override(self):
        req=self.continuation('--preset','inherit')
        self.assertEqual((req['model'],req['effort']),('',''))
        req=self.continuation('--preset','deep','--model','chosen-model','--effort','medium')
        self.assertEqual((req['model'],req['effort']),('chosen-model','medium'))

    def test_resume_inherited_configuration_remains_inherited(self):
        req=self.continuation(model='',effort='',preset='inherit')
        self.assertEqual((req['preset'],req['model'],req['effort']),('inherit','',''))
