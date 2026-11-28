from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from runner import dispatch


class DispatchV3Tests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT/'tests/.work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)

    def request(self, run_id, workdir=None, **options):
        return {
            'run_id': run_id, 'run_dir': str(self.path/'runs'/run_id),
            'state_dir': str(self.path), 'workdir': str(workdir or ROOT),
            'backend': 'cli', **options,
        }

    def test_v2_migration_keeps_mappings_and_backs_up_original_schema(self):
        filename = self.path/'dispatch-v1.sqlite3'
        db = sqlite3.connect(filename)
        try:
            db.executescript('''
                CREATE TABLE tickets (seq INTEGER PRIMARY KEY, run TEXT UNIQUE,
                    pid INTEGER, birth TEXT, lane TEXT, thread TEXT, active INTEGER DEFAULT 0, child TEXT);
                CREATE TABLE projects (key TEXT PRIMARY KEY, host TEXT, conversation TEXT,
                    title TEXT, workspace TEXT, desktop_id TEXT DEFAULT '');
                CREATE TABLE sessions (slot TEXT PRIMARY KEY, project TEXT, lane TEXT,
                    run_dir TEXT, thread TEXT, model TEXT, backend TEXT);
            ''')
            db.execute("INSERT INTO projects VALUES ('legacy','Host','Chat','Title',?,'Desktop')", (str(ROOT),))
            db.execute("INSERT INTO sessions VALUES ('legacy:work','legacy','work','run','a','old','sdk')")
            db.commit()
        finally:
            db.close()
        dispatch.preflight(self.path)
        dispatch.preflight(self.path)
        self.assertEqual(dispatch.list_threads(self.path, ROOT)[0]['thread_id'], 'a')
        backups = list(self.path.glob('dispatch-v1.pre-v3.*.sqlite3'))
        self.assertEqual(len(backups), 1)
        db = sqlite3.connect(backups[0])
        try:
            self.assertEqual(db.execute('SELECT desktop_id FROM projects').fetchone(), ('Desktop',))
            self.assertEqual(db.execute('PRAGMA user_version').fetchone(), (0,))
        finally:
            db.close()

    def test_atomic_reservation_and_conflict(self):
        requests = [self.request(str(i), task_id='assignment') for i in range(6)]
        with ThreadPoolExecutor() as pool:
            receipts = list(pool.map(lambda req: dispatch.reserve_task(req, 'same'), requests))
        self.assertEqual(receipts.count(None), 1)
        winner = requests[receipts.index(None)]
        self.assertEqual(set(receipts)-{None}, {winner['run_dir']})
        with self.assertRaisesRegex(ValueError, 'conflict'):
            dispatch.reserve_task(winner, 'different')
        dispatch.abandon_task(winner)
        self.assertIsNone(dispatch.reserve_task(winner, 'different'))

    def test_migration_declines_a_live_v2_ticket(self):
        with closing(sqlite3.connect(self.path/'dispatch-v1.sqlite3')) as db:
            db.execute('''CREATE TABLE tickets (
                seq INTEGER PRIMARY KEY, run TEXT UNIQUE, pid INTEGER, birth TEXT,
                lane TEXT, thread TEXT, active INTEGER DEFAULT 0, child TEXT)''')
            db.execute('INSERT INTO tickets(run,pid,birth,active) VALUES (?,?,?,1)',
                       ('legacy', os.getpid(), dispatch.process_birth(os.getpid())))
            db.commit()
        with self.assertRaisesRegex(ValueError, 'v2 queue is still active'):
            dispatch.preflight(self.path)
        self.assertEqual(list(self.path.glob('dispatch-v1.pre-v3.*.sqlite3')), [])

    def test_lane_and_thread_mutex_with_discovery(self):
        first = self.request('first', lane='work')
        ticket = dispatch.Ticket(first, Path(first['run_dir']))
        self.assertEqual(ticket.acquire(), {})
        ticket.record_thread('thread-1')
        second = self.request('second', self.path, resume='thread-1')
        other = dispatch.Ticket(second, Path(second['run_dir']))
        self.assertIsNone(other.acquire())
        ticket.finish({'thread_id': 'thread-1', 'execution': {'status': 'completed'}})
        self.assertEqual(other.acquire(), {})
        followup = self.request('followup', lane='work')
        lane = dispatch.Ticket(followup, Path(followup['run_dir']))
        self.assertIsNone(lane.acquire())
        other.finish({'thread_id': 'thread-1', 'execution': {'status': 'failed'}})
        self.assertEqual(lane.acquire()['thread'], 'thread-1')
        rows = dispatch.list_threads(self.path, ROOT)
        self.assertEqual(rows[0]['writer_lock'], 'unknown')
        self.assertEqual(rows[0]['thread_id'], 'thread-1')

    def test_prune_preserves_idempotency_and_active_runs(self):
        statuses = {'unused': 'ok', 'active': 'running', 'unknown': 'status_unknown',
                    'idempotent': 'ok', 'unregistered': 'ok'}
        for run_id, status in statuses.items():
            req = self.request(run_id, task_id='keep' if run_id == 'idempotent' else None)
            dispatch.reserve_task(req, 'fingerprint')
            run = Path(req['run_dir'])
            if run_id != 'unregistered':
                ticket = dispatch.Ticket(req, run)
                ticket.finish({'execution': {'status': 'failed'}})
            run.mkdir(parents=True)
            req['created_at'] = time.time() - 2*86400
            (run/'request.json').write_text(json.dumps(req), encoding='utf-8')
            result = {'run_id': run_id, 'run_dir': str(run), 'status': status}
            (run/'result.json').write_text(json.dumps(result), encoding='utf-8')
        wanted = self.path/'runs/unused'
        self.assertEqual(dispatch.prune_runs(self.path, 1, True), [str(wanted)])
        self.assertTrue(wanted.exists())
        self.assertEqual(dispatch.prune_runs(self.path, 1, False), [str(wanted)])
        self.assertFalse(wanted.exists())
        self.assertTrue((self.path/'runs/idempotent').exists())
        self.assertTrue((self.path/'runs/unregistered').exists())

    def test_ambiguous_remote_outcome_keeps_thread_reserved(self):
        req = self.request('first')
        ticket = dispatch.Ticket(req, Path(req['run_dir']))
        self.assertEqual(ticket.acquire(), {})
        ticket.finish({'thread_id': 'thread-1', 'pending_reconciliation': True,
                       'execution': {'status': 'failed'}})
        next_req = self.request('second', resume='THREAD-1')
        self.assertIsNone(dispatch.Ticket(next_req, Path(next_req['run_dir'])).acquire())
