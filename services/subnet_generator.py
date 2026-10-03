"""
Subnet Generator - модуль для генерации подсетей из IP-адресов.
"""

import ipaddress
import logging
from typing import Set

logger = logging.getLogger(__name__)


def generate_subnets(ip_set: Set[str], ipv4_prefix: int = 32) -> Set[str]:
    """
    Генерирует подсети из множества IP-адресов.
    Только IPv4 адреса с маской /32 (один IP-адрес).
    
    Args:
        ip_set: Множество IP-адресов
        ipv4_prefix: Префикс для IPv4 подсетей (по умолчанию /32)
        
    Returns:
        Множество IPv4 адресов (IPv6 игнорируется)
    """
    ipv4_addresses = set()
    
    for ip in ip_set:
        try:
            ip_obj = ipaddress.ip_address(ip)
            
            if isinstance(ip_obj, ipaddress.IPv4Address):
                # IPv4: добавляем только IPv4 адреса
                ipv4_addresses.add(ip)
                logger.debug(f"IPv4 {ip}")
            else:
                # IPv6: игнорируем
                logger.debug(f"IPv6 {ip} - игнорируется")
                
        except ValueError as e:
            logger.error(f"Ошибка обработки IP {ip}: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка для IP {ip}: {e}")
    
    logger.info(f"Сгенерировано {len(ipv4_addresses)} IPv4 адресов из {len(ip_set)} IP-адресов")
    return ipv4_addresses


def format_route_command(ip_address: str) -> str:
    """
    Форматирует IP-адрес в команду route для Windows Keenetic.
    
    Args:
        ip_address: IPv4 адрес (например, "192.168.1.1")
        
    Returns:
        Строка команды route
    """
    try:
        ip_obj = ipaddress.ip_address(ip_address)
        
        if isinstance(ip_obj, ipaddress.IPv4Address):
            # IPv4 команда для Windows Keenetic с маской /32
            return f"route add {ip_address} mask 255.255.255.255 0.0.0.0"
        else:
            # IPv6 не поддерживается
            logger.warning(f"IPv6 адрес {ip_address} не поддерживается")
            return ""
            
    except Exception as e:
        logger.error(f"Ошибка форматирования IP-адреса {ip_address}: {e}")
        return ""


def parse_existing_routes(output_file: str) -> Set[str]:
    """
    Парсит существующий bat-файл и возвращает множество IP-адресов.
    
    Args:
        output_file: Путь к bat-файлу
        
    Returns:
        Множество IPv4 адресов
    """
    import re
    import os
    
    existing = set()
    
    if not os.path.exists(output_file):
        return existing
    
    try:
        with open(output_file, "r", encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.lower().startswith("pause") or line.lower().startswith("rem"):
                    continue
                
                # Парсим IPv4: route add 109.95.210.132 mask 255.255.255.255 0.0.0.0
                match_ipv4 = re.match(
                    r"^route\s+add\s+(\d+\.\d+\.\d+\.\d+)\s+mask\s+\d+\.\d+\.\d+\.\d+\s+0\.0\.0\.0$",
                    line,
                    re.IGNORECASE,
                )
                if match_ipv4:
                    ip = match_ipv4.group(1)
                    existing.add(ip)
                    
    except Exception as e:
        logger.error(f"Ошибка чтения {output_file}: {e}")
    
    return existing


def write_routes_to_file(ip_addresses: Set[str], output_file: str, append: bool = True) -> int:
    """
    Записывает IP-адреса в bat-файл.
    
    Args:
        ip_addresses: Множество IP-адресов для записи
        output_file: Путь к выходному файлу
        append: Если True, добавляет к существующему файлу, иначе перезаписывает
        
    Returns:
        Количество добавленных IP-адресов
    """
    import os
    
    existing = set()
    if append and os.path.exists(output_file):
        existing = parse_existing_routes(output_file)
    
    new = ip_addresses - existing
    
    if not new:
        logger.info(f"Нет новых IP-адресов для {output_file}")
        return 0
    
    try:
        mode = "a" if append else "w"
        with open(output_file, mode, encoding='utf-8') as f:
            for ip_address in sorted(new):
                command = format_route_command(ip_address)
                if command:
                    f.write(command + "\n")
        
        logger.info(f"Добавлено {len(new)} IP-адресов в {output_file}")
        return len(new)
        
    except Exception as e:
        logger.error(f"Ошибка записи {output_file}: {e}")
        return 0
