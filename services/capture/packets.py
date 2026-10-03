"""
Разбор сетевых пакетов из системного захвата (NDIS packet capture):
ответы DNS и имя сайта (SNI) из начала TLS-соединения.

Нужно для программ на Chromium/Electron (Discord, VS Code, браузеры): они
резолвят домены сами, мимо службы DNS Windows, и иначе их домены не видны.
"""

import ipaddress
import struct
import time
from typing import Dict, List, Optional, Tuple

ETH_IPV4, ETH_IPV6, ETH_VLAN = 0x0800, 0x86DD, 0x8100
PROTO_TCP, PROTO_UDP = 6, 17


def _ip_str(raw: bytes) -> str:
    addr = ipaddress.ip_address(raw)
    if addr.version == 6 and addr.ipv4_mapped:
        return str(addr.ipv4_mapped)
    return str(addr)


SNAP = b'\xaa\xaa\x03\x00\x00\x00'


def _wifi_payload_offset(frame: bytes) -> Optional[int]:
    """Кадр данных 802.11 (Wi-Fi адаптеры отдают такие) -> смещение IP-пакета."""
    if len(frame) < 34:
        return None
    fc0, fc1 = frame[0], frame[1]
    if (fc0 >> 2) & 3 != 2:                      # не кадр данных
        return None
    hdr = 24 + (6 if fc1 & 3 == 3 else 0)        # ToDS+FromDS - четвёртый адрес
    if fc0 & 0x80:                               # QoS Data
        hdr += 2 + (4 if fc1 & 0x80 else 0)      # + HT Control
    if frame[hdr:hdr + 6] != SNAP:
        return None
    ethertype = struct.unpack_from('!H', frame, hdr + 6)[0]
    return hdr + 8 if ethertype in (ETH_IPV4, ETH_IPV6) else None


def parse_frame(frame: bytes):
    """
    Кадр Ethernet, 802.11 или «голый» IP-пакет (VPN-адаптеры) ->
    (протокол, src_ip, src_port, dst_ip, dst_port, payload, tcp_seq) или None.
    """
    off = _wifi_payload_offset(frame)
    if off is None and len(frame) >= 14:
        ethertype = struct.unpack_from('!H', frame, 12)[0]
        if ethertype == ETH_VLAN and len(frame) >= 18:
            ethertype = struct.unpack_from('!H', frame, 16)[0]
            off = 18 if ethertype in (ETH_IPV4, ETH_IPV6) else None
        elif ethertype in (ETH_IPV4, ETH_IPV6):
            off = 14
    if off is None:
        if frame and frame[0] >> 4 in (4, 6):
            off = 0
        else:
            return None

    version = frame[off] >> 4 if len(frame) > off else 0
    if version == 4:
        if len(frame) < off + 20:
            return None
        ihl = (frame[off] & 0x0F) * 4
        total = struct.unpack_from('!H', frame, off + 2)[0]
        frag = struct.unpack_from('!H', frame, off + 6)[0] & 0x1FFF
        if frag:
            return None
        proto = frame[off + 9]
        src, dst = frame[off + 12:off + 16], frame[off + 16:off + 20]
        start, end = off + ihl, min(len(frame), off + total) if total else len(frame)
    elif version == 6:
        if len(frame) < off + 40:
            return None
        plen = struct.unpack_from('!H', frame, off + 4)[0]
        proto = frame[off + 6]
        src, dst = frame[off + 8:off + 24], frame[off + 24:off + 40]
        start, end = off + 40, min(len(frame), off + 40 + plen)
    else:
        return None

    if proto == PROTO_UDP and end - start >= 8:
        sport, dport = struct.unpack_from('!HH', frame, start)
        return 'udp', _ip_str(src), sport, _ip_str(dst), dport, frame[start + 8:end], 0
    if proto == PROTO_TCP and end - start >= 20:
        sport, dport, seq = struct.unpack_from('!HHI', frame, start)
        data_off = (frame[start + 12] >> 4) * 4
        return 'tcp', _ip_str(src), sport, _ip_str(dst), dport, frame[start + data_off:end], seq
    return None


# ============================================================ DNS

def _dns_name(msg: bytes, off: int) -> Tuple[str, int]:
    labels, jumped, end, hops = [], False, off, 0
    while off < len(msg):
        length = msg[off]
        if length == 0:
            off += 1
            break
        if length & 0xC0 == 0xC0:
            if off + 1 >= len(msg) or hops > 20:
                raise ValueError('bad pointer')
            if not jumped:
                end = off + 2
            off = ((length & 0x3F) << 8) | msg[off + 1]
            jumped, hops = True, hops + 1
            continue
        labels.append(msg[off + 1:off + 1 + length].decode('ascii', 'replace'))
        off += 1 + length
    if not jumped:
        end = off
    return '.'.join(labels).lower(), end


def parse_dns_response(msg: bytes) -> Optional[Tuple[str, List[str]]]:
    """Ответ DNS -> (запрошенное имя, [IP из ответа]) или None."""
    if len(msg) < 12:
        return None
    _id, flags, qd, an = struct.unpack_from('!HHHH', msg, 0)
    if not flags & 0x8000 or qd != 1:
        return None
    try:
        qname, off = _dns_name(msg, 12)
        off += 4
        ips = []
        for _ in range(an):
            _name, off = _dns_name(msg, off)
            rtype, _cls, _ttl, rdlen = struct.unpack_from('!HHIH', msg, off)
            off += 10
            if rtype == 1 and rdlen == 4:
                ips.append(_ip_str(msg[off:off + 4]))
            elif rtype == 28 and rdlen == 16:
                ips.append(_ip_str(msg[off:off + 16]))
            off += rdlen
    except (ValueError, struct.error, IndexError):
        return None
    return (qname, ips) if qname else None


# ============================================================ TLS SNI

def tls_client_hello_length(payload: bytes) -> int:
    """Полная длина записи ClientHello, если payload с неё начинается, иначе 0."""
    if len(payload) >= 6 and payload[0] == 0x16 and payload[1] == 0x03 and payload[5] == 0x01:
        return 5 + struct.unpack_from('!H', payload, 3)[0]
    return 0


def parse_sni(record: bytes) -> Optional[str]:
    try:
        off = 5 + 4 + 2 + 32                       # заголовок записи, рукопожатия, версия, random
        off += 1 + record[off]                     # session id
        off += 2 + struct.unpack_from('!H', record, off)[0]   # cipher suites
        off += 1 + record[off]                     # compression
        end = off + 2 + struct.unpack_from('!H', record, off)[0]
        off += 2
        while off + 4 <= min(end, len(record)):
            etype, elen = struct.unpack_from('!HH', record, off)
            off += 4
            if etype == 0:                         # server_name
                p = off + 2
                while p + 3 <= off + elen:
                    ntype, nlen = record[p], struct.unpack_from('!H', record, p + 1)[0]
                    if ntype == 0:
                        return record[p + 3:p + 3 + nlen].decode('ascii', 'replace').lower().rstrip('.')
                    p += 3 + nlen
                return None
            off += elen
    except (IndexError, struct.error):
        return None
    return None


class SniCollector:
    """Собирает ClientHello из одного или нескольких TCP-сегментов (Chrome шлёт ~2 КБ)."""

    MAX = 16384

    def __init__(self):
        self.pending: Dict[tuple, list] = {}       # flow -> [buffer, нужно байт, след. seq, время]

    def feed(self, flow: tuple, payload: bytes, seq: int) -> Optional[str]:
        state = self.pending.get(flow)
        if state is None:
            need = tls_client_hello_length(payload)
            if not need:
                return None
            if len(payload) >= need:
                return parse_sni(payload[:need])
            if need > self.MAX:
                return None
            if len(self.pending) > 2000:
                self.cleanup(force=True)
            self.pending[flow] = [bytearray(payload), need, (seq + len(payload)) & 0xFFFFFFFF, time.time()]
            return None
        buf, need, next_seq, _t = state
        if seq != next_seq or not payload:
            return None                            # повтор того же сегмента или чужой порядок
        buf += payload
        state[2] = (seq + len(payload)) & 0xFFFFFFFF
        if len(buf) >= need:
            del self.pending[flow]
            return parse_sni(bytes(buf[:need]))
        return None

    def cleanup(self, force: bool = False) -> None:
        limit = time.time() - (0 if force else 5)
        for flow in [f for f, s in self.pending.items() if s[3] < limit]:
            del self.pending[flow]
