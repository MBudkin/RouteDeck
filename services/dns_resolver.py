"""
DNS Resolver - модуль для оптимизированного получения IP-адресов доменов.
Использует несколько DNS-серверов для получения всех возможных IP-адресов.
"""

import dns.resolver
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from typing import Set, List, Optional
import time

logger = logging.getLogger(__name__)


class DNSResolver:
    """Класс для оптимизированного DNS резолвинга через несколько серверов."""
    
    DNS_SERVERS = [
        '8.8.8.8',        # Google DNS
        '1.1.1.1',        # Cloudflare DNS
        '208.67.222.222', # OpenDNS
        '9.9.9.9',        # Quad9 DNS
    ]
    
    def __init__(self, max_retries: int = 3, timeout: int = 5, cache_ttl: int = 3600,
                 ipv6: bool = False):
        """
        Инициализация DNS резолвера.
        
        Args:
            max_retries: Максимальное количество попыток для каждого DNS-сервера
            timeout: Таймаут для DNS запросов в секундах
            cache_ttl: Время жизни кэша в секундах
        """
        self.max_retries = max_retries
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.ipv6 = ipv6  # AAAA-записи не нужны: маршруты строятся только для IPv4
        self.cache = {}  # {domain: {'ips': set(), 'timestamp': time}}
    
    def resolve_domain(self, domain: str, force: bool = False) -> Set[str]:
        """
        Получает все IP-адреса домена используя несколько DNS-серверов.
        
        Args:
            domain: Доменное имя
            
        Returns:
            Множество IP-адресов (IPv4 и IPv6)
        """
        domain = (domain or '').strip().lower().rstrip('.')
        if not domain:
            return set()

        # Проверка кэша
        cached = None if force else self._get_from_cache(domain)
        if cached is not None:
            logger.info(f"Использован кэш для {domain}: {len(cached)} IP-адресов")
            return cached
        
        ip_set = set()
        
        # Используем несколько DNS-серверов параллельно
        with ThreadPoolExecutor(max_workers=len(self.DNS_SERVERS)) as executor:
            futures = {
                executor.submit(self._resolve_with_server, domain, server): server
                for server in self.DNS_SERVERS
            }
            
            for future in as_completed(futures):
                server = futures[future]
                try:
                    ips = future.result(timeout=self.timeout)
                    if ips:
                        ip_set.update(ips)
                        logger.debug(f"DNS {server} вернул {len(ips)} IP для {domain}")
                except Exception as e:
                    logger.warning(f"Ошибка DNS {server} для {domain}: {e}")
        
        # Сохраняем в кэш
        self._save_to_cache(domain, ip_set)
        
        logger.info(f"Получено {len(ip_set)} IP-адресов для {domain}")
        return ip_set
    
    def _resolve_with_server(self, domain: str, dns_server: str) -> Set[str]:
        """
        Резолвит домен используя указанный DNS-сервер.
        
        Args:
            domain: Доменное имя
            dns_server: DNS-сервер для запроса
            
        Returns:
            Множество IP-адресов
        """
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [dns_server]
        resolver.timeout = self.timeout
        resolver.lifetime = self.timeout
        
        ip_set = set()
        
        # A записи (IPv4)
        try:
            answer = resolver.resolve(domain, 'A')
            for rdata in answer:
                ip_set.add(str(rdata))
        except Exception:
            pass
        
        # AAAA записи (IPv6)
        try:
            answer = resolver.resolve(domain, 'AAAA')
            for rdata in answer:
                ip_set.add(str(rdata))
        except Exception:
            pass
        
        return ip_set
    
    def _get_from_cache(self, domain: str) -> Optional[Set[str]]:
        """Получает IP-адреса из кэша, если они ещё актуальны."""
        if domain in self.cache:
            cached_data = self.cache[domain]
            age = time.time() - cached_data['timestamp']
            if age < self.cache_ttl:
                return cached_data['ips'].copy()
            else:
                # Удаляем устаревший кэш
                del self.cache[domain]
        return None
    
    def _save_to_cache(self, domain: str, ip_set: Set[str]):
        """Сохраняет IP-адреса в кэш."""
        self.cache[domain] = {
            'ips': ip_set.copy(),
            'timestamp': time.time()
        }
    
    def clear_cache(self):
        """Очищает кэш DNS."""
        self.cache.clear()
        logger.info("DNS кэш очищен")
    
    def resolve_multiple_domains(self, domains: List[str], force: bool = False) -> dict:
        """
        Резолвит несколько доменов параллельно.
        
        Args:
            domains: Список доменных имен
            
        Returns:
            Словарь {domain: set_of_ips}
        """
        result = {}
        domains = [domain for domain in domains if domain]
        if not domains:
            return result
        
        with ThreadPoolExecutor(max_workers=min(len(domains), 10)) as executor:
            futures = {executor.submit(self.resolve_domain, domain, force): domain for domain in domains}
            
            for future in as_completed(futures):
                domain = futures[future]
                try:
                    result[domain] = future.result()
                except Exception as e:
                    logger.error(f"Ошибка резолвинга {domain}: {e}")
                    result[domain] = set()
        
        return result


# Функция для обратной совместимости с существующим кодом
def get_ip_addresses(domains: Set[str]) -> Set[str]:
    """
    Получает IP-адреса для множества доменов (функция обратной совместимости).
    
    Args:
        domains: Множество доменных имен
        
    Returns:
        Множество IP-адресов
    """
    resolver = DNSResolver()
    ip_set = set()
    
    for domain in domains:
        try:
            ips = resolver.resolve_domain(domain)
            ip_set.update(ips)
        except Exception as e:
            logger.error(f"Ошибка получения IP {domain}: {e}")
    
    return ip_set
