"""
Settings - хранение настроек подключения к роутерам.

Роутеров может быть несколько; один из них активный. Пароль на Windows
шифруется через DPAPI (расшифровать его может только текущий пользователь
Windows на этом компьютере). На других ОС пароль хранится в base64 - файл
settings.json стоит держать в приватной папке.
"""

import base64
import json
import logging
import os
import sys
import threading
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROUTER_DEFAULTS: Dict[str, Any] = {
    'name': '',
    'router_host': '192.168.1.1',
    'router_login': 'admin',
    'router_password': '',
    'default_interface': '',
}
# Прежний формат: один роутер в корне файла. Ключи сохраняем для активного
# роутера, чтобы старая версия программы после отката тоже подключилась.
LEGACY_KEYS = ('router_host', 'router_login', 'router_password', 'default_interface')


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    func = ctypes.windll.crypt32.CryptProtectData if encrypt \
        else ctypes.windll.crypt32.CryptUnprotectData
    if not func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError('DPAPI error')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _protect(secret: str) -> str:
    if not secret:
        return ''
    raw = secret.encode('utf-8')
    if sys.platform == 'win32':
        try:
            return 'dpapi:' + base64.b64encode(_dpapi(raw, True)).decode()
        except OSError:
            logger.warning('DPAPI недоступен, пароль сохранён без шифрования')
    return 'b64:' + base64.b64encode(raw).decode()


def _unprotect(value: str) -> str:
    if not value:
        return ''
    try:
        kind, payload = value.split(':', 1)
        raw = base64.b64decode(payload)
        if kind == 'dpapi':
            raw = _dpapi(raw, False)
        return raw.decode('utf-8')
    except Exception:
        logger.warning('Не удалось расшифровать сохранённый пароль роутера')
        return ''


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


class Settings:
    def __init__(self, path: str = 'settings.json'):
        self.path = path
        self._lock = threading.RLock()
        self._routers: List[Dict[str, Any]] = []
        self._active = ''
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding='utf-8') as f:
                stored = json.load(f)
            items = stored.get('routers')
            if not isinstance(items, list):
                # Настройки прежней версии: один роутер, если пароль был сохранён или адрес изменён.
                items = [{k: stored[k] for k in LEGACY_KEYS if k in stored}] if any(k in stored for k in LEGACY_KEYS) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                router = dict(ROUTER_DEFAULTS)
                router.update({k: item[k] for k in ROUTER_DEFAULTS if k in item and item[k] is not None})
                router['router_password'] = _unprotect(item.get('router_password', ''))
                router['id'] = str(item.get('id') or _new_id())
                self._routers.append(router)
            active = str(stored.get('active_router') or '')
            self._active = active if self._find(active) else (self._routers[0]['id'] if self._routers else '')
        except Exception as e:
            logger.error(f'Ошибка чтения {self.path}: {e}')

    def _save(self):
        routers = []
        for r in self._routers:
            item = {k: r[k] for k in ('id', *ROUTER_DEFAULTS)}
            item['router_password'] = _protect(r['router_password'])
            routers.append(item)
        stored: Dict[str, Any] = {'routers': routers, 'active_router': self._active}
        active = next((item for item in routers if item['id'] == self._active), None)
        if active:
            stored.update({k: active[k] for k in LEGACY_KEYS})
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(stored, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _find(self, router_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return next((r for r in self._routers if r['id'] == router_id), None)

    # --- роутеры ---

    @property
    def active_id(self) -> str:
        return self._active

    def has_router(self, router_id: Optional[str]) -> bool:
        return self._find(router_id) is not None

    def router(self, router_id: Optional[str] = None) -> Dict[str, Any]:
        """Настройки роутера (с паролем); по умолчанию - активного. Пустые значения, если роутеров нет."""
        found = self._find(router_id or self._active)
        return dict(found) if found else dict(ROUTER_DEFAULTS, id='')

    def save_router(self, router_id: Optional[str] = None, **values) -> str:
        """Сохраняет настройки роутера; без router_id создаёт новый и делает его активным."""
        with self._lock:
            router = self._find(router_id) if router_id else None
            if router_id and router is None:
                raise ValueError('Роутер не найден — обновите страницу')
            if router is None:
                router = dict(ROUTER_DEFAULTS, id=_new_id())
                self._routers.append(router)
                self._active = router['id']
            for key, value in values.items():
                if key in ROUTER_DEFAULTS and value is not None:
                    router[key] = value.strip() if isinstance(value, str) and key != 'router_password' else value
            self._save()
            return router['id']

    def select(self, router_id: str) -> None:
        with self._lock:
            if not self._find(router_id):
                raise ValueError('Роутер не найден — обновите страницу')
            self._active = router_id
            self._save()

    def delete_router(self, router_id: str) -> None:
        with self._lock:
            if not self._find(router_id):
                raise ValueError('Роутер не найден — обновите страницу')
            self._routers = [r for r in self._routers if r['id'] != router_id]
            if self._active == router_id:
                self._active = self._routers[0]['id'] if self._routers else ''
            self._save()

    # --- совместимость: ключи активного роутера ---

    def get(self, key: str) -> Any:
        return self.router().get(key, ROUTER_DEFAULTS.get(key))

    def update(self, router_id: Optional[str] = None, **values) -> None:
        """Меняет настройки роутера router_id (по умолчанию активного, создаёт его при отсутствии)."""
        target = router_id if self._find(router_id) else self._active or None
        self.save_router(target, **values)

    def public(self) -> Dict[str, Any]:
        """Настройки для фронтенда - без паролей."""
        active = self.router()
        return {
            'router_host': active['router_host'],
            'router_login': active['router_login'],
            'default_interface': active['default_interface'],
            'name': active['name'],
            'has_password': bool(active['router_password']),
            'active_router': self._active,
            'routers': [{'id': r['id'], 'name': r['name'], 'router_host': r['router_host'],
                         'router_login': r['router_login'], 'has_password': bool(r['router_password'])}
                        for r in self._routers],
        }
