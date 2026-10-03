"""
RouteDeck - запуск как настольного приложения.

Поднимает локальный сервер и показывает интерфейс в собственном окне
программы (pywebview + WebView2). Окно принадлежит RouteDeck.exe, поэтому
на панели задач виден значок RouteDeck, а закрепление запускает именно
программу. Если WebView2 недоступен, интерфейс открывается в режиме
приложения Edge/Chrome. Повторный запуск выводит вперёд уже открытое окно.
"""

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

PREFERRED_PORT = 47815  # постоянный порт, чтобы тема и вкладка запоминались между запусками
from version import APP_NAME, APP_VERSION

WINDOW_TITLE = f'{APP_NAME} {APP_VERSION}'  # версия видна в заголовке окна и на панели задач
APP_ID = 'RouteDeck.Desktop'


def port_is_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def pick_port() -> int:
    if port_is_free(PREFERRED_PORT):
        return PREFERRED_PORT
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def running_instance(port_file: str):
    """Возвращает URL уже запущенной копии RouteDeck, если она есть."""
    try:
        with open(port_file, encoding='utf-8') as f:
            url = f.read().strip()
        with urllib.request.urlopen(url + 'api/router/settings', timeout=1.5) as resp:
            if resp.status == 200:
                return url
    except Exception:
        pass
    return None


def focus_existing_window() -> bool:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if not hwnd:
            return False
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


class ClipboardApi:
    """Доступ к буферу обмена Windows из страницы: window.pywebview.api.*"""

    def restore_window(self) -> None:
        """Повторно показываем окно после возвращения с защищённого экрана UAC."""
        import webview
        from services.capture import winapi
        if webview.windows:
            webview.windows[0].restore()
            webview.windows[0].show()
        winapi.bring_to_front(winapi.find_window(WINDOW_TITLE))

    def clipboard_read(self) -> str:
        CF_UNICODETEXT = 13
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        for _ in range(5):  # буфер может быть ненадолго занят другим приложением
            if user32.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            return ''
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ''
            ptr = kernel32.GlobalLock(handle)
            try:
                return ctypes.wstring_at(ptr) if ptr else ''
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def pick_exe(self) -> str:
        """Окно выбора программы для анализа: путь к .exe или пустая строка."""
        import webview
        dialog = getattr(getattr(webview, 'FileDialog', None), 'OPEN', None) or webview.OPEN_DIALOG
        result = webview.windows[0].create_file_dialog(
            dialog, file_types=('Программы (*.exe)', 'Все файлы (*.*)'))
        return result[0] if result else ''


def find_browser():
    local = os.environ.get('LOCALAPPDATA', '')
    candidates = [
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        os.path.join(local, r'Google\Chrome\Application\chrome.exe'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    ]
    return next((p for p in candidates if p and os.path.exists(p)), None)


def run_browser_fallback(url: str, profile_dir: str):
    """Запасной вариант: окно Edge/Chrome в режиме приложения."""
    browser = find_browser()
    if not browser:
        webbrowser.open(url)
        while True:
            time.sleep(3600)
    subprocess.Popen([browser, f'--app={url}', f'--user-data-dir={profile_dir}',
                      '--window-size=1440,920', '--no-first-run',
                      '--no-default-browser-check']).wait()
    lock = os.path.join(profile_dir, 'lockfile')
    while os.path.exists(lock):
        try:
            os.remove(lock)
        except OSError:
            time.sleep(1.5)


def main():
    try:
        # Свой AppUserModelID - Windows группирует и закрепляет окно как RouteDeck.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    from app import app, DATA_DIR, logger, capture
    from werkzeug.serving import make_server

    port_file = os.path.join(DATA_DIR, 'instance.url')
    if running_instance(port_file):
        focus_existing_window()
        return

    port = pick_port()
    url = f'http://127.0.0.1:{port}/'
    server = make_server('127.0.0.1', port, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with open(port_file, 'w', encoding='utf-8') as f:
        f.write(url)
    logger.info(f'{APP_NAME} (desktop) запущен: {url}')

    try:
        try:
            import webview
            webview.settings['ALLOW_DOWNLOADS'] = True
            webview.create_window(WINDOW_TITLE, url, width=1440, height=920,
                                  min_size=(900, 600), background_color='#0d1117',
                                  js_api=ClipboardApi())
            webview.start(private_mode=False, storage_path=os.path.join(DATA_DIR, 'webview'))
        except Exception as e:
            logger.warning(f'Встроенное окно недоступно ({e}), открываю в браузере')
            run_browser_fallback(url, os.path.join(DATA_DIR, 'window'))
    finally:
        capture.shutdown()  # принять последние события, пока локальный сервер ещё отвечает
        server.shutdown()
        try:
            os.remove(port_file)
        except OSError:
            pass


if __name__ == '__main__':
    if '--capture-agent' in sys.argv:
        # Сборщик для анализа программ: этот же exe, запущенный с правами администратора.
        from services.capture.agent import run as run_capture_agent
        sys.exit(run_capture_agent(sys.argv))
    main()
