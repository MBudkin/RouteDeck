"""
Сессия анализа программ: какие процессы отслеживаем, куда они подключаются,
какие домены запрашивают.

Обычный уровень работает прямо здесь: раз в 0,25 с опрашивается таблица
TCP-соединений Windows, раз в секунду - список процессов, раз в 2 с - кэш DNS.
Полный уровень - отдельный процесс-сборщик с правами администратора (agent.py).
Он запускается один раз (одно окно UAC) и живёт, пока открыта RouteDeck:
между анализами трассировка выключена, сборщик просто ждёт команды.
"""

import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set

import psutil

from . import winapi
from .netutil import is_public, normalize_ip, valid_domain
from .store import CaptureStore

logger = logging.getLogger(__name__)

POLL_CONNECTIONS = 0.25
POLL_PROCESSES = 1.0
POLL_DNS_CACHE = 2.0
SAVE_EVERY = 15.0
AGENT_TIMEOUT = 30.0     # сборщик должен выйти на связь после окна UAC
AGENT_LOST = 8.0         # нет вестей дольше - связь потеряна
GONE_KEEP = 30.0         # завершённый процесс помним, чтобы опоздавшие события не терялись
MAX_PORTS = 24


def now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def app_folder(exe: str) -> str:
    """Папка программы. У Squirrel-установщиков (Discord, Slack…) exe лежит
    в app-1.0.9xxx, которая меняется при обновлении, - берём папку выше."""
    folder = os.path.dirname(exe)
    if re.match(r'^app-\d', os.path.basename(folder), re.I):
        folder = os.path.dirname(folder)
    return folder


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ''


def _windir() -> str:
    return _norm(os.environ.get('SystemRoot') or r'C:\Windows')


def shared_folder(folder: str) -> bool:
    """Папка, где лежат exe разных программ: по ней процессы программы не определить."""
    if not folder:
        return True
    path = _norm(folder)
    if path == _windir() or path.startswith(_windir() + os.sep):
        return True
    home = os.path.expanduser('~')
    roots = [os.environ.get(v) for v in ('ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432', 'ProgramData',
                                         'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'TEMP')]
    roots += [os.path.join(home, d) for d in ('Desktop', 'Downloads', 'Documents')]
    roots += [os.path.join(os.environ.get('LOCALAPPDATA') or '', 'Programs')]
    return path in {_norm(r) for r in roots if r} or os.path.dirname(path) == path   # корень диска


@dataclass
class TargetApp:
    """Одна из анализируемых программ: по папке установки и/или по процессам."""
    name: str
    folder: str = ''
    exe: str = ''                                         # exe, запущенный из RouteDeck
    roots: Dict[int, int] = field(default_factory=dict)  # pid -> время запуска

    def to_dict(self) -> Dict:
        return {'name': self.name, 'folder': self.folder, 'exe': self.exe, 'pids': sorted(self.roots)}


@dataclass
class Target:
    name: str
    apps: List[TargetApp] = field(default_factory=list)
    children: bool = True

    def to_dict(self) -> Dict:
        return {'name': self.name, 'apps': [a.to_dict() for a in self.apps], 'children': self.children}


class CaptureSession:
    def __init__(self, target: Target, store: CaptureStore, full: bool):
        self.target = target
        self.store = store
        self.full = full                      # нужен ли полный режим (сборщик)
        self.lock = threading.RLock()
        self.started = time.time()
        self.started_iso = now_iso()
        self.ended: Optional[float] = None
        self.stop_event = threading.Event()
        self.folders = sorted([(_norm(a.folder), a.name) for a in target.apps if a.folder],
                              key=lambda item: len(item[0]), reverse=True)

        self.table = winapi.ProcessTable()
        self.procs: Dict[int, winapi.ProcInfo] = {}
        self.gone_at: Dict[int, float] = {}
        self.match_cache: Dict[int, str] = {}  # pid -> программа ('' - чужой процесс)
        self.matched: Dict[int, Dict] = {}     # процессы программ (для экрана)

        self.ips: Dict[str, Dict] = {}
        self.queried: Dict[str, Dict] = {}     # домены, которые запросили сами программы
        self.ipnames: Dict[str, Set[str]] = {} # IP -> домены (кэш DNS и все DNS-события)
        self.name_checked: Dict[str, float] = {}
        self.seen_conns: Set = set()
        self.local_ips = self._local_ips()
        self.agent_used = False                # данные полного режима действительно приходили

        self.session_id = store.create_session(target.name, target.to_dict(), 'basic', self.started_iso)
        self.dirty = False
        self.threads = [threading.Thread(target=self._poll_loop, name='capture-poll', daemon=True),
                        threading.Thread(target=self._dns_loop, name='capture-dns', daemon=True)]
        for t in self.threads:
            t.start()

    @staticmethod
    def _local_ips() -> Set[str]:
        ips = set()
        try:
            for addrs in psutil.net_if_addrs().values():
                for a in addrs:
                    ips.add(a.address.split('%')[0])
        except Exception:
            pass
        return ips

    # ------------------------------------------------------------ процессы

    def _merge_proc(self, info: winapi.ProcInfo) -> None:
        old = self.procs.get(info.pid)
        if old and old.name.lower() == info.name.lower() and (not info.created or not old.created or old.created == info.created):
            if info.exe and not old.exe:
                old.exe = info.exe
            if info.created and not old.created:
                old.created = info.created
            return
        self.procs[info.pid] = info
        self.match_cache.clear()

    def _refresh_processes(self) -> None:
        snapshot = self.table.refresh()
        now = time.time()
        with self.lock:
            for info in snapshot.values():
                self._merge_proc(winapi.ProcInfo(info.pid, info.ppid, info.name, info.exe, info.created))
                self.gone_at.pop(info.pid, None)
            for pid in list(self.procs):
                if pid not in snapshot:
                    since = self.gone_at.setdefault(pid, now)
                    if now - since > GONE_KEEP:
                        self.procs.pop(pid, None)
                        self.gone_at.pop(pid, None)
                        self.match_cache.clear()
            self._update_matched()

    def _match(self, pid: int, depth: int = 0) -> str:
        """Какой из анализируемых программ принадлежит процесс ('' - никакой)."""
        cached = self.match_cache.get(pid)
        if cached is not None:
            return cached
        p = self.procs.get(pid)
        result = ''
        for app in self.target.apps:
            if pid in app.roots:
                created = app.roots[pid]
                if p is None or not created or not p.created or p.created == created:
                    result = app.name
                    break
        if not result and p is not None:
            exe = _norm(p.exe) if p.exe else ''
            for folder, name in self.folders:
                if exe and exe.startswith(folder + os.sep):
                    result = name
                    break
            if not result and self.target.children and p.ppid and depth < 32 and p.ppid != pid:
                parent = self.procs.get(p.ppid)
                # Родитель должен быть старше потомка - иначе это чужой процесс с тем же PID.
                if parent and (not p.created or not parent.created or parent.created <= p.created):
                    result = self._match(p.ppid, depth + 1)
        self.match_cache[pid] = result
        return result

    def _update_matched(self) -> None:
        now = now_iso()
        alive = set()
        for pid, p in self.procs.items():
            if pid in self.gone_at:
                continue
            app = self._match(pid)
            if not app:
                continue
            alive.add(pid)
            m = self.matched.get(pid)
            if m is None or m['name'] != p.name:
                self.matched[pid] = {'pid': pid, 'name': p.name, 'exe': p.exe, 'app': app,
                                     'first': now, 'hits': 0, 'alive': True}
            else:
                m['alive'] = True
        for pid, m in self.matched.items():
            if pid not in alive:
                m['alive'] = False

    # ------------------------------------------------------------ сбор

    def _hit(self, pid: int, proto: str, ip: str, port: int, count: int = 1) -> None:
        ip = normalize_ip(ip)
        if ip in self.local_ips or not is_public(ip):
            return
        now = now_iso()
        item = self.ips.get(ip)
        if item is None:
            item = self.ips[ip] = {'protos': set(), 'ports': set(), 'procs': set(), 'apps': set(),
                                   'count': 0, 'first': now, 'last': now}
        item['protos'].add(proto)
        if port and len(item['ports']) < MAX_PORTS:
            item['ports'].add(int(port))
        p = self.procs.get(pid)
        if p:
            item['procs'].add(p.name)
        item['apps'].add(self._match(pid))
        item['count'] += count
        item['last'] = now
        m = self.matched.get(pid)
        if m:
            m['hits'] += count
        self.dirty = True

    def _poll_loop(self) -> None:
        next_procs = 0.0
        next_save = time.time() + SAVE_EVERY
        while not self.stop_event.is_set():
            now = time.time()
            try:
                if now >= next_procs:
                    self._refresh_processes()
                    next_procs = now + POLL_PROCESSES
                self._poll_connections()
                if now >= next_save:
                    self.save()
                    next_save = now + SAVE_EVERY
            except Exception:
                logger.exception('Ошибка опроса соединений')
            self.stop_event.wait(POLL_CONNECTIONS)

    def _poll_connections(self) -> None:
        try:
            conns = psutil.net_connections('tcp')
        except Exception as e:
            logger.warning(f'Таблица соединений недоступна: {e}')
            return
        conns = [c for c in conns if c.raddr and c.pid and c.status != psutil.CONN_LISTEN]
        # Новый процесс (например, короткоживущий дочерний) - узнаём о нём сразу, не ждём секунду.
        if any(c.pid not in self.procs for c in conns):
            self._refresh_processes()
        with self.lock:
            for c in conns:
                key = (c.pid, c.laddr.port, c.raddr.ip, c.raddr.port)
                if key in self.seen_conns or c.pid not in self.procs:
                    continue
                if not self._match(c.pid):
                    continue
                self.seen_conns.add(key)
                self._hit(c.pid, 'tcp', c.raddr.ip, c.raddr.port)

    def _dns_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._read_dns_cache()
            except Exception:
                logger.exception('Ошибка чтения кэша DNS')
            self.stop_event.wait(POLL_DNS_CACHE)

    def _read_dns_cache(self) -> None:
        now = time.time()
        # Имя перечитываем не чаще раза в 20 с: адреса меняются нечасто.
        names = {(n, t) for n, t in winapi.dns_cache_names() if now - self.name_checked.get(f'{n}/{t}', 0) > 20}
        if not names:
            return
        found = winapi.dns_cache(names)
        with self.lock:
            for n, t in names:
                self.name_checked[f'{n}/{t}'] = now
            for name, ips in found.items():
                self._learn(name, ips)

    def _learn(self, name: str, ips) -> None:
        for ip in ips:
            ip = normalize_ip(ip)
            if is_public(ip):
                self.ipnames.setdefault(ip, set()).add(name)

    def ingest(self, batch: Dict) -> None:
        """Данные от сборщика с правами администратора."""
        with self.lock:
            if batch.get('capturing') or batch.get('paused'):
                self.agent_used = True
            for p in batch.get('procs') or []:
                self._merge_proc(winapi.ProcInfo(int(p['pid']), int(p.get('ppid') or 0), p.get('name') or '',
                                                 p.get('exe') or '', int(p.get('created') or 0)))
            for pid in batch.get('gone') or []:
                self.gone_at.setdefault(int(pid), time.time())
            if batch.get('procs') or batch.get('gone'):
                self._update_matched()

            for pid, name, ips in batch.get('dns') or []:
                if not valid_domain(name):
                    continue
                self._learn(name, ips)
                app = self._match(int(pid)) if pid else ''
                if app:
                    q = self.queried.get(name)
                    if q is None:
                        q = self.queried[name] = {'ips': set(), 'apps': set(), 'count': 0, 'first': now_iso()}
                    q['ips'].update(normalize_ip(i) for i in ips if is_public(i))
                    q['apps'].add(app)
                    q['count'] += 1
                    q['last'] = now_iso()
                    self.dirty = True

            for pid, proto, ip, port, count in batch.get('net') or []:
                pid = int(pid)
                if pid in self.procs and self._match(pid):
                    self._hit(pid, proto, ip, port, int(count))

    # ------------------------------------------------------------ результаты

    def results(self) -> Dict:
        with self.lock:
            domains: Dict[str, Dict] = {}
            for name, q in self.queried.items():
                domains[name] = {'domain': name, 'ips': set(q['ips']), 'apps': set(q['apps']), 'queried': True,
                                 'count': q['count'], 'first': q['first'], 'last': q.get('last', q['first'])}
            ips = []
            for ip, item in self.ips.items():
                names = set(self.ipnames.get(ip, ()))
                names |= {n for n, q in self.queried.items() if ip in q['ips']}
                for n in names:
                    d = domains.get(n)
                    if d is None:
                        d = domains[n] = {'domain': n, 'ips': set(), 'apps': set(), 'queried': False, 'count': 0,
                                          'first': item['first'], 'last': item['last']}
                    d['ips'].add(ip)
                    d['apps'].update(item['apps'])
                    d['count'] += item['count']
                    d['last'] = max(d['last'], item['last'])
                ips.append({'ip': ip, 'protos': sorted(item['protos']), 'ports': sorted(item['ports']),
                            'procs': sorted(item['procs']), 'apps': sorted(a for a in item['apps'] if a),
                            'domains': sorted(names), 'count': item['count'],
                            'first': item['first'], 'last': item['last']})
            for d in domains.values():
                d['ips'] = sorted(d['ips'])
                d['apps'] = sorted(a for a in d['apps'] if a)
            return {'ips': ips, 'domains': list(domains.values())}

    @property
    def running(self) -> bool:
        return not self.stop_event.is_set()

    @property
    def mode(self) -> str:
        return 'full' if self.agent_used else 'basic'

    def state(self) -> Dict:
        with self.lock:
            processes = sorted(self.matched.values(), key=lambda m: (not m['alive'], -m['hits'], m['name'].lower()))
        return {
            'id': self.session_id,
            'running': self.running,
            'target': self.target.to_dict(),
            'started': self.started_iso,
            'elapsed': round((self.ended or time.time()) - self.started),
            'mode': self.mode,
            'full': self.full,
            'processes': processes[:300],
            'results': self.results(),
        }

    def save(self) -> None:
        if not self.dirty and self.running:
            return
        self.dirty = False
        try:
            self.store.save_results(self.session_id, self.results(), self.mode,
                                    None if self.running else now_iso())
        except Exception:
            logger.exception('Не удалось сохранить результаты анализа')

    def finish(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.ended = time.time()
        for t in self.threads:
            t.join(timeout=3)
        self.dirty = True
        self.save()


class AgentLink:
    """
    Сборщик с правами администратора. Запускается один раз (окно UAC) и
    работает, пока открыта RouteDeck. Каждые 0,5-1 с присылает данные и
    получает команду: собирать (capture) или ждать, либо завершиться (stop).
    """

    def __init__(self, launcher: Dict):
        self.launcher = launcher
        self.lock = threading.Lock()
        self.status = 'off'      # off|pending|starting|ready|capturing|denied|error
        self.error = ''
        self.token = ''
        self.contact = 0.0
        self.started = 0.0
        self.stats: Dict = {}
        self.session: Optional[CaptureSession] = None   # куда отдавать данные
        self.want = False                               # нужна ли сейчас трассировка
        self.exit = False
        self.paused = threading.Event()                 # сборщик выключил трассировку и отдал остаток

    @property
    def alive(self) -> bool:
        return self.status in ('pending', 'starting', 'ready', 'capturing')

    def ensure(self, base_url: str) -> None:
        """Запускает сборщик, если он ещё не работает."""
        self.snapshot()  # обнаружить потерю связи до решения о повторном запуске
        with self.lock:
            if self.alive:
                return
            self.token = secrets.token_urlsafe(24)
            self.status = 'pending'
            self.error = ''
            self.exit = False
            self.started = time.time()
            self.contact = 0.0
            self.stats = {}
        target = self._run_inprocess if winapi.is_admin() else self._elevate
        threading.Thread(target=target, args=(base_url,), daemon=True).start()

    def _elevate(self, base_url: str) -> None:
        hwnd = None
        try:
            exe, script = self.launcher['command']()
            # localhost может сначала резолвиться в ::1, а сервер слушает только 127.0.0.1.
            base_url = base_url.replace('//localhost', '//127.0.0.1')
            log = os.path.join(self.launcher['data_dir'], 'logs', 'capture-agent.log')
            params = (script + ' ' + subprocess.list2cmdline([
                '--capture-agent', '--url', base_url, '--token', self.token,
                '--parent', str(os.getpid()), '--log', log])).strip()
            hwnd = winapi.find_window(self.launcher['window'])
            winapi.run_elevated(exe, params, self.launcher['cwd'], hwnd)
        except winapi.ElevationCancelled:
            with self.lock:
                self.status = 'denied'
                self.error = 'Запрос прав администратора отклонён — работает обычный режим'
            return
        except Exception as e:
            with self.lock:
                self.status = 'error'
                self.error = f'Не удалось запустить сборщик: {e}'
            return
        finally:
            # После окна UAC Windows может оставить RouteDeck позади других окон.
            winapi.bring_to_front(hwnd or winapi.find_window(self.launcher['window']))
        with self.lock:
            if self.status == 'pending':
                self.status = 'starting'
                self.started = time.time()

    def _run_inprocess(self, _base_url: str) -> None:
        # RouteDeck уже запущен от администратора - собираем в этом же процессе.
        try:
            from .agent import serve
            serve(self.handle, lambda: self.exit)
        except Exception as e:
            logger.exception('Сборщик завершился с ошибкой')
            with self.lock:
                self.status = 'error'
                self.error = f'Полный режим недоступен: {e}'

    def check_token(self, token: str) -> bool:
        return bool(self.token) and secrets.compare_digest(token or '', self.token)

    def handle(self, batch: Dict) -> Dict:
        """Данные от сборщика -> текущей сессии; ответ - что делать дальше."""
        with self.lock:
            self.contact = time.time()
            if batch.get('error'):
                self.status = 'error'
                self.error = f"Полный режим недоступен: {batch['error']}"
                return {'stop': True}
            capturing = bool(batch.get('capturing'))
            self.status = 'capturing' if capturing else 'ready'
            self.error = ''
            if batch.get('stats'):
                self.stats = batch['stats']
            session = self.session
        if session is not None:
            session.ingest(batch)
        if batch.get('paused') or not capturing:
            self.paused.set()
        with self.lock:
            want = self.want and self.session is not None and self.session.running
            return {'stop': self.exit, 'capture': want}

    def snapshot(self) -> Dict:
        with self.lock:
            now = time.time()
            if self.status == 'starting' and now - self.started > AGENT_TIMEOUT:
                self.status = 'error'
                self.error = 'Сборщик не вышел на связь — подробности в logs\\capture-agent.log'
            elif self.status in ('ready', 'capturing') and now - self.contact > AGENT_LOST:
                self.status = 'error'
                self.error = 'Связь со сборщиком потеряна'
            return {'status': self.status, 'error': self.error, 'stats': self.stats}


class CaptureManager:
    """Одна активная сессия анализа и общий для всех сессий сборщик."""

    def __init__(self, store: CaptureStore, launcher: Dict):
        self.store = store
        self.agent = AgentLink(launcher)
        self.session: Optional[CaptureSession] = None
        self.lock = threading.Lock()

    @staticmethod
    def _parse_apps(spec: Dict) -> List[TargetApp]:
        apps = []
        items = spec.get('apps') or []
        # Совместимость с интерфейсом предыдущей версии.
        if not items and not spec.get('exe') and (spec.get('folder') or spec.get('pids')):
            items = [spec]
        for item in items:
            folder = (item.get('folder') or '').strip().strip('"')
            pids = [int(p) for p in item.get('pids') or []]
            name = (item.get('name') or '').strip() or os.path.basename(folder) or 'Программа'
            if folder and not os.path.isdir(folder):
                raise ValueError(f'Папка не найдена: {folder}')
            if shared_folder(folder):
                folder = ''
            roots = {pid: winapi.process_details(pid)[1] for pid in pids}
            if folder or roots:
                apps.append(TargetApp(name=name, folder=folder, roots=roots))
        exe = (spec.get('exe') or '').strip().strip('"')
        if exe:
            if not exe.lower().endswith('.exe'):
                raise ValueError('Укажите путь к .exe-файлу программы')
            if not os.path.isfile(exe):
                raise ValueError(f'Файл не найден: {exe}')
            folder = app_folder(exe)
            apps.append(TargetApp(name=(spec.get('name') or '').strip() or winapi.file_description(exe), exe=exe,
                                  folder='' if shared_folder(folder) else folder))
        if not apps:
            raise ValueError('Выберите программу')
        names = [a.name.casefold() for a in apps]
        if len(set(names)) != len(names):
            raise ValueError('У каждой программы должно быть отдельное название')
        return apps

    def start(self, spec: Dict, base_url: str) -> CaptureSession:
        apps = self._parse_apps(spec)
        name = (spec.get('name') or '').strip() or ' + '.join(dict.fromkeys(a.name for a in apps))
        full = bool(spec.get('full'))
        self.stop()
        with self.lock:
            target = Target(name=name, apps=apps, children=bool(spec.get('children', True)))
            session = CaptureSession(target, self.store, full)
            self.session = session

        for app in apps:
            if not app.exe:
                continue
            try:
                proc = subprocess.Popen([app.exe], cwd=os.path.dirname(app.exe), close_fds=True,
                                        creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
                with session.lock:
                    app.roots[proc.pid] = winapi.process_details(proc.pid)[1]
                    session.match_cache.clear()
            except OSError as e:
                self.stop()
                raise ValueError(f'Не удалось запустить программу: {e}')
        if full:
            self.enable_full(base_url)
        return session

    def enable_full(self, base_url: str) -> None:
        session = self.session
        if not session or not session.running:
            raise ValueError('Анализ не запущен')
        session.full = True
        with self.agent.lock:
            self.agent.session = session
            self.agent.want = True
        self.agent.ensure(base_url)

    def stop(self) -> Optional[CaptureSession]:
        with self.lock:
            session = self.session
        if not session or not session.running:
            return session
        agent = self.agent
        with agent.lock:
            capturing = agent.session is session and agent.status == 'capturing'
            agent.want = False
            agent.paused.clear()
        if capturing:
            # Сборщик выключит трассировку и пришлёт остаток данных.
            agent.paused.wait(timeout=6)
        session.finish()
        with agent.lock:
            if agent.session is session:
                agent.session = None
        return session

    def state(self) -> Optional[Dict]:
        session = self.session
        if not session:
            return None
        state = session.state()
        state['agent'] = self.agent.snapshot()
        return state

    def shutdown(self) -> None:
        try:
            self.stop()
        except Exception:
            logger.exception('Ошибка остановки анализа')
        self.agent.exit = True


def list_apps(include_system: bool = False) -> List[Dict]:
    """Запущенные программы, сгруппированные по папке установки."""
    procs = winapi.ProcessTable().refresh()
    windir = _windir()
    own = {os.getpid(), os.getppid()}
    conns: Dict[int, int] = {}
    try:
        for c in psutil.net_connections('tcp'):
            if c.raddr and c.pid and is_public(c.raddr.ip):
                conns[c.pid] = conns.get(c.pid, 0) + 1
    except Exception:
        pass

    groups: Dict[str, Dict] = {}
    for p in procs.values():
        if not p.exe or p.pid in own:
            continue
        if not include_system and _norm(p.exe).startswith(windir + os.sep):
            continue
        folder = app_folder(p.exe)
        # Программы из общих папок (System32, корень Program Files…) - отдельно по exe.
        key = _norm(p.exe) if shared_folder(folder) else _norm(folder)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {'key': key, 'folder': folder, 'pids': [], 'exes': {}, 'connections': 0, 'root': None}
        g['pids'].append(p.pid)
        g['exes'][p.exe] = g['exes'].get(p.exe, 0) + 1
        g['connections'] += conns.get(p.pid, 0)
        parent = procs.get(p.ppid)
        parent_key = parent and parent.exe and (_norm(parent.exe) if shared_folder(app_folder(parent.exe))
                                                else _norm(app_folder(parent.exe)))
        if g['root'] is None and parent_key != key:
            g['root'] = p.exe

    apps = []
    for g in groups.values():
        main_exe = g['root'] or max(g['exes'], key=g['exes'].get)
        apps.append({
            'key': g['key'],
            'title': winapi.file_description(main_exe),
            'exe': main_exe,
            'folder': g['folder'],
            'processes': len(g['pids']),
            'pids': g['pids'],
            'exe_names': sorted({os.path.basename(e) for e in g['exes']}),
            'connections': g['connections'],
            'system': _norm(main_exe).startswith(windir + os.sep),
        })
    apps.sort(key=lambda a: (-min(a['connections'], 1), a['title'].lower()))
    return apps


def agent_command_factory(frozen: bool, resource_dir: str):
    """Чем запускать сборщик: сам RouteDeck.exe или pythonw desktop.py."""
    def command():
        if frozen:
            return sys.executable, ''
        exe = sys.executable
        pythonw = os.path.join(os.path.dirname(exe), 'pythonw.exe')
        if os.path.exists(pythonw):
            exe = pythonw
        return exe, f'"{os.path.join(resource_dir, "desktop.py")}"'
    return command
