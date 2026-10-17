"""
Экспорт адресов в список раздельного туннелирования AmneziaVPN
(«Раздельное туннелирование» -> «Сайты» -> «Импорт»).

Формат - JSON-массив объектов, как его читает AmneziaVPN
(IpSplitTunnelingController::importSitesFromJson):
    [{"hostname": "91.108.4.0/22", "ip": ""},
     {"hostname": "telegram.org", "ip": "149.154.167.99", "ips": ["149.154.167.99", ...]}]

Особенности клиента, которые учитывает экспорт:
- hostname - домен или IPv4-адрес/подсеть. IPv6 Amnezia не принимает,
  строки без точки пропускает;
- при импорте домены не резолвятся. С AmneziaWG/WireGuard и на Android/iOS
  в туннель попадают только подсети и сохранённые IP доменов, поэтому
  IP доменов RouteDeck находит сам. «ips» понимает AmneziaVPN 5.x, «ip»
  (один адрес) - и прежние версии;
- запись домена в Amnezia относится только к самому имени, без поддоменов,
  в отличие от списков Keenetic. Поэтому к IP домена добавляются IP www.<домен>.
"""

import ipaddress
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterable, List, Optional

from services.keenetic import is_routable, normalize_entry

Resolver = Callable[[str], Iterable[str]]


def _ipv4_network(value: str) -> Optional[ipaddress.IPv4Network]:
    try:
        net = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None
    return net if net.version == 4 else None


def _domain_ips(domain: str, resolve: Resolver) -> List[str]:
    ips = set()
    for name in (domain, f'www.{domain}'):
        try:
            found = resolve(name) or ()
        except Exception:
            continue
        for ip in found:
            net = _ipv4_network(str(ip))
            if net and net.prefixlen == 32 and is_routable(net):
                ips.add(net.network_address)
    return [str(ip) for ip in sorted(ips)]


def build_sites(entries: Iterable[str], resolve: Optional[Resolver] = None,
                workers: int = 16) -> Dict:
    """
    Список записей (домены, IPv4, подсети) -> {'sites': [...], 'summary': {...}}.
    resolve(domain) возвращает IP домена; без него домены выгружаются без IP.
    """
    networks = set()
    domains = set()
    summary = {'networks': 0, 'domains': 0, 'resolved': 0, 'unresolved': 0,
               'skipped_ipv6': 0, 'skipped_local': 0, 'skipped_invalid': 0}

    for raw in entries:
        raw = str(raw or '').strip()
        if not raw:
            continue
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            net = None
        if net is not None:
            if net.version != 4:
                summary['skipped_ipv6'] += 1
            elif net.prefixlen == 0 or not is_routable(net):
                summary['skipped_local'] += 1  # маршрут по умолчанию и домашняя сеть
            else:
                networks.add(net)
            continue
        domain = normalize_entry(raw)
        if domain and _ipv4_network(domain) is None:
            domains.add(domain)
        else:
            summary['skipped_invalid'] += 1

    sites = [{'hostname': str(net), 'ip': ''}
             for net in sorted(networks, key=lambda n: (int(n.network_address), n.prefixlen))]

    ordered = sorted(domains)
    resolved: Dict[str, List[str]] = {}
    if resolve and ordered:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(ordered)))) as pool:
            resolved = dict(zip(ordered, pool.map(lambda d: _domain_ips(d, resolve), ordered)))

    for domain in ordered:
        ips = resolved.get(domain) or []
        site = {'hostname': domain, 'ip': ips[0] if ips else ''}
        if resolve:
            site['ips'] = ips
            summary['resolved' if ips else 'unresolved'] += 1
        sites.append(site)

    summary['networks'] = len(networks)
    summary['domains'] = len(domains)
    return {'sites': sites, 'summary': summary}

