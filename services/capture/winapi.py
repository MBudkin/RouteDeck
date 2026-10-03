"""
Обёртки над Windows API для анализа программ: список процессов, кэш DNS,
описание exe-файла, запуск с правами администратора.
"""

import ctypes
import ipaddress
import os
import string
from ctypes import wintypes as wt
from dataclasses import dataclass
from typing import Dict, Optional, Set

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
dnsapi = ctypes.WinDLL('dnsapi')
shell32 = ctypes.WinDLL('shell32', use_last_error=True)
version = ctypes.WinDLL('version')

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x2
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


# ============================================================ процессы

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wt.DWORD), ('cntUsage', wt.DWORD), ('th32ProcessID', wt.DWORD),
        ('th32DefaultHeapID', ctypes.c_void_p), ('th32ModuleID', wt.DWORD),
        ('cntThreads', wt.DWORD), ('th32ParentProcessID', wt.DWORD),
        ('pcPriClassBase', wt.LONG), ('dwFlags', wt.DWORD), ('szExeFile', wt.WCHAR * 260),
    ]


kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(ctypes.c_uint64)] * 4
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.QueryDosDeviceW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD]


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    name: str
    exe: str = ''
    created: int = 0       # FILETIME запуска (0 - неизвестно)

    def to_dict(self) -> Dict:
        return {'pid': self.pid, 'ppid': self.ppid, 'name': self.name, 'exe': self.exe, 'created': self.created}


def _snapshot():
    """[(pid, ppid, имя exe)] всех процессов - быстро, одним вызовом."""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    result = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            result.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return result


def process_details(pid: int):
    """(полный путь exe, время запуска) или ('', 0), если процесс недоступен."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return '', 0
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        exe = buf.value if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else ''
        created, t1, t2, t3 = (ctypes.c_uint64() for _ in range(4))
        ok = kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(t1), ctypes.byref(t2), ctypes.byref(t3))
        return exe, created.value if ok else 0
    finally:
        kernel32.CloseHandle(handle)


class ProcessTable:
    """Снимок процессов с кэшем путей: путь exe запрашивается один раз на процесс."""

    def __init__(self):
        self.procs: Dict[int, ProcInfo] = {}

    def refresh(self) -> Dict[int, ProcInfo]:
        fresh = {}
        for pid, ppid, name in _snapshot():
            old = self.procs.get(pid)
            if old and old.name == name and old.ppid == ppid:
                fresh[pid] = old
                continue
            exe, created = process_details(pid) if pid > 4 else ('', 0)
            fresh[pid] = ProcInfo(pid, ppid, name, exe, created)
        self.procs = fresh
        return fresh


def process_alive(pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wt.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


_dos_devices: Optional[Dict[str, str]] = None


def dos_path(path: str) -> str:
    r"""\Device\HarddiskVolume3\Users\... -> C:\Users\..."""
    global _dos_devices
    if not path or not path.startswith('\\Device\\'):
        return path
    if _dos_devices is None:
        _dos_devices = {}
        buf = ctypes.create_unicode_buffer(1024)
        for letter in string.ascii_uppercase:
            if kernel32.QueryDosDeviceW(f'{letter}:', buf, 1024):
                _dos_devices[buf.value.lower()] = f'{letter}:'
    low = path.lower()
    for device, drive in _dos_devices.items():
        if low.startswith(device + '\\'):
            return drive + path[len(device):]
    return path


# ============================================================ описание exe

version.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
version.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p]
version.VerQueryValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wt.UINT)]

_descriptions: Dict[str, str] = {}


def file_description(path: str) -> str:
    """Название программы из свойств exe («Visual Studio Code»), иначе имя файла."""
    if not path:
        return ''
    if path in _descriptions:
        return _descriptions[path]
    title = ''
    try:
        size = version.GetFileVersionInfoSizeW(path, None)
        if size:
            data = ctypes.create_string_buffer(size)
            if version.GetFileVersionInfoW(path, 0, size, data):
                ptr, length = ctypes.c_void_p(), wt.UINT()
                langs = []
                if version.VerQueryValueW(data, r'\VarFileInfo\Translation', ctypes.byref(ptr), ctypes.byref(length)) and length.value >= 4:
                    words = ctypes.cast(ptr, ctypes.POINTER(wt.WORD))
                    langs.append(f'{words[0]:04x}{words[1]:04x}')
                langs += ['040904b0', '040904e4', '041904b0']
                for lang in langs:
                    for key in ('FileDescription', 'ProductName'):
                        if version.VerQueryValueW(data, f'\\StringFileInfo\\{lang}\\{key}', ctypes.byref(ptr), ctypes.byref(length)) and length.value > 1:
                            title = ctypes.wstring_at(ptr, length.value - 1).strip()
                            if title:
                                break
                    if title:
                        break
    except Exception:
        title = ''
    if not title:
        title = os.path.splitext(os.path.basename(path))[0]
    _descriptions[path] = title
    return title


# ============================================================ кэш DNS Windows

class DNS_CACHE_ENTRY(ctypes.Structure):
    pass


DNS_CACHE_ENTRY._fields_ = [
    ('pNext', ctypes.POINTER(DNS_CACHE_ENTRY)), ('pszName', ctypes.c_void_p),
    ('wType', wt.WORD), ('wDataLength', wt.WORD), ('dwFlags', wt.DWORD),
]

dnsapi.DnsGetCacheDataTable.argtypes = [ctypes.POINTER(ctypes.POINTER(DNS_CACHE_ENTRY))]
dnsapi.DnsGetCacheDataTable.restype = wt.BOOL
dnsapi.DnsFree.argtypes = [ctypes.c_void_p, ctypes.c_int]
dnsapi.DnsQuery_W.argtypes = [wt.LPCWSTR, wt.WORD, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
dnsapi.DnsQuery_W.restype = ctypes.c_long
dnsapi.DnsRecordListFree.argtypes = [ctypes.c_void_p, ctypes.c_int]

DNS_TYPE_A, DNS_TYPE_AAAA = 1, 28
DNS_QUERY_STANDARD = 0x0
_DNS_RECORD_DATA = 32 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24


def _cache_names() -> Set[tuple]:
    head = ctypes.POINTER(DNS_CACHE_ENTRY)()
    if not dnsapi.DnsGetCacheDataTable(ctypes.byref(head)):
        return set()
    names = set()
    node = head
    while node:
        entry = node.contents
        nxt = entry.pNext
        if entry.pszName:
            if entry.wType in (DNS_TYPE_A, DNS_TYPE_AAAA):
                names.add((ctypes.wstring_at(entry.pszName).lower().rstrip('.'), entry.wType))
            dnsapi.DnsFree(entry.pszName, 0)
        dnsapi.DnsFree(ctypes.cast(node, ctypes.c_void_p), 0)
        node = nxt
    return names


def _cached_ips(name: str, qtype: int) -> Set[str]:
    # Запрос «только из кэша» (DNS_QUERY_NO_WIRE_QUERY) в Windows 11 без прав
    # администратора возвращает пустоту, поэтому спрашиваем обычным способом:
    # имя только что было в кэше, и ответ приходит оттуда же, без сети.
    ips = set()
    result = ctypes.c_void_p()
    if dnsapi.DnsQuery_W(name, qtype, DNS_QUERY_STANDARD, None, ctypes.byref(result), None) == 0:
        try:
            rec = result.value
            while rec:
                wtype = ctypes.c_uint16.from_address(rec + 2 * ctypes.sizeof(ctypes.c_void_p)).value
                data = rec + _DNS_RECORD_DATA
                if wtype == DNS_TYPE_A:
                    ips.add(str(ipaddress.IPv4Address(ctypes.string_at(data, 4))))
                elif wtype == DNS_TYPE_AAAA:
                    ips.add(str(ipaddress.IPv6Address(ctypes.string_at(data, 16))))
                rec = ctypes.c_void_p.from_address(rec).value
        finally:
            dnsapi.DnsRecordListFree(result, 1)
    return ips


def dns_cache_names() -> Set[tuple]:
    """Записи A/AAAA, которые сейчас лежат в кэше DNS Windows: {(домен, тип)}."""
    try:
        return _cache_names()
    except Exception:
        return set()


def dns_cache(names=None) -> Dict[str, Set[str]]:
    """Кэш DNS Windows: {домен: {IP}}. Права администратора не нужны."""
    out = {}
    try:
        for name, qtype in (dns_cache_names() if names is None else names):
            ips = _cached_ips(name, qtype)
            if ips:
                out.setdefault(name, set()).update(ips)
    except Exception:
        pass
    return out


# ============================================================ права администратора

def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wt.DWORD), ('fMask', wt.ULONG), ('hwnd', wt.HWND), ('lpVerb', wt.LPCWSTR),
        ('lpFile', wt.LPCWSTR), ('lpParameters', wt.LPCWSTR), ('lpDirectory', wt.LPCWSTR),
        ('nShow', ctypes.c_int), ('hInstApp', wt.HINSTANCE), ('lpIDList', ctypes.c_void_p),
        ('lpClass', wt.LPCWSTR), ('hkeyClass', wt.HKEY), ('dwHotKey', wt.DWORD),
        ('hIcon', wt.HANDLE), ('hProcess', wt.HANDLE),
    ]


shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
SEE_MASK_NOCLOSEPROCESS = 0x40
SEE_MASK_NOASYNC = 0x100
ERROR_CANCELLED = 1223


class ElevationCancelled(Exception):
    pass


def run_elevated(exe: str, params: str, cwd: str, hwnd=None) -> int:
    """
    Запускает процесс с правами администратора (окно UAC). Блокирует, пока
    пользователь не ответит. Возвращает PID; ElevationCancelled при отказе.
    """
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.hwnd = hwnd
    info.lpVerb = 'runas'
    info.lpFile = exe
    info.lpParameters = params
    info.lpDirectory = cwd
    info.nShow = 0  # SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        if err == ERROR_CANCELLED:
            raise ElevationCancelled()
        raise OSError(err, ctypes.FormatError(err))
    pid = 0
    if info.hProcess:
        pid = kernel32.GetProcessId(info.hProcess)
        kernel32.CloseHandle(info.hProcess)
    return pid


kernel32.GetProcessId.argtypes = [wt.HANDLE]


user32 = ctypes.WinDLL('user32')
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.c_void_p]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]


def find_window(title: str):
    try:
        exact = user32.FindWindowW(None, title)
        if exact:
            return exact
        found = []

        @WNDENUMPROC
        def visit(hwnd, _param):
            # В браузере заголовок дополняется «— Microsoft Edge» / «— Google Chrome».
            text = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, text, len(text))
            if text.value.startswith(title) and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(visit, 0)
        return found[0] if found else None
    except Exception:
        return None


def bring_to_front(hwnd) -> None:
    """Возвращает окно на передний план (после окна UAC оно может оказаться позади)."""
    if not hwnd:
        return
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)          # SW_SHOW
        # Windows не даёт фоновому процессу забрать фокус - временно присоединяемся
        # к потоку активного окна, как это делают сами системные утилиты.
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        own_thread = kernel32.GetCurrentThreadId()
        attached = bool(fg_thread) and fg_thread != own_thread and user32.AttachThreadInput(own_thread, fg_thread, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(own_thread, fg_thread, False)
    except Exception:
        pass
