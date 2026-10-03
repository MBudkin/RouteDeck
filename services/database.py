"""
Database module - модуль для работы с SQLite базой данных сайтов.
"""

import sqlite3
import json
import logging
from typing import List, Dict, Optional, Set
from datetime import datetime
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class Database:
    """Класс для работы с базой данных сайтов."""
    
    def __init__(self, db_path: str = "sites.db"):
        """
        Инициализация подключения к базе данных.
        
        Args:
            db_path: Путь к файлу базы данных SQLite
        """
        self.db_path = db_path
        self._init_db()
    
    @contextmanager
    def _get_connection(self):
        """Контекстный менеджер для подключения к базе данных."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка базы данных: {e}")
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Инициализирует структуру базы данных."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Проверяем, нужно ли выполнить миграцию
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sites'")
            table_exists = cursor.fetchone()
            
            if table_exists:
                # Проверяем, есть ли ограничение UNIQUE на поле domain
                cursor.execute("PRAGMA table_info(sites)")
                columns = cursor.fetchall()
                
                # Проверяем структуру таблицы
                cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sites'")
                table_sql = cursor.fetchone()
                
                # Если в SQL есть "domain TEXT NOT NULL UNIQUE", значит нужно мигрировать
                if table_sql and 'domain TEXT NOT NULL UNIQUE' in table_sql[0]:
                    logger.info("Обнаружена старая структура базы данных, выполняется миграция...")
                    self._migrate_database(conn)
                    return
            
            # Таблица сайтов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_ip_update TIMESTAMP,
                    ip_addresses TEXT,
                    subnets TEXT,
                    is_active BOOLEAN DEFAULT 1
                )
            """)
            
            # Таблица bat-файлов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bat_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER,
                    filename TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (site_id) REFERENCES sites(id)
                )
            """)
            
            logger.info("База данных инициализирована")
    
    def _migrate_database(self, conn):
        """
        Выполняет миграцию базы данных с UNIQUE на domain на UNIQUE на url.
        """
        cursor = conn.cursor()
        
        try:
            # Создаем временную таблицу с новой структурой
            cursor.execute("""
                CREATE TABLE sites_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_ip_update TIMESTAMP,
                    ip_addresses TEXT,
                    subnets TEXT,
                    is_active BOOLEAN DEFAULT 1
                )
            """)
            
            # Копируем данные из старой таблицы в новую
            cursor.execute("""
                INSERT INTO sites_new (id, domain, url, created_at, updated_at, last_ip_update, ip_addresses, subnets, is_active)
                SELECT id, domain, url, created_at, updated_at, last_ip_update, ip_addresses, subnets, is_active
                FROM sites
            """)
            
            # Удаляем старую таблицу
            cursor.execute("DROP TABLE sites")
            
            # Переименовываем новую таблицу
            cursor.execute("ALTER TABLE sites_new RENAME TO sites")
            
            # Пересоздаем таблицу bat-файлов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bat_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER,
                    filename TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (site_id) REFERENCES sites(id)
                )
            """)
            
            logger.info("Миграция базы данных завершена успешно")
            
        except Exception as e:
            logger.error(f"Ошибка миграции базы данных: {e}")
            # В случае ошибки удаляем временную таблицу
            try:
                cursor.execute("DROP TABLE IF EXISTS sites_new")
            except:
                pass
            raise
    
    def add_site(self, domain: str, url: str, ip_addresses: Optional[Set[str]] = None, 
                 subnets: Optional[Set[str]] = None) -> Optional[int]:
        """
        Добавляет новый сайт в базу данных.
        
        Args:
            domain: Доменное имя
            url: Полный URL
            ip_addresses: Множество IP-адресов (опционально)
            subnets: Множество подсетей (опционально)
            
        Returns:
            ID добавленного сайта или None при ошибке
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                ip_json = json.dumps(list(ip_addresses)) if ip_addresses else None
                subnets_json = json.dumps(list(subnets)) if subnets else None
                
                cursor.execute("""
                    INSERT INTO sites (domain, url, ip_addresses, subnets, last_ip_update)
                    VALUES (?, ?, ?, ?, ?)
                """, (domain, url, ip_json, subnets_json, datetime.now().isoformat()))
                
                site_id = cursor.lastrowid
                logger.info(f"Добавлен сайт: {domain} (ID: {site_id})")
                return site_id
                
        except sqlite3.IntegrityError:
            logger.warning(f"Сайт {domain} уже существует")
            return None
        except Exception as e:
            logger.error(f"Ошибка добавления сайта {domain}: {e}")
            return None
    
    def get_site(self, site_id: int) -> Optional[Dict]:
        """
        Получает информацию о сайте по ID.
        
        Args:
            site_id: ID сайта
            
        Returns:
            Словарь с информацией о сайте или None
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM sites WHERE id = ?", (site_id,))
                row = cursor.fetchone()
                
                if row:
                    return self._row_to_dict(row)
                return None
                
        except Exception as e:
            logger.error(f"Ошибка получения сайта {site_id}: {e}")
            return None
    
    def get_site_by_domain(self, domain: str) -> Optional[Dict]:
        """
        Получает информацию о сайте по домену.
        
        Args:
            domain: Доменное имя
            
        Returns:
            Словарь с информацией о сайте или None
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM sites WHERE domain = ?", (domain,))
                row = cursor.fetchone()
                
                if row:
                    return self._row_to_dict(row)
                return None
                
        except Exception as e:
            logger.error(f"Ошибка получения сайта {domain}: {e}")
            return None
    
    def get_site_by_url(self, url: str) -> Optional[Dict]:
        """
        Получает информацию о сайте по полному URL.
        
        Args:
            url: Полный URL
            
        Returns:
            Словарь с информацией о сайте или None
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM sites WHERE url = ?", (url,))
                row = cursor.fetchone()
                
                if row:
                    return self._row_to_dict(row)
                return None
                
        except Exception as e:
            logger.error(f"Ошибка получения сайта по URL {url}: {e}")
            return None
    
    def get_all_sites(self, active_only: bool = True) -> List[Dict]:
        """
        Получает список всех сайтов.
        
        Args:
            active_only: Если True, возвращает только активные сайты
            
        Returns:
            Список словарей с информацией о сайтах
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                if active_only:
                    cursor.execute("SELECT * FROM sites WHERE is_active = 1 ORDER BY domain")
                else:
                    cursor.execute("SELECT * FROM sites ORDER BY domain")
                
                rows = cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"Ошибка получения списка сайтов: {e}")
            return []
    
    def get_sites_by_ids(self, site_ids: List[int]) -> List[Dict]:
        """
        Получает список сайтов по их ID.
        
        Args:
            site_ids: Список ID сайтов
            
        Returns:
            Список словарей с информацией о сайтах
        """
        if not site_ids:
            return []
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                placeholders = ','.join('?' * len(site_ids))
                cursor.execute(f"SELECT * FROM sites WHERE id IN ({placeholders})", site_ids)
                
                rows = cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"Ошибка получения сайтов по ID: {e}")
            return []
    
    def update_site_ips(self, site_id: int, ip_addresses: Set[str], subnets: Set[str]) -> bool:
        """
        Заменяет IP-адреса сайта актуальным набором и обновляет подсети сайта.
        
        Args:
            site_id: ID сайта
            ip_addresses: Актуальное множество IP-адресов
            subnets: Новое множество подсетей
            
        Returns:
            True при успехе, False при ошибке
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                ip_json = json.dumps(sorted(ip_addresses))
                subnets_json = json.dumps(sorted(subnets))
                
                cursor.execute("""
                    UPDATE sites
                    SET ip_addresses = ?, subnets = ?, last_ip_update = ?, updated_at = ?
                    WHERE id = ?
                """, (ip_json, subnets_json, datetime.now().isoformat(), datetime.now().isoformat(), site_id))
                
                logger.info(f"Обновлено {len(ip_addresses)} IP для сайта ID {site_id}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка обновления IP для сайта {site_id}: {e}")
            return False
    
    def update_site(self, site_id: int, **kwargs) -> bool:
        """
        Обновляет информацию о сайте.
        
        Args:
            site_id: ID сайта
            **kwargs: Поля для обновления (url, is_active и т.д.)
            
        Returns:
            True при успехе, False при ошибке
        """
        if not kwargs:
            return False
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Добавляем updated_at
                kwargs['updated_at'] = datetime.now().isoformat()
                
                # Формируем SQL запрос
                set_clause = ', '.join([f"{k} = ?" for k in kwargs.keys()])
                values = list(kwargs.values()) + [site_id]
                
                cursor.execute(f"UPDATE sites SET {set_clause} WHERE id = ?", values)
                
                logger.info(f"Обновлён сайт ID {site_id}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка обновления сайта {site_id}: {e}")
            return False
    
    def delete_sites(self, site_ids: List[int]) -> int:
        """
        Полностью удаляет сайты из базы данных.
        
        Args:
            site_ids: Список ID сайтов для удаления
            
        Returns:
            Количество удалённых сайтов
        """
        if not site_ids:
            return 0
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                placeholders = ','.join('?' * len(site_ids))
                cursor.execute(
                    f"DELETE FROM sites WHERE id IN ({placeholders})",
                    site_ids
                )
                
                deleted = cursor.rowcount
                logger.info(f"Полностью удалено {deleted} сайтов")
                return deleted
                
        except Exception as e:
            logger.error(f"Ошибка удаления сайтов: {e}")
            return 0
    

    
    def add_bat_file(self, site_id: int, filename: str) -> Optional[int]:
        """
        Добавляет запись о bat-файле.
        
        Args:
            site_id: ID сайта
            filename: Имя файла
            
        Returns:
            ID записи или None при ошибке
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO bat_files (site_id, filename)
                    VALUES (?, ?)
                """, (site_id, filename))
                
                return cursor.lastrowid
                
        except Exception as e:
            logger.error(f"Ошибка добавления bat-файла: {e}")
            return None
    
    def get_bat_files(self, site_id: Optional[int] = None) -> List[Dict]:
        """
        Получает список bat-файлов.
        
        Args:
            site_id: Если указан, возвращает файлы только для этого сайта
            
        Returns:
            Список словарей с информацией о bat-файлах
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                if site_id:
                    cursor.execute("SELECT * FROM bat_files WHERE site_id = ?", (site_id,))
                else:
                    cursor.execute("SELECT * FROM bat_files ORDER BY created_at DESC")
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"Ошибка получения bat-файлов: {e}")
            return []
    
    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        """Преобразует строку базы данных в словарь."""
        result = dict(row)
        
        # Парсим JSON поля
        if result.get('ip_addresses'):
            try:
                result['ip_addresses'] = json.loads(result['ip_addresses'])
            except:
                result['ip_addresses'] = []
        else:
            result['ip_addresses'] = []
        
        if result.get('subnets'):
            try:
                result['subnets'] = json.loads(result['subnets'])
            except:
                result['subnets'] = []
        else:
            result['subnets'] = []
        
        # Конвертируем is_active в булево значение
        if 'is_active' in result:
            result['is_active'] = bool(result['is_active'])
        
        return result
    
    def get_stats(self) -> Dict:
        """
        Получает статистику базы данных.
        
        Returns:
            Словарь со статистикой
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT COUNT(*) FROM sites")
                total_sites = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM sites WHERE is_active = 1")
                active_sites = cursor.fetchone()[0]
                
                # Количество bat-файлов
                cursor.execute("SELECT COUNT(*) FROM bat_files")
                bat_files_count = cursor.fetchone()[0]
                
                return {
                    'total_sites': total_sites,
                    'active_sites': active_sites,
                    'bat_files': bat_files_count
                }
                
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}")
            return {
                'total_sites': 0,
                'active_sites': 0,
                'bat_files': 0
            }
