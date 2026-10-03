"""Stable RouteDeck releases: anonymous checks and explicit verified downloads.

No GitHub credentials are read. This module never installs or starts an EXE.
"""
import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

import requests

REPOSITORY = 'MBudkin/RouteDeck'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
API_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
MAX_EXE_SIZE = 150 * 1024 * 1024
MAX_JSON_SIZE = 1024 * 1024
VERSION_RE = re.compile(r'^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$')
DOWNLOAD_HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}


class UpdateError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class AnonymousAuth(requests.auth.AuthBase):
    """Keep proxy/TLS environment support without reading ~/.netrc credentials."""
    def __call__(self, request):
        return request


def version_tuple(value):
    if not isinstance(value, str) or len(value) > 100:
        raise UpdateError('invalid_release', 'GitHub вернул некорректный номер версии.')
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise UpdateError('invalid_release', 'В релизе указан некорректный номер стабильной версии.')
    return tuple(int(match.group(i)) for i in (1, 2, 3))


def validate_url(url, hosts=DOWNLOAD_HOSTS):
    if not isinstance(url, str) or len(url) > 16384:
        raise UpdateError('unsafe_asset', 'Некорректная ссылка на файл обновления.')
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme == 'https' and parsed.hostname in hosts and parsed.port in (None, 443)
    except ValueError:
        valid = False
    if not valid or parsed.username or parsed.password or parsed.fragment or '\\' in url or any(ord(c) < 33 for c in url):
        raise UpdateError('unsafe_asset', 'Ссылка обновления не принадлежит разрешённому HTTPS-адресу GitHub.')
    return parsed


def parse_release(data):
    if not isinstance(data, dict) or data.get('draft') is not False or data.get('prerelease') is not False:
        raise UpdateError('invalid_release', 'GitHub не вернул опубликованный стабильный релиз.')
    tag = data.get('tag_name')
    number = version_tuple(tag)
    page = validate_url(data.get('html_url'), {'github.com'})
    if unquote(page.path) != f'/{REPOSITORY}/releases/tag/{tag}' or page.query:
        raise UpdateError('invalid_release', 'Релиз относится к другому репозиторию.')
    assets = data.get('assets')
    if not isinstance(assets, list):
        raise UpdateError('invalid_release', 'В релизе отсутствует список файлов.')
    exe = [a for a in assets if isinstance(a, dict) and a.get('name') == 'RouteDeck.exe']
    if len(exe) != 1:
        raise UpdateError('invalid_asset', 'В релизе должен быть ровно один файл RouteDeck.exe.')
    asset = exe[0]
    size, digest = asset.get('size'), asset.get('digest')
    if asset.get('state') != 'uploaded' or type(size) is not int or not 2 <= size <= MAX_EXE_SIZE:
        raise UpdateError('invalid_asset', 'Файл обновления ещё не готов или имеет некорректный размер.')
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise UpdateError('invalid_asset', 'GitHub не предоставил SHA-256 файла. Автоматическое скачивание недоступно.')
    url = asset.get('browser_download_url')
    download = validate_url(url, {'github.com'})
    if unquote(download.path) != f'/{REPOSITORY}/releases/download/{tag}/RouteDeck.exe' or download.query:
        raise UpdateError('unsafe_asset', 'Файл обновления относится к другому релизу или репозиторию.')
    notes = data.get('body') or ''
    if not isinstance(notes, str):
        raise UpdateError('invalid_release', 'Некорректное описание релиза.')
    return {'version': '.'.join(map(str, number)), 'tag': tag, 'url': data['html_url'],
            'asset_url': url, 'size': size, 'sha256': digest.split(':', 1)[1].lower(),
            'notes': notes[:12000]}


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class UpdateManager:
    def __init__(self, current_version, data_dir, *, session=None, clock=time.time):
        self.current = version_tuple(current_version)
        self.directory = Path(data_dir).resolve() / 'updates'
        self.session = session if session is not None else requests.Session()
        if session is None: self.session.auth = AnonymousAuth()
        self.clock = clock
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.etag = None
        self.last_attempt = 0
        self.retry_at = 0
        self.state = {'status': 'idle', 'current_version': current_version, 'checked_at': None,
                      'release': None, 'error': None, 'repository_url': RELEASES_URL,
                      'download': {'status': 'idle', 'bytes': 0, 'total': 0, 'error': None}}
        self._check_thread = None
        self._download_thread = None
        self._ready = None

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def check(self, manual=False):
        with self.lock:
            # Downloads use an immutable release snapshot; don't change it mid-download.
            if self.state['status'] == 'checking' or self.state['download']['status'] == 'downloading':
                return self.snapshot()
            now = self.clock()
            if now < self.retry_at or (self.last_attempt and now-self.last_attempt < (60 if manual else 6*3600)):
                return self.snapshot()
            self.last_attempt = now
            self.state['status'] = 'checking'
            self.state['error'] = None
            self._check_thread = threading.Thread(target=self._check_worker, daemon=True, name='release-check')
            self._check_thread.start()
            return self.snapshot()

    def _check_worker(self):
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'RouteDeck/'+self.state['current_version'],
                   'X-GitHub-Api-Version': '2022-11-28'}
        if self.etag: headers['If-None-Match'] = self.etag
        response = None
        try:
            response = self.session.get(API_URL, headers=headers, timeout=(4, 8), stream=True, allow_redirects=False)
            if response.status_code == 304 and self.state['release']:
                release = self.state['release']
            else:
                if response.status_code in (403, 429):
                    now = self.clock()
                    try:
                        reset = float(response.headers.get('X-RateLimit-Reset', now+300))
                        delay = float(response.headers.get('Retry-After', max(60, reset-now)))
                    except (ValueError, TypeError): delay = 300
                    self.retry_at = now + min(3600, max(60, delay))
                    raise UpdateError('rate_limited', 'GitHub временно ограничил запросы. Повторите проверку позже.')
                if response.status_code == 404:
                    raise UpdateError('unavailable', 'Опубликованный стабильный релиз пока недоступен.')
                if response.status_code != 200:
                    raise UpdateError('offline', 'GitHub сейчас недоступен. Работа с роутером не затронута.')
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_JSON_SIZE: raise UpdateError('invalid_release', 'Ответ GitHub слишком большой.')
                try: metadata = json.loads(raw)
                except (ValueError, UnicodeError): raise UpdateError('invalid_release', 'GitHub вернул некорректный ответ.')
                release = parse_release(metadata)
                self.etag = response.headers.get('ETag')
            with self.lock:
                if self.state['release'] and self.state['release']['sha256'] != release['sha256']:
                    self._ready = None
                    self.state['download'] = {'status':'idle', 'bytes':0, 'total':0, 'error':None}
                self.state.update(status='available' if version_tuple(release['version']) > self.current else 'current',
                                  release=release, error=None)
        except requests.RequestException:
            self._failure('offline', 'Нет связи с GitHub. Проверить обновления можно позже.')
        except UpdateError as error:
            self._failure(error.code, str(error))
        except Exception:
            self._failure('invalid_release', 'Не удалось проверить ответ GitHub. Попробуйте позже.')
        finally:
            if response is not None: response.close()
            with self.lock:
                self.state['checked_at'] = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()

    def _failure(self, code, message):
        with self.lock:
            # Keep a previously validated update visible during temporary network failures.
            self.state['status'] = 'available' if self.state['release'] and version_tuple(self.state['release']['version']) > self.current else 'unavailable'
            self.state['error'] = {'code': code, 'message': message}

    def download(self):
        with self.lock:
            if self.state['download']['status'] == 'downloading': return self.snapshot()
            if self.state['status'] == 'checking':
                raise UpdateError('checking', 'Дождитесь завершения проверки обновлений.')
            release = copy.deepcopy(self.state['release'])
            if not release or version_tuple(release['version']) <= self.current:
                raise UpdateError('not_available', 'Новая версия ещё не найдена. Сначала проверьте обновления.')
            self.cancel.clear()
            self._ready = None
            self.state['download'] = {'status':'downloading', 'bytes':0, 'total':release['size'], 'error':None}
            self._download_thread = threading.Thread(target=self._download_worker, args=(release,), daemon=True, name='release-download')
            self._download_thread.start()
            return self.snapshot()

    def cancel_download(self):
        self.cancel.set()
        return self.snapshot()

    def _asset_response(self, url):
        for _ in range(6):
            parsed = validate_url(url)
            if parsed.hostname == 'github.com' and not unquote(parsed.path).startswith(f'/{REPOSITORY}/releases/download/'):
                raise UpdateError('unsafe_asset', 'Недопустимое перенаправление файла обновления.')
            if self.cancel.is_set(): raise UpdateError('cancelled', 'Скачивание отменено.')
            response = self.session.get(url, headers={'User-Agent':'RouteDeck/'+self.state['current_version']},
                                        timeout=(5, 15), stream=True, allow_redirects=False)
            if response.status_code in (301,302,303,307,308):
                next_url = urljoin(url, response.headers.get('Location',''))
                response.close()
                if not next_url or next_url == url: raise UpdateError('unsafe_asset', 'Некорректное перенаправление GitHub.')
                validate_url(next_url)
                url = next_url
                continue
            if response.status_code != 200:
                response.close()
                raise UpdateError('download_error', 'Не удалось скачать файл с GitHub. Повторите позже.')
            return response
        raise UpdateError('unsafe_asset', 'Слишком много перенаправлений GitHub.')

    def _download_worker(self, release):
        temporary = None
        response = None
        try:
            target_dir = self.directory / release['version']
            target_dir.mkdir(parents=True, exist_ok=True)
            response = self._asset_response(release['asset_url'])
            declared = response.headers.get('Content-Length')
            if declared is not None:
                try: correct_size = int(declared) == release['size']
                except (ValueError,TypeError): correct_size = False
                if not correct_size: raise UpdateError('invalid_asset', 'Размер загрузки не совпадает с релизом GitHub.')
            digest = hashlib.sha256()
            count = 0
            prefix = bytearray()
            with tempfile.NamedTemporaryFile(dir=target_dir, prefix='download-', suffix='.part', delete=False) as file:
                temporary = Path(file.name)
                for chunk in response.iter_content(256*1024):
                    if self.cancel.is_set(): raise UpdateError('cancelled', 'Скачивание отменено.')
                    if not chunk: continue
                    count += len(chunk)
                    if count > release['size']: raise UpdateError('invalid_asset', 'Загрузка превышает ожидаемый размер.')
                    if len(prefix)<2: prefix.extend(chunk[:2-len(prefix)])
                    file.write(chunk)
                    digest.update(chunk)
                    with self.lock: self.state['download']['bytes'] = count
                file.flush()
                os.fsync(file.fileno())
            if self.cancel.is_set(): raise UpdateError('cancelled', 'Скачивание отменено.')
            if count != release['size'] or digest.hexdigest() != release['sha256'] or bytes(prefix) != b'MZ':
                raise UpdateError('checksum', 'Файл не прошёл проверку размера, формата или SHA-256. Он не будет запущен.')
            target = target_dir/'RouteDeck.exe'
            os.replace(temporary, target)
            temporary = None
            with self.lock:
                self._ready = {'path':target, 'release':release}
                self.state['download'].update(status='ready', bytes=count, error=None)
        except requests.RequestException:
            self._download_failure('download_error', 'Загрузка прервана. Можно скачать файл заново.')
        except UpdateError as error:
            self._download_failure(error.code, str(error))
        except OSError:
            self._download_failure('disk', 'Не удалось сохранить обновление. Проверьте свободное место и доступ к папке данных.')
        except Exception:
            self._download_failure('download_error', 'Не удалось завершить загрузку. Повторите позже.')
        finally:
            if response is not None: response.close()
            if temporary is not None: temporary.unlink(missing_ok=True)

    def _download_failure(self, code, message):
        with self.lock:
            self._ready = None
            self.state['download'].update(status='cancelled' if code=='cancelled' else 'error', error={'code':code,'message':message})

    def verified_file(self):
        with self.lock:
            ready = copy.copy(self._ready)
        if not ready or self.state['download']['status'] != 'ready':
            raise UpdateError('not_ready', 'Сначала скачайте и проверьте обновление.')
        path, release = ready['path'], ready['release']
        try:
            valid = path.is_file() and path.stat().st_size == release['size'] and file_sha256(path) == release['sha256']
        except OSError: valid = False
        if not valid:
            self._download_failure('checksum', 'Скачанный файл изменён или недоступен. Скачайте обновление заново.')
            raise UpdateError('checksum', 'Скачанный файл изменён или недоступен. Скачайте обновление заново.')
        return path, release
