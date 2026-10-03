"""
Settings - хранение настроек подключения к роутеру.

Пароль на Windows шифруется через DPAPI (расшифровать его может только
текущий пользователь Windows на этом компьютере). На других ОС пароль
хранится в base64 - файл settings.json стоит держать в приватной папке.
"""

import base64
import json
import logging
import os
import sys
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    'router_host': '192.168.1.1',
    'router_login': 'admin',
    'router_password': '',
    'default_interface': '',
}


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


class Settings:
    def __init__(self, path: str = 'settings.json'):
        self.path = path
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding='utf-8') as f:
                stored = json.load(f)
            for key in DEFAULTS:
                if key in stored:
                    self._data[key] = stored[key]
            self._data['router_password'] = _unprotect(stored.get('router_password', ''))
        except Exception as e:
            logger.error(f'Ошибка чтения {self.path}: {e}')

    def _save(self):
        stored = dict(self._data)
        stored['router_password'] = _protect(self._data['router_password'])
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(stored, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def update(self, **values) -> None:
        with self._lock:
            for key, value in values.items():
                if key in DEFAULTS and value is not None:
                    self._data[key] = value
            self._save()

    def public(self) -> Dict[str, Any]:
        """Настройки для фронтенда - без пароля."""
        data = {k: v for k, v in self._data.items() if k != 'router_password'}
        data['has_password'] = bool(self._data['router_password'])
        return data
