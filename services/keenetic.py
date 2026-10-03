"""
Keenetic RCI client - управление роутером Keenetic через его HTTP API (RCI).

RCI - это JSON-зеркало командной строки KeeneticOS, которым пользуется
штатный веб-интерфейс. Авторизация - challenge-схема:

    GET  /auth  -> 401 + заголовки X-NDM-Realm, X-NDM-Challenge и cookie сессии
    POST /auth  {"login": ..., "password": sha256(challenge + md5(login:realm:password))}

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
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional

import requests

logger = logging.getLogger(__name__)

# KeeneticOS 5.x ограничивает один список 300 записями.
MAX_GROUP_ENTRIES = 300

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

    # --- транспорт ---

    def authenticate(self) -> None:
        with self._lock:
            try:
                resp = self._session.get(f'{self.base_url}/auth', timeout=self.timeout)
            except requests.RequestException as e:
                raise KeeneticError(f'Роутер недоступен ({self.base_url}): {e}') from e

            if resp.status_code == 200:
                self._authenticated = True
                return
            if resp.status_code != 401:
                raise KeeneticError(f'Неожиданный ответ /auth: HTTP {resp.status_code}')

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

            resp = self._session.post(
                f'{self.base_url}/auth',
                json={'login': self.login, 'password': key},
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                self._authenticated = False
                raise KeeneticAuthError('Неверный логин или пароль роутера')
            self._authenticated = True
            logger.info('Авторизация на роутере %s выполнена', self.base_url)

    def _request(self, method: str, path: str, payload: Any = None) -> Any:
        url = f'{self.base_url}/rci/{path.lstrip("/")}'
        with self._lock:
            if not self._authenticated:
                self.authenticate()
            for attempt in range(2):
                try:
                    resp = self._session.request(method, url, json=payload, timeout=self.timeout)
                except requests.RequestException as e:
                    raise KeeneticError(f'Роутер недоступен: {e}') from e
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
        return self._request('POST', '', batch)

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

    def delete_dns_route(self, index: str) -> None:
        self.write([{'dns-proxy': {'route': {'index': index, 'no': True}}}])

    def update_dns_route(self, index: str, auto: bool, reject: bool) -> DnsRoute:
        """
        Меняет параметры правила («добавлять автоматически», «эксклюзивный»).
        RCI не даёт изменить их на месте, поэтому правило пересоздаётся: удаляется
        и тут же добавляется в одном пакете. После записи проверяем результат и при
        необходимости восстанавливаем правило.
        """
        route = next((r for r in self.dns_routes() if r.index == index), None)
        if route is None:
            raise KeeneticError('Правило не найдено — обновите страницу')
        data: Dict[str, Any] = {'group': route.group, 'interface': route.interface,
                                'auto': auto, 'reject': reject}
        if not route.enabled:
            data['disable'] = True
        try:
            self.write([{'dns-proxy': {'route': {'index': index, 'no': True}}},
                        {'dns-proxy': {'route': data}}])
        finally:
            present = [r for r in self.dns_routes()
                       if r.group == route.group and r.interface == route.interface]
            if not present:
                logger.warning('Правило %s пропало после изменения - восстанавливаю', index)
                self.write([{'dns-proxy': {'route': data}}])
        return next((r for r in self.dns_routes()
                     if r.group == route.group and r.interface == route.interface), route)

    def move_dns_routes(self, indices: Iterable[str], interface: str) -> int:
        """Пересоздаёт правила с другим интерфейсом, сохраняя остальные параметры."""
        wanted = set(indices)
        all_routes = self.dns_routes()
        routes = [r for r in all_routes if r.index in wanted and r.interface != interface]
        # У списка уже есть правило на целевой интерфейс - второе не создаём.
        on_target = {r.group for r in all_routes if r.interface == interface}
        # Сначала создаём новые правила, потом удаляем старые: если роутер отклонит
        # команду посреди пакета, трафик не останется без маршрута.
        commands = []
        for r in routes:
            if r.group in on_target:
                continue
            on_target.add(r.group)
            data: Dict[str, Any] = {'group': r.group, 'interface': interface,
                                    'auto': r.auto, 'reject': r.reject}
            if not r.enabled:
                data['disable'] = True
            commands.append({'dns-proxy': {'route': data}})
        commands += [{'dns-proxy': {'route': {'index': r.index, 'no': True}}} for r in routes]
        if commands:
            self.write(commands)
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
            ))
        return routes

    def add_ip_routes(self, destinations: Iterable[str], interface: str,
                      auto: bool = True, comment: str = '') -> Dict[str, int]:
        """
        Добавляет маршруты. Уже существующие адреса не дублируются; если у
        существующего маршрута нет описания, а новое передано - оно дописывается.
        """
        current = {r.destination: r for r in self.ip_routes()}
        commands, added, updated = [], 0, 0
        for dest in destinations:
            try:
                net = ipaddress.ip_network(dest, strict=False)
            except ValueError:
                continue
            if net.version != 4 or net.prefixlen == 0:
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
        return {'added': added, 'updated': updated}

    def delete_ip_routes(self, indices: Iterable[str]) -> int:
        commands = [{'ip': {'route': {'index': i, 'no': True}}}
                    for i in dict.fromkeys(indices) if i]
        if commands:
            self.write(commands)
        return len(commands)

    def move_ip_routes(self, indices: Iterable[str], interface: str) -> int:
        """Переводит статические маршруты на другой интерфейс."""
        wanted = set(indices)
        all_routes = self.ip_routes()
        routes = [r for r in all_routes if r.index in wanted and r.interface != interface]
        on_target = {r.destination for r in all_routes if r.interface == interface}
        commands = []
        for r in routes:
            if r.destination not in on_target:  # на целевом интерфейсе такого маршрута ещё нет
                commands.append(ip_route_command(r, interface=interface))
                on_target.add(r.destination)
        # Старые маршруты удаляем только после добавления новых.
        commands += [{'ip': {'route': {'index': r.index, 'no': True}}} for r in routes]
        if commands:
            self.write(commands)
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
