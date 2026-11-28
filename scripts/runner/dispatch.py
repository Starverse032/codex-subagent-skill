"""Shared per-user admission queue, task receipts, and thread index."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import time
import uuid

from .processes import owned_tree_stopped, process_birth
from .store import plain_directory, read_json

LIMIT = 10
DATABASE = 'dispatch-v1.sqlite3'
SCHEMA_VERSION = 3


def state_dir(path=None):
    override = path or os.environ.get('CODEX_SUBAGENT_STATE_DIR')
    return Path(override or Path.home()/'.codex-subagent').expanduser().resolve()


def ticket_alive(row):
    try:
        if process_birth(row['pid']) == row['birth']:
            return True
    except (OSError, ValueError):
        return True
    return bool(row['child'] and not owned_tree_stopped(json.loads(row['child'])))


def backup_database(path):
    # BEGIN IMMEDIATE excludes other writers without changing the source.
    # A separate read connection copies the committed snapshot, including WAL.
    target = path.parent/f'dispatch-v1.pre-v3.{uuid.uuid4().hex}.sqlite3'
    temporary = target.with_suffix('.tmp')
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as backup:
                source.backup(backup)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def migrate(db, filename):
    existing = tables(db)
    if existing & {'tickets', 'projects', 'sessions', 'project_runs'}:
        if 'tickets' in existing and any(ticket_alive(row) for row in db.execute('SELECT * FROM tickets')):
            raise ValueError('v2 queue is still active; stop submitting v2 jobs and wait for them to finish before migration')
        backup_database(filename)

    statements = (
        """CREATE TABLE IF NOT EXISTS tickets (
            seq INTEGER PRIMARY KEY, run TEXT UNIQUE, pid INTEGER, birth TEXT,
            lane TEXT, thread TEXT, active INTEGER DEFAULT 0, child TEXT)""",
        """CREATE TABLE IF NOT EXISTS projects (
            key TEXT PRIMARY KEY, host TEXT, conversation TEXT, title TEXT,
            workspace TEXT, desktop_id TEXT DEFAULT '')""",
        """CREATE UNIQUE INDEX IF NOT EXISTS project_desktop_binding
            ON projects(desktop_id) WHERE desktop_id <> ''""",
        """CREATE TABLE IF NOT EXISTS sessions (
            slot TEXT PRIMARY KEY, project TEXT, lane TEXT, run_dir TEXT,
            thread TEXT, model TEXT, backend TEXT)""",
        """CREATE TABLE IF NOT EXISTS project_runs (
            run_dir TEXT PRIMARY KEY, project TEXT, lane TEXT)""",
        """CREATE TABLE IF NOT EXISTS task_receipts (
            task_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, run_dir TEXT NOT NULL,
            run_id TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS threads (
            thread_id TEXT PRIMARY KEY, workdir TEXT NOT NULL, lane TEXT, run_dir TEXT NOT NULL,
            backend TEXT, updated_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS run_records (
            run_id TEXT PRIMARY KEY, run_dir TEXT UNIQUE NOT NULL)""",
    )
    for statement in statements:
        db.execute(statement)
    columns = {row['name'] for row in db.execute('PRAGMA table_info(tickets)')}
    if 'reconcile' not in columns:
        db.execute('ALTER TABLE tickets ADD COLUMN reconcile INTEGER NOT NULL DEFAULT 0')
    if 'reuse' not in columns:
        db.execute('ALTER TABLE tickets ADD COLUMN reuse INTEGER NOT NULL DEFAULT 1')
    db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')


@contextmanager
def transaction(path=None):
    path = state_dir(path)
    path.mkdir(parents=True, exist_ok=True)
    filename = path/DATABASE
    db = sqlite3.connect(filename, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version > SCHEMA_VERSION:
            raise ValueError(f'shared queue schema {version} requires a newer runner')
        if version < SCHEMA_VERSION:
            migrate(db, filename)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def read_state(path=None):
    filename = state_dir(path)/DATABASE
    if not filename.is_file():
        yield None
        return
    with closing(sqlite3.connect(filename.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        yield db


def preflight(path=None):
    with transaction(path) as db:
        # Probe the real journal and write path without leaving a task record.
        db.execute('SAVEPOINT probe')
        db.execute('INSERT INTO task_receipts VALUES (?,?,?,?)', (uuid.uuid4().hex, '', '', ''))
        db.execute('ROLLBACK TO probe')
        db.execute('RELEASE probe')


def reserve_task(request, fingerprint):
    task_id = request.get('task_id')
    if not task_id:
        return None
    with transaction(request.get('state_dir')) as db:
        row = db.execute('SELECT * FROM task_receipts WHERE task_id=?', (task_id,)).fetchone()
        if row:
            if row['fingerprint'] != fingerprint:
                raise ValueError(f'task ID conflict: {task_id!r} was submitted with different parameters')
            return row['run_dir']
        db.execute('INSERT INTO task_receipts VALUES (?,?,?,?)',
                   (task_id, fingerprint, request['run_dir'], request['run_id']))
    return None


def abandon_task(request):
    """Release only this reservation after a confirmed pre-launch failure."""
    if request.get('task_id'):
        with transaction(request.get('state_dir')) as db:
            db.execute('DELETE FROM task_receipts WHERE task_id=? AND run_id=?',
                       (request['task_id'], request['run_id']))


def list_threads(path=None, workdir=None, limit=20):
    """Read runner-registered threads, including retained v2 mappings."""
    if limit <= 0:
        raise ValueError('thread limit must be positive')
    workspace = os.path.normcase(str(Path(workdir).resolve())) if workdir else None
    with read_state(path) as db:
        if db is None:
            return []
        present = tables(db)
        registered = ([dict(row, source='runner', writer_lock='unknown')
                       for row in db.execute('SELECT * FROM threads ORDER BY updated_at DESC')]
                      if 'threads' in present else [])
        if {'sessions', 'projects'} <= present:
            legacy = db.execute('''SELECT s.thread AS thread_id, p.workspace AS workdir,
                                   s.lane, s.run_dir, s.backend, NULL AS updated_at
                                   FROM sessions s JOIN projects p ON s.project=p.key
                                   ORDER BY s.rowid DESC''')
            registered.extend(dict(row, source='v2-session', writer_lock='unknown') for row in legacy)
    matching = {}
    for row in registered:
        if workspace and os.path.normcase(str(Path(row['workdir']).resolve())) != workspace:
            continue
        matching.setdefault(row['thread_id'], row)
        if len(matching) >= limit:
            break
    return list(matching.values())


def contains_redirect(path):
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, 'st_file_attributes', 0) &
                                                 stat.FILE_ATTRIBUTE_REPARSE_POINT)


def raise_walk_error(error):
    raise error


def prune_runs(path, older_than_days, dry_run):
    """Prune only owned terminal runs in the selected state's central root."""
    state = state_dir(path)
    root = state/'runs'
    if older_than_days < 0:
        raise ValueError('prune age must be nonnegative')
    if not root.is_dir() or not (state/DATABASE).is_file():
        return []
    plain_directory(root)
    if contains_redirect(root):
        raise ValueError('the central run root is redirected')
    cutoff = time.time() - older_than_days*86400
    selected = []
    with (read_state(state) if dry_run else transaction(state)) as db:
        if db is None:
            return []
        existing = tables(db)
        if 'run_records' not in existing:
            return []
        protected = {os.path.normcase(str(Path(row[0]).resolve()))
                     for table in ('sessions', 'threads', 'task_receipts', 'project_runs') if table in existing
                     for row in db.execute(f'SELECT run_dir FROM {table}') if row[0]}
        tickets = ({row[0] for row in db.execute('SELECT run FROM tickets')}
                   if 'tickets' in existing else set())
        known = db.execute('SELECT run_id,run_dir FROM run_records ORDER BY run_dir').fetchall()
        for record in known:
            try:
                run = Path(record['run_dir'])
                if run.parent != root or not run.is_dir() or contains_redirect(run) or run.resolve() != run:
                    continue
                if os.path.normcase(str(run)) in protected:
                    continue
                metadata = [run/'request.json', run/'result.json']
                if not all(file.is_file() and not contains_redirect(file) for file in metadata):
                    continue
                req, result = (read_json(file, {}) for file in metadata)
                if not isinstance(req, dict) or not isinstance(result, dict):
                    continue
                if req.get('run_dir') != str(run) or result.get('run_dir') != str(run):
                    continue
                if req.get('run_id') != record['run_id'] or result.get('run_id') != record['run_id']:
                    continue
                if record['run_id'] in tickets:
                    continue
                created_at = req.get('created_at')
                if not isinstance(created_at, (int, float)) or not 0 < created_at < cutoff:
                    continue
                if result.get('pending_reconciliation') or result.get('status') not in {
                    'ok', 'failed', 'timeout', 'cancelled', 'image_missing', 'verification_failed',
                    'internal_error', 'thread_busy',
                }:
                    continue
                if any(contains_redirect(Path(folder)/name)
                       for folder, dirs, files in os.walk(run, onerror=raise_walk_error, followlinks=False)
                       for name in dirs+files):
                    continue
                if not dry_run:
                    shutil.rmtree(run)
                    db.execute('DELETE FROM run_records WHERE run_id=?', (record['run_id'],))
                selected.append(str(run))
            except (ValueError, FileNotFoundError):
                # Partial/malformed run data and concurrent removals are not candidates.
                continue
    return selected


def project_record(group, desktop_id='', path=None):
    with transaction(path) as db:
        db.execute("""INSERT OR IGNORE INTO projects(key,host,conversation,title,workspace)
                      VALUES (?,?,?,?,?)""",
                   tuple(group[k] for k in ('key', 'host', 'conversation', 'title', 'workspace')))
        row = db.execute('SELECT * FROM projects WHERE key=?', (group['key'],)).fetchone()
        if row['title'] != group['title']:
            raise ValueError(f'conversation already recorded as {row["title"]!r}; use its original project title')
        if desktop_id:
            if row['desktop_id'] and row['desktop_id'] != desktop_id:
                raise ValueError('conversation is already bound to a different Codex Desktop project')
            existing = db.execute('SELECT key FROM projects WHERE desktop_id=?', (desktop_id,)).fetchone()
            if existing and existing['key'] != group['key']:
                raise ValueError('this Codex Desktop project already belongs to another conversation')
            db.execute('UPDATE projects SET desktop_id=? WHERE key=?', (desktop_id, group['key']))
        data = dict(row)
        data['desktop_id'] = desktop_id or data['desktop_id']
        data['sessions'] = [dict(s) for s in db.execute(
            'SELECT lane,run_dir,thread,model,backend FROM sessions WHERE project=? ORDER BY lane', (group['key'],))]
        data['runs'] = [dict(r) for r in db.execute(
            'SELECT run_dir,lane FROM project_runs WHERE project=? ORDER BY rowid', (group['key'],))]
        return data


class Ticket:
    def __init__(self, request, run):
        self.req = request
        self.run = run
        self.key = request['run_id']
        self.path = request.get('state_dir')
        group = request.get('project')
        self.slot = ''
        if group:
            self.slot = f'{group["key"]}:{request["lane"]}'
        elif request.get('lane'):
            workspace = os.path.normcase(str(Path(request['workdir']).resolve()))
            key = json.dumps([workspace, request['lane']], ensure_ascii=False).encode('utf-8')
            self.slot = 'workspace:'+hashlib.sha256(key).hexdigest()
        self.child = None
        birth = process_birth(os.getpid())
        if not birth:
            raise OSError('cannot establish supervisor identity for shared queue')
        if group:
            record = project_record(group, path=self.path)
            requested = request.get('codex_project_id', '')
            if requested and record['desktop_id'] and requested != record['desktop_id']:
                raise ValueError('conversation is already bound to a different Codex project')
            request['codex_project_id'] = requested or record['desktop_id']
        with transaction(self.path) as db:
            db.execute('INSERT INTO tickets(run,pid,birth,lane,thread,reuse) VALUES (?,?,?,?,?,?)',
                       (self.key, os.getpid(), birth, self.slot, (request.get('resume') or '').casefold(),
                        not request.get('fresh_session') and not request.get('ephemeral')))
            db.execute('INSERT INTO run_records VALUES (?,?)', (self.key, str(run)))
            if group:
                db.execute('INSERT INTO project_runs VALUES (?,?,?)', (str(run), group['key'], request['lane']))

    def acquire(self):
        """Admit the oldest eligible ticket, without blocking independent lanes."""
        with transaction(self.path) as db:
            rows = db.execute('SELECT * FROM tickets ORDER BY seq').fetchall()
            live = []
            for row in rows:
                if row['run'] != self.key and not row['reconcile'] and not ticket_alive(row):
                    db.execute('DELETE FROM tickets WHERE run=?', (row['run'],))
                else:
                    live.append(row)
            active = [r for r in live if r['active']]
            occupied_lanes = {r['lane'] for r in active if r['lane']}
            occupied_threads = {r['thread'].casefold() for r in active if r['thread']}
            if len(active) >= LIMIT:
                return None
            for row in live:
                if row['active'] or row['lane'] in occupied_lanes:
                    continue
                saved = (db.execute('SELECT * FROM sessions WHERE slot=?', (row['lane'],)).fetchone()
                         if row['lane'] and row['reuse'] else None)
                thread = row['thread'] or (saved['thread'] if saved else '') or ''
                if thread.casefold() in occupied_threads:
                    continue
                if row['run'] != self.key:
                    return None
                resume = self.req.get('resume') or ''
                use_saved = saved and not resume
                effective = resume or (saved['thread'] if use_saved else '')
                db.execute('UPDATE tickets SET active=1,thread=? WHERE run=?', (effective.casefold(), self.key))
                return dict(saved) if use_saved else {}
            return None

    def record_child(self, identity):
        # The child is blocked on its plan pipe until this record has committed.
        with transaction(self.path) as db:
            db.execute('UPDATE tickets SET child=? WHERE run=?', (json.dumps(identity), self.key))
        self.child = identity

    def child_closed(self):
        with transaction(self.path) as db:
            db.execute('UPDATE tickets SET child=NULL WHERE run=?', (self.key,))
        self.child = None

    def record_thread(self, thread_id):
        with transaction(self.path) as db:
            db.execute('UPDATE tickets SET thread=? WHERE run=? AND active=1', (thread_id.casefold(), self.key))

    def finish(self, result):
        if self.child and not owned_tree_stopped(self.child):
            return
        with transaction(self.path) as db:
            thread = result.get('thread_id')
            if result.get('pending_reconciliation'):
                if thread:
                    db.execute('UPDATE tickets SET thread=? WHERE run=?', (thread.casefold(), self.key))
                db.execute('UPDATE tickets SET active=1,reconcile=1 WHERE run=?', (self.key,))
                return
            if thread and result['execution'].get('status') == 'completed' and not self.req.get('ephemeral'):
                group = self.req.get('project') or {}
                lane, backend = self.req.get('lane'), self.req.get('backend')
                workdir = self.req.get('workdir') or group.get('workspace')
                if workdir:
                    db.execute('INSERT OR REPLACE INTO threads VALUES (?,?,?,?,?,?)',
                               (thread, workdir, lane, str(self.run), backend, time.time()))
                if self.slot:
                    db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?)',
                               (self.slot, group.get('key', ''), lane, str(self.run), thread,
                                self.req.get('model', ''), backend))
            db.execute('DELETE FROM tickets WHERE run=?', (self.key,))
