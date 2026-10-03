"""Сохранение результатов анализа программ в sites.db."""

import json
import sqlite3
from contextlib import contextmanager
from typing import Dict, List


class CaptureStore:
    def __init__(self, db_path: str = 'sites.db'):
        self.db_path = db_path
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS capture_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    target TEXT,
                    mode TEXT,
                    started_at TEXT,
                    ended_at TEXT
                );
                CREATE TABLE IF NOT EXISTS capture_ips (
                    session_id INTEGER NOT NULL,
                    ip TEXT NOT NULL,
                    protos TEXT, ports TEXT, procs TEXT, domains TEXT,
                    count INTEGER, first_seen TEXT, last_seen TEXT,
                    PRIMARY KEY (session_id, ip)
                );
                CREATE TABLE IF NOT EXISTS capture_domains (
                    session_id INTEGER NOT NULL,
                    domain TEXT NOT NULL,
                    ips TEXT, queried INTEGER, count INTEGER, first_seen TEXT, last_seen TEXT,
                    PRIMARY KEY (session_id, domain)
                );
                CREATE TABLE IF NOT EXISTS capture_session_apps (
                    session_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    PRIMARY KEY (session_id, name)
                );
            """)
            # Столбец «программа» появился, когда стало можно анализировать несколько программ сразу.
            for table in ('capture_ips', 'capture_domains'):
                columns = {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
                if 'apps' not in columns:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN apps TEXT')
            # Старые одиночные и уже начатые совместные анализы сохраняем без потери данных.
            for row in c.execute('SELECT id, name, target FROM capture_sessions').fetchall():
                target = json.loads(row['target'] or '{}')
                names = self._names(target, row['name'])
                c.executemany('INSERT OR IGNORE INTO capture_session_apps VALUES (?, ?)',
                              [(row['id'], name) for name in names])

    @staticmethod
    def _names(target: Dict, fallback: str) -> List[str]:
        return list(dict.fromkeys(a['name'] for a in target.get('apps', []) if a.get('name'))) or [fallback]

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_session(self, name: str, target: Dict, mode: str, started_at: str) -> int:
        with self._conn() as c:
            cur = c.execute('INSERT INTO capture_sessions (name, target, mode, started_at) VALUES (?, ?, ?, ?)',
                            (name, json.dumps(target, ensure_ascii=False), mode, started_at))
            c.executemany('INSERT INTO capture_session_apps VALUES (?, ?)',
                          [(cur.lastrowid, app) for app in self._names(target, name)])
            return cur.lastrowid

    def save_results(self, session_id: int, results: Dict, mode: str, ended_at: str = None) -> None:
        """Перезаписывает результаты сессии (вызывается периодически и при остановке)."""
        with self._conn() as c:
            c.execute('UPDATE capture_sessions SET mode = ?, ended_at = COALESCE(?, ended_at) WHERE id = ?',
                      (mode, ended_at, session_id))
            c.execute('DELETE FROM capture_ips WHERE session_id = ?', (session_id,))
            c.execute('DELETE FROM capture_domains WHERE session_id = ?', (session_id,))
            c.executemany(
                'INSERT INTO capture_ips (session_id, ip, protos, ports, procs, domains, count, first_seen, last_seen, apps) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [(session_id, i['ip'], json.dumps(i['protos']), json.dumps(i['ports']), json.dumps(i['procs']),
                  json.dumps(i['domains']), i['count'], i['first'], i['last'], json.dumps(i.get('apps', [])))
                 for i in results['ips']])
            c.executemany(
                'INSERT INTO capture_domains (session_id, domain, ips, queried, count, first_seen, last_seen, apps) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                [(session_id, d['domain'], json.dumps(d['ips']), int(d['queried']), d['count'],
                  d['first'], d['last'], json.dumps(d.get('apps', []))) for d in results['domains']])

    def programs(self) -> List[Dict]:
        """История, сгруппированная по программе."""
        with self._conn() as c:
            sessions = c.execute('SELECT a.name, s.id, s.mode, s.started_at FROM capture_session_apps a '
                                 'JOIN capture_sessions s ON s.id = a.session_id').fetchall()
            by_session = {}
            programs = {}
            for s in sessions:
                by_session.setdefault(s['id'], set()).add(s['name'])
                p = programs.setdefault(s['name'], {'name': s['name'], 'sessions': 0, 'last': '',
                                                     'ips': set(), 'domains': set(), 'full': False})
                p['sessions'] += 1
                p['last'] = max(p['last'], s['started_at'] or '')
                p['full'] = p['full'] or s['mode'] == 'full'
            for table, field, count in [('capture_ips', 'ip', 'ips'), ('capture_domains', 'domain', 'domains')]:
                for r in c.execute(f'SELECT session_id, {field}, apps FROM {table}'):
                    owners = by_session.get(r['session_id'], set())
                    tags = set(json.loads(r['apps'] or '[]'))
                    for name in (owners & tags if tags else owners):
                        programs[name][count].add(r[field])
            for p in programs.values():
                p['ips'], p['domains'] = len(p['ips']), len(p['domains'])
            return sorted(programs.values(), key=lambda p: (p['last'], p['name']), reverse=True)

    def program_results(self, name: str) -> Dict:
        """Объединённые результаты всех сессий программы."""
        with self._conn() as c:
            sessions = [dict(r) for r in c.execute(
                'SELECT s.id, s.mode, s.started_at, s.ended_at, s.target FROM capture_sessions s '
                'JOIN capture_session_apps a ON a.session_id = s.id WHERE a.name = ? ORDER BY s.id', (name,))]
            ids = [s['id'] for s in sessions]
            if not ids:
                return {'sessions': [], 'ips': [], 'domains': []}
            marks = ','.join('?' * len(ids))
            ip_rows = c.execute(f'SELECT * FROM capture_ips WHERE session_id IN ({marks})', ids).fetchall()
            dom_rows = c.execute(f'SELECT * FROM capture_domains WHERE session_id IN ({marks})', ids).fetchall()

        ips: Dict[str, Dict] = {}
        for r in ip_rows:
            apps = json.loads(r['apps'] or '[]')
            if apps and name not in apps:
                continue
            item = ips.setdefault(r['ip'], {'ip': r['ip'], 'protos': set(), 'ports': set(), 'procs': set(), 'apps': set(),
                                            'domains': set(), 'count': 0, 'first': r['first_seen'], 'last': r['last_seen']})
            item['apps'].add(name)
            item['protos'].update(json.loads(r['protos'] or '[]'))
            item['ports'].update(json.loads(r['ports'] or '[]'))
            item['procs'].update(json.loads(r['procs'] or '[]'))
            item['domains'].update(json.loads(r['domains'] or '[]'))
            item['count'] += r['count'] or 0
            item['first'] = min(item['first'] or '', r['first_seen'] or '') or item['first']
            item['last'] = max(item['last'] or '', r['last_seen'] or '')
        domains: Dict[str, Dict] = {}
        for r in dom_rows:
            apps = json.loads(r['apps'] or '[]')
            if apps and name not in apps:
                continue
            item = domains.setdefault(r['domain'], {'domain': r['domain'], 'ips': set(), 'apps': set(), 'queried': False,
                                                    'count': 0, 'first': r['first_seen'], 'last': r['last_seen']})
            item['apps'].add(name)
            item['ips'].update(json.loads(r['ips'] or '[]'))
            item['queried'] = item['queried'] or bool(r['queried'])
            item['count'] += r['count'] or 0
            item['last'] = max(item['last'] or '', r['last_seen'] or '')

        def finish(item):
            return {k: sorted(v) if isinstance(v, set) else v for k, v in item.items()}

        for s in sessions:
            s['target'] = json.loads(s['target'] or '{}')
        return {'sessions': sessions,
                'ips': [finish(i) for i in ips.values()],
                'domains': [finish(d) for d in domains.values()]}

    def delete_program(self, name: str) -> int:
        with self._conn() as c:
            ids = [r[0] for r in c.execute('SELECT session_id FROM capture_session_apps WHERE name = ?', (name,))]
            for session_id in ids:
                c.execute('DELETE FROM capture_session_apps WHERE session_id = ? AND name = ?', (session_id, name))
                remaining = [r[0] for r in c.execute('SELECT name FROM capture_session_apps WHERE session_id = ?', (session_id,))]
                for table, key in [('capture_ips', 'ip'), ('capture_domains', 'domain')]:
                    if not remaining:
                        c.execute(f'DELETE FROM {table} WHERE session_id = ?', (session_id,))
                        continue
                    for r in c.execute(f'SELECT {key}, apps FROM {table} WHERE session_id = ?', (session_id,)).fetchall():
                        apps = json.loads(r['apps'] or '[]')
                        if name not in apps:
                            continue
                        apps = [app for app in apps if app != name]
                        if apps:
                            c.execute(f'UPDATE {table} SET apps = ? WHERE session_id = ? AND {key} = ?',
                                      (json.dumps(apps), session_id, r[key]))
                        else:
                            c.execute(f'DELETE FROM {table} WHERE session_id = ? AND {key} = ?', (session_id, r[key]))
                if not remaining:
                    c.execute('DELETE FROM capture_sessions WHERE id = ?', (session_id,))
                else:
                    target = json.loads(c.execute('SELECT target FROM capture_sessions WHERE id = ?', (session_id,)).fetchone()[0] or '{}')
                    if 'apps' in target:
                        target['apps'] = [a for a in target['apps'] if a.get('name') != name]
                    target['name'] = ' + '.join(remaining)
                    c.execute('UPDATE capture_sessions SET name = ?, target = ? WHERE id = ?',
                              (target['name'], json.dumps(target, ensure_ascii=False), session_id))
            return len(ids)
