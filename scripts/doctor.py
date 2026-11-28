#!/usr/bin/env python
"""Read-only diagnostics. Does not launch model turns or terminate processes."""
from pathlib import Path
import argparse
import importlib.util
import re
import sys

ROOT=Path(__file__).resolve().parent.parent
from runner.backends import probe_codex, resolve_runtime

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
        for name, flag in [('openai_codex', '--sdk'), ('jsonschema', '--schema'), ('PIL', '--image')]:
            if importlib.util.find_spec(name) is None:
                print(f'[INFO] Optional {name} unavailable. To enable it, run python scripts/setup_runtime.py {flag}')
        try:
            exe=resolve_runtime('', ROOT/'config/runtime.local.json')
            check('local CLI', True, f'{probe_codex(exe)} ({exe})')
        except (ValueError, OSError, TypeError) as exc:
            check('local CLI', False, str(exc))
        try:
            legacy=Path.home()/'.gemini/config/agents/codex-lane-coordinator.md'
            if legacy.is_file():
                print(f'[INFO] Legacy Antigravity coordinator: {legacy}. v3 setup no longer manages this file.')
        except (OSError, RuntimeError):
            pass  # Some hosts hide the user profile.
    print(f'{len(failures)} failure(s)')
    return 4 if failures else 0

if __name__=='__main__':
    raise SystemExit(main())
