"""
Вход в KeeneticOS 5.2 по схеме NDW4 (``x-ndw4-interactive``).

В 5.2 веб-вход перешёл на SCRAM-подобный обмен на Argon2id и HMAC-SHA3-512.
Когда роутер переносит хранение пароля на новую схему, старый вход
(md5 + sha256 по X-NDM-Challenge) отклоняется даже с правильным паролем,
хотя роутер продолжает его предлагать.

Схема официально не описана. Формат сообщений, вывод ключей и их метки
взяты из открытых клиентов, проверенных на роутерах с 5.2:
cataseven/Keenetic-Router-Pro (Apache-2.0), hoaxisr/awg-manager (MIT),
Eralde/rci-tools (ISC).

Обмен идёт в одной cookie-сессии, которую роутер выдал на GET /auth:
    1. POST endpoint {login, nonce}               -> X-NDM-Data: salt, iter, memcost, nonce
    2. POST endpoint {login, nonce, proof}        -> X-NDM-Data: signature
    3. POST endpoint {login, nonce, signature-proof} -> 200
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from typing import Any, Dict, Optional, Tuple

SCHEME = 'x-ndw4-interactive'

_CLIENT_KEY_LABEL = b'NDW4 Interactive Client Key'
_SERVER_KEY_LABEL = b'NDW4 Interactive Server Key'

# Пределы стоимости Argon2id, которую может запросить роутер: ответ с мусором
# не должен заставить программу занять гигабайты памяти.
_ITER_RANGE = (1, 64)
_MEMCOST_KIB_RANGE = (8, 262144)

# Адрес обмена роутер называет сам; принимаем только путь на том же хосте.
_ENDPOINT_RE = re.compile(r'/(?!/)[A-Za-z0-9._~/-]*')


class Ndw4Error(Exception):
    """Вход по NDW4 не удался."""


class Ndw4CredentialsError(Ndw4Error):
    """Роутер отклонил логин или пароль. Повторять нельзя: это попытка в счёт блокировки."""


class Ndw4ProtocolError(Ndw4Error):
    """Ответ роутера не соответствует обмену. О пароле ничего не говорит."""


def header_values(response: Any, name: str) -> list:
    """Все значения повторяющегося заголовка: 5.2 присылает два WWW-Authenticate."""
    raw = getattr(response, 'raw', None)
    getlist = getattr(getattr(raw, 'headers', None), 'getlist', None)
    if getlist is not None:
        values = getlist(name)
        if values:
            return list(values)
    value = response.headers.get(name)
    return [value] if value else []


def parse_offer(response: Any) -> Tuple[bool, Optional[str]]:
    """(предлагается ли NDW4, адрес обмена) по ответу GET /auth."""
    for value in header_values(response, 'WWW-Authenticate'):
        scheme, _, params = (value or '').strip().partition(' ')
        if scheme.lower() != SCHEME:
            continue
        match = re.search(r'endpoint="([^"]*)"', params)
        endpoint = match.group(1) if match else ''
        return True, endpoint if _ENDPOINT_RE.fullmatch(endpoint) else None
    return False, None


def login(session: Any, base_url: str, endpoint: Optional[str], username: str,
          password: str, timeout: float) -> None:
    """
    Выполняет обмен в сессии ``session`` (requests.Session с cookie от GET /auth).
    После успеха сессия авторизована.
    """
    if endpoint is None:
        raise Ndw4ProtocolError('роутер не указал адрес для входа')
    url = base_url.rstrip('/') + endpoint
    client_nonce = base64.b64encode(secrets.token_bytes(16)).decode('ascii')

    # 1. Представляемся, получаем соль, стоимость Argon2id и общий nonce.
    status, reply = _post(session, url, {'login': username, 'nonce': client_nonce}, timeout)
    if reply is None:
        if status == 401:  # так роутер отвечает на неизвестное имя пользователя
            raise Ndw4CredentialsError('роутер не принял имя пользователя')
        raise Ndw4ProtocolError(f'шаг 1: HTTP {status} без X-NDM-Data')
    _raise_on_error_field(reply, 'шаг 1')
    salt_b64 = _text(reply, 'salt', 'шаг 1')
    combined_nonce = _text(reply, 'nonce', 'шаг 1')
    if not combined_nonce.startswith(client_nonce) or combined_nonce == client_nonce:
        raise Ndw4ProtocolError('шаг 1: nonce роутера не продолжает наш')
    salt = _b64decode(salt_b64, 'salt')
    if len(salt) != 16:
        raise Ndw4ProtocolError('шаг 1: соль не 16 байт')
    iter_raw, iterations = _cost(reply.get('iter'), 'iter', _ITER_RANGE)
    mem_raw, memory_kib = _cost(reply.get('memcost'), 'memcost', _MEMCOST_KIB_RANGE)

    # Стоимость подписывается ровно в том виде, в каком её прислал роутер.
    auth_message = (f'login1={username},nonce1={client_nonce};'
                    f'iter2={iter_raw},memcost2={mem_raw},'
                    f'nonce2={combined_nonce},salt2={salt_b64};'
                    f'login3={username},nonce3={combined_nonce}')
    client_key, stored_key, server_key = derive_keys(
        password.encode('utf-8'), salt, iterations, memory_kib)

    # 2. Доказательство знания пароля; роутер отвечает своей подписью.
    status, reply = _post(session, url, {
        'login': username, 'nonce': combined_nonce,
        'proof': proof(client_key, stored_key, auth_message),
    }, timeout)
    if reply is None:
        # На неверный пароль роутер тоже присылает подпись, так что её отсутствие -
        # сбой обмена (например, истекла 20-секундная сессия).
        raise Ndw4ProtocolError(f'шаг 2: HTTP {status} без X-NDM-Data')
    _raise_on_error_field(reply, 'шаг 2')
    signature_b64 = _text(reply, 'signature', 'шаг 2')
    if not hmac.compare_digest(_b64decode(signature_b64, 'signature'),
                               _hmac(server_key, auth_message.encode('utf-8'))):
        # Подлинная подпись роутера не сходится с ключами от неверного пароля.
        raise Ndw4CredentialsError('роутер не принял пароль')

    # 3. Подтверждаем проверенную подпись.
    status, _ = _post(session, url, {
        'login': username, 'nonce': combined_nonce,
        'signature-proof': proof(client_key, stored_key, f'{auth_message};signature4={signature_b64}'),
    }, timeout)
    if status != 200:
        raise Ndw4ProtocolError(f'шаг 3: HTTP {status}')


def derive_keys(password: bytes, salt: bytes, iterations: int,
                memory_kib: int) -> Tuple[bytes, bytes, bytes]:
    """(client_key, stored_key, server_key) из пароля."""
    from argon2.low_level import Type, hash_secret_raw
    salted = hash_secret_raw(secret=password, salt=salt, time_cost=iterations,
                             memory_cost=memory_kib, parallelism=1, hash_len=64,
                             type=Type.ID, version=19)
    client_key = _hmac(salted, _CLIENT_KEY_LABEL)
    server_key = _hmac(salted, _SERVER_KEY_LABEL)
    return client_key, hashlib.sha3_512(client_key).digest(), server_key


def proof(client_key: bytes, stored_key: bytes, message: str) -> str:
    """ClientKey XOR HMAC(StoredKey, message) в base64 - доказательство SCRAM."""
    signature = _hmac(stored_key, message.encode('utf-8'))
    return base64.b64encode(bytes(a ^ b for a, b in zip(client_key, signature))).decode('ascii')


def _hmac(key: bytes, message: bytes) -> bytes:
    return hmac.new(key, message, hashlib.sha3_512).digest()


def _post(session: Any, url: str, payload: Dict[str, str],
          timeout: float) -> Tuple[int, Optional[dict]]:
    resp = session.post(url, json=payload, timeout=timeout, allow_redirects=False)
    encoded = resp.headers.get('X-NDM-Data')
    if not encoded:
        return resp.status_code, None
    try:
        data = json.loads(base64.b64decode(encoded, validate=True).decode('utf-8'))
    except (binascii.Error, ValueError) as e:
        raise Ndw4ProtocolError('X-NDM-Data - не JSON в base64') from e
    if not isinstance(data, dict):
        raise Ndw4ProtocolError('X-NDM-Data - не JSON-объект')
    return resp.status_code, data


def _raise_on_error_field(reply: dict, stage: str) -> None:
    message = reply.get('error') or reply.get('e')
    if isinstance(message, str) and message:
        raise Ndw4CredentialsError(f'{stage}: {message}')


def _text(reply: dict, key: str, stage: str) -> str:
    value = reply.get(key)
    if not isinstance(value, str) or not value:
        raise Ndw4ProtocolError(f"{stage}: нет поля '{key}'")
    return value


def _cost(value: Any, key: str, bounds: Tuple[int, int]) -> Tuple[str, int]:
    if isinstance(value, bool):
        raise Ndw4ProtocolError(f'неверный {key}')
    if isinstance(value, int):
        raw, parsed = str(value), value
    elif isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
        raw, parsed = value, int(value)
    else:
        raise Ndw4ProtocolError(f'неверный {key}')
    if not bounds[0] <= parsed <= bounds[1]:
        raise Ndw4ProtocolError(f'{key}={parsed} вне {bounds[0]}..{bounds[1]}')
    return raw, parsed


def _b64decode(value: str, label: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as e:
        raise Ndw4ProtocolError(f'{label} - не base64') from e
