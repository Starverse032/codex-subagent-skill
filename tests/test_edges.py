import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

from test_runner import RunnerTests,verify_artifacts,requires_images,requires_schema
from runner.cli import worker_alive
from runner.processes import OwnedProcess,process_birth,owned_tree_stopped
from runner.store import read_json,write_json
from runner.verify import RESPONSE_SCHEMA

# unittest also discovers imported TestCase classes; keep helpers in a plain mixin.
import unittest
class EdgeTests(unittest.TestCase):
    setUp=RunnerTests.setUp
    tearDown=RunnerTests.tearDown
    cli=RunnerTests.cli
    task=RunnerTests.task
    artifact_case=RunnerTests.artifact_case

    def wait_for(self,predicate,seconds=10):
        deadline=time.monotonic()+seconds
        while not predicate():
            self.assertLess(time.monotonic(),deadline,'condition did not become true')
            time.sleep(.05)

    @requires_schema
    def test_model_cannot_weaken_schema(self):
        schema=self.base/'schema.json'
        write_json(schema,{'type':'object','properties':{'accepted':{'const':True}},'required':['accepted']})
        p,run,result=self.task('fixture:weaken-schema','--schema',schema)
        self.assertEqual(read_json(run/'response-schema.json'),{'type':'object'})
        self.assertEqual(p.returncode,9,result)
        self.assertEqual(result['verification']['status'],'failed')

    def test_transient_progress_read_does_not_fail_task(self):
        self.env['CODEX_SUBAGENT_TEST_PROGRESS_READ_LOCK']='1'
        p,run,result=self.task()
        self.assertTrue((run/'progress-read-collisions').is_file())
        self.assertEqual(p.returncode,0,result)
        self.assertEqual(result['errors'],[])

    def test_progress_write_failure_does_not_abort_acceptance(self):
        self.env['CODEX_SUBAGENT_TEST_PROGRESS_WRITE_LOCK']='1'
        p,run,result=self.task('fixture:artifact')
        self.assertEqual(p.returncode,0,result)
        self.assertEqual(result['verification']['status'],'passed')
        self.assertTrue(result['warnings'])
        self.assertFalse((run/'artifact-manifest.json').exists())

    def test_control_json_retries_a_transient_sharing_violation(self):
        from runner import store
        with patch.object(Path,'read_text',side_effect=[PermissionError('sharing violation'),'{"ok":true}']) as read, \
             patch.object(store.time,'sleep'):
            self.assertEqual(store.read_json(self.base/'control.json'),{'ok':True})
        self.assertEqual(read.call_count,2)

    @requires_schema
    def test_final_manifest_describes_post_check_file(self):
        run=self.base/'final-manifest'
        target=run/'artifacts/output.txt'
        spec=self.base/'task.json'
        write_json(spec,{'goal':'fixture:artifact','checks':[[sys.executable,'-c',
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'post-check content')",str(target)]]})
        schema=self.base/'schema.json'
        write_json(schema,RESPONSE_SCHEMA)
        p=self.cli('--task-spec',spec,'--workdir',self.base,'--run-dir',run,'--schema',schema)
        result=read_json(run/'result.json')
        self.assertEqual(p.returncode,0,result)
        record=read_json(run/'artifact-manifest.json')[0]
        self.assertEqual(record['sha256'],hashlib.sha256(b'post-check content').hexdigest())
        self.assertEqual(target.read_bytes(),b'post-check content')

    @requires_images
    def test_image_corrupted_by_checks_fails_final_verification(self):
        run=self.base/'corrupted-image'
        spec=self.base/'task.json'
        write_json(spec,{'goal':'fixture:image','checks':[[sys.executable,'-c',
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'broken image')",str(run/'artifacts/image.png')]]})
        p=self.cli('--task-spec',spec,'--workdir',self.base,'--run-dir',run,'--gen-image')
        result=read_json(run/'result.json')
        self.assertEqual(result['execution']['status'],'completed')
        self.assertEqual(p.returncode,9,result)
        self.assertEqual(result['verification']['status'],'failed')
        self.assertFalse(result['images'][0]['valid'])

    @requires_images
    def test_valid_image_replacement_has_no_generation_provenance(self):
        run=self.base/'replaced-image'
        spec=self.base/'task.json'
        write_json(spec,{'goal':'fixture:image','checks':[[sys.executable,'-c',
                   "from PIL import Image; import sys; Image.new('RGB',(16,16),'blue').save(sys.argv[1])",str(run/'artifacts/image.png')]]})
        p=self.cli('--task-spec',spec,'--workdir',self.base,'--run-dir',run,'--gen-image')
        result=read_json(run/'result.json')
        self.assertEqual(p.returncode,9,result)
        self.assertTrue(result['images'][0]['valid'])
        self.assertEqual(result['images'][0]['confidence'],'unverified')

    @requires_images
    def test_generation_collision_keeps_both_files(self):
        from PIL import Image
        run,req=self.artifact_case()
        existing=run/'artifacts/generated-01.png'
        Image.new('RGB',(16,16),'red').save(existing)
        before=existing.read_bytes()
        source=self.base/'generated.png';Image.new('RGB',(16,16),'green').save(source)
        checked=verify_artifacts(req,{'final_message':json.dumps({'summary':'x','artifacts':[]}),
                                 'generation_sources':[str(source)]},run)
        self.assertEqual(checked['status'],'passed',checked)
        self.assertEqual(existing.read_bytes(),before)
        self.assertNotEqual(checked['images'][0]['dst'],str(existing))
        self.assertEqual(Path(checked['images'][0]['dst']).read_bytes(),source.read_bytes())

    def test_redirected_artifact_root_is_rejected(self):
        run,req=self.artifact_case()
        owned=run/'artifacts';owned.rmdir()
        outside=self.base/'outside';outside.mkdir()
        (outside/'file.txt').write_text('fixture')
        if os.name=='nt':
            created=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(owned),str(outside)],capture_output=True)
            if created.returncode:
                self.skipTest('host prevents creating the junction fixture')
        else:
            owned.symlink_to(outside,target_is_directory=True)
        try:
            checked=verify_artifacts(req,{'final_message':json.dumps({'summary':'x',
                'artifacts':[{'path':str(owned/'file.txt'),'kind':'file'}]}),'generation_sources':[]},run)
            self.assertEqual(checked['status'],'failed',checked)
            self.assertEqual(checked['artifacts'],[])
        finally:
            self.assertEqual(owned.resolve(),outside.resolve())
            if os.name=='nt': os.rmdir(owned)
            else: owned.unlink()

    def test_blocking_artifact_verification_obeys_timeout(self):
        self.env['CODEX_SUBAGENT_TEST_BLOCK_VERIFY']='1'
        started=time.monotonic()
        p,run,result=self.task('fixture','--timeout','2')
        self.assertTrue((run/'verifier-entered').is_file(),result)
        self.assertEqual(p.returncode,5,result)
        self.assertEqual(result['status'],'timeout')
        self.assertEqual(result['execution']['status'],'completed')
        self.assertLess(time.monotonic()-started,7)
        self.assertTrue(owned_tree_stopped(result['active_process']))

    def test_blocking_artifact_verification_can_be_cancelled(self):
        self.env['CODEX_SUBAGENT_TEST_BLOCK_VERIFY']='1'
        p=self.cli('start','--task','fixture','--workdir',self.base)
        self.assertEqual(p.returncode,0,p.stderr)
        run=Path(json.loads(p.stdout)['run_dir'])
        self.wait_for(lambda:(run/'verifier-entered').is_file())
        self.assertEqual(self.cli('cancel',run).returncode,0)
        self.wait_for(lambda:read_json(run/'result.json')['status'] not in {'queued','running'},seconds=5)
        result=read_json(run/'result.json')
        self.assertEqual(result['status'],'cancelled',result)
        self.assertTrue(owned_tree_stopped(result['active_process']))

    @unittest.skipUnless(os.name=='nt','automatic job teardown after a supervisor crash is Windows-specific')
    def test_lost_supervisor_can_resume_after_owned_tree_exits(self):
        p=self.cli('start','--task','fixture:long','--workdir',self.base)
        self.assertEqual(p.returncode,0,p.stderr)
        run=Path(json.loads(p.stdout)['run_dir'])
        self.wait_for(lambda:bool(read_json(run/'result.json').get('thread_id')))
        state=read_json(run/'result.json')
        pid=state['worker_pid']
        # Crash only the supervisor whose birth identity matches our test job.
        self.assertEqual(process_birth(pid),state['worker_birth'])
        os.kill(pid,signal.SIGTERM)
        self.wait_for(lambda:not worker_alive(pid))
        self.wait_for(lambda:owned_tree_stopped(state['active_process']))
        self.assertEqual(json.loads(self.cli('status',run).stdout)['status'],'lost')
        p=self.cli('resume',run,'--task','fixture resumed')
        self.assertEqual(p.returncode,0,p.stderr)
        result=read_json(Path(json.loads(p.stdout)['run_dir'])/'result.json')
        self.assertEqual(result['status'],'ok',result)
        self.assertEqual(result['thread_id'],state['thread_id'])

    def test_lost_resume_refuses_a_still_active_owned_tree(self):
        _,run,state=self.task()
        child=OwnedProcess([sys.executable,'-c','import time;time.sleep(60)'],cwd=self.base,
                           job_name='Local\\codex-subagent-test-'+uuid.uuid4().hex)
        try:
            state.update(status='running',active_process=child.identity())
            write_json(run/'result.json',state)
            p=self.cli('resume',run,'--task','fixture follow-up')
            self.assertEqual(p.returncode,2,p.stderr)
            self.assertIn('cannot safely resume lost run',p.stderr)
            self.assertIsNone(child.proc.poll())
        finally:
            child.close()

    def test_lost_legacy_run_requires_ownership_evidence(self):
        _,run,state=self.task()
        state.update(status='running')
        state.pop('active_process',None)
        write_json(run/'result.json',state)
        p=self.cli('resume',run,'--task','fixture follow-up')
        self.assertEqual(p.returncode,2,p.stderr)
        self.assertIn('cannot safely resume lost run',p.stderr)

# Do not discover the imported base TestCase a second time in this module.
del RunnerTests
