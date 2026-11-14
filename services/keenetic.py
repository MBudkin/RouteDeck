"""
Keenetic RCI client - управление роутером Keenetic через его HTTP API (RCI).

RCI - это JSON-зеркало командной строки KeeneticOS, которым пользуется
штатный веб-интерфейс. Авторизация - challenge-схема:

    GET  /auth  -> 401 + заголовки X-NDM-Realm, X-NDM-Challenge и cookie сессии
    POST /auth  {"login": ..., "password": sha256(challenge + md5(login:realm:password))}

KeeneticOS 5.2 дополнительно предлагает схему NDW4 (WWW-Authenticate:
x-ndw4-interactive) и после переноса хранения пароля принимает только её -
см. services/ndw4.py.

После этого запросы к /rci/... выполняются в той же cookie-сессии.

Используемые разделы конфигурации (KeeneticOS 4.x/5.x):
    object-group fqdn <name>        - списки доменных имён («Маршруты DNS»)
    dns-proxy route object-group    - привязка списка к интерфейсу
    ip route                        - статические IPv4-маршруты
"""

import hashlib
import ipaddress
import logging
import re
import threading
import time
from dataclasses import dataclass, field, asdict, replace
from typing import Any, Dict, Iterable, List, Optional

import requests

from services import ndw4

logger = logging.getLogger(__name__)

# KeeneticOS 5.x ограничивает один список 300 записями.
MAX_GROUP_ENTRIES = 300

# Сколько ждать соединения с роутером и сколько добавлять к ожиданию ответа
# на каждую команду пакета: 1400 маршрутов роутер переносит около 40 секунд.
CONNECT_TIMEOUT = 10
SECONDS_PER_COMMAND = 0.05
MAX_WRITE_TIMEOUT = 600

# Keenetic может несколько секунд возвращать прежний show/sc после успешной
# записи. Ожидаем подтверждения чтением, не повторяя команды изменения.
ROUTE_READBACK_TIMEOUT = 20.0
ROUTE_READBACK_POLL = 0.5

# Локальные и служебные сети: маршрут к ним через туннель уводит в него
# ответы роутера компьютерам домашней сети, и веб-интерфейс перестаёт открываться.
NON_ROUTABLE_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '0.0.0.0/8', '10.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
    '192.168.0.0/16', '224.0.0.0/4', '240.0.0.0/4',
))

# Типы интерфейсов, через которые обычно пускают трафик (туннели и прокси).
TUNNEL_TYPES = {
    'wireguard', 'openvpn', 'pptp', 'l2tp', 'sstp', 'ikev2', 'ipsec',
    'proxy', 'ipip', 'gre', 'eoip', 'zerotier', 'openconnect',
}

_DOMAIN_RE = re.compile(
    r'^(?=.{1,253}$)(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$'
)


class KeeneticError(RuntimeError):
    """Ошибка при работе с роутером."""


class KeeneticWriteUncertainError(KeeneticError):
    """Ответ на запись потерян; повторять команды или откат вслепую нельзя."""


class KeeneticAuthError(KeeneticError):
    """Неверный логин/пароль или роутер отклонил авторизацию."""


# === Модели ===

@dataclass
class DomainGroup:
    name: str
    description: str
    entries: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.description or self.name

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['title'] = self.title
        data['count'] = len(self.entries)
        data['limit'] = MAX_GROUP_ENTRIES
        return data


@dataclass
class DnsRoute:
    index: str
    group: str
    interface: str = ''
    auto: bool = False
    reject: bool = False
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IpRoute:
    index: str
    destination: str
    interface: str = ''
    gateway: str = ''
    auto: bool = False
    reject: bool = False
    enabled: bool = True
    comment: str = ''
    metric: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# === Нормализация ввода ===

def normalize_entry(raw: str) -> Optional[str]:
    """
    Приводит строку пользователя к записи списка доменов Keenetic.

    Принимает домен, URL, IPv4-адрес или IPv4-подсеть. Возвращает None,
    если строка не распознана. Префикс www. отбрасывается - запись домена
    в Keenetic и так покрывает все его поддомены.
    """
    value = (raw or '').strip().strip(',;').strip()
    if not value or value.startswith('#'):
        return None

    try:
        return str(ipaddress.ip_network(value, strict=False)) if '/' in value \
            else str(ipaddress.ip_address(value))
    except ValueError:
        pass

    value = re.sub(r'^[a-z][a-z0-9+.-]*://', '', value, flags=re.IGNORECASE)
    value = value.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0]
    value = value.rsplit('@', 1)[-1]
    value = re.sub(r':\d+$', '', value)
    value = value.strip().strip('.').lower()
    if value.startswith('*.'):
        value = value[2:]
    if value.startswith('www.'):
        value = value[4:]

    try:
        value = value.encode('idna').decode('ascii')
    except UnicodeError:
        return None

    return value if _DOMAIN_RE.match(value) else None


def parse_entries(text_or_list: Any) -> Dict[str, List[str]]:
    """Разбирает текст/список на валидные записи и отброшенные строки."""
    if isinstance(text_or_list, str):
        items = re.split(r'[\s,;]+', text_or_list)
    else:
        items = [str(x) for x in (text_or_list or [])]

    valid, invalid, seen = [], [], set()
    for item in items:
        if not item.strip():
            continue
        entry = normalize_entry(item)
        if entry is None:
            invalid.append(item.strip())
        elif entry not in seen:
            seen.add(entry)
            valid.append(entry)
    return {'valid': valid, 'invalid': invalid}


# === Клиент ===

class KeeneticClient:
    """HTTP-клиент RCI API роутера Keenetic с автоматическим входом."""

    def __init__(self, host: str, login: str, password: str, timeout: int = 15):
        host = (host or '').strip().rstrip('/')
        if not host:
            raise KeeneticError('Не указан адрес роутера')
        if not re.match(r'^https?://', host, re.IGNORECASE):
            host = 'http://' + host
        self.base_url = host
        self.login = login or 'admin'
        self.password = password or ''
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            'Accept': 'application/json',
            'User-Agent': 'Sites4Router/2.0',
        })
        self._lock = threading.RLock()
        self._authenticated = False
        # После отказа в пароле не входим повторно: 5 неудачных попыток за 3 минуты
        # роутер наказывает блокировкой IP компьютера на 15 минут. Сброс - новым
        # клиентом после изменения настроек или проверки подключения.
        self._auth_error: Optional[KeeneticAuthError] = None

    # --- транспорт ---

    def authenticate(self) -> None:
        """
        Вход по схеме, которую предлагает роутер: NDW4 (KeeneticOS 5.2) или
        прежней md5/sha256 по X-NDM-Challenge. В 5.2 роутер предлагает обе, но
        после переноса хранения пароля старую отклоняет даже с верным паролем,
        поэтому NDW4 всегда в приоритете.
        """
        with self._lock:
            if self._auth_error:
                raise self._auth_error
            self._session.cookies.clear()
            resp = self._auth_probe()
            if resp.status_code == 200:
                self._authenticated = True
                return

            offered, endpoint = ndw4.parse_offer(resp)
            if offered:
                try:
                    self._ndw4_login(endpoint)
                    return
                except ndw4.Ndw4CredentialsError:
                    # Окончательно: попытка старым способом добавила бы ещё одну
                    # ошибку входа в счёт блокировки.
                    self._fail_auth()
                except ndw4.Ndw4UnavailableError as e:
                    logger.info('Вход NDW4 на %s недоступен (%s), вхожу прежним способом',
                                self.base_url, e)
                    resp = self._auth_probe()
                except ndw4.Ndw4Error as e:
                    logger.warning('Вход NDW4 на %s не удался (%s), пробую прежний способ',
                                   self.base_url, e)
                    resp = self._auth_probe()
                    if not resp.headers.get('X-NDM-Challenge'):
                        raise KeeneticError(f'Не удалось войти на роутер по новой схеме KeeneticOS 5.2: {e}')
            self._ndw2_login(resp)

    def _auth_probe(self) -> requests.Response:
        """GET /auth: схемы входа, которые предлагает роутер, и cookie новой сессии."""
        try:
            resp = self._session.get(f'{self.base_url}/auth', timeout=self.timeout,
                                     allow_redirects=False)
        except requests.RequestException as e:
            raise KeeneticError(f'Роутер недоступен ({self.base_url}): {e}') from e
        if resp.is_redirect:
            raise KeeneticError(f'Роутер перенаправляет {self.base_url} на '
                                f'{resp.headers.get("Location")}: укажите в настройках этот адрес')
        if resp.status_code not in (200, 401):
            raise KeeneticError(f'Неожиданный ответ /auth: HTTP {resp.status_code}')
        return resp

    def _ndw4_login(self, endpoint: Optional[str]) -> None:
        try:
            ndw4.login(self._session, self.base_url, endpoint, self.login, self.password, self.timeout)
        except ndw4.Ndw4ProtocolError as e:
            # Сессия роутера живёт 20 секунд; сбой обмена лечится новой сессией.
            logger.info('Вход NDW4: %s - повторяю с новой сессией', e)
            self._session.cookies.clear()
            offered, endpoint = ndw4.parse_offer(self._auth_probe())
            if not offered:
                raise
            ndw4.login(self._session, self.base_url, endpoint, self.login, self.password, self.timeout)
        except requests.RequestException as e:
            raise KeeneticError(f'Роутер недоступен ({self.base_url}): {e}') from e
        self._confirm_login('NDW4')

    def _ndw2_login(self, resp: requests.Response) -> None:
        """Вход KeeneticOS до 5.2: sha256(challenge + md5(login:realm:password))."""
        realm = resp.headers.get('X-NDM-Realm')
        challenge = resp.headers.get('X-NDM-Challenge')
        if not realm or not challenge:
            www = resp.headers.get('WWW-Authenticate', '')
            realm = realm or _header_param(www, 'realm')
            challenge = challenge or _header_param(www, 'challenge')
        if not realm or not challenge:
            raise KeeneticError('Роутер не прислал challenge - это точно Keenetic?')

        md5 = hashlib.md5(f'{self.login}:{realm}:{self.password}'.encode()).hexdigest()
        key = hashlib.sha256(f'{challenge}{md5}'.encode()).hexdigest()
        try:
            resp = self._session.post(f'{self.base_url}/auth', timeout=self.timeout,
                                      json={'login': self.login, 'password': key},
                                      allow_redirects=False)
        except requests.RequestException as e:
            raise KeeneticError(f'Роутер недоступен ({self.base_url}): {e}') from e
        if resp.status_code != 200:
            self._fail_auth()
        self._authenticated = True
        logger.info('Авторизация на роутере %s выполнена', self.base_url)

    def _confirm_login(self, scheme: str) -> None:
        try:
            resp = self._session.get(f'{self.base_url}/auth', timeout=self.timeout,
                                     allow_redirects=False)
        except requests.RequestException as e:
            raise KeeneticError(f'Роутер недоступен ({self.base_url}): {e}') from e
        if resp.status_code != 200:
            raise KeeneticError(f'Роутер не подтвердил вход {scheme}: HTTP {resp.status_code}')
        self._authenticated = True
        logger.info('Авторизация на роутере %s выполнена (%s)', self.base_url, scheme)

    def _fail_auth(self) -> None:
        self._authenticated = False
        self._auth_error = KeeneticAuthError(
            'Неверный логин или пароль роутера. Повторных попыток входа не будет, '
            'пока не измените настройки: иначе роутер заблокирует компьютер на 15 минут')
        raise self._auth_error

    def _request(self, method: str, path: str, payload: Any = None,
                 read_timeout: Optional[float] = None) -> Any:
        url = f'{self.base_url}/rci/{path.lstrip("/")}'
        timeout = (min(CONNECT_TIMEOUT, self.timeout), read_timeout or self.timeout)
        with self._lock:
            if not self._authenticated:
                self.authenticate()
            for attempt in range(2):
                try:
                    resp = self._session.request(method, url, json=payload, timeout=timeout)
                except requests.ReadTimeout as e:
                    if method != 'GET':
                        # Соединение было, команды переданы - роутер их ещё выполняет.
                        raise KeeneticWriteUncertainError(
                            f'Роутер не ответил за {timeout[1]:.0f} с, но команды, скорее всего, '
                            'ещё выполняются. Подождите минуту и обновите страницу.') from e
                    raise KeeneticError(f'Роутер не ответил за {timeout[1]:.0f} с: {e}') from e
                except requests.RequestException as e:
                    error = KeeneticError if method == 'GET' else KeeneticWriteUncertainError
                    raise error(f'Роутер недоступен: {e}') from e
                if resp.status_code == 401 and attempt == 0:
                    # Сессия истекла (5 минут простоя) - входим заново.
                    self._authenticated = False
                    self.authenticate()
                    continue
                break

            if resp.status_code >= 400:
                raise KeeneticError(f'Роутер ответил ошибкой HTTP {resp.status_code} '
                                    f'({path or "пакет команд"})')
            if not resp.content:
                return {}
            try:
                data = resp.json()
            except ValueError as e:
                raise KeeneticError('Роутер вернул некорректный JSON') from e
        _raise_on_error(data)
        return data

    def get(self, path: str) -> Any:
        return self._request('GET', path)

    def write(self, commands: List[Dict[str, Any]], save: bool = True) -> Any:
        """Выполняет пакет команд и сохраняет конфигурацию."""
        batch = list(commands)
        if save:
            batch.append({'system': {'configuration': {'save': {}}}})
        read_timeout = min(MAX_WRITE_TIMEOUT, self.timeout + len(batch) * SECONDS_PER_COMMAND)
        return self._request('POST', '', batch, read_timeout=read_timeout)

    # --- информация ---

    def system_info(self) -> Dict[str, Any]:
        version = self.get('show/version') or {}
        return {
            'model': version.get('model') or version.get('device') or 'Keenetic',
            'release': version.get('title') or version.get('release') or '',
            'hw_id': version.get('hw_id', ''),
        }

    def interfaces(self) -> List[Dict[str, Any]]:
        raw = self.get('show/interface') or {}
        items = raw.items() if isinstance(raw, dict) else \
            ((i.get('id', ''), i) for i in raw if isinstance(i, dict))

        result = []
        for ident, data in items:
            if not ident or not isinstance(data, dict):
                continue
            itype = str(data.get('type', '')).lower()
            state = str(data.get('state', '')).lower()
            link = str(data.get('link', '')).lower()
            connected = str(data.get('connected', '')).lower()
            result.append({
                'id': ident,
                'description': data.get('description') or '',
                'type': data.get('type', ''),
                'tunnel': itype in TUNNEL_TYPES or any(t in ident.lower() for t in TUNNEL_TYPES),
                'up': state == 'up' and (link in ('', 'up')) and connected in ('', 'yes'),
            })
        result.sort(key=lambda i: (not i['tunnel'], not i['description'], i['id'].lower()))
        return result

    # --- списки доменов (object-group fqdn) ---

    def groups(self) -> List[DomainGroup]:
        raw = self.get('show/sc/object-group/fqdn') or {}
        if not isinstance(raw, dict):
            return []
        groups = []
        for name, data in raw.items():
            data = data if isinstance(data, dict) else {}
            includes = data.get('include', [])
            if isinstance(includes, dict):
                includes = [includes]
            entries = [str(i['address']) for i in includes
                       if isinstance(i, dict) and i.get('address')]
            groups.append(DomainGroup(name=str(name),
                                      description=str(data.get('description') or ''),
                                      entries=entries))
        groups.sort(key=lambda g: g.title.lower())
        return groups

    def get_group(self, name: str) -> Optional[DomainGroup]:
        return next((g for g in self.groups() if g.name == name), None)

    def next_group_name(self) -> str:
        existing = {g.name for g in self.groups()}
        n = 0
        while f'domain-list{n}' in existing:
            n += 1
        return f'domain-list{n}'

    def set_group_entries(self, name: str, entries: Iterable[str],
                          description: Optional[str] = None) -> DomainGroup:
        """Записывает список целиком (добавленные и удалённые записи)."""
        entries = list(dict.fromkeys(entries))
        if len(entries) > MAX_GROUP_ENTRIES:
            raise KeeneticError(
                f'В одном списке не больше {MAX_GROUP_ENTRIES} записей '
                f'(сейчас {len(entries)}). Разделите список на несколько.')

        current = self.get_group(name)
        body: Dict[str, Any] = {}
        commands = []
        if description is not None:
            body['description'] = description

        if current is None:
            body['include'] = [{'address': e} for e in entries]
        else:
            removed = set(current.entries) - set(entries)
            if removed:
                # Штатный способ: очистить include и записать заново.
                commands.append({'object-group': {'fqdn': {name: {'include': {'no': True}}}}})
                body['include'] = [{'address': e} for e in entries]
            else:
                added = [e for e in entries if e not in set(current.entries)]
                if added:
                    body['include'] = [{'address': e} for e in added]

        if body:
            commands.append({'object-group': {'fqdn': {name: body}}})
        if commands:
            self.write(commands)
        return DomainGroup(name=name,
                           description=description if description is not None
                           else (current.description if current else ''),
                           entries=entries)

    def create_group(self, description: str, entries: Iterable[str],
                     interface: Optional[str] = None,
                     auto: bool = True, reject: bool = False) -> DomainGroup:
        entries = list(dict.fromkeys(entries))
        if len(entries) > MAX_GROUP_ENTRIES:
            raise KeeneticError(f'В одном списке не больше {MAX_GROUP_ENTRIES} записей')
        name = self.next_group_name()
        commands = [{'object-group': {'fqdn': {name: {
            'description': description or name,
            'include': [{'address': e} for e in entries],
        }}}}]
        if interface:
            commands.append({'dns-proxy': {'route': {
                'group': name, 'interface': interface,
                'auto': auto, 'reject': reject,
            }}})
        self.write(commands)
        return DomainGroup(name=name, description=description, entries=entries)

    def merge_groups(self, target: str, sources: Iterable[str], title: Optional[str] = None) -> Dict[str, Any]:
        """
        Объединяет списки: записи sources переходят в target, sources удаляются.
        Правила sources на интерфейсы, которых у target нет, переносятся в target.
        """
        sources = [s for s in dict.fromkeys(sources) if s != target]
        if not target or not sources:
            raise ValueError('Выберите хотя бы два списка')
        groups = {g.name: g for g in self.groups()}
        if target not in groups or any(n not in groups for n in sources):
            raise ValueError('Список не найден на роутере - обновите страницу')
        entries = list(dict.fromkeys(groups[target].entries + [e for n in sources for e in groups[n].entries]))
        if len(entries) > MAX_GROUP_ENTRIES:
            raise ValueError(f'В объединённом списке будет {len(entries)} записей, а Keenetic принимает '
                             f'не больше {MAX_GROUP_ENTRIES}. Выберите меньше списков.')
        group = self.set_group_entries(target, entries, title or None)
        routes = self.dns_routes()
        used = {r.interface for r in routes if r.group == target}
        moved = 0
        for r in routes:
            if r.group in sources and r.interface not in used:
                self.add_dns_route(target, r.interface, auto=r.auto, reject=r.reject)
                used.add(r.interface)
                moved += 1
        for name in sources:
            self.delete_group(name)
        return {'group': group, 'removed': sources, 'rules_moved': moved, 'entries': len(entries)}

    def delete_group(self, name: str) -> None:
        commands = [{'dns-proxy': {'route': {'index': r.index, 'no': True}}}
                    for r in self.dns_routes() if r.group == name and r.index]
        commands.append({'object-group': {'fqdn': {'name': name, 'no': True}}})
        self.write(commands)

    # --- маршруты DNS (dns-proxy route) ---

    def dns_routes(self) -> List[DnsRoute]:
        raw = self.get('show/sc/dns-proxy/route')
        routes = []
        for item in _as_list(raw):
            if isinstance(item, dict):
                routes.append(DnsRoute(
                    index=str(item.get('index', '')),
                    group=str(item.get('group', '')),
                    interface=str(item.get('interface', '')),
                    auto=bool(item.get('auto', False)),
                    reject=bool(item.get('reject', False)),
                    enabled=not bool(item.get('disable', False)),
                ))
        return routes

    def add_dns_route(self, group: str, interface: str,
                      auto: bool = True, reject: bool = False) -> None:
        self.write([{'dns-proxy': {'route': {
            'group': group, 'interface': interface, 'auto': auto, 'reject': reject,
        }}}])

    def set_dns_route_enabled(self, index: str, enabled: bool) -> None:
        self.write([{'dns-proxy': {'route': {'disable': {'index': index, 'no': enabled}}}}])

    def set_dns_routes_enabled(self, indices: Iterable[str], enabled: bool) -> int:
        """Включает или выключает правила DNS-маршрутизации одним пакетом."""
        commands = [{'dns-proxy': {'route': {'disable': {'index': i, 'no': enabled}}}}
                    for i in dict.fromkeys(indices) if i]
        if commands:
            self.write(commands)
        return len(commands)

    def delete_dns_route(self, index: str) -> None:
        self.write([{'dns-proxy': {'route': {'index': index, 'no': True}}}])

    def _require_interface(self, interface: str) -> None:
        if not interface or interface not in {i['id'] for i in self.interfaces()}:
            raise KeeneticError('Выбранное подключение больше не существует на роутере. '
                                'Обновите список и выберите подключение заново.')

    def update_dns_route(self, index: str, auto: bool, reject: bool,
                         expected_route: Optional[Dict[str, Any]] = None) -> DnsRoute:
        """Применяет флаги после сохранения и проверяет их чтением с роутера."""
        if not index:
            raise ValueError('Не выбрано DNS-правило')
        with self._lock:
            all_routes = self.dns_routes()
            route = _selected_routes(all_routes, [index],
                                     [expected_route] if expected_route is not None else None)[0]
            if any(r.index != route.index and (r.group, r.interface) == (route.group, route.interface)
                   for r in all_routes):
                raise KeeneticError('У DNS-списка есть совпадающие правила. Сначала проверьте правила.')
            self._require_interface(route.interface)
            desired = replace(route, auto=auto, reject=reject)
            if _route_signature(route) == _route_signature(desired):
                return route
            try:
                self.write([_delete_dns_command(route), _dns_command(desired)], save=False)
                present = _wait_route_state(
                    self.dns_routes, lambda rows: _dns_binding_matches(rows, desired),
                    'Роутер не подтвердил параметры DNS-правила за 20 секунд')
                actual = next(r for r in present if _route_signature(r) == _route_signature(desired))
            except KeeneticWriteUncertainError:
                # Таймаут не означает, что пакет остановился. Не повторяем его.
                raise
            except KeeneticError as error:
                # После завершённой неудачной записи возвращаем исходные флаги,
                # включая выключенное состояние. Запрошенные флаги не выдаём за успех.
                present = self.dns_routes()
                binding = [r for r in present if (r.group, r.interface) == (route.group, route.interface)]
                if len(binding) != 1 or _route_signature(binding[0]) != _route_signature(route):
                    commands = [_delete_dns_command(route)] if binding else []
                    self.write(commands + [_dns_command(route)])
                _wait_route_state(
                    self.dns_routes, lambda rows: _dns_binding_matches(rows, route),
                    'Не удалось подтвердить восстановление DNS-правила. Используйте резервную копию.')
                raise KeeneticError(f'{error}. Исходное DNS-правило сохранено.') from error
            self.write([])  # Сохраняем только подтверждённые параметры.
            return actual

    def validate_dns_move(self, indices: Iterable[str], interface: str,
                          expected_routes: Optional[List[Dict[str, Any]]] = None):
        self._require_interface(interface)
        all_routes = self.dns_routes()
        selected = _selected_routes(all_routes, indices, expected_routes)
        routes = [r for r in selected if r.interface != interface]
        chosen = {r.index for r in selected}
        source_keys = {(r.group, r.interface) for r in routes}
        if any((r.group, r.interface) in source_keys and r.index not in chosen for r in all_routes):
            raise KeeneticError('У выбранных DNS-списков есть совпадающие правила. '
                                'Сначала проверьте правила: перенос остановлен до изменений.')
        targets = {}
        for route in routes:
            desired = replace(route, index='', interface=interface)
            key = (desired.group, desired.interface)
            existing = [r for r in all_routes if (r.group, r.interface) == key]
            if key in targets:
                existing.append(targets[key])
            if any(_route_signature(r) != _route_signature(desired) for r in existing):
                raise KeeneticError('На выбранном подключении есть DNS-правило с другими параметрами. '
                                    'Согласуйте параметры перед переносом.')
            targets[key] = desired
        return routes, list(targets.values())

    def move_dns_routes(self, indices: Iterable[str], interface: str,
                        expected_routes: Optional[List[Dict[str, Any]]] = None) -> int:
        """Сначала создаёт и проверяет новые правила, затем убирает исходные."""
        with self._lock:
            routes, targets = self.validate_dns_move(indices, interface, expected_routes)
            if not routes:
                return 0
            present = {_route_signature(r) for r in self.dns_routes()}
            commands = [_dns_command(r) for r in targets if _route_signature(r) not in present]
            if commands:
                self.write(commands, save=False)
            after = _wait_route_state(
                self.dns_routes, lambda rows: _targets_present(targets, rows),
                'Роутер не подтвердил новые DNS-маршруты за 20 секунд. Исходные правила не удалялись.')
            # Служебный index может измениться/переиспользоваться после записи.
            # Удаляем по исходной паре список/интерфейс, отдельно от добавления.
            self._require_interface(interface)
            originals = {(r.group, r.interface): _route_signature(r) for r in routes}
            if any((r.group, r.interface) in originals and _route_signature(r) != originals[(r.group, r.interface)] for r in after):
                raise KeeneticError('Исходные DNS-правила изменились во время переноса. Удаление остановлено.')
            source_keys = {(r.group, r.interface) for r in after}
            self.write([_delete_dns_command(r) for r in routes if (r.group, r.interface) in source_keys])
            sources = {(r.group, r.interface) for r in routes}
            _wait_route_state(
                self.dns_routes,
                lambda rows: _targets_present(targets, rows) and not sources.intersection(
                    (r.group, r.interface) for r in rows),
                'Роутер не подтвердил завершение переноса DNS за 20 секунд. Обновите список.')
            return len(routes)

    # --- статические IPv4-маршруты (ip route) ---

    def ip_routes(self) -> List[IpRoute]:
        raw = self.get('show/sc/ip/route')
        routes = []
        for item in _as_list(raw):
            if not isinstance(item, dict):
                continue
            if item.get('default'):
                destination = '0.0.0.0/0'
            elif item.get('host'):
                destination = f"{item['host']}/32"
            elif item.get('network'):
                try:
                    destination = str(ipaddress.ip_network(
                        f"{item['network']}/{item.get('mask', '255.255.255.255')}", strict=False))
                except ValueError:
                    destination = str(item['network'])
            else:
                continue
            routes.append(IpRoute(
                index=str(item.get('index', '')),
                destination=destination,
                interface=str(item.get('interface', '')),
                gateway=str(item.get('gateway', '')),
                auto=bool(item.get('auto', False)),
                reject=bool(item.get('reject', False)),
                enabled=not bool(item.get('disable', False)),
                comment=str(item.get('comment', '')),
                metric=item.get('metric'),
            ))
        return routes

    def add_ip_routes(self, destinations: Iterable[str], interface: str,
                      auto: bool = True, comment: str = '') -> Dict[str, int]:
        """
        Добавляет маршруты. Уже существующие адреса не дублируются; если у
        существующего маршрута нет описания, а новое передано - оно дописывается.
        """
        current = {r.destination: r for r in self.ip_routes()}
        commands, added, updated, skipped = [], 0, 0, 0
        for dest in destinations:
            try:
                net = ipaddress.ip_network(dest, strict=False)
            except ValueError:
                continue
            if net.version != 4 or net.prefixlen == 0:
                continue
            if not is_routable(net):
                skipped += 1
                continue
            existing = current.get(str(net))
            if existing:
                if comment and not existing.comment:
                    commands.append(ip_route_command(existing, comment=comment))
                    existing.comment = comment
                    updated += 1
                continue
            route = IpRoute(index='', destination=str(net), interface=interface,
                            auto=auto, comment=comment)
            current[str(net)] = route
            commands.append(ip_route_command(route))
            added += 1
        if commands:
            self.write(commands)
        if skipped:
            logger.warning('Пропущено локальных адресов при добавлении маршрутов: %s', skipped)
        return {'added': added, 'updated': updated, 'skipped': skipped}

    def delete_ip_routes(self, indices: Iterable[str]) -> int:
        commands = [{'ip': {'route': {'index': i, 'no': True}}}
                    for i in dict.fromkeys(indices) if i]
        if commands:
            self.write(commands)
        return len(commands)

    def validate_ip_move(self, indices: Iterable[str], interface: str,
                         expected_routes: Optional[List[Dict[str, Any]]] = None):
        self._require_interface(interface)
        all_routes = self.ip_routes()
        selected = _selected_routes(all_routes, indices, expected_routes)
        routes = [r for r in selected if r.interface != interface]
        chosen = {r.index for r in selected}
        source_keys = {_ip_key(r) for r in routes}
        if any(_ip_key(r) in source_keys and r.index not in chosen for r in all_routes):
            raise KeeneticError('У выбранных адресов есть совпадающие маршруты в других группах. '
                                'Сначала проверьте повторы: перенос остановлен до изменений.')
        targets = {}
        for route in routes:
            desired = replace(route, index='', interface=interface, gateway='')
            key = _ip_key(desired)
            existing = [r for r in all_routes if _ip_key(r) == key]
            if key in targets:
                existing.append(targets[key])
            if any(_route_signature(r) != _route_signature(desired) for r in existing):
                raise KeeneticError('На выбранном подключении уже есть такой IP-маршрут '
                                    'с другим описанием или параметрами. Перенос остановлен.')
            targets[key] = desired
        return routes, list(targets.values())

    def move_ip_routes(self, indices: Iterable[str], interface: str,
                       expected_routes: Optional[List[Dict[str, Any]]] = None) -> int:
        """Перенос с проверкой новых маршрутов до удаления исходных."""
        with self._lock:
            routes, targets = self.validate_ip_move(indices, interface, expected_routes)
            if not routes:
                return 0
            present = {_route_signature(r) for r in self.ip_routes()}
            commands = [ip_route_command(r) for r in targets if _route_signature(r) not in present]
            if commands:
                self.write(commands, save=False)
            after = _wait_route_state(
                self.ip_routes, lambda rows: _targets_present(targets, rows),
                'Роутер не подтвердил новые IP-маршруты за 20 секунд. Исходные маршруты не удалялись.')
            # RCI исполняет весь пакет, даже если одна из команд отклонена.
            # Никогда не посылаем удаления вместе с непроверенными добавлениями.
            # Полный исходный адрес/интерфейс/шлюз защищает и от смены index.
            self._require_interface(interface)
            originals = {_ip_key(r): _route_signature(r) for r in routes}
            if any(_ip_key(r) in originals and _route_signature(r) != originals[_ip_key(r)] for r in after):
                raise KeeneticError('Исходные IP-маршруты изменились во время переноса. Удаление остановлено.')
            source_keys = {_ip_key(r) for r in after}
            self.write([_delete_ip_command(r) for r in routes if _ip_key(r) in source_keys])
            sources = {_ip_key(r) for r in routes}
            _wait_route_state(
                self.ip_routes,
                lambda rows: _targets_present(targets, rows) and not sources.intersection(_ip_key(r) for r in rows),
                'Роутер не подтвердил завершение переноса IP за 20 секунд. Обновите список.')
            logger.info('Перенос IP-маршрутов на %s подтверждён: %s', interface, len(routes))
            return len(routes)

    def set_ip_routes_comment(self, indices: Iterable[str], comment: str) -> int:
        """
        Задаёт описание маршрутам. Маршрут с тем же адресом и интерфейсом
        записывается повторно - KeeneticOS обновляет его параметры на месте.
        """
        wanted = set(indices)
        routes = [r for r in self.ip_routes() if r.index in wanted and r.comment != comment]
        if routes:
            self.write([ip_route_command(r, comment=comment) for r in routes])
        return len(routes)

    def set_ip_route_enabled(self, index: str, enabled: bool) -> None:
        self.set_ip_routes_enabled([index], enabled)

    def set_ip_routes_enabled(self, indices: Iterable[str], enabled: bool) -> int:
        """Включает или выключает маршруты одним пакетом (маршрут остаётся в настройках)."""
        commands = [{'ip': {'route': {'disable': {'index': i, 'no': enabled}}}}
                    for i in dict.fromkeys(indices) if i]
        if commands:
            self.write(commands)
        return len(commands)


# === Вспомогательные функции ===

def _route_signature(route) -> tuple:
    return tuple((k, v) for k, v in asdict(route).items() if k != 'index')


def _selected_routes(routes, indices, expected=None) -> list:
    wanted = {str(i) for i in indices if i}
    selected = [r for r in routes if r.index in wanted]
    if {r.index for r in selected} != wanted:
        raise KeeneticError('Выбранные маршруты изменились на роутере. Обновите список и повторите выбор.')
    if expected is not None:
        if not isinstance(expected, list) or any(not isinstance(e, dict) for e in expected):
            raise ValueError('Некорректный снимок выбранных маршрутов')
        by_index = {str(e.get('index', '')): e for e in expected}
        if set(by_index) != wanted or len(by_index) != len(expected):
            raise KeeneticError('Выбор маршрутов изменился. Обновите список и повторите выбор.')
        for route in selected:
            snapshot = by_index[route.index]
            identity = 'destination' if isinstance(route, IpRoute) else 'group'
            if identity not in snapshot or 'interface' not in snapshot or any(
                    key in snapshot and snapshot[key] != value for key, value in asdict(route).items()):
                raise KeeneticError('Параметры выбранных маршрутов изменились на роутере. '
                                    'Обновите список и повторите выбор.')
    return selected


def _targets_present(targets, present) -> bool:
    signatures = {_route_signature(r) for r in present}
    return all(_route_signature(r) in signatures for r in targets)


def _dns_binding_matches(rows, desired: DnsRoute) -> bool:
    binding = [r for r in rows if (r.group, r.interface) == (desired.group, desired.interface)]
    return len(binding) == 1 and _route_signature(binding[0]) == _route_signature(desired)


def _wait_route_state(read, matches, error: str) -> list:
    """Ожидает актуальную конфигурацию; повторяет только безопасное чтение."""
    deadline = time.monotonic() + ROUTE_READBACK_TIMEOUT
    while True:
        rows = read()
        if matches(rows):
            return rows
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning('Истекло ожидание конфигурации: %s', error)
            raise KeeneticError(error)
        time.sleep(min(ROUTE_READBACK_POLL, remaining))


def _ip_key(route: IpRoute) -> tuple:
    return route.destination, route.interface, route.gateway, route.metric


def _delete_ip_command(route: IpRoute) -> Dict[str, Any]:
    data = ip_route_command(route)['ip']['route']
    for key in ('auto', 'reject', 'comment', 'disable'):
        data.pop(key, None)
    data['no'] = True
    return {'ip': {'route': data}}


def _dns_command(route: DnsRoute) -> Dict[str, Any]:
    data = {k: v for k, v in asdict(route).items() if k not in ('index', 'enabled')}
    if not route.enabled:
        data['disable'] = True
    return {'dns-proxy': {'route': data}}


def _delete_dns_command(route: DnsRoute) -> Dict[str, Any]:
    return {'dns-proxy': {'route': {'group': route.group, 'interface': route.interface, 'no': True}}}


def is_routable(net: Any) -> bool:
    """False для адресов домашней сети, loopback, multicast и прочих служебных сетей."""
    net = ipaddress.ip_network(net, strict=False)
    return not any(net.version == b.version and net.subnet_of(b) for b in NON_ROUTABLE_NETWORKS)


def ip_route_command(route: IpRoute, interface: Optional[str] = None,
                     comment: Optional[str] = None) -> Dict[str, Any]:
    """Команда RCI `ip route` для маршрута (с заменой интерфейса/описания, если заданы)."""
    net = ipaddress.ip_network(route.destination, strict=False)
    data: Dict[str, Any] = {
        'auto': route.auto,
        'reject': route.reject,
        'comment': route.comment if comment is None else comment,
    }
    iface = interface or route.interface
    if iface:
        data['interface'] = iface
    if route.gateway and not interface:
        data['gateway'] = route.gateway
    if route.metric is not None:
        data['metric'] = route.metric
    if net.prefixlen == 0:
        data['default'] = True
    elif net.prefixlen == 32:
        data['host'] = str(net.network_address)
    else:
        data['network'] = str(net.network_address)
        data['mask'] = str(net.netmask)
    if not route.enabled:
        data['disable'] = True
    return {'ip': {'route': data}}


def _header_param(header: str, name: str) -> Optional[str]:
    match = re.search(rf'{name}="([^"]*)"', header or '')
    return match.group(1) if match else None


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def _raise_on_error(value: Any, path: str = '') -> None:
    """RCI возвращает HTTP 200 даже при ошибке команды - ищем status=error."""
    if isinstance(value, dict):
        status = value.get('status')
        if isinstance(status, list):
            for item in status:
                if isinstance(item, dict) and item.get('status') == 'error':
                    raise KeeneticError(f"{item.get('message') or 'ошибка RCI'}"
                                        f"{f' ({path})' if path else ''}")
        elif status == 'error':
            raise KeeneticError(str(value.get('message') or 'ошибка RCI'))
        for key, nested in value.items():
            if key != 'status':
                _raise_on_error(nested, f'{path}.{key}' if path else str(key))
    elif isinstance(value, list):
        for nested in value:
            _raise_on_error(nested, path)
