"""
RouteDeck - панель для управления маршрутизацией сайтов через VPN на Keenetic.

Два режима работы:
  * «Роутер»  - прямое управление списками доменов («Маршруты DNS») и
                статическими маршрутами Keenetic через RCI API;
  * «Сайты»   - классический режим: резолвинг IP-адресов сайтов и генерация
                bat-файлов для импорта на странице «Маршрутизация».
"""

import atexit
import json
import logging
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import wraps
from logging.handlers import RotatingFileHandler
from typing import Callable, Dict, List
from urllib.parse import urlparse

from flask import Flask, render_template, request, jsonify, send_from_directory, send_file, abort

from services.database import Database
from services.bat_generator import BatGenerator
from services.dns_resolver import DNSResolver
from services.subnet_generator import generate_subnets
from services.settings import Settings
from services import transfer
from services.updates import UpdateManager, UpdateError
from services.capture.session import CaptureManager, agent_command_factory, list_apps
from services.capture.store import CaptureStore
from services.keenetic import (
    KeeneticClient, KeeneticError, KeeneticAuthError, MAX_GROUP_ENTRIES,
    normalize_entry, parse_entries,
)

from version import APP_NAME, APP_VERSION
FROZEN = getattr(sys, 'frozen', False)

# Ресурсы (шаблоны, статика) лежат рядом с кодом или внутри EXE,
# а данные (база, настройки, логи) - в папке проекта или в %APPDATA%\RouteDeck.
RESOURCE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
if FROZEN:
    DATA_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), APP_NAME)
    os.makedirs(DATA_DIR, exist_ok=True)
    # Первый запуск EXE: подхватываем базу сайтов из папки программы или проекта.
    exe_dir = os.path.dirname(sys.executable)
    for candidate in (exe_dir, os.path.dirname(exe_dir)):
        for name in ('sites.db', 'settings.json'):
            src, dst = os.path.join(candidate, name), os.path.join(DATA_DIR, name)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
else:
    DATA_DIR = RESOURCE_DIR
DATA_DIR = os.environ.get('S4R_DATA_DIR') or DATA_DIR
os.makedirs(DATA_DIR, exist_ok=True)
os.chdir(DATA_DIR)
os.makedirs('logs', exist_ok=True)

_handlers = [RotatingFileHandler('logs/app.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')]
if sys.stderr:
    _handlers.append(logging.StreamHandler())
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=_handlers,
)
logger = logging.getLogger(__name__)

app = Flask(__name__,
            template_folder=os.path.join(RESOURCE_DIR, 'templates'),
            static_folder=os.path.join(RESOURCE_DIR, 'static'))
app.json.ensure_ascii = False

db = Database()
dns_resolver = DNSResolver()
bat_generator = BatGenerator(dns_resolver=dns_resolver)
settings = Settings()
updates = UpdateManager(APP_VERSION, DATA_DIR)
capture = CaptureManager(CaptureStore(), launcher={
    'command': agent_command_factory(FROZEN, RESOURCE_DIR),
    'cwd': os.path.dirname(sys.executable) if FROZEN else RESOURCE_DIR,
    'data_dir': DATA_DIR,
    'window': f'{APP_NAME} {APP_VERSION}',
})
atexit.register(capture.shutdown)  # результаты анализа сохраняются и при закрытии программы

_router_lock = threading.Lock()
_router_client = None


# === Защита локального API ===
# Панель управляет роутером, поэтому изменяющие запросы принимаются только
# как JSON и только со своей страницы: чужой сайт в браузере не сможет
# отправить такой запрос без CORS-preflight, а CORS мы не разрешаем.

@app.before_request
def csrf_guard():
    if request.method in ('GET', 'HEAD', 'OPTIONS') or not request.path.startswith('/api/'):
        return None
    if not request.is_json:
        return jsonify({'success': False, 'error': 'Ожидается JSON'}), 415
    origin = request.headers.get('Origin')
    if origin and urlparse(origin).netloc != request.host:
        return jsonify({'success': False, 'error': 'Запрос с чужого источника'}), 403
    return None


def api(func: Callable):
    """Единая обработка ошибок для API-эндпоинтов."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except KeeneticAuthError as e:
            return jsonify({'success': False, 'error': str(e), 'code': 'auth'}), 401
        except KeeneticError as e:
            logger.warning(f'{func.__name__}: {e}')
            return jsonify({'success': False, 'error': str(e), 'code': 'router'}), 502
        except UpdateError as e:
            return jsonify({'success': False, 'error': str(e), 'code': e.code}), 400
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        except Exception as e:
            logger.exception(f'Ошибка в {func.__name__}')
            return jsonify({'success': False, 'error': str(e)}), 500
    return wrapper


def body() -> Dict:
    return request.get_json(silent=True) or {}


# === Обновления приложения (без учётных данных GitHub и без доступа к роутеру) ===

def update_view(data):
    return jsonify({'success': True, **data, 'can_install': FROZEN and sys.platform == 'win32'})


@app.route('/api/updates', methods=['GET'])
@api
def updates_status():
    return update_view(updates.snapshot())


@app.route('/api/updates/check', methods=['POST'])
@api
def updates_check():
    return update_view(updates.check(manual=body().get('manual') is True))


@app.route('/api/updates/download', methods=['POST'])
@api
def updates_download():
    return update_view(updates.download())


@app.route('/api/updates/cancel', methods=['POST'])
@api
def updates_cancel():
    return update_view(updates.cancel_download())


@app.route('/api/updates/file', methods=['GET'])
@api
def updates_file():
    path, release = updates.verified_file()
    return send_file(path, as_attachment=True, download_name='RouteDeck.exe', mimetype='application/octet-stream')


# === Вспомогательные функции ===

def normalize_url(url: str) -> str:
    """Нормализует URL (добавляет https:// если отсутствует)."""
    url = (url or '').strip()
    if not url.startswith(('http://', 'https://')):
        return 'https://' + url
    return url


def extract_base_domain(url: str) -> str:
    """Извлекает домен из URL (без www.)."""
    try:
        domain = (urlparse(url).hostname or '').strip().lower().rstrip('.')
        return domain[4:] if domain.startswith('www.') else domain
    except Exception:
        return ''


def site_view(site: Dict) -> Dict:
    site = dict(site)
    site['bat_name'] = bat_generator.bat_filename(site['domain'])
    site['ip_count'] = len(site.get('ip_addresses') or [])
    return site


def router() -> KeeneticClient:
    """Возвращает (и кэширует) клиент роутера по текущим настройкам."""
    global _router_client
    with _router_lock:
        if _router_client is None:
            if not settings.get('router_password'):
                raise KeeneticAuthError('Укажите пароль роутера в настройках')
            _router_client = KeeneticClient(
                settings.get('router_host'),
                settings.get('router_login'),
                settings.get('router_password'),
            )
        return _router_client


def reset_router():
    global _router_client
    with _router_lock:
        _router_client = None


def refresh_site_ips(sites: List[Dict], with_html: bool) -> List[Dict]:
    """Параллельно обновляет IP-адреса сайтов и перезаписывает bat-файлы."""
    def work(site):
        try:
            if with_html:
                result = bat_generator.update_site_ips_with_html(site['domain'], site['url'])
            else:
                result = bat_generator.update_site_ips(site['domain'], site['url'])
            if not result['success']:
                return {'site_id': site['id'], 'domain': site['domain'], 'success': False,
                        'error': result.get('error') or 'Неизвестная ошибка'}
            ips = set(result.get('ip_addresses', []))
            db.update_site_ips(site['id'], ips, set())
            return {'site_id': site['id'], 'domain': site['domain'],
                    'success': True, 'ip_count': len(ips)}
        except Exception as e:
            logger.error(f"Ошибка обновления IP для {site['domain']}: {e}")
            return {'site_id': site['id'], 'domain': site['domain'],
                    'success': False, 'error': str(e)}

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(sites)))) as pool:
        return list(pool.map(work, sites))


# === Страницы ===

@app.route('/')
def index():
    return render_template('index.html', app_name=APP_NAME, app_version=APP_VERSION)


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(app.static_folder, 'favicon.svg', mimetype='image/svg+xml')


# === API: сайты (режим bat-файлов) ===

@app.route('/api/sites', methods=['GET'])
@api
def get_sites():
    active_only = request.args.get('active_only', 'true').lower() != 'false'
    return jsonify({'success': True,
                    'sites': [site_view(s) for s in db.get_all_sites(active_only=active_only)]})


@app.route('/api/sites/<int:site_id>', methods=['GET'])
@api
def get_site(site_id: int):
    site = db.get_site(site_id)
    if not site:
        return jsonify({'success': False, 'error': 'Сайт не найден'}), 404
    return jsonify({'success': True, 'site': site_view(site)})


@app.route('/api/sites', methods=['POST'])
@api
def add_site():
    data = body()
    url = (data.get('url') or '').strip()
    if not url:
        raise ValueError('URL обязателен')

    url = normalize_url(url)
    domain = extract_base_domain(url)
    if not domain:
        raise ValueError('Не удалось определить домен из URL')

    existing = db.get_site_by_url(url)
    if existing:
        if not existing.get('is_active', True):
            db.update_site(existing['id'], is_active=True)
            return jsonify({'success': True, 'site': site_view(existing),
                            'message': f'Сайт {domain} снова активен'})
        raise ValueError(f'Сайт {url} уже есть в списке')

    ip_addresses = set()
    if data.get('generate_bat', True):
        ip_addresses = generate_subnets(dns_resolver.resolve_domain(domain))

    site_id = db.add_site(domain, url, ip_addresses, set())
    if not site_id:
        return jsonify({'success': False, 'error': 'Ошибка добавления сайта'}), 500
    if ip_addresses:
        bat_generator.write_site_routes(domain, url, ip_addresses)
    return jsonify({'success': True, 'site': site_view(db.get_site(site_id)),
                    'message': f'Сайт {domain} добавлен, найдено IP: {len(ip_addresses)}'})


@app.route('/api/sites/<int:site_id>', methods=['PUT'])
@api
def update_site(site_id: int):
    update_data = {k: v for k, v in body().items() if k in ('url', 'is_active')}
    if 'url' in update_data:
        update_data['url'] = normalize_url(update_data['url'])
        domain = extract_base_domain(update_data['url'])
        if not domain:
            raise ValueError('Не удалось определить домен из URL')
        update_data['domain'] = domain
    if not db.update_site(site_id, **update_data):
        return jsonify({'success': False, 'error': 'Ошибка обновления сайта'}), 500
    return jsonify({'success': True, 'site': site_view(db.get_site(site_id)),
                    'message': 'Сайт обновлён'})


@app.route('/api/sites', methods=['DELETE'])
@api
def delete_sites():
    site_ids = body().get('site_ids') or []
    if not site_ids:
        raise ValueError('Не указаны ID сайтов')

    sites = db.get_sites_by_ids(site_ids)
    deleted = db.delete_sites(site_ids)
    # bat-файл удаляем, только если домен больше не используется другими записями.
    remaining = {s['domain'] for s in db.get_all_sites(active_only=False)}
    for site in sites:
        if site['domain'] not in remaining:
            bat_generator.delete_bat_file(site['domain'])
    return jsonify({'success': True, 'deleted': deleted, 'message': f'Удалено сайтов: {deleted}'})


@app.route('/api/sites/update-ips', methods=['POST'])
@api
def update_sites_ips():
    return _update_ips(with_html=False)


@app.route('/api/sites/update-ips-with-html', methods=['POST'])
@api
def update_sites_ips_with_html():
    return _update_ips(with_html=True)


def _update_ips(with_html: bool):
    site_ids = body().get('site_ids') or []
    if not site_ids:
        raise ValueError('Не указаны ID сайтов')
    results = refresh_site_ips(db.get_sites_by_ids(site_ids), with_html)
    ok = sum(1 for r in results if r['success'])
    return jsonify({'success': True, 'results': results, 'updated': ok, 'total': len(results),
                    'message': f'Обновлено {ok} из {len(results)}'})


@app.route('/api/generate', methods=['POST'])
@api
def generate_bat_files():
    """Генерирует bat-файлы из базы данных (без DNS-запросов)."""
    data = body()
    site_ids = data.get('site_ids') or []
    sites = db.get_sites_by_ids(site_ids) if site_ids else db.get_all_sites(active_only=True)
    if not sites:
        raise ValueError('Нет сайтов для генерации')

    if data.get('merge_all'):
        all_ips = set()
        for site in sites:
            all_ips.update(site.get('ip_addresses') or [])
        if not all_ips:
            raise ValueError('Нет IP-адресов для генерации')
        bat_generator._write_all_routes(all_ips, sites)
        return jsonify({'success': True, 'files': [bat_generator.all_routes_file],
                        'message': f'Сгенерирован {bat_generator.all_routes_file} ({len(all_ips)} маршрутов)'})

    files = []
    for site in sites:
        if site.get('ip_addresses'):
            bat_generator.write_site_routes(site['domain'], site['url'], set(site['ip_addresses']))
            files.append(bat_generator.bat_filename(site['domain']))
    files = list(dict.fromkeys(files))
    return jsonify({'success': True, 'files': files,
                    'message': f'Сгенерировано bat-файлов: {len(files)}'})


@app.route('/api/status', methods=['GET'])
@api
def get_status():
    stats = db.get_stats()
    bat_files = bat_generator.get_bat_files()
    stats['bat_files'] = len(bat_files)
    return jsonify({'success': True, 'stats': stats, 'bat_files': bat_files,
                    'timestamp': datetime.now().isoformat()})


@app.route('/api/dns/clear-cache', methods=['POST'])
@api
def clear_dns_cache():
    dns_resolver.clear_cache()
    return jsonify({'success': True, 'message': 'DNS-кэш очищен'})


@app.route('/api/bat-files', methods=['GET'])
@api
def list_bat_files():
    return jsonify({'success': True, 'files': bat_generator.get_bat_files()})


@app.route('/api/bat-files/<path:filename>', methods=['GET'])
def download_bat_file(filename: str):
    if not filename.endswith('.bat'):
        abort(404)
    return send_from_directory(os.path.abspath(bat_generator.bat_dir), filename, as_attachment=True)


@app.route('/api/parse-entries', methods=['POST'])
@api
def api_parse_entries():
    """Предпросмотр: какие строки станут записями списка доменов."""
    return jsonify({'success': True, **parse_entries(body().get('text', ''))})


# === API: роутер Keenetic ===

@app.route('/api/router/settings', methods=['GET'])
@api
def router_settings():
    return jsonify({'success': True, 'settings': settings.public()})


@app.route('/api/router/settings', methods=['PUT'])
@api
def router_settings_save():
    data = body()
    values = {k: data.get(k) for k in ('router_host', 'router_login', 'default_interface')
              if data.get(k) is not None}
    if data.get('router_password'):
        values['router_password'] = data['router_password']
    settings.update(**values)
    reset_router()
    return jsonify({'success': True, 'settings': settings.public(), 'message': 'Настройки сохранены'})


@app.route('/api/router/test', methods=['POST'])
@api
def router_test():
    reset_router()
    client = router()
    client.authenticate()
    return jsonify({'success': True, 'system': client.system_info(),
                    'message': 'Подключение к роутеру установлено'})


@app.route('/api/router/overview', methods=['GET'])
@api
def router_overview():
    client = router()
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_sys = pool.submit(client.system_info)
        f_ifaces = pool.submit(client.interfaces)
        f_groups = pool.submit(client.groups)
        f_routes = pool.submit(client.dns_routes)
        system, interfaces, groups, routes = (f.result() for f in (f_sys, f_ifaces, f_groups, f_routes))

    names = {i['id']: i for i in interfaces}
    result_groups = []
    for group in groups:
        item = group.to_dict()
        item['routes'] = [dict(r.to_dict(), interface_title=names.get(r.interface, {}).get('description') or r.interface,
                               interface_up=names.get(r.interface, {}).get('up', False))
                          for r in routes if r.group == group.name]
        result_groups.append(item)

    return jsonify({'success': True, 'system': system, 'interfaces': interfaces,
                    'groups': result_groups, 'limit': MAX_GROUP_ENTRIES,
                    'default_interface': settings.get('default_interface')})


@app.route('/api/router/groups', methods=['POST'])
@api
def router_group_create():
    data = body()
    title = (data.get('title') or '').strip()
    if not title:
        raise ValueError('Укажите название списка')
    entries = parse_entries(data.get('entries', ''))['valid']
    interface = data.get('interface') or None
    group = router().create_group(title, entries, interface,
                                  auto=bool(data.get('auto', True)),
                                  reject=bool(data.get('reject', False)))
    if interface:
        settings.update(default_interface=interface)
    return jsonify({'success': True, 'group': group.to_dict(),
                    'message': f'Список «{title}» создан, записей: {len(entries)}'})


@app.route('/api/router/groups/<name>', methods=['PUT'])
@api
def router_group_update(name: str):
    """Полностью заменяет содержимое списка (и, если передано, название)."""
    data = body()
    entries = parse_entries(data.get('entries', []))['valid']
    description = data.get('title')
    group = router().set_group_entries(name, entries,
                                       description.strip() if isinstance(description, str) else None)
    return jsonify({'success': True, 'group': group.to_dict(), 'message': 'Список сохранён'})


@app.route('/api/router/groups/<name>/entries', methods=['POST'])
@api
def router_group_add(name: str):
    """Добавляет записи в список, пропуская уже существующие."""
    parsed = parse_entries(body().get('entries', ''))
    client = router()
    group = client.get_group(name)
    if group is None:
        raise ValueError('Список не найден на роутере')
    new = [e for e in parsed['valid'] if e not in group.entries]
    if new:
        group = client.set_group_entries(name, group.entries + new)
    return jsonify({'success': True, 'group': group.to_dict(), 'added': new,
                    'invalid': parsed['invalid'],
                    'message': f'Добавлено: {len(new)}' if new else 'Все записи уже есть в списке'})


@app.route('/api/router/groups/<name>/entries', methods=['DELETE'])
@api
def router_group_remove(name: str):
    remove = {normalize_entry(e) or e for e in (body().get('entries') or [])}
    client = router()
    group = client.get_group(name)
    if group is None:
        raise ValueError('Список не найден на роутере')
    left = [e for e in group.entries if e not in remove]
    group = client.set_group_entries(name, left)
    return jsonify({'success': True, 'group': group.to_dict(),
                    'message': f'Удалено: {len(remove)}'})


@app.route('/api/router/groups/<name>', methods=['DELETE'])
@api
def router_group_delete(name: str):
    router().delete_group(name)
    return jsonify({'success': True, 'message': 'Список удалён'})


@app.route('/api/router/dns-routes', methods=['POST'])
@api
def router_dns_route_add():
    data = body()
    if not data.get('group') or not data.get('interface'):
        raise ValueError('Нужны список и интерфейс')
    router().add_dns_route(data['group'], data['interface'],
                           auto=bool(data.get('auto', True)), reject=bool(data.get('reject', False)))
    settings.update(default_interface=data['interface'])
    return jsonify({'success': True, 'message': 'Правило маршрутизации добавлено'})


@app.route('/api/router/dns-routes/toggle', methods=['POST'])
@api
def router_dns_route_toggle():
    data = body()
    router().set_dns_route_enabled(str(data.get('index', '')), bool(data.get('enabled')))
    return jsonify({'success': True, 'message': 'Правило включено' if data.get('enabled') else 'Правило выключено'})


@app.route('/api/router/dns-routes/update', methods=['POST'])
@api
def router_dns_route_update():
    """Меняет параметры правила: «эксклюзивный маршрут» и «добавлять автоматически»."""
    data = body()
    route = router().update_dns_route(str(data.get('index', '')),
                                      auto=bool(data.get('auto', True)),
                                      reject=bool(data.get('reject', False)))
    return jsonify({'success': True, 'route': route.to_dict(), 'message': 'Правило обновлено'})


@app.route('/api/router/dns-routes/delete', methods=['POST'])
@api
def router_dns_route_delete():
    router().delete_dns_route(str(body().get('index', '')))
    return jsonify({'success': True, 'message': 'Правило удалено'})


@app.route('/api/router/dns-routes/move', methods=['POST'])
@api
def router_dns_routes_move():
    """Переводит правила DNS-маршрутизации на другой интерфейс."""
    data = body()
    if not data.get('interface'):
        raise ValueError('Выберите интерфейс')
    moved = router().move_dns_routes([str(i) for i in data.get('indices') or []], data['interface'])
    settings.update(default_interface=data['interface'])
    return jsonify({'success': True, 'moved': moved, 'message': f'Переведено правил: {moved}'})


@app.route('/api/router/ip-routes/move', methods=['POST'])
@api
def router_ip_routes_move():
    """Переводит статические маршруты на другой интерфейс."""
    data = body()
    if not data.get('interface'):
        raise ValueError('Выберите интерфейс')
    moved = router().move_ip_routes([str(i) for i in data.get('indices') or []], data['interface'])
    return jsonify({'success': True, 'moved': moved, 'message': f'Переведено маршрутов: {moved}'})


@app.route('/api/router/export', methods=['GET'])
@api
def router_export():
    data = transfer.export_config(router(),
                                  include_dns=request.args.get('dns', '1') != '0',
                                  include_ip=request.args.get('ip', '1') != '0',
                                  app_name=APP_NAME)
    return jsonify({'success': True, 'data': data})


def _import_args():
    data = body()
    return dict(
        data=transfer.validate(data.get('data')),
        mode=data.get('mode', 'merge'),
        interface_map=data.get('interface_map') or {},
        group_titles=data.get('groups'),
        include_ip=bool(data.get('include_ip', True)),
        ip_comments=data.get('ip_comments'),
        ip_destinations=data.get('ip_destinations'),
    )


@app.route('/api/router/import/preview', methods=['POST'])
@api
def router_import_preview():
    plan = transfer.plan_import(router(), **_import_args())
    return jsonify({'success': True, 'summary': plan['summary'], 'commands': len(plan['commands'])})


@app.route('/api/router/import', methods=['POST'])
@api
def router_import():
    client = router()
    args = _import_args()
    # Резервная копия текущей конфигурации - чтобы импорт можно было откатить.
    os.makedirs('backups', exist_ok=True)
    backup = os.path.join(DATA_DIR, 'backups', f"before-import-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(backup, 'w', encoding='utf-8') as f:
        json.dump(transfer.export_config(client, app_name=APP_NAME), f, ensure_ascii=False, indent=2)

    plan = transfer.plan_import(client, **args)
    transfer.apply_import(client, plan)
    logger.info(f"Импорт ({args['mode']}): {plan['summary']}, резервная копия: {backup}")
    return jsonify({'success': True, 'summary': plan['summary'], 'backup': backup,
                    'message': 'Импорт выполнен'})


@app.route('/api/router/ip-routes', methods=['GET'])
@api
def router_ip_routes():
    client = router()
    names = {i['id']: i.get('description') or i['id'] for i in client.interfaces()}
    routes = [dict(r.to_dict(), interface_title=names.get(r.interface, r.interface))
              for r in client.ip_routes()]
    return jsonify({'success': True, 'routes': routes})


@app.route('/api/router/ip-routes', methods=['POST'])
@api
def router_ip_routes_add():
    """Добавляет статические маршруты: из IP сайтов (site_ids) или из списка destinations."""
    data = body()
    interface = data.get('interface')
    if not interface:
        raise ValueError('Выберите интерфейс')
    comment = (data.get('comment') or '').strip()
    auto = bool(data.get('auto', True))
    client = router()
    total = {'added': 0, 'updated': 0}

    def add(dests, note):
        result = client.add_ip_routes(dests, interface, auto=auto, comment=note)
        for k in total:
            total[k] += result[k]

    if data.get('destinations'):
        add(list(data['destinations']), comment)
    # IP сайтов: описанием становится домен - сразу понятно, чьи это адреса.
    for site in db.get_sites_by_ids(data.get('site_ids') or []):
        add(site.get('ip_addresses') or [], comment or site['domain'])

    parts = []
    if total['added']:
        parts.append(f"добавлено {total['added']}")
    if total['updated']:
        parts.append(f"дополнено описание у {total['updated']}")
    return jsonify({'success': True, **total,
                    'message': ('Маршруты: ' + ', '.join(parts)) if parts else 'Все адреса уже есть на роутере'})


@app.route('/api/router/ip-routes/comment', methods=['POST'])
@api
def router_ip_routes_comment():
    data = body()
    comment = (data.get('comment') or '').strip()
    changed = router().set_ip_routes_comment([str(i) for i in data.get('indices') or []], comment)
    return jsonify({'success': True, 'changed': changed, 'message': f'Описание обновлено у {changed}'})


@app.route('/api/router/move', methods=['POST'])
@api
def router_move():
    """Единая смена туннеля: выбранные DNS-списки и IP-маршруты переводятся на интерфейс."""
    data = body()
    interface = data.get('interface')
    if not interface:
        raise ValueError('Выберите подключение')
    client = router()
    groups = set(data.get('groups') or [])
    source = data.get('from') or ''
    dns_indices = [r.index for r in client.dns_routes()
                   if r.group in groups and (not source or r.interface == source)]
    moved_dns = client.move_dns_routes(dns_indices, interface) if dns_indices else 0
    ip_indices = [str(i) for i in data.get('ip_indices') or []]
    moved_ip = client.move_ip_routes(ip_indices, interface) if ip_indices else 0
    settings.update(default_interface=interface)
    return jsonify({'success': True, 'dns': moved_dns, 'ip': moved_ip,
                    'message': f'Переведено: правил {moved_dns}, IP-маршрутов {moved_ip}'})


@app.route('/api/router/ip-routes/delete', methods=['POST'])
@api
def router_ip_routes_delete():
    deleted = router().delete_ip_routes([str(i) for i in body().get('indices') or []])
    return jsonify({'success': True, 'deleted': deleted, 'message': f'Удалено маршрутов: {deleted}'})


@app.route('/api/router/ip-routes/toggle', methods=['POST'])
@api
def router_ip_route_toggle():
    """Включает/выключает один маршрут (index) или сразу несколько (indices)."""
    data = body()
    indices = [str(i) for i in data.get('indices') or []] or [str(data.get('index', ''))]
    enabled = bool(data.get('enabled'))
    n = router().set_ip_routes_enabled(indices, enabled)
    word = 'Включено' if enabled else 'Выключено'
    return jsonify({'success': True, 'changed': n, 'message': f'{word} маршрутов: {n}'})


# === API: анализ программ ===

@app.route('/api/capture/apps', methods=['GET'])
@api
def capture_apps():
    return jsonify({'success': True, 'apps': list_apps(include_system=request.args.get('system') == '1')})


@app.route('/api/capture/start', methods=['POST'])
@api
def capture_start():
    session = capture.start(body(), request.host_url)
    logger.info(f'Анализ программ начат: {session.target.to_dict()}')
    return jsonify({'success': True, 'session': capture.state()})


@app.route('/api/capture/stop', methods=['POST'])
@api
def capture_stop():
    capture.stop()
    return jsonify({'success': True, 'session': capture.state()})


@app.route('/api/capture/elevate', methods=['POST'])
@api
def capture_elevate():
    """Включает полный режим в уже идущем анализе."""
    capture.enable_full(request.host_url)
    return jsonify({'success': True, 'session': capture.state()})


@app.route('/api/capture/state', methods=['GET'])
@api
def capture_state():
    return jsonify({'success': True, 'session': capture.state(), 'agent': capture.agent.snapshot()})


@app.route('/api/capture/agent', methods=['POST'])
def capture_agent():
    """Данные от общего сборщика (ключ действует до закрытия RouteDeck)."""
    if not capture.agent.check_token(request.headers.get('X-Capture-Token', '')):
        return jsonify({'success': False, 'stop': True}), 403
    return jsonify({'success': True, **capture.agent.handle(body())})


@app.route('/api/capture/history', methods=['GET'])
@api
def capture_history():
    return jsonify({'success': True, 'programs': capture.store.programs()})


@app.route('/api/capture/program', methods=['GET'])
@api
def capture_program():
    name = request.args.get('name', '')
    return jsonify({'success': True, 'name': name, **capture.store.program_results(name)})


@app.route('/api/capture/program/delete', methods=['POST'])
@api
def capture_program_delete():
    name = body().get('name', '')
    if capture.session and capture.session.running and any(a.name == name for a in capture.session.target.apps):
        raise ValueError('Сначала остановите анализ этой программы')
    deleted = capture.store.delete_program(name)
    return jsonify({'success': True, 'deleted': deleted, 'message': f'История «{name}» удалена'})


# === Ошибки ===

@app.errorhandler(404)
def not_found(error):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': 'Не найдено'}), 404
    return error


if __name__ == '__main__':
    host = os.environ.get('S4R_HOST', '127.0.0.1')
    port = int(os.environ.get('S4R_PORT', '5000'))
    debug = os.environ.get('S4R_DEBUG') == '1'
    logger.info(f'{APP_NAME} запущен: http://{host}:{port}')
    app.run(host=host, port=port, debug=debug, threaded=True)
