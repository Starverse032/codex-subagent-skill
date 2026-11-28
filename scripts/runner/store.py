from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid
from . import VERSION

TERMINAL = {'ok', 'failed', 'timeout', 'cancelled', 'image_missing',
            'verification_failed', 'internal_error', 'lost', 'thread_busy', 'status_unknown'}
EXIT = {'ok': 0, 'failed': 6, 'timeout': 5, 'cancelled': 130,
        'image_missing': 8, 'verification_failed': 9, 'internal_error': 4, 'lost': 4,
        'thread_busy': 10, 'status_unknown': 11}

def read_json(path: Path, default=None, *, attempts=20):
    for attempt in range(attempts):
        try:
            return json.loads(path.read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            return default
        except PermissionError:
            if attempt==attempts-1: raise
            time.sleep(.01)

def write_json(path: Path, value, *, attempts=80) -> None:
    """Replace a complete snapshot; readers never see half a JSON document."""
    temp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Antivirus/read handles can briefly prevent replace on Windows.
        for attempt in range(attempts):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == attempts-1:
                    raise
                time.sleep(.025)
    finally:
        temp.unlink(missing_ok=True)

def initial_result(request: dict) -> dict:
    return {'schema': 'codex-subagent/result@1', 'runner_version': VERSION,
            'status': 'queued', 'exit_code': None, 'codex_returncode': None,
            'run_id': request['run_id'], 'run_dir': request['run_dir'],
            'duration_s': 0, 'request': request, 'final_message': '',
            'final_message_source': '', 'thread_id': '', 'turn_id': '',
            'usage': {}, 'tool_calls': [], 'images': [], 'warnings': [], 'errors': [],
            'changed_files': [], 'diff_stat': None,
            'execution': {'status': 'queued'}, 'verification': {'status': 'not_run'},
            'artifacts': {'events': 'events.jsonl', 'prompt': 'prompt.md',
                          'stderr': 'stderr.log', 'last_message': 'last-message.md',
                          'request': 'request.json', 'manifest': 'artifact-manifest.json'}}

def contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

def plain_directory(path: Path) -> Path:
    """Require the originally named directory, including all parent components."""
    absolute = Path(os.path.abspath(path))
    if not absolute.is_dir() or absolute.resolve(strict=True) != absolute:
        raise ValueError(f'owned directory is missing or redirected: {absolute}')
    return absolute

def owned_artifact_root(run: Path) -> Path:
    run = plain_directory(run)
    return plain_directory(run/'artifacts')

def owned_file(path: Path, root: Path) -> Path:
    root = plain_directory(root)
    if '..' in path.parts:
        raise ValueError('artifact paths cannot contain parent traversal')
    absolute = path.absolute()
    resolved = absolute.resolve(strict=True)
    if resolved != absolute or not contained(resolved, root) or not resolved.is_file():
        raise ValueError('artifact must be an unredirected regular file inside this run/artifacts')
    return resolved
