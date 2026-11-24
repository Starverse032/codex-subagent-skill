import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch,MagicMock
import contextlib
import io

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from runner.backends import cli_command,cli_backend
from runner.cli import parser,prepare,worker_alive
from runner.store import read_json,write_json
from runner.verify import RESPONSE_SCHEMA,probe_image,verify_artifacts as verify_final,digest

def verify_artifacts(req,execution,run,*,schema=RESPONSE_SCHEMA):
    # Synthetic completed generation events in these isolated image fixtures.
    execution=dict(execution)
    if 'generation_evidence' not in execution:
        execution['generation_evidence']=[{'path':p,'sha256':digest(p)} for p in execution.get('generation_sources',[])]
    return verify_final(req,execution,run,schema=schema)

class RunnerTests(unittest.TestCase):
    def setUp(self):
        scratch=ROOT/'tests/.work'
        scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='codex-subagent-test-',dir=scratch)
        self.base=Path(self.temp.name)
        self.env=os.environ.copy()
        self.env['CODEX_SUBAGENT_STATE_DIR']=str(self.base/'shared-state')
        self.env['PYTHONPATH']=str(ROOT/'tests/fixtures')+os.pathsep+self.env.get('PYTHONPATH','')
    def tearDown(self): self.temp.cleanup()
    def cli(self,*args,timeout=20):
        return subprocess.run([sys.executable,'-B',str(ROOT/'scripts/codex_run.py'),*map(str,args)],
                              env=self.env,capture_output=True,text=True,encoding='utf-8',timeout=timeout)
    def task(self,text='fixture',*extra):
        result=self.cli('--task',text,'--workdir',self.base,*extra)
        self.assertTrue(result.stdout.strip(),result.stderr)
        run=Path(result.stdout.strip())
        return result,run,read_json(run/'result.json')
    def test_default_sol_medium_and_dryrun_no_writes(self):
        before=list(self.base.iterdir())
        result=self.cli('--task','fixture','--workdir',self.base,'--dry-run')
        self.assertEqual(result.returncode,0,result.stderr)
        req=json.loads(result.stdout)['request']
        self.assertEqual((req['preset'],req['model'],req['effort']),('standard','gpt-6.2-sol','medium'))
        self.assertEqual(before,list(self.base.iterdir()))
    def test_permission_guard_all_routes(self):
        for flags in [('--danger',),('--sandbox','danger-full-access'),('-c','sandbox_mode="danger-full-access"'),('-c',r'"\u0073andbox_mode"="danger-full-access"')]:
            p=self.cli('--task','fixture','--workdir',self.base,*flags)
            self.assertEqual(p.returncode,2,p.stderr)
        self.assertEqual(list(self.base.iterdir()),[])
    def test_auto_review_command_has_no_sandbox_flag(self):
        p=self.cli('--task','fixture','--workdir',self.base,'--backend','cli','--codex-path',sys.executable,'--approval','approve-for-me','--dry-run')
        command=json.loads(p.stdout)['command']
        self.assertIn('--approve-for-me',command)
        self.assertNotIn('-s',command)
    def test_cli_failed_event_cannot_become_success(self):
        req,_,_=prepare(parser().parse_args(['--task','x','--workdir',str(self.base),
                         '--backend','cli','--codex-path',sys.executable]))
        run=Path(req['run_dir']);run.mkdir(parents=True)
        (run/'prompt.md').write_text('x')
        events=[{'type':'turn.failed','error':'failed'},
                {'type':'item.completed','item':{'type':'agent_message','text':'partial'}},
                {'type':'turn.completed','usage':{}}]
        proc=MagicMock()
        proc.stdout=[(json.dumps(e)+'\n').encode() for e in events]
        proc.wait.return_value=0
        with patch('runner.backends.subprocess.Popen',return_value=proc):
            result=cli_backend(req,run)
        self.assertEqual(result['status'],'failed',result)
    def test_success_real_supervision_and_schema(self):
        p,run,result=self.task('fixture:artifact')
        self.assertEqual(p.returncode,0,result)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['execution']['status'],'completed')
        self.assertEqual(result['verification']['status'],'passed')
        self.assertEqual(result['final_message'],'FIXTURE_OK')
        self.assertTrue(read_json(run/'artifact-manifest.json'))
    def test_empty_and_failed_turns_fail(self):
        for text in ['fixture:empty','fixture:failed']:
            p,_,result=self.task(text)
            self.assertEqual(p.returncode,6,result)
            self.assertEqual(result['status'],'failed')
    def test_malformed_response_fails_verification(self):
        p,_,result=self.task('fixture:malformed')
        self.assertEqual(p.returncode,9,result)
        self.assertEqual(result['status'],'verification_failed')
    def test_reused_run_keeps_user_files(self):
        run=self.base/'existing'
        run.mkdir()
        note=run/'keep.txt'
        note.write_text('keep')
        p=self.cli('--task','fixture','--workdir',self.base,'--run-dir',run)
        self.assertEqual(p.returncode,2,p.stderr)
        self.assertEqual(note.read_text(),'keep')
    def test_start_query_cancel_and_unrelated_process(self):
        other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
        try:
            p=self.cli('start','--task','fixture:long','--workdir',self.base)
            self.assertEqual(p.returncode,0,p.stderr)
            run=Path(p.stdout.strip())
            deadline=time.monotonic()+10
            while not (run/'result.json').exists() or read_json(run/'result.json')['status']=='queued':
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.05)
            status=self.cli('status',run)
            self.assertEqual(json.loads(status.stdout)['status'],'running')
            self.assertEqual(self.cli('cancel',run).returncode,0)
            while read_json(run/'result.json')['status'] in {'queued','running'}:
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.05)
            final=read_json(run/'result.json')
            self.assertEqual(final['status'],'cancelled',json.dumps(final,ensure_ascii=False))
            self.assertIsNone(other.poll())
        finally:
            other.terminate();other.wait(timeout=5)
    def test_timeout_is_bounded(self):
        started=time.monotonic()
        p,_,result=self.task('fixture:long','--timeout','1')
        self.assertEqual(result['status'],'timeout',result)
        self.assertEqual(p.returncode,5)
        self.assertLess(time.monotonic()-started,8)
    def test_resume_uses_explicit_thread_new_run(self):
        _,old,first=self.task()
        p=self.cli('resume',old,'--task','fixture continued')
        self.assertEqual(p.returncode,0,p.stderr)
        new=Path(p.stdout.strip())
        self.assertNotEqual(old,new)
        self.assertEqual(read_json(new/'result.json')['thread_id'],first['thread_id'])

    def test_group_follow_up_reuses_thread_and_isolates_other_chat(self):
        flags=['--host','Antigravity','--project-title','Inventory']
        _,first,a=self.task('fixture',*flags,'--conversation','chat-1')
        _,second,b=self.task('fixture follow-up',*flags,'--conversation','chat-1','--effort','xhigh')
        _,_,c=self.task('fixture',*flags,'--conversation','chat-2')
        self.assertEqual(a['thread_id'],b['thread_id'])
        self.assertNotEqual(b['thread_id'],c['thread_id'])
        self.assertEqual(b['request']['reused_from'],str(first))
        self.assertEqual(b['request']['effort'],'xhigh')
        project=self.cli('project','--workdir',self.base,*flags,'--conversation','chat-1')
        self.assertEqual(project.returncode,0,project.stderr)
        project=json.loads(project.stdout)
        self.assertEqual(project['title'],'Antigravity | Inventory')
        self.assertEqual(project['sessions'][0]['run_dir'],str(second))
        self.assertEqual(len(project['runs']),2)
        self.assertEqual(project['desktop_id'],a['codex_project_id'])
        self.assertEqual(a['codex_project_id'],b['codex_project_id'])
        self.assertNotEqual(b['codex_project_id'],c['codex_project_id'])

    def test_waiting_same_lane_can_cancel_without_starting_backend(self):
        flags=['--host','Antigravity','--project-title','Inventory','--conversation','shared-chat']
        first_cmd=self.cli('start','--task','fixture:long','--workdir',self.base,*flags)
        first=Path(first_cmd.stdout.strip())
        deadline=time.monotonic()+10
        try:
            while read_json(first/'result.json')['status']=='queued':
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.05)
            second_cmd=self.cli('start','--task','fixture','--workdir',self.base,*flags)
            second=Path(second_cmd.stdout.strip())
            self.assertEqual(self.cli('cancel',second).returncode,0)
            while read_json(second/'result.json')['status']=='queued':
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.05)
            self.assertEqual(read_json(second/'result.json')['status'],'cancelled')
            self.assertFalse((second/'events.jsonl').exists())
        finally:
            self.cli('cancel',first)
            while read_json(first/'result.json')['status'] not in {'cancelled','failed','timeout'}:
                if time.monotonic()>deadline+5: break
                time.sleep(.05)
    def test_orphan_child_is_removed(self):
        p,run,result=self.task('fixture:orphan')
        self.assertEqual(p.returncode,0,result)
        pid=int((run/'orphan.pid').read_text())
        self.assertFalse(worker_alive(pid))
    def test_40000_character_task_file(self):
        task=self.base/'task.md'
        task.write_text('fixture '+'x'*40000,encoding='utf-8')
        p=self.cli('--task-file',task,'--workdir',self.base)
        self.assertEqual(p.returncode,0,p.stderr)
        req=json.loads(self.cli('--task','x','--workdir',self.base,'--backend','cli','--codex-path',sys.executable,'--dry-run').stdout)['request']
        self.assertEqual(cli_command(req)[-1],'-')
    def test_acceptance_check_has_independent_result(self):
        spec=self.base/'task.json'
        write_json(spec,{'goal':'fixture','checks':[[sys.executable,'-c','raise SystemExit(3)']]})
        p=self.cli('--task-spec',spec,'--workdir',self.base)
        result=read_json(Path(p.stdout.strip())/'result.json')
        self.assertEqual(result['execution']['status'],'completed')
        self.assertEqual(result['verification']['checks'][0]['exit_code'],3)
        self.assertEqual(result['status'],'verification_failed')
    def artifact_case(self,count=1):
        run=self.base/'image-run'
        run.mkdir();(run/'artifacts').mkdir()
        write_json(run/'response-schema.json',RESPONSE_SCHEMA)
        return run,{'run_id':'fixture','gen_image':True,'image_count':count,'image_size':[16,16],
                    'custom_schema':False,'image_dir':''}
    def test_corrupt_png_is_not_decodable(self):
        path=self.base/'bad.png'
        data=bytearray(1024);data[:8]=b'\x89PNG\r\n\x1a\n';data[16:24]=(1024).to_bytes(4,'big')*2
        path.write_bytes(data)
        self.assertFalse(probe_image(path)['valid'])
    def test_reference_and_traversal_not_accepted(self):
        run,req=self.artifact_case()
        ref=self.base/'reference.png'
        from PIL import Image
        Image.new('RGB',(16,16)).save(ref)
        result=verify_artifacts(req,{'final_message':json.dumps({'summary':'x','artifacts':[{'path':str(ref),'kind':'image'}]}),'generation_sources':[]},run)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['images'],[])
        self.assertTrue(ref.exists())
    def test_generated_images_require_count_and_provenance(self):
        run,req=self.artifact_case(count=2)
        from PIL import Image
        source=self.base/'generated.png';Image.new('RGB',(16,16)).save(source)
        result=verify_artifacts(req,{'final_message':json.dumps({'summary':'x','artifacts':[]}),
                                    'generation_sources':[str(source)]},run)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(result['images']),1)
        self.assertTrue(result['images'][0]['valid'])
        self.assertEqual(result['images'][0]['confidence'],'high')
    def test_export_preserves_unrelated_file(self):
        run,req=self.artifact_case()
        dest=self.base/'export';dest.mkdir();note=dest/'notes.txt';note.write_text('keep')
        req['image_dir']=str(dest)
        from PIL import Image
        source=self.base/'generated.png';Image.new('RGB',(16,16)).save(source)
        execution={'final_message':json.dumps({'summary':'x','artifacts':[]}), 'generation_sources':[str(source)]}
        result=verify_artifacts(req,execution,run)
        self.assertEqual(result['status'],'passed',result)
        self.assertEqual(note.read_text(),'keep')
        self.assertTrue(Path(result['images'][0]['exported_to']).is_file())
    def test_owned_copy_without_generation_is_unverified(self):
        run,req=self.artifact_case()
        from PIL import Image
        path=run/'artifacts/copied-reference.png';Image.new('RGB',(16,16)).save(path)
        execution={'final_message':json.dumps({'summary':'x','artifacts':[{'path':str(path),'kind':'image'}]}),
                   'generation_sources':[]}
        result=verify_artifacts(req,execution,run)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['images'][0]['confidence'],'unverified')
    def test_keyboard_interrupt_requests_task_cancellation(self):
        from runner import cli
        run=self.base/'interrupt-run';run.mkdir()
        req={'run_dir':str(run),'run_id':'owned-fixture'}
        child=MagicMock();child.poll.return_value=None
        with patch.object(cli,'prepare',return_value=(req,'',{})), patch.object(cli,'start',return_value=child), \
             patch.object(cli.time,'sleep',side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            code=cli.main(['--task','fixture'])
        self.assertEqual(code,130)
        self.assertEqual((run/'cancel.request').read_text(),'owned-fixture')
        child.wait.assert_called_once_with(timeout=10)

if __name__=='__main__': unittest.main()
