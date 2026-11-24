#!/usr/bin/env python
"""Install dependencies into this skill's private .venv, never globally."""
import argparse
import os
from pathlib import Path
import subprocess
import venv

root=Path(__file__).resolve().parent.parent
p=argparse.ArgumentParser()
p.add_argument('--wheelhouse',help='Optional offline directory containing all required wheels')
p.add_argument('--local-cli',nargs='?',const='auto',help='Use an existing native CLI instead of downloading the SDK runtime')
args=p.parse_args()
runtime=root/'.venv'
venv.EnvBuilder(with_pip=True).create(runtime)
python=runtime/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
extra=['--no-index','--find-links',str(Path(args.wheelhouse).resolve())] if args.wheelhouse else []
if args.local_cli:
    from runner.backends import find_codex
    from runner.store import write_json
    exe=find_codex('' if args.local_cli=='auto' else args.local_cli)
    subprocess.run([str(python),'-m','pip','install','pydantic>=2.12','packaging>=26.2',
                    'jsonschema==4.26.0','Pillow>=10.4,<14',*extra],check=True)
    subprocess.run([str(python),'-m','pip','install','--no-deps','openai-codex==0.163.0',*extra],check=True)
    write_json(root/'config/runtime.local.json',{'codex_path':str(exe),'mode':'existing-cli'})
else:
    subprocess.run([str(python),'-m','pip','install','-r',str(root/'requirements.txt'),*extra],check=True)
    (root/'config/runtime.local.json').unlink(missing_ok=True)
print(f'Ready: {python}')
