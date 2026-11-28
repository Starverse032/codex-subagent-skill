#!/usr/bin/env python
"""Run Codex with the caller's Python interpreter."""
import sys

sys.dont_write_bytecode = True
from runner.cli import main
if __name__ == '__main__':
    raise SystemExit(main())
