"""Only the test subprocess PYTHONPATH enables this blocking verifier fixture."""
import os
from pathlib import Path
import sys
import time

if os.environ.get('CODEX_SUBAGENT_TEST_BLOCK_VERIFY')=='1' and '_verify' in sys.argv:
    sys.path.insert(0,str(Path(sys.argv[0]).resolve().parent))
    from runner import verify
    original=verify.run_checks
    def blocked(req,run,deadline):
        (run/'verifier-entered').write_text('inside artifact verification',encoding='utf-8')
        time.sleep(60)
        return original(req,run,deadline)
    verify.run_checks=blocked

if os.environ.get('CODEX_SUBAGENT_TEST_PROGRESS_READ_LOCK')=='1' and '_worker' in sys.argv:
    sys.path.insert(0,str(Path(sys.argv[0]).resolve().parent))
    from runner import cli
    original_read=cli.read_json
    remaining=[5]
    def intermittent_read(path,default=None,**kwargs):
        if path.name=='progress.json' and remaining[0]:
            remaining[0]-=1
            (path.parent/'progress-read-collisions').write_text('observed',encoding='utf-8')
            raise PermissionError('simulated Windows sharing violation')
        return original_read(path,default,**kwargs)
    cli.read_json=intermittent_read

if os.environ.get('CODEX_SUBAGENT_TEST_PROGRESS_WRITE_LOCK')=='1' and any(x in sys.argv for x in ('_backend','_verify')):
    sys.path.insert(0,str(Path(sys.argv[0]).resolve().parent))
    from runner import backends
    original_write=backends.write_json
    def locked_progress_write(path,value,**kwargs):
        if path.name=='progress.json':
            raise PermissionError('simulated Windows sharing violation')
        return original_write(path,value,**kwargs)
    backends.write_json=locked_progress_write
