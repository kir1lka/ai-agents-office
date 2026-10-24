import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import threading
import json
from urllib.request import urlopen

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).parents[1] / 'office-bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ActivityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.now = 2000000000
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite', isolation_level=None)) as db:
            db.executescript('''
                CREATE TABLE thread_turns (thread_id, turn_id, status, started_at, completed_at);
                CREATE TABLE thread_items (thread_id, turn_id, item_type, started_at_ms,
                    completed_at_ms, rollout_ordinal, item_json);
            ''')
        with closing(sqlite3.connect(self.home / 'state_5.sqlite', isolation_level=None)) as db:
            db.execute('CREATE TABLE threads (id, cwd, archived, agent_nickname, agent_path, thread_source, title)')
            db.execute('CREATE TABLE thread_spawn_edges (parent_thread_id, child_thread_id, status)')
            db.execute('INSERT INTO threads VALUES (?, ?, 0, NULL, NULL, ?, ?)', ('chat1', r'C:\Work\My project', 'user', 'Проверка проекта'))

    def tearDown(self):
        self.temp.cleanup()

    def turn(self, chat='chat1', turn='turn1', status='inProgress', age=10, end=None):
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite', isolation_level=None)) as db:
            db.execute('INSERT INTO thread_turns VALUES (?, ?, ?, ?, ?)',
                       (chat, turn, status, self.now - age, end))
            db.execute('INSERT INTO thread_items VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (chat, turn, 'fileChange', (self.now - age) * 1000, None, 1,
                        json.dumps({'changes': [{'path': r'C:\Work\app.py'}]})))

    def test_working_and_workspace_label(self):
        self.turn()
        agents = bridge.codex_agents(self.home, self.now)
        self.assertEqual(agents[0]['status'], 'working')
        self.assertEqual(agents[0]['tool'], 'Edit')
        self.assertEqual(agents[0]['name'], 'Codex · My project')
        self.assertEqual(agents[0]['activity_title'], 'Редактирует файлы')
        self.assertEqual(agents[0]['activity_detail'], 'app.py')
        self.assertEqual(agents[0]['task'], 'Проверка проекта')

    def test_finished_task_rests_with_same_id(self):
        self.turn(status='completed', end=self.now - 1)
        agent = bridge.codex_agents(self.home, self.now)[0]
        self.assertEqual(agent['status'], 'idle')
        self.assertEqual(agent['id'], 'codex-main')
        self.assertEqual(agent['tool'], '')

    def test_new_turn_overrides_previous_completion(self):
        self.turn(status='completed', age=100, end=self.now - 90)
        self.turn(turn='turn2', age=5)
        agents = bridge.codex_agents(self.home, self.now)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]['status'], 'working')

    def test_parallel_tasks_are_distinct(self):
        self.turn()
        self.turn(chat='chat2')
        with closing(sqlite3.connect(self.home / 'state_5.sqlite', isolation_level=None)) as db:
            db.execute('INSERT INTO threads VALUES (?, ?, 0, ?, ?, ?, ?)',
                       ('chat2', r'C:\Work\My project', 'Turing', '/root/ui', 'subagent', 'Проверка интерфейса'))
            db.execute('INSERT INTO thread_spawn_edges VALUES (?, ?, ?)', ('chat1', 'chat2', 'open'))
        agents = bridge.codex_agents(self.home, self.now)
        self.assertEqual(len({a['id'] for a in agents}), 2)
        helper = next(a for a in agents if a['kind'] == 'codex-agent')
        self.assertEqual(helper['label'], 'TURING')
        self.assertEqual(helper['status'], 'working')

    def test_closed_app_removes_agents(self):
        self.turn()
        self.assertEqual(bridge.codex_agents(self.home, self.now, app_open=False), [])

    def test_stale_active_task_does_not_work_forever(self):
        self.turn(age=bridge.STALE_AFTER + 10)
        self.assertEqual(bridge.codex_agents(self.home, self.now)[0]['id'], 'codex-main')
        self.assertEqual(bridge.codex_agents(self.home, self.now)[0]['status'], 'idle')

    def test_expired_finished_task_leaves(self):
        self.turn(status='completed', age=1000, end=self.now - bridge.IDLE_KEEP - 1)
        self.assertEqual(bridge.codex_agents(self.home, self.now)[0]['id'], 'codex-main')

    def test_waiting_status(self):
        self.turn(status='waitingForInput')
        self.assertEqual(bridge.codex_agents(self.home, self.now)[0]['status'], 'waiting')

    def test_reasoning_shows_last_tool_without_private_reasoning(self):
        self.turn()
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite', isolation_level=None)) as db:
            db.execute('INSERT INTO thread_items VALUES (?, ?, ?, ?, ?, ?, ?)',
                       ('chat1', 'turn1', 'reasoning', self.now * 1000, None, 2,
                        json.dumps({'summary': 'PRIVATE REASONING', 'content': 'PRIVATE CONTENT'})))
        agent = bridge.codex_agents(self.home, self.now)[0]
        self.assertEqual(agent['activity_title'], 'Обдумывает задачу')
        self.assertIn('app.py', agent['activity_detail'])
        self.assertNotIn('PRIVATE', json.dumps(agent))

    def test_public_progress_and_command_details(self):
        self.turn()
        with closing(sqlite3.connect(self.home / 'thread_history_1.sqlite', isolation_level=None)) as db:
            db.execute('INSERT INTO thread_items VALUES (?, ?, ?, ?, ?, ?, ?)',
                       ('chat1', 'turn1', 'agentMessage', self.now * 1000, None, 2,
                        json.dumps({'phase': 'commentary', 'text': 'Проверяю интерфейс'})))
            db.execute('INSERT INTO thread_items VALUES (?, ?, ?, ?, ?, ?, ?)',
                       ('chat1', 'turn1', 'commandExecution', self.now * 1000, None, 3,
                        json.dumps({'command': 'python -m unittest', 'aggregatedOutput': 'PRIVATE OUTPUT'})))
        agent = bridge.codex_agents(self.home, self.now)[0]
        self.assertEqual(agent['activity_title'], 'Запускает проверки')
        self.assertEqual(agent['activity_detail'], 'python -m unittest')
        self.assertEqual(agent['progress'], 'Проверяю интерфейс')
        self.assertNotIn('PRIVATE OUTPUT', json.dumps(agent))

    def test_http_serves_real_state_and_wallpaper(self):
        monitor = bridge.Monitor(self.home)
        monitor.snapshot['agents'] = [bridge.idle_agent(), bridge.chatgpt_agent()]
        server = bridge.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        server.monitor = monitor
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/state', timeout=2) as response:
                data = json.load(response)
                self.assertEqual([a['kind'] for a in data['agents']], ['codex', 'chatgpt'])
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
            with urlopen(base + '/', timeout=2) as response:
                self.assertIn('<html lang="ru">', response.read().decode('utf-8'))
            with urlopen(base + '/fonts/tiny5-cyr.woff2', timeout=2) as response:
                self.assertGreater(len(response.read()), 100)
            with urlopen(base + '/office-panel.js', timeout=2) as response:
                self.assertIn('function panel', response.read().decode('utf-8'))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
