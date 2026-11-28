#!/usr/bin/env python
"""Configure native CLI discovery or install explicitly requested optional dependencies."""
import argparse
import os
from pathlib import Path
import subprocess

from runner.backends import find_codex, probe_codex
from runner.store import write_json

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', help='Directory containing all required wheels for offline setup')
    parser.add_argument('--local-cli', nargs='?', const='auto', metavar='PATH',
                        help='Use automatic discovery when omitted, or persist a fixed native CLI path')
    parser.add_argument('--sdk', action='store_true', help='Install the optional SDK backend')
    parser.add_argument('--schema', action='store_true', help='Install the optional JSON Schema validator')
    parser.add_argument('--image', action='store_true', help='Install the optional image decoder')
    args = parser.parse_args()
    packages = [name for enabled, name in ((args.sdk, 'openai-codex==0.163.0'),
                                           (args.schema, 'jsonschema==4.26.0'),
                                           (args.image, 'Pillow>=10.4,<14')) if enabled]
    runtime = ROOT/'.venv'
    interpreter = runtime/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    try:
        if args.local_cli is not None:
            config = {'mode': 'auto'}
            if args.local_cli != 'auto':
                exe = find_codex(args.local_cli)
                probe_codex(exe)
                config = {'mode': 'fixed', 'codex_path': str(exe)}
            write_json(ROOT/'config/runtime.local.json', config)
            print(f'Configured native CLI discovery mode: {config["mode"]}')
        if packages:
            import venv
            venv.EnvBuilder(with_pip=True).create(runtime)
            extra = ['--no-index', '--find-links', str(Path(args.wheelhouse).resolve())] if args.wheelhouse else []
            subprocess.run([str(interpreter), '-m', 'pip', 'install', *extra, *packages], check=True)
            print(f'Use this interpreter for the optional mode: {interpreter}')
        elif args.local_cli is None:
            print('The standard CLI path needs no optional Python packages. Run python scripts/doctor.py to check it.')
    except ImportError:
        parser.exit(4, '[FAIL] Optional setup needs Python with the standard venv and ensurepip modules.\n')
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(4, f'[FAIL] {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
