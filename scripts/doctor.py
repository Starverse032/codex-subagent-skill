#!/usr/bin/env python
"""Read-only diagnostics. Does not launch model turns or terminate processes."""
from pathlib import Path
import argparse
import importlib.metadata
import importlib.util
import os
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
runtime=ROOT/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
if runtime.is_file() and Path(sys.prefix).resolve()!=runtime.parent.parent.resolve():
    raise SystemExit(subprocess.call([str(runtime),'-B',str(Path(__file__).resolve()),*sys.argv[1:]]))
from runner.backends import find_codex
from runner.store import read_json

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--check-frontmatter',action='store_true')
    p.add_argument('--refresh-models',action='store_true',help='Removed: model availability is resolved by the selected runtime')
    p.add_argument('--kill-stale',action='store_true',help='Removed: cancel a specific run using codex_run.py cancel RUN_DIR')
    args=p.parse_args()
    if args.kill_stale or args.refresh_models:
        p.error('global process termination and snapshot rewriting are removed; use cancel RUN_DIR or the runtime model catalog')
    failures=[]
    def check(name,ok,detail=''):
        print(f'[{"PASS" if ok else "FAIL"}] {name}: {detail}')
        if not ok: failures.append(name)
    text=(ROOT/'SKILL.md').read_text('utf-8-sig')
    match=re.match(r'---\r?\n(.*?)\r?\n---',text,re.S)
    check('frontmatter',bool(match),'present' if match else 'missing')
    if match:
        keys=re.findall(r'(?m)^([\w-]+):',match[1])
        check('frontmatter keys',set(keys)<={'name','description','license','compatibility','metadata','allowed-tools'},', '.join(keys))
        check('skill name',bool(re.search(r'(?m)^name: codex-subagent\s*$',match[1])),'codex-subagent')
    if not args.check_frontmatter:
        check('Python',sys.version_info>=(3,10),sys.version.split()[0])
        for name in ['openai_codex','jsonschema','PIL']:
            spec=importlib.util.find_spec(name)
            check(name,spec is not None,'installed' if spec else 'run scripts/setup_runtime.py')
        local=read_json(ROOT/'config/runtime.local.json',{})
        if local.get('codex_path'):
            exe=find_codex(local['codex_path'])
            print(f'[INFO] SDK uses explicitly configured local runtime: {exe}')
        else:
            exe=find_codex()
            print('[INFO] SDK uses its bundled runtime; CLI backend uses PATH/native discovery')
        proc=subprocess.run([str(exe),'--version'],stdin=subprocess.DEVNULL,capture_output=True,timeout=15)
        check('local CLI',proc.returncode==0,proc.stdout.decode('utf-8','replace').strip())
    print(f'{len(failures)} failure(s)')
    return 4 if failures else 0

if __name__=='__main__':
    raise SystemExit(main())
