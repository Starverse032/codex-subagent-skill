#!/usr/bin/env python
"""Compatibility entry point; prefer the skill's private runtime when installed."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
runtime = ROOT/'.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if runtime.is_file() and Path(sys.prefix).resolve() != runtime.parent.parent.resolve():
    child=subprocess.Popen([str(runtime),'-B',str(Path(__file__).resolve()),*sys.argv[1:]])
    try:
        raise SystemExit(child.wait())
    except KeyboardInterrupt:
        try:
            child.wait(timeout=12)
        except subprocess.TimeoutExpired:
            pass
        raise SystemExit(130)

from runner.cli import main
if __name__ == '__main__':
    raise SystemExit(main())
