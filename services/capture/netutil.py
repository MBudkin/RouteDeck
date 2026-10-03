"""Вспомогательные функции: публичные адреса, основной домен."""

import ipaddress
from functools import lru_cache

# Суффиксы, под которыми регистрируют домены третьего уровня (example.co.uk).
_MULTI_SUFFIXES = {
    'co.uk', 'org.uk', 'ac.uk', 'gov.uk', 'me.uk', 'net.uk', 'com.au', 'net.au', 'org.au',
    'com.br', 'net.br', 'com.cn', 'net.cn', 'org.cn', 'co.jp', 'ne.jp', 'or.jp', 'co.kr',
    'co.in', 'co.id', 'co.il', 'co.nz', 'co.za', 'com.tr', 'com.ua', 'org.ua', 'net.ua',
    'com.ru', 'msk.ru', 'spb.ru', 'net.ru', 'org.ru', 'pp.ru', 'com.kz', 'com.by',
    'com.mx', 'com.ar', 'com.sg', 'com.hk', 'com.tw', 'com.vn', 'com.pl', 'com.es',
}

# Общие облачные домены: у каждого клиента свой поддомен, и весь amazonaws.com
# в список добавлять незачем - основным считаем на уровень глубже.
_SHARED_SUFFIXES = {
    'amazonaws.com', 'cloudfront.net', 'azureedge.net', 'azurefd.net', 'azurewebsites.net',
    'blob.core.windows.net', 'trafficmanager.net', 'cloudapp.net', 'cloudapp.azure.com',
    'akamaized.net', 'akamaiedge.net', 'akamai.net', 'edgekey.net', 'edgesuite.net',
    'fastly.net', 'fastlylb.net', 'cdn.cloudflare.net', 'googleusercontent.com',
    'githubusercontent.com', 'appspot.com', 'herokuapp.com', 'web.app', 'firebaseapp.com',
    'vercel.app', 'netlify.app', 'pages.dev', 'workers.dev', 'r2.dev', 'b-cdn.net',
}


@lru_cache(maxsize=8192)
def is_public(ip: str) -> bool:
    """Адрес из интернета: не локальный, не служебный, не мультикаст."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def normalize_ip(ip: str) -> str:
    """::ffff:1.2.3.4 -> 1.2.3.4"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6 and addr.ipv4_mapped:
        return str(addr.ipv4_mapped)
    return str(addr)


@lru_cache(maxsize=8192)
def base_domain(name: str) -> str:
    """Домен, который стоит добавить в DNS-список: api.discord.gg -> discord.gg."""
    name = name.lower().rstrip('.')
    labels = name.split('.')
    if len(labels) <= 2:
        return name
    for suffix in _SHARED_SUFFIXES:
        if name.endswith('.' + suffix):
            depth = suffix.count('.') + 2
            return '.'.join(labels[-depth:]) if len(labels) >= depth else name
    depth = 3 if '.'.join(labels[-2:]) in _MULTI_SUFFIXES else 2
    return '.'.join(labels[-depth:])


def valid_domain(name: str) -> bool:
    if not name or len(name) > 253 or '.' not in name:
        return False
    if name.endswith(('.local', '.lan', '.home', '.arpa', '.localdomain', '.internal')):
        return False
    try:
        ipaddress.ip_address(name)
        return False
    except ValueError:
        return all(part and len(part) <= 63 for part in name.split('.'))
