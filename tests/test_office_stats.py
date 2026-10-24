from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from office_stats import DailyStats, day_bounds, merge_intervals


class DailyStatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc).timestamp()
        self.rollout = self.home / 'rollout.jsonl'
        self.rollout.write_bytes(b'')
        os.utime(self.rollout, (self.now, self.now))
        with closing(sqlite3.connect(self.home / 'state_5.sqlite', isolation_level=None)) as db:
            db.execute('CREATE TABLE threads (id, agent_nickname, agent_path, thread_source, rollout_path)')
            db.execute('INSERT INTO threads VALUES (?, NULL, NULL, ?, ?)', ('main', 'user', str(self.rollout)))
            db.execute('INSERT INTO threads VALUES (?, ?, ?, ?, NULL)', ('child', 'Turing', '/root/ui', 'subagent'))
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite', isolation_level=None)) as db:
            db.execute('CREATE TABLE thread_turns (thread_id, turn_id, status, started_at, completed_at)')
            db.execute('CREATE TABLE thread_items (thread_id, turn_id, started_at_ms, completed_at_ms)')
        self.stats = DailyStats(self.home, self.home / 'observed.json')

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, response='one', age=0, usage=None):
        data = {'type': 'token_usage_record',
                'timestamp': datetime.fromtimestamp(self.now - age, timezone.utc).isoformat(),
                'payload': {'response_id': response, 'usage': usage or {'input_tokens': 100, 'output_tokens': 20,
                    'cached_input_tokens': 80, 'total_tokens': 120},
                    'thread_token_usage': {'total_tokens': 999999}}}
        with self.rollout.open('a', encoding='utf-8') as f:
            f.write(json.dumps(data) + '\n')
        os.utime(self.rollout, (self.now, self.now))

    def test_usage_deduplicates_responses_and_does_not_add_cache_again(self):
        self.record()
        self.record()
        self.record('old', age=86400)
        result = self.stats.update(self.now)
        self.assertEqual(result['tokens'], {'input': 100, 'output': 20, 'cached': 80, 'total': 120})
        self.assertEqual(self.stats.update(self.now + 5)['tokens']['total'], 120)
        self.record('two')
        self.assertEqual(self.stats.update(self.now + 10)['tokens']['total'], 240)

    def test_overlapping_main_chats_are_not_counted_twice(self):
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite', isolation_level=None)) as db:
            for tid, turn, a, b in [('main', 'one', 200, 100), ('main', 'two', 150, 50), ('child', 'three', 80, 20)]:
                db.execute('INSERT INTO thread_turns VALUES (?, ?, ?, ?, ?)', (tid, turn, 'completed', self.now-a, self.now-b))
        result = self.stats.update(self.now)
        rows = {r['label']: r for r in result['ranking']}
        self.assertEqual(rows['CODEX']['seconds'], 150)
        self.assertEqual(rows['CODEX']['tasks'], 2)
        self.assertEqual(rows['TURING']['seconds'], 60)
        self.assertEqual(sum(result['hours']), 210)

    def test_midnight_uses_moscow_and_resets_daily_tokens(self):
        stamp = datetime(2026, 10, 23, 21, 1, tzinfo=timezone.utc).timestamp()
        self.assertEqual(day_bounds(stamp)[0], '2026-10-24')
        self.record()
        self.assertTrue(self.stats.update(self.now)['tokens_available'])
        result = self.stats.update(stamp)
        self.assertEqual(result['tokens']['total'], 0)
        self.assertFalse(result['tokens_available'])

    def test_incomplete_last_record_is_retried(self):
        self.record()
        original = self.rollout.read_bytes()
        self.rollout.write_bytes(original[:-1])
        os.utime(self.rollout, (self.now, self.now))
        self.assertFalse(self.stats.update(self.now)['tokens_available'])
        with self.rollout.open('ab') as f:
            f.write(b'\n')
        self.assertEqual(self.stats.update(self.now + 5)['tokens']['total'], 120)

    def test_interval_merge(self):
        self.assertEqual(merge_intervals([(1, 5), (3, 7), (8, 9), (9, 10)]), [[1, 7], [8, 10]])


if __name__ == '__main__':
    unittest.main()
