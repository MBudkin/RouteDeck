"""
Transfer - экспорт и импорт конфигурации маршрутизации между роутерами Keenetic.

Формат файла (JSON):
    {
      "app": "RouteDeck", "format": 1, "exported_at": "...",
      "source": {"model": "...", "release": "..."},
      "interfaces": [{"id": "Wireguard0", "title": "JHRouter"}],
      "groups": [{"title": "Telegram", "entries": [...],
                  "routes": [{"interface": "Wireguard0", "auto": true, "reject": false, "enabled": true}]}],
      "ip_routes": [{"destination": "91.108.4.0/22", "interface": "Wireguard0",
                     "auto": true, "reject": false, "enabled": true, "comment": ""}]
    }

Интерфейсы в файле указаны по id исходного роутера; при импорте их
сопоставляют с интерфейсами целевого (interface_map), потому что названия
VPN-подключений на разных роутерах отличаются.

IP-маршруты группируются по описанию (comment): описание - это «чьи это адреса».

Режимы импорта:
    merge    - добавить к текущему: новые списки создаются, в списки с тем же
               названием дописываются недостающие записи, ничего не удаляется;
               у уже существующих IP без описания описание дописывается;
    replace  - заменить совпадающие: списки с тем же названием и IP-группы с тем
               же описанием перезаписываются целиком, остальные не трогаются;
    replace_all - заменить всё: текущие списки, правила и статические маршруты
               (кроме маршрута по умолчанию) удаляются и загружаются из файла.
"""

import ipaddress
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.keenetic import (
    IpRoute, KeeneticClient, MAX_GROUP_ENTRIES, ip_route_command, normalize_entry,
)

FORMAT_VERSION = 1
MODES = ('merge', 'replace', 'replace_all')


def export_config(client: KeeneticClient, include_dns: bool = True, include_ip: bool = True,
                  app_name: str = 'RouteDeck') -> Dict[str, Any]:
    system = client.system_info()
    interfaces = client.interfaces()
    titles = {i['id']: i['description'] or i['id'] for i in interfaces}
    used = set()

    groups = []
    if include_dns:
        routes = client.dns_routes()
        for g in client.groups():
            g_routes = [r for r in routes if r.group == g.name]
            used.update(r.interface for r in g_routes)
            groups.append({
                'title': g.title,
                'entries': g.entries,
                'routes': [{'interface': r.interface, 'auto': r.auto,
                            'reject': r.reject, 'enabled': r.enabled} for r in g_routes],
            })

    ip_routes = []
    if include_ip:
        for r in client.ip_routes():
            if r.destination == '0.0.0.0/0' or not r.interface:
                continue  # маршрут по умолчанию и маршруты через шлюз не переносим
            used.add(r.interface)
            ip_routes.append({'destination': r.destination, 'interface': r.interface,
                              'auto': r.auto, 'reject': r.reject, 'enabled': r.enabled,
                              'comment': r.comment})

    return {
        'app': app_name,
        'format': FORMAT_VERSION,
        'exported_at': datetime.now().isoformat(timespec='seconds'),
        'source': system,
        'interfaces': [{'id': i, 'title': titles.get(i, i)} for i in sorted(used)],
        'groups': groups,
        'ip_routes': ip_routes,
    }


def validate(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict) or data.get('format') != FORMAT_VERSION \
            or not isinstance(data.get('groups', []), list):
        raise ValueError('Это не файл экспорта RouteDeck (или он из несовместимой версии)')
    return data


def plan_import(client: KeeneticClient, data: Dict[str, Any], mode: str,
                interface_map: Dict[str, str], group_titles: Optional[List[str]] = None,
                include_ip: bool = True, ip_comments: Optional[List[str]] = None,
                ip_destinations: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Строит пакет команд RCI для импорта и сводку изменений.
    interface_map: {id интерфейса в файле: id на этом роутере или '' (не создавать правила)}.
    group_titles: какие списки из файла импортировать (None - все).
    """
    validate(data)
    if mode not in MODES:
        raise ValueError('Неизвестный режим импорта')

    valid_ifaces = {i['id'] for i in client.interfaces()}
    iface_map = {src: dst for src, dst in (interface_map or {}).items() if dst in valid_ifaces}

    current_groups = client.groups()
    current_routes = client.dns_routes()
    current_ip = client.ip_routes() if include_ip else []

    summary = {'lists_created': 0, 'lists_updated': 0, 'lists_removed': 0,
               'entries_added': 0, 'rules_added': 0, 'rules_removed': 0,
               'ip_added': 0, 'ip_removed': 0, 'ip_updated': 0,
               'skipped_entries': 0, 'warnings': []}
    head: List[Dict[str, Any]] = []   # удаления, которые должны пройти первыми
    body: List[Dict[str, Any]] = []   # создание и изменение
    tail: List[Dict[str, Any]] = []   # удаления старых правил после создания новых

    if mode == 'replace_all':
        head += [{'dns-proxy': {'route': {'index': r.index, 'no': True}}} for r in current_routes]
        head += [{'object-group': {'fqdn': {'name': g.name, 'no': True}}} for g in current_groups]
        summary['rules_removed'] += len(current_routes)
        summary['lists_removed'] += len(current_groups)
        used_names = set()
        by_title = {}
        routes_by_group: Dict[str, list] = {}
    else:
        used_names = {g.name for g in current_groups}
        by_title = {g.title.casefold(): g for g in current_groups}
        routes_by_group = {}
        for r in current_routes:
            routes_by_group.setdefault(r.group, []).append(r)

    def new_name() -> str:
        n = 0
        while f'domain-list{n}' in used_names:
            n += 1
        used_names.add(f'domain-list{n}')
        return f'domain-list{n}'

    wanted = None if group_titles is None else {t.casefold() for t in group_titles}

    for item in data.get('groups', []):
        title = str(item.get('title') or '').strip()
        if not title or (wanted is not None and title.casefold() not in wanted):
            continue
        entries = []
        for raw in item.get('entries', []):
            e = normalize_entry(str(raw))
            if e and e not in entries:
                entries.append(e)
            elif not e:
                summary['skipped_entries'] += 1

        routes = []
        for r in item.get('routes', []):
            dst = iface_map.get(r.get('interface'))
            if dst:
                routes.append({'interface': dst, 'auto': bool(r.get('auto', True)),
                               'reject': bool(r.get('reject', False)),
                               'enabled': bool(r.get('enabled', True))})

        target = by_title.get(title.casefold())
        if target and mode == 'merge':
            final = target.entries + [e for e in entries if e not in target.entries]
        else:
            final = entries

        chunks = [final[i:i + MAX_GROUP_ENTRIES] for i in range(0, len(final), MAX_GROUP_ENTRIES)] or [[]]
        if len(chunks) > 1:
            summary['warnings'].append(
                f'«{title}»: больше {MAX_GROUP_ENTRIES} записей — разбит на {len(chunks)} списка')

        for n, chunk in enumerate(chunks):
            chunk_title = title if n == 0 else f'{title} ({n + 1})'
            existing = target if n == 0 else by_title.get(chunk_title.casefold())

            if existing:
                name = existing.name
                added = [e for e in chunk if e not in existing.entries]
                removed = [e for e in existing.entries if e not in chunk]
                if removed:
                    body.append({'object-group': {'fqdn': {name: {'include': {'no': True}}}}})
                    body.append({'object-group': {'fqdn': {name: {
                        'include': [{'address': e} for e in chunk]}}}})
                elif added:
                    body.append({'object-group': {'fqdn': {name: {
                        'include': [{'address': e} for e in added]}}}})
                if added or removed:
                    summary['lists_updated'] += 1
                summary['entries_added'] += len(added)
            else:
                name = new_name()
                body.append({'object-group': {'fqdn': {name: {
                    'description': chunk_title,
                    'include': [{'address': e} for e in chunk]}}}})
                summary['lists_created'] += 1
                summary['entries_added'] += len(chunk)

            old_routes = routes_by_group.get(name, [])
            if mode == 'replace' and existing and routes:
                # Правила совпадающего списка заменяются правилами из файла.
                keep = []
                for r in old_routes:
                    if any(r.interface == x['interface'] and r.auto == x['auto'] and r.reject == x['reject']
                           and r.enabled == x['enabled'] for x in routes):
                        keep.append(r)
                    else:
                        tail.append({'dns-proxy': {'route': {'index': r.index, 'no': True}}})
                        summary['rules_removed'] += 1
                old_routes = keep

            for r in routes:
                if any(o.interface == r['interface'] for o in old_routes):
                    continue
                cmd = {'group': name, 'interface': r['interface'], 'auto': r['auto'], 'reject': r['reject']}
                if not r['enabled']:
                    cmd['disable'] = True
                body.append({'dns-proxy': {'route': cmd}})
                summary['rules_added'] += 1

    if include_ip:
        if mode == 'replace_all':
            for r in current_ip:
                if r.destination != '0.0.0.0/0' and r.index:
                    head.append({'ip': {'route': {'index': r.index, 'no': True}}})
                    summary['ip_removed'] += 1
            current_ip = [r for r in current_ip if r.destination == '0.0.0.0/0']

        # Отбор: группы (по описанию) и/или конкретные адреса.
        comments_filter = None if ip_comments is None else {c.strip().casefold() for c in ip_comments}
        dest_filter = None if ip_destinations is None else set(ip_destinations)
        imported = []
        for r in data.get('ip_routes', []):
            comment = str(r.get('comment') or '').strip()
            try:
                net = ipaddress.ip_network(str(r.get('destination')), strict=False)
            except ValueError:
                continue
            if net.version != 4 or net.prefixlen == 0:
                continue
            if comments_filter is not None and comment.casefold() not in comments_filter:
                continue
            if dest_filter is not None and str(net) not in dest_filter:
                continue
            dst = iface_map.get(r.get('interface'))
            if dst:
                imported.append((r, str(net), comment, dst))

        removed = set()
        if mode == 'replace':
            # Группа с тем же описанием перезаписывается: адреса, которых нет в файле, удаляются.
            by_comment: Dict[str, set] = {}
            for _, dest, comment, _ in imported:
                if comment:
                    by_comment.setdefault(comment.casefold(), set()).add(dest)
            for c in current_ip:
                keep = by_comment.get(c.comment.strip().casefold())
                if keep is not None and c.destination not in keep and c.index:
                    tail.append({'ip': {'route': {'index': c.index, 'no': True}}})
                    removed.add(c.index)
                    summary['ip_removed'] += 1

        for r, dest, comment, dst in imported:
            same = [c for c in current_ip if c.destination == dest and c.index not in removed]
            on_target = next((c for c in same if c.interface == dst), None)
            if on_target:
                # Маршрут уже есть: дописываем описание, если его не было (в режиме
                # «Заменить» - и если оно отличается).
                if comment and on_target.comment != comment and (mode != 'merge' or not on_target.comment):
                    body.append(ip_route_command(on_target, comment=comment))
                    on_target.comment = comment
                    summary['ip_updated'] += 1
                continue
            if mode == 'merge' and same:
                # Адрес уже направлен через другое подключение - в режиме «Дополнить» не трогаем,
                # но описание всё равно дописываем.
                if comment and not same[0].comment:
                    body.append(ip_route_command(same[0], comment=comment))
                    same[0].comment = comment
                    summary['ip_updated'] += 1
                continue
            if mode == 'replace':
                for c in same:
                    if c.index not in removed:
                        tail.append({'ip': {'route': {'index': c.index, 'no': True}}})
                        removed.add(c.index)
                        summary['ip_removed'] += 1
            route = IpRoute(index='', destination=dest, interface=dst,
                            auto=bool(r.get('auto', True)), reject=bool(r.get('reject', False)),
                            enabled=bool(r.get('enabled', True)), comment=comment)
            body.append(ip_route_command(route))
            current_ip.append(route)
            summary['ip_added'] += 1

    unmapped = [i for i in data.get('interfaces', []) if not iface_map.get(i.get('id'))]
    if unmapped:
        summary['warnings'].append('Без подключения (правила не создаются): '
                                   + ', '.join(i.get('title') or i.get('id') for i in unmapped))

    return {'commands': head + body + tail, 'summary': summary}


def apply_import(client: KeeneticClient, plan: Dict[str, Any]) -> None:
    if plan['commands']:
        client.write(plan['commands'])
