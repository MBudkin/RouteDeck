"""
Минимальный потребитель ETW (Event Tracing for Windows) на ctypes.

Создаёт сессию трассировки в реальном времени, подключает провайдеров и
вызывает обработчик для каждого события с уже разобранными полями.
Поля разбираются по схеме из TDH (tdh.dll), схема кэшируется для каждого
типа события. Нужны права администратора.
"""

import ctypes
import logging
import struct
import threading
import uuid
from ctypes import wintypes as wt
from typing import Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

advapi32 = ctypes.WinDLL('advapi32')
tdh = ctypes.WinDLL('tdh')

PTR_SIZE = ctypes.sizeof(ctypes.c_void_p)


class GUID(ctypes.Structure):
    _fields_ = [('Data1', wt.DWORD), ('Data2', wt.WORD), ('Data3', wt.WORD), ('Data4', ctypes.c_ubyte * 8)]

    @classmethod
    def from_string(cls, value: str) -> 'GUID':
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def guid_key(value: str) -> bytes:
    """Ключ провайдера в том виде, в каком GUID лежит в заголовке события."""
    return uuid.UUID(value).bytes_le


class WNODE_HEADER(ctypes.Structure):
    _fields_ = [('BufferSize', wt.ULONG), ('ProviderId', wt.ULONG), ('HistoricalContext', ctypes.c_uint64),
                ('TimeStamp', ctypes.c_int64), ('Guid', GUID), ('ClientContext', wt.ULONG), ('Flags', wt.ULONG)]


class EVENT_TRACE_PROPERTIES(ctypes.Structure):
    _fields_ = [('Wnode', WNODE_HEADER), ('BufferSize', wt.ULONG), ('MinimumBuffers', wt.ULONG),
                ('MaximumBuffers', wt.ULONG), ('MaximumFileSize', wt.ULONG), ('LogFileMode', wt.ULONG),
                ('FlushTimer', wt.ULONG), ('EnableFlags', wt.ULONG), ('AgeLimit', wt.LONG),
                ('NumberOfBuffers', wt.ULONG), ('FreeBuffers', wt.ULONG), ('EventsLost', wt.ULONG),
                ('BuffersWritten', wt.ULONG), ('LogBuffersLost', wt.ULONG), ('RealTimeBuffersLost', wt.ULONG),
                ('LoggerThreadId', wt.HANDLE), ('LogFileNameOffset', wt.ULONG), ('LoggerNameOffset', wt.ULONG)]


class TRACE_PROPERTIES_BUFFER(ctypes.Structure):
    _fields_ = [('props', EVENT_TRACE_PROPERTIES), ('name', wt.WCHAR * 1024)]


class SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wt.WORD) for n in ('wYear', 'wMonth', 'wDayOfWeek', 'wDay', 'wHour', 'wMinute', 'wSecond', 'wMilliseconds')]


class TIME_ZONE_INFORMATION(ctypes.Structure):
    _fields_ = [('Bias', wt.LONG), ('StandardName', wt.WCHAR * 32), ('StandardDate', SYSTEMTIME),
                ('StandardBias', wt.LONG), ('DaylightName', wt.WCHAR * 32), ('DaylightDate', SYSTEMTIME),
                ('DaylightBias', wt.LONG)]


class TRACE_LOGFILE_HEADER(ctypes.Structure):
    _fields_ = [('BufferSize', wt.ULONG), ('Version', wt.ULONG), ('ProviderVersion', wt.ULONG),
                ('NumberOfProcessors', wt.ULONG), ('EndTime', ctypes.c_int64), ('TimerResolution', wt.ULONG),
                ('MaximumFileSize', wt.ULONG), ('LogFileMode', wt.ULONG), ('BuffersWritten', wt.ULONG),
                ('LogInstanceGuid', GUID), ('LoggerName', ctypes.c_void_p), ('LogFileName', ctypes.c_void_p),
                ('TimeZone', TIME_ZONE_INFORMATION), ('BootTime', ctypes.c_int64), ('PerfFreq', ctypes.c_int64),
                ('StartTime', ctypes.c_int64), ('ReservedFlags', wt.ULONG), ('BuffersLost', wt.ULONG)]


class EVENT_TRACE_HEADER(ctypes.Structure):
    _fields_ = [('Size', wt.USHORT), ('FieldTypeFlags', wt.USHORT), ('Version', wt.ULONG),
                ('ThreadId', wt.ULONG), ('ProcessId', wt.ULONG), ('TimeStamp', ctypes.c_int64),
                ('Guid', GUID), ('ProcessorTime', ctypes.c_uint64)]


class EVENT_TRACE(ctypes.Structure):
    _fields_ = [('Header', EVENT_TRACE_HEADER), ('InstanceId', wt.ULONG), ('ParentInstanceId', wt.ULONG),
                ('ParentGuid', GUID), ('MofData', ctypes.c_void_p), ('MofLength', wt.ULONG),
                ('ClientContext', wt.ULONG)]


EVENT_RECORD_CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.c_void_p)


class EVENT_TRACE_LOGFILEW(ctypes.Structure):
    _fields_ = [('LogFileName', wt.LPWSTR), ('LoggerName', wt.LPWSTR), ('CurrentTime', ctypes.c_int64),
                ('BuffersRead', wt.ULONG), ('ProcessTraceMode', wt.ULONG), ('CurrentEvent', EVENT_TRACE),
                ('LogfileHeader', TRACE_LOGFILE_HEADER), ('BufferCallback', ctypes.c_void_p),
                ('BufferSize', wt.ULONG), ('Filled', wt.ULONG), ('EventsLost', wt.ULONG),
                ('EventRecordCallback', EVENT_RECORD_CALLBACK), ('IsKernelTrace', wt.ULONG),
                ('Context', ctypes.c_void_p)]


TRACEHANDLE = ctypes.c_uint64
advapi32.StartTraceW.argtypes = [ctypes.POINTER(TRACEHANDLE), wt.LPCWSTR, ctypes.c_void_p]
advapi32.StartTraceW.restype = wt.ULONG
advapi32.ControlTraceW.argtypes = [TRACEHANDLE, wt.LPCWSTR, ctypes.c_void_p, wt.ULONG]
advapi32.ControlTraceW.restype = wt.ULONG
advapi32.EnableTraceEx2.argtypes = [TRACEHANDLE, ctypes.POINTER(GUID), wt.ULONG, ctypes.c_ubyte,
                                    ctypes.c_uint64, ctypes.c_uint64, wt.ULONG, ctypes.c_void_p]
advapi32.EnableTraceEx2.restype = wt.ULONG
advapi32.OpenTraceW.argtypes = [ctypes.POINTER(EVENT_TRACE_LOGFILEW)]
advapi32.OpenTraceW.restype = TRACEHANDLE
advapi32.ProcessTrace.argtypes = [ctypes.POINTER(TRACEHANDLE), wt.ULONG, ctypes.c_void_p, ctypes.c_void_p]
advapi32.ProcessTrace.restype = wt.ULONG
advapi32.CloseTrace.argtypes = [TRACEHANDLE]
advapi32.CloseTrace.restype = wt.ULONG
tdh.TdhGetEventInformation.argtypes = [ctypes.c_void_p, wt.ULONG, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wt.ULONG)]
tdh.TdhGetEventInformation.restype = wt.ULONG

WNODE_FLAG_TRACED_GUID = 0x00020000
EVENT_TRACE_REAL_TIME_MODE = 0x00000100
EVENT_TRACE_CONTROL_STOP = 1
EVENT_CONTROL_CODE_ENABLE_PROVIDER = 1
PROCESS_TRACE_MODE_REAL_TIME = 0x00000100
PROCESS_TRACE_MODE_EVENT_RECORD = 0x10000000
INVALID_PROCESSTRACE_HANDLE = 0xFFFFFFFFFFFFFFFF
ERROR_SUCCESS = 0
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
ERROR_INSUFFICIENT_BUFFER = 122

EVENT_HEADER_FLAG_32_BIT_HEADER = 0x20

# Типы полей TDH (TDH_INTYPE_*)
IN_UNICODESTRING, IN_ANSISTRING = 1, 2
IN_INT8, IN_UINT8, IN_INT16, IN_UINT16, IN_INT32, IN_UINT32, IN_INT64, IN_UINT64 = range(3, 11)
IN_FLOAT, IN_DOUBLE, IN_BOOLEAN, IN_BINARY, IN_GUID, IN_POINTER, IN_FILETIME, IN_SYSTEMTIME, IN_SID = range(11, 20)
IN_HEXINT32, IN_HEXINT64 = 20, 21

_FIXED = {
    IN_INT8: '<b', IN_UINT8: '<B', IN_INT16: '<h', IN_UINT16: '<H', IN_INT32: '<i', IN_UINT32: '<I',
    IN_INT64: '<q', IN_UINT64: '<Q', IN_FLOAT: '<f', IN_DOUBLE: '<d', IN_BOOLEAN: '<I',
    IN_FILETIME: '<Q', IN_HEXINT32: '<I', IN_HEXINT64: '<Q',
}

PROP_STRUCT, PROP_PARAM_LENGTH, PROP_PARAM_COUNT = 0x1, 0x2, 0x4
PROP_PARAM_FIXED_COUNT = 0x20

# Смещения в EVENT_RECORD (x64 и x86 совпадают до UserData)
_HDR = struct.Struct('<HHHHII')            # Size, HeaderType, Flags, EventProperty, ThreadId, ProcessId
_OFF_PROVIDER = 24
_OFF_EVENT_ID = 40
_OFF_USERDATA_LEN = 86
_OFF_USERDATA = 88 + PTR_SIZE              # после ExtendedData


class EtwError(RuntimeError):
    def __init__(self, code: int, what: str):
        self.code = code
        text = 'нужны права администратора' if code == ERROR_ACCESS_DENIED else ctypes.FormatError(code).strip()
        super().__init__(f'{what}: {text} (код {code})')


# Поле схемы: (имя, тип, флаги, количество, длина)
Layout = List[Tuple[str, int, int, int, int]]


def _event_layout(record: int) -> Optional[Layout]:
    size = wt.ULONG(0)
    status = tdh.TdhGetEventInformation(record, 0, None, None, ctypes.byref(size))
    if status != ERROR_INSUFFICIENT_BUFFER:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if tdh.TdhGetEventInformation(record, 0, None, buf, ctypes.byref(size)) != ERROR_SUCCESS:
        return None
    raw = buf.raw
    top_count = struct.unpack_from('<I', raw, 104)[0]
    layout = []
    for i in range(top_count):
        base = 112 + i * 24
        flags, name_off, in_type, _out_type, _map, count, length = struct.unpack_from('<IIHHIHH', raw, base)
        end = name_off
        while raw[end:end + 2] != b'\x00\x00':
            end += 2
        layout.append((raw[name_off:end].decode('utf-16-le'), in_type, flags, count, length))
    return layout


def _decode(layout: Layout, data: bytes, ptr_size: int) -> Dict:
    """Разбирает плоские поля события. На неподдерживаемом поле останавливается."""
    values: Dict = {}
    by_index: List = []
    off = 0
    n = len(data)
    for name, in_type, flags, count, length in layout:
        if flags & PROP_STRUCT:
            break
        if flags & PROP_PARAM_COUNT:
            items = by_index[count] if count < len(by_index) else 1
        else:
            items = count or 1
        if items != 1:
            break
        if in_type in _FIXED:
            fmt = _FIXED[in_type]
            size = struct.calcsize(fmt)
            if off + size > n:
                break
            value = struct.unpack_from(fmt, data, off)[0]
            off += size
        elif in_type == IN_POINTER:
            fmt = '<Q' if ptr_size == 8 else '<I'
            if off + ptr_size > n:
                break
            value = struct.unpack_from(fmt, data, off)[0]
            off += ptr_size
        elif in_type == IN_UNICODESTRING:
            if flags & PROP_PARAM_LENGTH or length:
                chars = by_index[length] if flags & PROP_PARAM_LENGTH else length
                value = data[off:off + chars * 2].decode('utf-16-le', 'replace').rstrip('\x00')
                off += chars * 2
            else:
                end = off
                while end + 1 < n and data[end:end + 2] != b'\x00\x00':
                    end += 2
                value = data[off:end].decode('utf-16-le', 'replace')
                off = end + 2
        elif in_type == IN_ANSISTRING:
            if flags & PROP_PARAM_LENGTH or length:
                chars = by_index[length] if flags & PROP_PARAM_LENGTH else length
                value = data[off:off + chars].decode('latin-1').rstrip('\x00')
                off += chars
            else:
                end = data.find(b'\x00', off)
                end = n if end < 0 else end
                value = data[off:end].decode('latin-1')
                off = end + 1
        elif in_type == IN_BINARY:
            size = by_index[length] if flags & PROP_PARAM_LENGTH else length
            value = data[off:off + size]
            off += size
        elif in_type == IN_GUID:
            value = str(uuid.UUID(bytes_le=data[off:off + 16]))
            off += 16
        elif in_type == IN_SYSTEMTIME:
            value = data[off:off + 16]
            off += 16
        elif in_type == IN_SID:
            if off + 2 > n:
                break
            size = 8 + 4 * data[off + 1]
            value = data[off:off + size]
            off += size
        else:
            break
        values[name] = value
        by_index.append(value)
    return values


EventHandler = Callable[[bytes, int, int, Dict], None]
RawHandler = Callable[[int, int, Layout, bytes], None]


def field_offset(layout: Layout, name: str) -> Optional[int]:
    """Смещение поля, если все поля перед ним фиксированного размера."""
    off = 0
    for fname, in_type, flags, _count, _length in layout:
        if fname == name:
            return off
        fmt = _FIXED.get(in_type)
        if fmt is None or flags & (PROP_STRUCT | PROP_PARAM_COUNT | PROP_PARAM_LENGTH):
            return None
        off += struct.calcsize(fmt)
    return None


class EtwTrace:
    """
    Сессия ETW реального времени.

    providers: [(guid, уровень, ключевые слова)]
    wanted:    {guid_key(guid): set(id событий) или None - все}
    handler(provider_key, event_id, pid, fields)
    raw:       {guid_key(guid): handler(event_id, pid, схема, байты)} - без разбора полей
               (для частых событий вроде сетевых пакетов)
    """

    def __init__(self, name: str, providers: Iterable[Tuple[str, int, int]],
                 wanted: Dict[bytes, Optional[set]], handler: EventHandler,
                 raw: Optional[Dict[bytes, RawHandler]] = None):
        self.name = name
        self.providers = list(providers)
        self.wanted = wanted
        self.handler = handler
        self.raw = raw or {}
        self.session = TRACEHANDLE(0)
        self.trace = TRACEHANDLE(INVALID_PROCESSTRACE_HANDLE)
        self.thread: Optional[threading.Thread] = None
        self.layouts: Dict[Tuple[bytes, int, int], Optional[Layout]] = {}
        self.events = 0
        self.errors = 0
        self._callback = EVENT_RECORD_CALLBACK(self._on_record)
        self._props = None

    def _properties(self) -> TRACE_PROPERTIES_BUFFER:
        buf = TRACE_PROPERTIES_BUFFER()
        p = buf.props
        p.Wnode.BufferSize = ctypes.sizeof(buf)
        p.Wnode.Flags = WNODE_FLAG_TRACED_GUID
        p.Wnode.ClientContext = 1  # QPC
        p.Wnode.Guid = GUID.from_buffer_copy(uuid.uuid4().bytes_le)
        p.BufferSize = 64          # КБ
        p.MinimumBuffers = 16
        p.MaximumBuffers = 64
        p.LogFileMode = EVENT_TRACE_REAL_TIME_MODE
        p.FlushTimer = 1           # секунда: события приходят без задержки
        p.LoggerNameOffset = ctypes.sizeof(EVENT_TRACE_PROPERTIES)
        return buf

    def stop_stale(self) -> None:
        """Останавливает оставшуюся после сбоя сессию с тем же именем."""
        buf = self._properties()
        advapi32.ControlTraceW(0, self.name, ctypes.byref(buf), EVENT_TRACE_CONTROL_STOP)

    def start(self) -> None:
        self._props = self._properties()
        status = advapi32.StartTraceW(ctypes.byref(self.session), self.name, ctypes.byref(self._props))
        if status == ERROR_ALREADY_EXISTS:
            self.stop_stale()
            self._props = self._properties()
            status = advapi32.StartTraceW(ctypes.byref(self.session), self.name, ctypes.byref(self._props))
        if status != ERROR_SUCCESS:
            raise EtwError(status, 'Не удалось создать сессию трассировки')

        try:
            for guid, level, keywords in self.providers:
                status = advapi32.EnableTraceEx2(self.session, ctypes.byref(GUID.from_string(guid)),
                                                 EVENT_CONTROL_CODE_ENABLE_PROVIDER, level, keywords, 0, 0, None)
                if status != ERROR_SUCCESS:
                    raise EtwError(status, f'Не удалось подключить провайдера {guid}')

            logfile = EVENT_TRACE_LOGFILEW()
            logfile.LoggerName = self.name
            logfile.ProcessTraceMode = PROCESS_TRACE_MODE_REAL_TIME | PROCESS_TRACE_MODE_EVENT_RECORD
            logfile.EventRecordCallback = self._callback
            self.trace = TRACEHANDLE(advapi32.OpenTraceW(ctypes.byref(logfile)))
            if self.trace.value == INVALID_PROCESSTRACE_HANDLE:
                raise EtwError(ctypes.GetLastError(), 'Не удалось открыть трассировку')
        except Exception:
            self._stop_session()
            raise

        self.thread = threading.Thread(target=self._run, name='etw', daemon=True)
        self.thread.start()

    def _run(self) -> None:
        status = advapi32.ProcessTrace(ctypes.byref(self.trace), 1, None, None)
        if status not in (ERROR_SUCCESS, 1223):  # 1223 - ERROR_CANCELLED при закрытии
            logger.warning(f'ProcessTrace завершился с кодом {status}')

    def _stop_session(self) -> None:
        if self.session.value and self._props is not None:
            advapi32.ControlTraceW(self.session, None, ctypes.byref(self._props), EVENT_TRACE_CONTROL_STOP)
            self.session = TRACEHANDLE(0)

    def stop(self) -> None:
        self._stop_session()
        if self.trace.value != INVALID_PROCESSTRACE_HANDLE:
            advapi32.CloseTrace(self.trace)
            self.trace = TRACEHANDLE(INVALID_PROCESSTRACE_HANDLE)
        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None

    def _on_record(self, record: int) -> None:
        try:
            provider = ctypes.string_at(record + _OFF_PROVIDER, 16)
            wanted = self.wanted.get(provider, False)
            if wanted is False:
                return
            event_id, version = struct.unpack_from('<HB', ctypes.string_at(record + _OFF_EVENT_ID, 3))
            if wanted is not None and event_id not in wanted:
                return
            _size, _htype, flags, _prop, _tid, pid = _HDR.unpack(ctypes.string_at(record, 16))
            key = (provider, event_id, version)
            layout = self.layouts.get(key, False)
            if layout is False:
                layout = self.layouts[key] = _event_layout(record)
            if not layout:
                return
            length = struct.unpack_from('<H', ctypes.string_at(record + _OFF_USERDATA_LEN, 2))[0]
            ptr = ctypes.c_void_p.from_address(record + _OFF_USERDATA).value
            data = ctypes.string_at(ptr, length) if ptr and length else b''
            ptr_size = 4 if flags & EVENT_HEADER_FLAG_32_BIT_HEADER else 8
            self.events += 1
            raw = self.raw.get(provider)
            if raw is not None:
                raw(event_id, pid, layout, data)
            else:
                self.handler(provider, event_id, pid, _decode(layout, data, ptr_size))
        except Exception:
            self.errors += 1
            if self.errors <= 5:
                logger.exception('Ошибка разбора события ETW')
