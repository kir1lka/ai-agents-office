"""Local, read-only Codex activity bridge for the pixel office. Python stdlib only."""
from __future__ import annotations

import argparse
import csv
from contextlib import closing
import io
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from office_stats import DailyStats

ROOT = Path(__file__).resolve().parent
IDLE_KEEP = 300  # A finished task rests for five minutes, then leaves.
MAX_AGENTS = 8
STALE_AFTER = 1800  # Do not keep interrupted/crashed old turns working forever.
WAITING = {'waiting', 'waitingForInput', 'requiresAction', 'awaitingInput'}
WORKING = {'inProgress', 'in_progress', 'running'}
TOOLS = {
    'reasoning': ('думает', ''),
    'commandExecution': ('Bash', ''),
    'fileChange': ('Edit', ''),
    'webSearch': ('WebSearch', ''),
    'agentMessage': ('Write', 'ответ'),
    'collabAgentToolCall': ('Agent', ''),
    'mcpToolCall': ('думает', 'инструмент'),
    'customToolCall': ('думает', 'инструмент'),
}


def read_only(db: Path):
    return closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.3))


def running_apps():
    """Tasklist reads names only: no command lines, prompts, or credentials."""
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    result = subprocess.run(
        ['tasklist.exe', '/FO', 'CSV', '/NH'], capture_output=True,
        timeout=6, creationflags=flags,
    )
    if result.returncode:
        raise OSError('tasklist failed')
    rows = csv.reader(io.StringIO(result.stdout.decode('utf-8', errors='replace')))
    return {r[0].lower() for r in rows if r}


def display_text(value, limit=180):
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    text = re.sub(r'(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[\w./+\-]+', r'\1[скрыто]', text)
    text = re.sub(r'\bsk-[\w-]{10,}', '[ключ скрыт]', text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def task_text(value):
    text = str(value or '')
    # Attachment envelopes are transport metadata, not the user's task title.
    request = re.search(r'^#{1,3}\s*My request:\s*', text, flags=re.M | re.I)
    if request:
        text = text[request.end():]
    text = re.sub(r'<image\b[^>]*>.*?</image>', '', text, flags=re.S | re.I)
    return display_text(text, 160)


def operation(item):
    """Describe public tool metadata; never read reasoning content or tool output."""
    typ = item['item_type']
    if typ == 'commandExecution':
        actions = json.loads(item['command_actions'] or '[]')
        action = actions[0] if actions else {}
        if action.get('type') == 'read':
            return 'Читает файл', display_text(action.get('path') or action.get('name'))
        if action.get('type') == 'search':
            return 'Ищет в проекте', display_text(action.get('query') or action.get('path'))
        command = display_text(action.get('command') or item['command'], 220)
        # Prefer an actual operation over an environment assignment at the start.
        command = re.sub(r'^\$env:\w+\s*=\s*[^;]+;\s*', '', command)
        if re.search(r'(?i)\b(unittest|pytest|npm test|pnpm test|vitest)\b', command):
            return 'Запускает проверки', command
        return 'Выполняет команду', command or 'Команда в терминале'
    if typ == 'fileChange':
        paths = json.loads(item['file_paths'] or '[]')
        names = [str(p).replace('\\', '/').split('/')[-1] for p in paths if p]
        return 'Редактирует файлы', display_text(' · '.join(names), 200) or 'Изменения проекта'
    if typ == 'webSearch':
        return 'Ищет в интернете', display_text(item['query'])
    if typ == 'agentMessage':
        return 'Сообщает о ходе работы', ''
    if typ == 'collabAgentToolCall':
        return 'Координирует помощников', display_text(item['tool'])
    if typ in ['mcpToolCall', 'customToolCall']:
        return 'Использует инструмент', display_text(item['tool']) or 'Выполняет действие'
    return 'Обдумывает задачу', ''


def codex_agents(home: Path, now: float, app_open=True):
    if not app_open:
        return []
    history = home / 'thread_history_1.sqlite'
    if not history.exists():
        return [idle_agent()]
    with read_only(history) as db:
        db.row_factory = sqlite3.Row
        # Latest turn for each recently used chat. Old active turns can be crash residue.
        turns = db.execute('''
            SELECT thread_id, turn_id, status, started_at, completed_at
            FROM thread_turns WHERE started_at > ?
            ORDER BY started_at DESC LIMIT 200
        ''', (now - 86400,)).fetchall()
        latest = {}
        for turn in turns:
            latest.setdefault(turn['thread_id'], turn)
        candidates = []
        for tid, turn in latest.items():
            items = db.execute('''
                SELECT item_type, started_at_ms, completed_at_ms,
                    json_extract(item_json, '$.command') AS command,
                    json_extract(item_json, '$.commandActions') AS command_actions,
                    (SELECT json_group_array(json_extract(value, '$.path'))
                     FROM json_each(item_json, '$.changes')) AS file_paths,
                    json_extract(item_json, '$.query') AS query,
                    json_extract(item_json, '$.tool') AS tool,
                    CASE WHEN item_type = 'agentMessage' THEN json_extract(item_json, '$.text') END AS public_text,
                    json_extract(item_json, '$.phase') AS phase
                FROM thread_items WHERE thread_id = ? AND turn_id = ?
                ORDER BY rollout_ordinal DESC LIMIT 12
            ''', (tid, turn['turn_id'])).fetchall()
            item = items[0] if items else None
            active = turn['status'] in WORKING | WAITING
            touched = max(turn['started_at'], (item['started_at_ms'] or 0) / 1000 if item else 0,
                          (item['completed_at_ms'] or 0) / 1000 if item else 0)
            if active and now - touched > STALE_AFTER:
                continue
            finished = turn['completed_at'] or 0
            if not active and (not finished or now - finished > IDLE_KEEP):
                continue
            tool, detail = TOOLS.get(item['item_type'] if item else '', ('думает', ''))
            status = 'waiting' if turn['status'] in WAITING else 'working' if active else 'idle'
            title, description = operation(item) if item else ('Обдумывает задачу', '')
            recent = next((i for i in items if i['item_type'] not in ['reasoning', 'agentMessage']), None)
            if item and item['item_type'] == 'reasoning' and recent:
                previous_title, previous_detail = operation(recent)
                description = 'Последнее действие: ' + previous_title.lower() + (': ' + previous_detail if previous_detail else '')
            progress = next((display_text(i['public_text'], 240) for i in items
                             if i['item_type'] == 'agentMessage' and i['phase'] == 'commentary'), '')
            request = db.execute('''SELECT json_extract(item_json, '$.content[0].text')
                FROM thread_items WHERE thread_id = ? AND turn_id = ? AND item_type = 'userMessage'
                ORDER BY rollout_ordinal DESC LIMIT 1''', (tid, turn['turn_id'])).fetchone()
            candidates.append((status != 'idle', touched, {
                'id': 'codex-' + tid, 'kind': 'codex', 'name': 'Codex',
                'status': status, 'tool': tool if active else '',
                'detail': detail if active else '', 'hooks': True,
                'activity_title': title if active else 'Задача завершена',
                'activity_detail': description if active else 'Отдыхает в офисе до следующей задачи',
                'progress': progress if active else '', 'event_at': int(touched * 1000),
                'task': task_text(request[0]) if request and request[0] else '',
            }))
    agents = [row[2] for row in sorted(candidates, key=lambda row: row[:2], reverse=True)]
    # Add names and a fallback chat title. Only display fields go to the wallpaper.
    metadata = home / 'state_5.sqlite'
    if agents and metadata.exists():
        try:
            with read_only(metadata) as db:
                for agent in agents:
                    tid = agent['id'][6:]
                    row = db.execute('''SELECT cwd, agent_nickname, agent_path, thread_source, title
                        FROM threads WHERE id = ? AND archived = 0''', (tid,)).fetchone()
                    if not row or row[3] == 'guardian_review':
                        agent['hidden'] = True
                        continue
                    agent['task'] = agent['task'] or display_text(row[4], 110)
                    child = db.execute('SELECT 1 FROM thread_spawn_edges WHERE child_thread_id = ? LIMIT 1', (tid,)).fetchone()
                    if row[3] == 'subagent' or row[2] or child:
                        nickname = row[1] or (str(row[2] or '').split('/')[-1]) or 'Agent'
                        agent.update(kind='codex-agent', name='Агент · ' + nickname[:22],
                                     label=''.join(c for c in nickname.upper() if c.isascii() and c.isalnum())[:10] or 'AGENT')
                        continue
                    if row and row[0]:
                        folder = str(row[0]).replace('\\', '/').rstrip('/').split('/')[-1][:22]
                        if folder:
                            agent['name'] += ' · ' + folder
        except sqlite3.Error:
            pass
    agents = [a for a in agents if not a.get('hidden')]
    main = next((a for a in agents if a['kind'] == 'codex'), idle_agent())
    main['id'] = 'codex-main'
    helpers = [a for a in agents if a['kind'] == 'codex-agent'][:MAX_AGENTS - 2]
    return [main, *helpers]


def idle_agent():
    return {'id': 'codex-main', 'kind': 'codex', 'name': 'Codex', 'status': 'idle',
            'tool': '', 'detail': '', 'hooks': True, 'activity_title': 'Свободен',
            'activity_detail': 'Ждёт новую задачу в Codex', 'task': '', 'progress': ''}


def chatgpt_agent(working=False):
    return {'id': 'chatgpt-office', 'kind': 'chatgpt', 'name': 'ChatGPT',
            'status': 'working' if working else 'idle', 'tool': '', 'detail': '', 'hooks': False,
            'activity_title': 'Приложение активно' if working else 'Отдыхает в офисе',
            'activity_detail': 'Оценка по нагрузке приложения. Текст текущей задачи недоступен.' if working else 'Гуляет, общается и ждёт следующую задачу',
            'progress': '', 'task': ''}


class Monitor:
    def __init__(self, home):
        self.home = home
        self.lock = threading.Lock()
        self.snapshot = {'agents': [], 'scan': False, 'version': 'local-ru-2'}
        self.closed = threading.Event()
        self.daily = DailyStats(home, ROOT / 'office-observed.json')

    def run(self):
        names, next_process_scan = set(), 0
        chat_cpu, chat_busy_until, next_stats = None, 0, 0
        while not self.closed.is_set():
            try:
                now = time.time()
                if now >= next_process_scan:
                    names = running_apps()
                    next_process_scan = now + 4
                    if 'chatgpt.exe' in names:
                        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                            '(Get-Process ChatGPT -ErrorAction SilentlyContinue | Measure-Object CPU -Sum).Sum'],
                            capture_output=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        try:
                            cpu = float(result.stdout.decode().strip().replace(',', '.'))
                            if chat_cpu is not None and cpu - chat_cpu > 0.2:
                                chat_busy_until = now + 12
                            chat_cpu = cpu
                        except ValueError:
                            pass
                    else:
                        chat_cpu, chat_busy_until = None, 0
                agents = codex_agents(self.home, now, 'codex.exe' in names)
                if agents or 'chatgpt.exe' in names:
                    agents.append(chatgpt_agent(now < chat_busy_until))
                with self.lock:
                    self.snapshot.update(agents=agents, scan=True, now=int(now * 1000))
                if now >= next_stats:
                    try:
                        stats = self.daily.update(now, now < chat_busy_until)
                        with self.lock:
                            self.snapshot['stats'] = stats
                    except (OSError, sqlite3.Error, ValueError):
                        pass
                    next_stats = now + 5
            except (OSError, sqlite3.Error, subprocess.SubprocessError, ValueError):
                # A transient lock/schema change must not empty a healthy office.
                with self.lock:
                    self.snapshot['scan'] = False
            self.closed.wait(1)

    def state(self):
        with self.lock:
            return dict(self.snapshot)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond_headers(self, status, kind):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()

    def do_OPTIONS(self):
        self.respond_headers(204, 'text/plain')

    def do_GET(self):
        route = urlsplit(self.path).path
        if route in ['/state', '/health']:
            self.respond_headers(200, 'application/json; charset=utf-8')
            self.wfile.write(json.dumps(self.server.monitor.state(), ensure_ascii=False).encode('utf-8'))
            return
        if route in ['/', '/index.html']:
            file, kind = ROOT / 'index.html', 'text/html; charset=utf-8'
        elif route == '/office-panel.js':
            file, kind = ROOT / 'office-panel.js', 'text/javascript; charset=utf-8'
        elif route.startswith('/fonts/') and Path(route).name in ['tiny5-cyr.woff2', 'tiny5-lat.woff2']:
            file, kind = ROOT / 'fonts' / Path(route).name, 'font/woff2'
        else:
            self.respond_headers(404, 'text/plain')
            return
        try:
            data = file.read_bytes()
        except OSError:
            self.respond_headers(404, 'text/plain')
            return
        self.respond_headers(200, kind)
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=7777)
    parser.add_argument('--codex-home', type=Path,
                        default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    args = parser.parse_args()
    monitor = Monitor(args.codex_home)
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as error:
        if getattr(error, 'winerror', None) == 10048 or error.errno in [48, 98, 10048]:
            return  # Already running; do not start another copy.
        raise
    server.monitor = monitor
    threading.Thread(target=monitor.run, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        monitor.closed.set()
        server.server_close()


if __name__ == '__main__':
    main()
