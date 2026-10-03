"""
Сборщик с правами администратора.

Запускается как `RouteDeck.exe --capture-agent --url ... --token ... --parent PID`
(из исходников - `pythonw desktop.py --capture-agent ...`). Читает системную
трассировку Windows и раз в полсекунды отправляет накопленное в основную
программу по HTTP на localhost. Сам ничего не фильтрует и не хранит, к роутеру
и настройкам доступа не имеет. Завершается по команде основной программы
или когда она закрывается.

Источники (ETW):
  * Kernel-Process - запуск процессов (дерево процессов программы);
  * Kernel-Network - TCP и UDP с PID процесса;
  * DNS-Client     - домены, запрошенные через службу DNS Windows;
  * NDIS-PacketCapture - пакеты: ответы DNS и SNI из TLS. Нужны для программ
    на Chromium/Electron, которые резолвят домены сами. Пакет к процессу
    привязывается по порту: (протокол, локальный порт, адрес, порт сервера)
    берётся из событий Kernel-Network.
"""

import argparse
import ipaddress
import json
import logging
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional

from . import winapi
from .etw import EtwTrace, field_offset, guid_key
from .netutil import is_public, normalize_ip
from .packets import SniCollector, parse_dns_response, parse_frame

logger = logging.getLogger('capture.agent')

SESSION_NAME = 'RouteDeck-Capture'

NET = '7DD42A49-5329-4832-8DFD-43D979153A88'    # Microsoft-Windows-Kernel-Network
PROC = '22FB2CD6-0E7B-422B-A0C7-2FAD1FD0E716'   # Microsoft-Windows-Kernel-Process
DNS = '1C95126E-7EEA-49A9-A3FE-A378B03DDB4D'    # Microsoft-Windows-DNS-Client
PCAP = '2ED6006E-4729-4609-B423-3EE7BCD678EF'   # Microsoft-Windows-NDIS-PacketCapture

NET_KEY, PROC_KEY, DNS_KEY, PCAP_KEY = guid_key(NET), guid_key(PROC), guid_key(DNS), guid_key(PCAP)

# Kernel-Network: TCP IPv4 10-16, TCP IPv6 26-32, UDP IPv4 42/43, UDP IPv6 58/59
UDP_EVENTS = {42, 43, 58, 59}
NET_EVENTS = set(range(10, 17)) | set(range(26, 33)) | UDP_EVENTS
PROC_START, PROC_STOP = 1, 2
DNS_EVENTS = {3008, 3020}   # 3008 - запрос выполнен (в процессе программы), 3020 - ответ (служба DNS)
PCAP_FRAGMENT = 1001

FLOW_KEEP = 120.0           # сколько помнить, какому процессу принадлежит соединение
PENDING_KEEP = 5.0          # сколько ждать события Kernel-Network для пакета


def _ip(value) -> Optional[str]:
    if isinstance(value, int):
        return str(ipaddress.IPv4Address(struct.pack('<I', value)))
    if isinstance(value, (bytes, bytearray)) and len(value) in (4, 16):
        return normalize_ip(str(ipaddress.ip_address(bytes(value))))
    return None


def parse_dns_results(text: str):
    """«1.2.3.4;::ffff:5.6.7.8;type:  5 cdn.example.com;» -> [IP]"""
    ips = []
    for token in (text or '').split(';'):
        token = token.strip()
        if not token or token.startswith('type'):
            continue
        try:
            ips.append(normalize_ip(str(ipaddress.ip_address(token))))
        except ValueError:
            pass
    return ips


def local_addresses():
    ips = set()
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family in (socket.AF_INET, socket.AF_INET6):
                    ips.add(normalize_ip(a.address.split('%')[0]))
    except Exception:
        pass
    return ips


class Sensor:
    """Подписка на ETW и накопление событий между отправками."""

    def __init__(self, packets: bool = True):
        self.lock = threading.Lock()
        self.table = winapi.ProcessTable()
        self.sent_pids: Dict[int, int] = {}     # pid -> created, уже отправленные процессы
        self.local_ips = local_addresses()
        self.local_checked = time.time()
        self.addr_cache: Dict = {}
        self.flows: Dict[tuple, tuple] = {}     # (proto, лок. порт, адрес, порт) -> (pid, время)
        self.flows_pruned = time.time()
        self.pending = []                       # [(flow, имя, [IP], время)] - пакеты без процесса
        self.sni = SniCollector()
        self.frag_offsets: Dict[int, Optional[int]] = {}
        self.counters = {'packets': 0, 'dns_packets': 0, 'sni': 0}
        self._reset()
        providers = [(NET, 0xFF, 0x30), (PROC, 0xFF, 0x10), (DNS, 0xFF, 0xFFFFFFFFFFFFFFFF)]
        wanted = {NET_KEY: NET_EVENTS, PROC_KEY: {PROC_START, PROC_STOP}, DNS_KEY: DNS_EVENTS}
        if packets:
            providers.append((PCAP, 0xFF, 0xFFFFFFFFFFFFFFFF))
            wanted[PCAP_KEY] = {PCAP_FRAGMENT}
        self.trace = EtwTrace(SESSION_NAME, providers=providers, wanted=wanted,
                              handler=self.on_event, raw={PCAP_KEY: self.on_packet})

    def _reset(self):
        self.procs = []
        self.gone = []
        self.net: Dict = {}
        self.dns: Dict = {}

    def start(self):
        self.trace.stop_stale()
        self.trace.start()
        with self.lock:
            for info in self.table.refresh().values():
                self.procs.append(info.to_dict())
                self.sent_pids[info.pid] = info.created

    def stop(self):
        self.trace.stop()

    def stats(self):
        return {'events': self.trace.events, 'errors': self.trace.errors, **self.counters}

    def _addr(self, raw) -> str:
        key = raw if isinstance(raw, int) else bytes(raw or b'')
        value = self.addr_cache.get(key)
        if value is None:
            if len(self.addr_cache) > 50000:
                self.addr_cache.clear()
            value = self.addr_cache[key] = _ip(raw) or ''
        return value

    # Обработчики вызываются в потоке ETW - только быстрые операции под замком.
    def on_event(self, provider: bytes, event_id: int, pid: int, f: Dict):
        if provider == NET_KEY:
            self._on_net(event_id, f)
        elif provider == DNS_KEY:
            name = (f.get('QueryName') or '').strip().lower().rstrip('.')
            if not name:
                return
            ips = parse_dns_results(f.get('QueryResults', ''))
            # 3008 пишется в процессе программы; 3020 - в службе DNS, но с полем ClientPID.
            owner = pid if event_id == 3008 else int(f.get('ClientPID') or 0)
            with self.lock:
                self.dns.setdefault((owner, name), set()).update(ips)
        elif provider == PROC_KEY:
            if event_id == PROC_START:
                exe = winapi.dos_path(f.get('ImageName') or '')
                info = {'pid': f.get('ProcessID', 0), 'ppid': f.get('ParentProcessID', 0),
                        'name': os.path.basename(exe), 'exe': exe, 'created': f.get('CreateTime', 0)}
                with self.lock:
                    self.procs.append(info)
                    self.sent_pids[info['pid']] = info['created']
            elif event_id == PROC_STOP:
                with self.lock:
                    self.gone.append(f.get('ProcessID', 0))

    def _on_net(self, event_id: int, f: Dict):
        pid = f.get('PID')
        if pid is None:
            return
        d, s = self._addr(f.get('daddr')), self._addr(f.get('saddr'))
        dport, sport = socket.ntohs(f.get('dport', 0) & 0xFFFF), socket.ntohs(f.get('sport', 0) & 0xFFFF)
        # Обычно daddr - удалённая сторона; если там наш адрес - событие «входящее».
        if d in self.local_ips and s not in self.local_ips:
            remote, rport, lport = s, sport, dport
        else:
            remote, rport, lport = d, dport, sport
        if not remote:
            return
        proto = 'udp' if event_id in UDP_EVENTS else 'tcp'
        with self.lock:
            self.flows[(proto, lport, remote, rport)] = (pid, time.time())
            if remote in self.local_ips or not is_public(remote):
                return
            key = (pid, proto, remote, rport)
            self.net[key] = self.net.get(key, 0) + 1

    def on_packet(self, event_id: int, pid: int, layout, data: bytes):
        off = self.frag_offsets.get(id(layout), -1)
        if off == -1:
            off = self.frag_offsets[id(layout)] = field_offset(layout, 'Fragment')
        if off is None:
            return
        self.counters['packets'] += 1
        pkt = parse_frame(data[off:])
        if pkt is None:
            return
        proto, src, sport, dst, dport, payload, seq = pkt
        if not payload:
            return
        if proto == 'udp' and sport == 53:
            res = parse_dns_response(payload)
            if res:
                self.counters['dns_packets'] += 1
                with self.lock:
                    self.pending.append((('udp', dport, src, 53), res[0], res[1], time.time()))
        elif proto == 'tcp':
            name = self.sni.feed((src, sport, dst, dport), payload, seq)
            if name:
                self.counters['sni'] += 1
                with self.lock:
                    self.pending.append((('tcp', sport, dst, dport), name, [dst], time.time()))

    def _resolve_pending(self, now: float):
        """Пакеты DNS/SNI -> процесс по порту. Без процесса - через PENDING_KEEP с pid 0."""
        keep = []
        for flow, name, ips, t in self.pending:
            owner = self.flows.get(flow)
            if owner:
                self.dns.setdefault((owner[0], name), set()).update(ips)
            elif now - t > PENDING_KEEP:
                self.dns.setdefault((0, name), set()).update(ips)
            else:
                keep.append((flow, name, ips, t))
        self.pending = keep

    def drain(self) -> Dict:
        now = time.time()
        if now - self.local_checked > 15:
            self.local_ips = local_addresses() or self.local_ips    # мог подняться VPN
            self.local_checked = now
        with self.lock:
            self._resolve_pending(now)
            if now - self.flows_pruned > 10:
                self.flows = {k: v for k, v in self.flows.items() if now - v[1] < FLOW_KEEP}
                self.flows_pruned = now
            procs, gone, net, dns = self.procs, self.gone, self.net, self.dns
            self._reset()
        self.sni.cleanup()
        # Процессы, о которых основная программа ещё не знает (запущены до старта ETW).
        unknown = {pid for (pid, *_rest) in net if pid not in self.sent_pids}
        unknown |= {pid for (pid, _name) in dns if pid and pid not in self.sent_pids}
        if unknown:
            table = self.table.refresh()
            for pid in unknown:
                info = table.get(pid)
                if info:
                    procs.append(info.to_dict())
                    self.sent_pids[pid] = info.created
        return {
            'procs': procs,
            'gone': gone,
            'net': [[pid, proto, ip, port, n] for (pid, proto, ip, port), n in net.items()],
            'dns': [[pid, name, sorted(ips)] for (pid, name), ips in dns.items()],
            'stats': self.stats(),
        }


def serve(send: Callable[[Dict], Dict], should_exit: Callable[[], bool]) -> None:
    """
    Основной цикл сборщика. Пока RouteDeck не просит собирать (capture=False),
    трассировка выключена и раз в секунду отправляется только «я на связи».
    Когда анализ начинается - трассировка включается, когда заканчивается -
    выключается, и RouteDeck получает остаток данных (paused=True).
    """
    sensor: Optional[Sensor] = None
    fails = 0

    def pause() -> None:
        nonlocal sensor
        sensor.stop()
        final = sensor.drain()
        final.update(capturing=False, paused=True)
        logger.info(f'Трассировка выключена: {sensor.stats()}')
        sensor = None
        try:
            send(final)
        except Exception as e:
            logger.warning(f'Остаток данных не отправлен: {e}')

    try:
        while not should_exit():
            time.sleep(0.5 if sensor else 1.0)
            batch = sensor.drain() if sensor else {}
            batch['capturing'] = sensor is not None
            try:
                reply = send(batch)
                fails = 0
            except Exception as e:
                fails += 1
                logger.warning(f'Нет связи с RouteDeck ({fails}): {e}')
                if fails >= 10:
                    return
                continue
            if reply.get('stop'):
                return
            if reply.get('capture') and sensor is None:
                try:
                    sensor = start_sensor()
                    logger.info('Трассировка включена')
                except Exception as e:
                    logger.error(f'Трассировка не запущена: {e}')
                    try:
                        send({'error': str(e)})
                    except Exception:
                        pass
                    return
            elif not reply.get('capture') and sensor is not None:
                pause()
    finally:
        if sensor is not None:
            pause()


def http_sender(url: str, token: str) -> Callable[[Dict], Dict]:
    endpoint = url.rstrip('/') + '/api/capture/agent'

    def send(batch: Dict) -> Dict:
        req = urllib.request.Request(endpoint, data=json.dumps(batch).encode('utf-8'), method='POST',
                                     headers={'Content-Type': 'application/json', 'X-Capture-Token': token})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 409):
                return {'stop': True}    # сессия анализа закончилась или начата новая
            raise
    return send


def start_sensor() -> Sensor:
    """Запускает трассировку; если захват пакетов недоступен - без него."""
    sensor = Sensor(packets=True)
    try:
        sensor.start()
        return sensor
    except Exception as e:
        logger.warning(f'Трассировка с захватом пакетов не запущена ({e}), пробую без него')
    sensor = Sensor(packets=False)
    sensor.start()
    return sensor


def run(argv) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture-agent', action='store_true')
    parser.add_argument('--url', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--parent', type=int, default=0)
    parser.add_argument('--log', default='')
    args = parser.parse_args(argv[1:])

    handlers = [logging.FileHandler(args.log, encoding='utf-8')] if args.log else [logging.NullHandler()]
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format='%(asctime)s - agent - %(levelname)s - %(message)s')
    send = http_sender(args.url, args.token)
    logger.info(f'Сборщик запущен, PID {os.getpid()}, RouteDeck PID {args.parent}')
    parent_gone = lambda: bool(args.parent) and not winapi.process_alive(args.parent)
    serve(send, parent_gone)
    logger.info('Сборщик завершён')
    return 0
