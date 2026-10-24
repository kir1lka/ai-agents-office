"""Daily local Codex metrics. Counts response usage once; never parses chat content."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

MOSCOW = timezone(timedelta(hours=3))


def day_bounds(now):
    date = datetime.fromtimestamp(now, MOSCOW)
    start = date.replace(hour=0, minute=0, second=0, microsecond=0)
    return date.date().isoformat(), start.timestamp(), (start + timedelta(days=1)).timestamp()


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def connect(path):
    return closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.3))


class DailyStats:
    def __init__(self, home, observed_file):
        self.home, self.observed_file = Path(home), Path(observed_file)
        self.files, self.day, self.last_poll, self.last_save = {}, '', 0, 0
        self.chat_seconds = 0
        self.value = {'available': False, 'tokens_available': False, 'ranking': [], 'hours': [0] * 24}
        try:
            saved = json.loads(self.observed_file.read_text(encoding='utf-8'))
            self.saved = saved
        except (OSError, ValueError):
            self.saved = {}

    def update(self, now, chat_working=False):
        day, start, end = day_bounds(now)
        if day != self.day:
            self.files, self.day = {}, day
            self.chat_seconds = self.saved.get('seconds', 0) if self.saved.get('day') == day else 0
            self.last_poll = 0
        if self.last_poll and chat_working:
            self.chat_seconds += max(0, min(5, now - self.last_poll))
        self.last_poll = now
        if now - self.last_save > 30:
            self.last_save = now
            try:
                temporary = self.observed_file.with_suffix('.tmp')
                temporary.write_text(json.dumps({'day': day, 'seconds': self.chat_seconds}), encoding='utf-8')
                temporary.replace(self.observed_file)
            except OSError:
                pass
        history, metadata = self.home / 'thread_history_1.sqlite', self.home / 'state_5.sqlite'
        if not history.exists() or not metadata.exists():
            return self.value
        with connect(metadata) as db:
            rows = db.execute('SELECT id, agent_nickname, agent_path, thread_source, rollout_path FROM threads').fetchall()
        people = {r[0]: r for r in rows}
        with connect(history) as db:
            turns = db.execute('''SELECT thread_id, turn_id, status, started_at, completed_at
                FROM thread_turns WHERE started_at < ? AND (completed_at >= ? OR completed_at IS NULL)''', (end, start)).fetchall()
            employees = {'codex': {'id': 'codex', 'name': 'Codex', 'label': 'CODEX', 'intervals': [], 'tasks': 0}}
            for tid, turn, status, began, finished in turns:
                person = people.get(tid)
                if not person or person[3] == 'guardian_review':
                    continue
                if finished is None:
                    if status not in ['inProgress', 'in_progress', 'running']:
                        continue
                    touched = db.execute('''SELECT MAX(COALESCE(completed_at_ms, started_at_ms))
                        FROM thread_items WHERE thread_id=? AND turn_id=?''', (tid, turn)).fetchone()[0]
                    touched = (touched or began * 1000) / 1000
                    finished = now if now - touched < 1800 else touched
                a, b = max(start, began), min(now, end, finished)
                helper = person[3] == 'subagent' or person[2]
                key = tid if helper else 'codex'
                nickname = person[1] or str(person[2] or '').split('/')[-1] or 'Agent'
                employee = employees.setdefault(key, {'id': key, 'name': nickname if helper else 'Codex',
                    'label': ''.join(c for c in nickname.upper() if c.isascii() and c.isalnum())[:7] if helper else 'CODEX',
                    'intervals': [], 'tasks': 0})
                if b > a:
                    employee['intervals'].append((a, b))
                if status == 'completed' and start <= finished < end:
                    employee['tasks'] += 1
        hours, ranking = [0] * 24, []
        for employee in employees.values():
            intervals = merge_intervals(employee.pop('intervals'))
            employee['seconds'] = int(sum(b - a for a, b in intervals))
            ranking.append(employee)
            for a, b in intervals:
                for hour in range(24):
                    hours[hour] += max(0, min(b, start + (hour + 1) * 3600) - max(a, start + hour * 3600))
        ranking.append({'id': 'chatgpt', 'name': 'ChatGPT', 'label': 'GPT', 'seconds': int(self.chat_seconds), 'tasks': None, 'estimated': True})
        # Only append-new bytes from files that could contain today's usage. Partial
        # final lines are retried on the next scan, and cumulative counters are ignored.
        for person in rows:
            if not person[4]:
                continue
            file = Path(person[4])
            try:
                stat = file.stat()
                if stat.st_mtime < start:
                    continue
                cached = self.files.setdefault(str(file), {'offset': 0, 'records': {}})
                if stat.st_size < cached['offset']:
                    cached.update(offset=0, records={})
                with file.open('rb') as stream:
                    stream.seek(cached['offset'])
                    while True:
                        raw = stream.readline()
                        if not raw or not raw.endswith(b'\n'):
                            break
                        cached['offset'] = stream.tell()
                        if b'"token_usage_record"' not in raw[:200]:
                            continue
                        try:
                            event = json.loads(raw)
                            if event.get('type') != 'token_usage_record':
                                continue
                            stamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')).timestamp()
                            if not start <= stamp < end:
                                continue
                            payload = event['payload']
                            key = payload.get('response_id') or (str(file), cached['offset'])
                            cached['records'][str(key)] = payload.get('usage') or {}
                        except (ValueError, KeyError, TypeError):
                            continue
            except OSError:
                continue
        usage = {}
        for file in self.files.values():
            usage.update(file['records'])
        tokens = {'input': 0, 'output': 0, 'cached': 0, 'total': 0}
        for record in usage.values():
            input_ = max(0, int(record.get('input_tokens') or 0))
            output = max(0, int(record.get('output_tokens') or 0))
            tokens['input'] += input_
            tokens['output'] += output
            tokens['cached'] += max(0, int(record.get('cached_input_tokens') or 0))
            tokens['total'] += max(0, int(record.get('total_tokens', input_ + output) or 0))
        self.value = {'available': True, 'tokens_available': bool(usage), 'day': day, 'timezone': 'Europe/Moscow',
            'ranking': sorted(ranking, key=lambda r: (-r['seconds'], r['name']))[:8],
            'hours': [int(x) for x in hours], 'tokens': tokens, 'updated_at': int(now * 1000)}
        return self.value
