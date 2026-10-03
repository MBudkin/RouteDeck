"""
BAT Generator - модуль для генерации bat-файлов для маршрутизации через VPN.
"""

import os
import logging
from typing import Set, Dict, List, Optional
from datetime import datetime

from services.dns_resolver import DNSResolver
from services.subnet_generator import generate_subnets, write_routes_to_file

logger = logging.getLogger(__name__)


class BatGenerator:
    """Класс для генерации bat-файлов."""
    
    def __init__(self, bat_dir: str = "bat_files", all_routes_file: str = "all-routes.bat",
                 dns_resolver: Optional[DNSResolver] = None):
        """
        Инициализация генератора bat-файлов.
        
        Args:
            bat_dir: Директория для bat-файлов
            all_routes_file: Путь к объединённому файлу маршрутов
        """
        self.bat_dir = bat_dir
        self.all_routes_file = all_routes_file
        self.dns_resolver = dns_resolver or DNSResolver()
        
        # Создаём директорию если не существует
        os.makedirs(bat_dir, exist_ok=True)

    def _bat_path_for_domain(self, domain: str) -> str:
        """Возвращает безопасный путь к bat-файлу домена."""
        safe_name = self._safe_bat_filename(domain)
        return os.path.join(self.bat_dir, safe_name)

    def _safe_bat_filename(self, domain: str) -> str:
        """Формирует имя bat-файла без символов путей и Windows-разделителей."""
        import re

        clean = (domain or '').strip().lower().strip('.')
        clean = re.sub(r'[^a-z0-9а-яё._-]+', '_', clean, flags=re.IGNORECASE)
        clean = clean.strip('._-')
        if not clean:
            clean = 'routes'
        return f"{clean}.bat"

    def write_site_routes(self, domain: str, url: str, ip_addresses: Set[str]) -> str:
        """Записывает маршруты сайта и возвращает путь к созданному bat-файлу."""
        bat_file_path = self._bat_path_for_domain(domain)
        self._write_bat_file(bat_file_path, domain, url, ip_addresses)
        return bat_file_path
    
    def generate_for_site(self, domain: str, url: str, merge_all: bool = False,
                          force: bool = False) -> Dict:
        """
        Генерирует bat-файл для одного сайта.
        
        Args:
            domain: Доменное имя
            url: Полный URL
            merge_all: Если True, добавляет маршруты в all-routes.bat
            
        Returns:
            Словарь с результатом генерации
        """
        result = {
            'domain': domain,
            'url': url,
            'success': False,
            'ip_count': 0,
            'ip_addresses': [],
            'bat_file': '',
            'error': None,
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # Получаем все связанные домены
            domains = self._get_all_domains(url)
            domains.add(domain)
            
            # Резолвим домен и связанные домены параллельно.
            ips = set()
            resolved = self.dns_resolver.resolve_multiple_domains(sorted(domains), force=force)
            for domain_ips in resolved.values():
                ips.update(domain_ips)
            
            # Генерируем IP-адреса (только IPv4)
            ip_addresses = generate_subnets(ips)
            result['ip_count'] = len(ip_addresses)
            result['ip_addresses'] = sorted(ip_addresses)
            
            if not ip_addresses:
                result['error'] = "Не удалось получить IP-адреса"
                return result
            
            # Генерируем bat-файл
            bat_file_path = self.write_site_routes(domain, url, ip_addresses)
            
            result['bat_file'] = bat_file_path
            result['success'] = True
            
            # Если нужно, добавляем в all-routes.bat
            if merge_all:
                self._add_to_all_routes(ip_addresses)
            
            logger.info(f"Сгенерирован bat-файл для {domain}: {len(ip_addresses)} IP-адресов")
            
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Ошибка генерации bat-файла для {domain}: {e}")
        
        return result
    
    def generate_for_sites(self, sites: List[Dict], merge_all: bool = False) -> List[Dict]:
        """
        Генерирует bat-файлы для нескольких сайтов.
        
        Args:
            sites: Список словарей [{'domain': 'example.com', 'url': 'https://example.com'}]
            merge_all: Если True, создаёт один объединённый файл
            
        Returns:
            Список результатов генерации для каждого сайта
        """
        results = []
        all_ips = set()
        
        for site in sites:
            domain = site.get('domain')
            url = site.get('url')
            
            if not domain or not url:
                results.append({
                    'domain': domain or 'unknown',
                    'url': url or 'unknown',
                    'success': False,
                    'error': 'Отсутствуют domain или url'
                })
                continue
            
            result = self.generate_for_site(domain, url, merge_all=False)
            results.append(result)
            
            if result['success']:
                all_ips.update(result.get('ip_addresses', []))
        
        # Если нужно, создаём объединённый файл
        if merge_all and all_ips:
            self._write_all_routes(all_ips, sites)
        
        return results
    
    def update_site_ips(self, domain: str, url: str) -> Dict:
        """
        Обновляет IP-адреса для сайта и перезаписывает bat-файл.
        Получает IP только для домена (без извлечения ссылок из HTML).
        
        Args:
            domain: Доменное имя
            url: Полный URL
            
        Returns:
            Словарь с результатом обновления
        """
        result = {
            'domain': domain,
            'url': url,
            'success': False,
            'ip_count': 0,
            'ip_addresses': [],
            'bat_file': '',
            'error': None,
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # Получаем IP-адреса только для домена
            ips = set()
            try:
                ips.update(self.dns_resolver.resolve_domain(domain, force=True))
            except Exception as e:
                logger.warning(f"Ошибка получения IP для {domain}: {e}")
            
            # Генерируем IP-адреса (только IPv4)
            ip_addresses = generate_subnets(ips)
            result['ip_count'] = len(ip_addresses)
            result['ip_addresses'] = sorted(ip_addresses)
            
            if not ip_addresses:
                result['error'] = "Не удалось получить IP-адреса"
                return result
            
            # Генерируем bat-файл
            bat_file_path = self.write_site_routes(domain, url, ip_addresses)
            
            result['bat_file'] = bat_file_path
            result['success'] = True
            
            logger.info(f"Обновлён bat-файл для {domain}: {len(ip_addresses)} IP-адресов")
            
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Ошибка обновления bat-файла для {domain}: {e}")
        
        return result
    
    def update_site_ips_with_html(self, domain: str, url: str) -> Dict:
        """
        Обновляет IP-адреса для сайта с извлечением ссылок из HTML.
        Получает IP для домена и всех связанных доменов из HTML страницы.
        
        Args:
            domain: Доменное имя
            url: Полный URL
            
        Returns:
            Словарь с результатом обновления
        """
        # Перегенерируем bat-файл с извлечением ссылок из HTML
        return self.generate_for_site(domain, url, force=True)

    def bat_filename(self, domain: str) -> str:
        """Имя bat-файла домена (как оно будет сохранено на диске)."""
        return self._safe_bat_filename(domain)
    
    def _get_all_domains(self, url: str) -> Set[str]:
        """Получает все связанные домены из HTML страницы."""
        import requests
        from urllib.parse import urlparse, urljoin
        from bs4 import BeautifulSoup
        import re
        
        domains = set()
        
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            })
            
            response = session.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            tags = soup.find_all(["a", "link", "script", "img", "iframe"])
            for tag in tags:
                attr = "href" if tag.name in ["a", "link"] else "src"
                value = tag.get(attr)
                if value:
                    full_url = urljoin(url, value)
                    domain = self._extract_base_domain(full_url)
                    if domain:
                        domains.add(domain)
                        
        except Exception as e:
            logger.warning(f"Ошибка получения доменов из {url}: {e}")
        
        return domains
    
    def _extract_base_domain(self, url: str) -> Optional[str]:
        """Извлекает базовый домен из URL."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            if domain.startswith("www."):
                domain = domain[4:]
            
            if not domain or "." not in domain:
                return None
            
            return domain
        except Exception:
            return None
    
    def _write_bat_file(self, file_path: str, domain: str, url: str, ip_addresses: Set[str]):
        """Записывает bat-файл только с маршрутами."""
        from services.subnet_generator import format_route_command
        
        with open(file_path, 'w', encoding='utf-8') as f:
            # Только маршруты
            for ip_address in sorted(ip_addresses):
                command = format_route_command(ip_address)
                if command:
                    f.write(command + "\n")
    
    def _write_all_routes(self, ip_addresses: Set[str], sites: List[Dict]):
        """Записывает объединённый файл маршрутов."""
        from services.subnet_generator import format_route_command
        
        # Создаём путь к файлу в папке bat_files
        all_routes_path = os.path.join(self.bat_dir, self.all_routes_file)
        
        with open(all_routes_path, 'w', encoding='utf-8') as f:
            # Только маршруты
            for ip_address in sorted(ip_addresses):
                command = format_route_command(ip_address)
                if command:
                    f.write(command + "\n")
    
    def _add_to_all_routes(self, ip_addresses: Set[str]):
        """Добавляет маршруты в существующий all-routes.bat."""
        all_routes_path = os.path.join(self.bat_dir, self.all_routes_file)
        write_routes_to_file(ip_addresses, all_routes_path, append=True)
    
    def get_bat_files(self) -> List[str]:
        """Возвращает список всех bat-файлов в директории."""
        files = []
        for filename in os.listdir(self.bat_dir):
            if filename.endswith('.bat'):
                files.append(os.path.join(self.bat_dir, filename))
        return sorted(files)
    
    def delete_bat_file(self, domain: str) -> bool:
        """Удаляет bat-файл для указанного домена."""
        bat_file = self._bat_path_for_domain(domain)
        if os.path.exists(bat_file):
            try:
                os.remove(bat_file)
                logger.info(f"Удалён bat-файл: {bat_file}")
                return True
            except Exception as e:
                logger.error(f"Ошибка удаления {bat_file}: {e}")
                return False
        return False
