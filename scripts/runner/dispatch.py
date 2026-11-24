"""Shared per-user admission queue and conversation-to-thread index."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

from .processes import owned_tree_stopped, process_birth

LIMIT = 10


def state_dir():
    override = os.environ.get('CODEX_SUBAGENT_STATE_DIR')
    return Path(override).expanduser().resolve() if override else Path.home()/'.codex-subagent'


@contextmanager
def transaction():
    path = state_dir()
    path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path/'dispatch-v1.sqlite3', timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute("""CREATE TABLE IF NOT EXISTS tickets (
            seq INTEGER PRIMARY KEY, run TEXT UNIQUE, pid INTEGER, birth TEXT,
            lane TEXT, thread TEXT, active INTEGER DEFAULT 0, child TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS projects (
            key TEXT PRIMARY KEY, host TEXT, conversation TEXT, title TEXT,
            workspace TEXT, desktop_id TEXT DEFAULT '')""")
        db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS project_desktop_binding
                      ON projects(desktop_id) WHERE desktop_id <> ''""")
        db.execute("""CREATE TABLE IF NOT EXISTS sessions (
            slot TEXT PRIMARY KEY, project TEXT, lane TEXT, run_dir TEXT,
            thread TEXT, model TEXT, backend TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS project_runs (
            run_dir TEXT PRIMARY KEY, project TEXT, lane TEXT)""")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def project_record(group, desktop_id=''):
    with transaction() as db:
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
        group = request.get('project')
        self.slot = f'{group["key"]}:{request["lane"]}' if group else ''
        self.child = None
        birth = process_birth(os.getpid())
        if not birth:
            raise OSError('cannot establish supervisor identity for shared queue')
        if group:
            record = project_record(group)
            requested = request.get('codex_project_id', '')
            if requested and record['desktop_id'] and requested != record['desktop_id']:
                raise ValueError('conversation is already bound to a different Codex project')
            request['codex_project_id'] = requested or record['desktop_id']
        with transaction() as db:
            db.execute('INSERT INTO tickets(run,pid,birth,lane,thread) VALUES (?,?,?,?,?)',
                       (self.key, os.getpid(), birth, self.slot, request.get('resume', '')))
            if group:
                db.execute('INSERT INTO project_runs VALUES (?,?,?)', (str(run), group['key'], request['lane']))

    def acquire(self):
        """Admit the oldest eligible ticket, without blocking independent lanes."""
        with transaction() as db:
            rows = db.execute('SELECT * FROM tickets ORDER BY seq').fetchall()
            live = []
            for row in rows:
                try:
                    alive = row['run'] == self.key or process_birth(row['pid']) == row['birth']
                except (OSError, ValueError):
                    alive = True
                if not alive and (not row['child'] or owned_tree_stopped(json.loads(row['child']))):
                    db.execute('DELETE FROM tickets WHERE run=?', (row['run'],))
                else:
                    live.append(row)
            active = [r for r in live if r['active']]
            occupied_lanes = {r['lane'] for r in active if r['lane']}
            occupied_threads = {r['thread'] for r in active if r['thread']}
            if len(active) >= LIMIT:
                return None
            for row in live:
                if row['active'] or row['lane'] in occupied_lanes:
                    continue
                saved = db.execute('SELECT * FROM sessions WHERE slot=?', (row['lane'],)).fetchone()
                thread = row['thread'] or (saved['thread'] if saved else '')
                if thread in occupied_threads:
                    continue
                if row['run'] != self.key:
                    return None
                resume = self.req.get('resume', '')
                use_saved = saved and not self.req.get('fresh_session') and not resume
                effective = resume or (saved['thread'] if use_saved else '')
                db.execute('UPDATE tickets SET active=1,thread=? WHERE run=?', (effective, self.key))
                return dict(saved) if use_saved else {}
            return None

    def record_child(self, identity):
        # The child is blocked on its plan pipe until this record has committed.
        with transaction() as db:
            db.execute('UPDATE tickets SET child=? WHERE run=?', (json.dumps(identity), self.key))
        self.child = identity

    def child_closed(self):
        with transaction() as db:
            db.execute('UPDATE tickets SET child=NULL WHERE run=?', (self.key,))
        self.child = None

    def finish(self, result):
        if self.child and not owned_tree_stopped(self.child):
            return
        with transaction() as db:
            thread = result.get('thread_id')
            if (self.slot and thread and result['execution'].get('status') == 'completed'
                    and not self.req.get('ephemeral')):
                db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?)',
                           (self.slot, self.req['project']['key'], self.req['lane'],
                            str(self.run), thread, self.req['model'], self.req['backend']))
            db.execute('DELETE FROM tickets WHERE run=?', (self.key,))
