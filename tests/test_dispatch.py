from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from runner.dispatch import Ticket


class DispatchTests(unittest.TestCase):
    def test_ten_slots_then_fifo(self):
        scratch = ROOT/'tests/.work'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as state, patch.dict(
                os.environ, CODEX_SUBAGENT_STATE_DIR=state):
            tickets = [Ticket({'run_id': str(i)}, Path(state)/str(i)) for i in range(12)]
            for ticket in tickets[:10]:
                self.assertEqual(ticket.acquire(), {})
            self.assertIsNone(tickets[10].acquire())
            self.assertIsNone(tickets[11].acquire())
            tickets[0].finish({'execution': {'status': 'completed'}})
            self.assertIsNone(tickets[11].acquire())
            self.assertEqual(tickets[10].acquire(), {})
            self.assertIsNone(tickets[11].acquire())
            tickets[10].finish({'execution': {'status': 'completed'}})
            self.assertEqual(tickets[11].acquire(), {})
