"""Regression scenarios for route loss. No requests reach a real router."""
import copy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from services.keenetic import (KeeneticClient, KeeneticError, KeeneticWriteUncertainError,
                              IpRoute, DnsRoute, DomainGroup, _ip_key, _route_signature)


class Router(KeeneticClient):
    """RCI executes the remainder of a batch even after a rejected command."""
    def __init__(self, ip=(), dns=()):
        super().__init__('router.invalid', 'test', '')
        self.ip = copy.deepcopy(list(ip))
        self.dns = copy.deepcopy(list(dns))
        self.available = ['old', 'new', 'other']
        self.writes = []
        self.counter = 10000
        self.reject_destinations = set()
        self.drop_destinations = set()
        self.reindex = False
        self.rewrite = False
        self.timeout_add = False
        self.fail_readback = False
        self.reject_dns_once = False
        self.ignore_dns_once = False
        self.before_write = None
        self.read_error = False

    def interfaces(self):
        return [{'id': i, 'description': i, 'up': i != 'new', 'tunnel': True} for i in self.available]

    def system_info(self):
        return {'model': 'Test', 'release': '5.0'}

    def groups(self):
        return [DomainGroup('group', 'Sites', ['example.org'])]

    def ip_routes(self):
        if self.read_error:
            self.read_error = False
            raise KeeneticError('Readback unavailable')
        return copy.deepcopy(self.ip)

    def dns_routes(self):
        return copy.deepcopy(self.dns)

    def write(self, commands, save=True):
        with self._lock:
            if self.before_write:
                self.before_write()
            self.writes.append((copy.deepcopy(commands), save))
            error = None
            added = False
            for command in commands:
                kind = 'ip' if 'ip' in command else 'dns-proxy'
                data = command[kind]['route']
                rows = self.ip if kind == 'ip' else self.dns
                if data.get('no'):
                    if 'index' in data:
                        rows[:] = [r for r in rows if r.index != data['index']]
                    elif kind == 'ip':
                        key = _ip_key(self.parse_ip(data))
                        rows[:] = [r for r in rows if _ip_key(r) != key]
                    else:
                        rows[:] = [r for r in rows if (r.group, r.interface) != (data['group'], data['interface'])]
                    continue
                added = True
                if kind == 'ip':
                    route = self.parse_ip(data)
                    if route.destination in self.reject_destinations or route.interface not in self.available:
                        error = KeeneticError('Rejected route')
                        continue  # RCI does NOT roll back or stop the batch.
                    if route.destination in self.drop_destinations:
                        continue
                    old = next((r for r in rows if _ip_key(r) == _ip_key(route)), None)
                    if old is None and self.rewrite:
                        old = next((r for r in rows if r.destination == route.destination), None)
                else:
                    route = DnsRoute('', data['group'], data.get('interface', ''),
                                     data.get('auto', False), data.get('reject', False), not data.get('disable', False))
                    if self.reject_dns_once:
                        self.reject_dns_once = False
                        error = KeeneticError('Rejected DNS options')
                        continue
                    if self.ignore_dns_once:
                        self.ignore_dns_once = False
                        route.reject = not route.reject
                    old = next((r for r in rows if (r.group, r.interface) == (route.group, route.interface)), None)
                if old is not None:
                    route.index = old.index
                    rows.remove(old)
                else:
                    self.counter += 1
                    route.index = str(self.counter)
                rows.append(route)
                if self.reindex:
                    for r in rows:
                        self.counter += 1
                        r.index = str(self.counter)
            if added and self.fail_readback:
                self.read_error = True
            if added and self.timeout_add:
                raise KeeneticWriteUncertainError('Write timed out')
            if error:
                raise error
            return {}

    @staticmethod
    def parse_ip(data):
        import ipaddress
        destination = '0.0.0.0/0' if data.get('default') else data.get('host', '') + '/32' if data.get('host') else str(
            ipaddress.ip_network(f"{data['network']}/{data['mask']}", strict=False))
        return IpRoute('', destination, data.get('interface', ''), data.get('gateway', ''),
                       data.get('auto', False), data.get('reject', False), not data.get('disable', False),
                       data.get('comment', ''), data.get('metric'))


def ip(index='1', destination='8.8.8.8/32', interface='old', **kwargs):
    options = {'auto': True, 'comment': 'Selected'}
    options.update(kwargs)
    return IpRoute(index, destination, interface, **options)


def signatures(routes):
    return {_route_signature(r) for r in routes}


class MoveTests(unittest.TestCase):
    def test_only_checked_noncontiguous_routes_move(self):
        router = Router([ip(), ip('2', '1.1.1.0/24'), ip('3', '9.9.9.9/32')])
        untouched = _route_signature(router.ip[1])
        selected = [router.ip[0].to_dict(), router.ip[2].to_dict()]
        self.assertEqual(router.move_ip_routes(['1', '3'], 'new', selected), 2)
        self.assertEqual(len(router.ip), 3)
        self.assertIn(untouched, signatures(router.ip))
        self.assertEqual({r.destination for r in router.ip if r.interface == 'new'}, {'8.8.8.8/32', '9.9.9.9/32'})
        self.assertFalse(any('index' in c['ip']['route'] for c in router.writes[-1][0]))
        self.assertFalse(any(c['ip']['route'].get('no') for c in router.writes[0][0]))

    def test_large_group_preserves_every_address(self):
        routes = [ip(str(i), f'11.{i // 256}.{i % 256}.0/24') for i in range(1400)]
        router = Router(routes)
        self.assertEqual(router.move_ip_routes([r.index for r in routes], 'new'), 1400)
        self.assertEqual({r.destination for r in routes}, {r.destination for r in router.ip})
        self.assertTrue(all(r.interface == 'new' for r in router.ip))

    def test_deleted_source_tunnel_can_be_replaced(self):
        router = Router([ip()]); router.available.remove('old')
        router.move_ip_routes(['1'], 'new')
        self.assertEqual([(r.destination, r.interface) for r in router.ip], [('8.8.8.8/32', 'new')])

    def test_deleted_target_blocks_before_any_write(self):
        router = Router([ip()]); router.available.remove('new')
        with self.assertRaisesRegex(KeeneticError, 'подключение'):
            router.move_ip_routes(['1'], 'new')
        self.assertEqual(router.writes, [])

    def test_disconnected_target_is_valid_and_flags_survive(self):
        route = ip(reject=True, enabled=False, gateway='192.0.2.1', metric=7)
        router = Router([route]); router.move_ip_routes(['1'], 'new')
        result = router.ip[0]
        self.assertEqual((result.auto, result.reject, result.enabled, result.comment, result.gateway, result.metric),
                         (True, True, False, 'Selected', '', 7))

    def test_same_target_is_noop(self):
        router = Router([ip(interface='new')])
        self.assertEqual(router.move_ip_routes(['1'], 'new'), 0)
        self.assertEqual(router.writes, [])

    def test_existing_identical_target_is_kept(self):
        router = Router([ip(), ip('2', interface='new')]); router.move_ip_routes(['1'], 'new')
        self.assertEqual([r.index for r in router.ip], ['2'])
        self.assertTrue(all(c['ip']['route'].get('no') for c in router.writes[0][0]))

    def test_conflicting_target_does_not_erase_group(self):
        router = Router([ip(), ip('2', interface='new', reject=True)])
        before = signatures(router.ip)
        with self.assertRaisesRegex(KeeneticError, 'другим'):
            router.move_ip_routes(['1'], 'new')
        self.assertEqual(signatures(router.ip), before)
        self.assertFalse(router.writes)

    def test_rejected_addition_cannot_delete_any_source(self):
        router = Router([ip(), ip('2', '1.1.1.1/32')])
        router.reject_destinations.add('1.1.1.1/32')
        before = signatures(router.ip)
        with self.assertRaises(KeeneticError):
            router.move_ip_routes(['1', '2'], 'new')
        self.assertTrue(before <= signatures(router.ip))
        self.assertFalse(any(c['ip']['route'].get('no') for batch, _ in router.writes for c in batch))

    def test_silent_ignored_addition_blocks_deletion(self):
        router = Router([ip()]); router.drop_destinations.add('8.8.8.8/32')
        with self.assertRaisesRegex(KeeneticError, 'не подтвердил'):
            router.move_ip_routes(['1'], 'new')
        self.assertEqual(router.ip[0].interface, 'old')
        self.assertEqual(len(router.writes), 1)

    def test_timeout_never_replays_or_deletes(self):
        router = Router([ip()]); router.timeout_add = True
        with self.assertRaises(KeeneticWriteUncertainError):
            router.move_ip_routes(['1'], 'new')
        self.assertEqual(len(router.writes), 1)
        self.assertTrue(any(r.interface == 'old' for r in router.ip))

    def test_failed_readback_blocks_deletion(self):
        router = Router([ip()]); router.fail_readback = True
        with self.assertRaises(KeeneticError):
            router.move_ip_routes(['1'], 'new')
        self.assertEqual(len(router.writes), 1)
        self.assertTrue(any(r.interface == 'old' for r in router.ip))

    def test_router_reusing_index_does_not_delete_replacement(self):
        router = Router([ip()]); router.rewrite = True
        router.move_ip_routes(['1'], 'new')
        self.assertEqual([(r.index, r.interface) for r in router.ip], [('1', 'new')])
        self.assertEqual(router.writes[-1][0], [])

    def test_router_reindexing_does_not_affect_deletion(self):
        router = Router([ip(), ip('2', '1.1.1.1/32')]); router.reindex = True
        router.move_ip_routes(['1', '2'], 'new')
        self.assertEqual(len(router.ip), 2)
        self.assertTrue(all(r.interface == 'new' for r in router.ip))

    def test_stale_selection_is_rejected_before_writing(self):
        for changed in ('missing', 'interface', 'comment', 'enabled'):
            with self.subTest(changed=changed):
                router = Router([ip()]); snapshot = router.ip[0].to_dict()
                if changed == 'missing': router.ip.clear()
                else: setattr(router.ip[0], changed, {'interface': 'other', 'comment': 'Changed', 'enabled': False}[changed])
                with self.assertRaisesRegex(KeeneticError, 'изменил'):
                    router.move_ip_routes(['1'], 'new', [snapshot])
                self.assertEqual(router.writes, [])

    def test_unselected_coincident_route_blocks_broad_deletion(self):
        router = Router([ip(), ip('2', comment='Another group')])
        with self.assertRaisesRegex(KeeneticError, 'других группах'):
            router.move_ip_routes(['1'], 'new')
        self.assertEqual(len(router.ip), 2)
        self.assertEqual(router.writes, [])

    def test_source_changed_after_add_is_not_deleted(self):
        router = Router([ip()]); original_reader = router.ip_routes
        def read():
            if router.writes:
                next(r for r in router.ip if r.interface == 'old').comment = 'Changed on router'
            return original_reader()
        router.ip_routes = read
        with self.assertRaisesRegex(KeeneticError, 'изменились во время'):
            router.move_ip_routes(['1'], 'new')
        self.assertTrue(any(r.interface == 'old' for r in router.ip))
        self.assertEqual(len(router.writes), 1)

    def test_target_removed_after_add_stops_source_deletion(self):
        router = Router([ip()]); original_reader = router.ip_routes
        def read():
            if router.writes and 'new' in router.available:
                router.available.remove('new')
            return original_reader()
        router.ip_routes = read
        with self.assertRaisesRegex(KeeneticError, 'подключение'):
            router.move_ip_routes(['1'], 'new')
        self.assertTrue(any(r.interface == 'old' for r in router.ip))
        self.assertEqual(len(router.writes), 1)

    def test_concurrent_move_cannot_use_intermediate_table(self):
        router = Router([ip()]); entered = threading.Event(); release = threading.Event(); errors = []
        def block():
            if not entered.is_set():
                entered.set(); release.wait(3)
        router.before_write = block
        def move(target):
            try: router.move_ip_routes(['1'], target)
            except KeeneticError as e: errors.append(e)
        one = threading.Thread(target=move, args=('new',)); one.start()
        self.assertTrue(entered.wait(3))
        two = threading.Thread(target=move, args=('other',)); two.start()
        self.assertTrue(two.is_alive())
        release.set(); one.join(3); two.join(3)
        self.assertFalse(one.is_alive() or two.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual([r.interface for r in router.ip], ['new'])


class DnsTests(unittest.TestCase):
    def route(self, **kwargs):
        return DnsRoute('dns1', 'group', 'old', **kwargs)

    def test_move_preserves_exclusive_auto_and_disabled(self):
        router = Router(dns=[self.route(auto=True, reject=True, enabled=False)])
        router.reindex = True
        router.move_dns_routes(['dns1'], 'new')
        self.assertEqual(len(router.dns), 1)
        self.assertEqual((router.dns[0].interface, router.dns[0].auto, router.dns[0].reject, router.dns[0].enabled),
                         ('new', True, True, False))

    def test_rejected_move_keeps_original(self):
        router = Router(dns=[self.route()]); router.reject_dns_once = True
        before = signatures(router.dns)
        with self.assertRaises(KeeneticError): router.move_dns_routes(['dns1'], 'new')
        self.assertEqual(signatures(router.dns), before)

    def test_unselected_duplicate_rule_blocks_before_write(self):
        router = Router(dns=[self.route(), DnsRoute('dns2', 'group', 'old', reject=True)])
        with self.assertRaisesRegex(KeeneticError, 'совпадающие'):
            router.move_dns_routes(['dns1'], 'new')
        self.assertEqual(len(router.dns), 2)
        self.assertEqual(router.writes, [])

    def test_options_are_applied_and_read_back(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                router = Router(dns=[self.route(enabled=enabled)])
                actual = router.update_dns_route('dns1', auto=True, reject=True)
                self.assertEqual((actual.auto, actual.reject, actual.enabled), (True, True, enabled))
                self.assertNotEqual(actual.index, 'dns1')
                self.assertTrue(router.writes[-1][1])

    def test_rejected_options_restore_original_flags(self):
        router = Router(dns=[self.route(auto=False, reject=False, enabled=False)])
        before = signatures(router.dns); router.reject_dns_once = True
        with self.assertRaisesRegex(KeeneticError, 'Исходное'):
            router.update_dns_route('dns1', auto=True, reject=True)
        self.assertEqual(signatures(router.dns), before)

    def test_ignored_flags_are_not_reported_as_success(self):
        router = Router(dns=[self.route()]); before = signatures(router.dns)
        router.ignore_dns_once = True
        with self.assertRaisesRegex(KeeneticError, 'не подтвердил'):
            router.update_dns_route('dns1', auto=True, reject=True)
        self.assertEqual(signatures(router.dns), before)

    def test_options_do_not_remove_unselected_duplicate(self):
        router = Router(dns=[self.route(), DnsRoute('dns2', 'group', 'old', reject=True)])
        with self.assertRaisesRegex(KeeneticError, 'совпадающие'):
            router.update_dns_route('dns1', auto=True, reject=True)
        self.assertEqual(len(router.dns), 2)
        self.assertEqual(router.writes, [])

    def test_stale_options_write_nothing(self):
        router = Router(dns=[self.route()]); expected = router.dns[0].to_dict(); router.dns[0].reject = True
        with self.assertRaises(KeeneticError): router.update_dns_route('dns1', True, True, expected)
        self.assertEqual(router.writes, [])

    def test_timeout_does_not_blindly_recreate_rule(self):
        router = Router(dns=[self.route()]); router.timeout_add = True
        with self.assertRaises(KeeneticWriteUncertainError): router.update_dns_route('dns1', True, True)
        self.assertEqual(len(router.writes), 1)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ['S4R_DATA_DIR'] = cls.dir.name
        import app
        cls.module = app
        cls.client = app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        os.chdir(Path(__file__).resolve().parents[1]); cls.dir.cleanup()

    def setUp(self):
        self.data = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.router = Router([ip()], [DnsRoute('dns1', 'group', 'old')])
        self.patches = [patch.object(self.module, 'router', return_value=self.router),
                        patch.object(self.module, 'DATA_DIR', self.data.name),
                        patch.object(self.module, 'remember_interface')]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.data.cleanup()

    def post(self, url, **data):
        return self.client.post(url, json=data)

    def test_backup_exists_before_first_move_command(self):
        import json
        observed = []
        def before():
            files = list(Path(self.data.name).glob('backups/*.json'))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text('utf-8'))
            observed.append(data['ip_routes'][0]['interface'])
            self.assertEqual(data['ip_routes_snapshot'][0]['index'], '1')
        self.router.before_write = before
        result = self.post('/api/router/ip-routes/move', indices=['1'], interface='new',
                           expected_routes=[self.router.ip[0].to_dict()])
        self.assertEqual(result.status_code, 200)
        self.assertTrue(Path(result.get_json()['backup']).exists())
        self.assertEqual(observed, ['old', 'old'])

    def test_backup_failure_aborts_without_changes(self):
        with patch.object(self.module, 'backup_router_change', side_effect=OSError('Disk full')):
            result = self.post('/api/router/ip-routes/move', indices=['1'], interface='new')
        self.assertEqual(result.status_code, 500)
        self.assertEqual(self.router.writes, [])

    def test_unified_move_validates_ip_before_moving_dns(self):
        result = self.post('/api/router/move', groups=['group'], ip_indices=['missing'], interface='new')
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.router.writes, [])
        self.assertEqual(self.router.dns[0].interface, 'old')

    def test_unified_move_preserves_checked_dns_and_ip(self):
        result = self.post('/api/router/move', groups=['group'], ip_indices=['1'], interface='new',
                           expected_dns_routes=[self.router.dns[0].to_dict()],
                           expected_ip_routes=[self.router.ip[0].to_dict()])
        self.assertEqual(result.status_code, 200)
        self.assertEqual((result.get_json()['dns'], result.get_json()['ip']), (1, 1))
        self.assertEqual((self.router.dns[0].interface, self.router.ip[0].interface), ('new', 'new'))

    def test_dns_options_backup_contains_original_flags(self):
        import json
        result = self.post('/api/router/dns-routes/update', index='dns1', auto=True, reject=True,
                           expected_route=self.router.dns[0].to_dict())
        self.assertEqual(result.status_code, 200)
        data = result.get_json()
        self.assertTrue(data['route']['reject'])
        backup = json.loads(Path(data['backup']).read_text('utf-8'))
        self.assertFalse(backup['groups'][0]['routes'][0]['reject'])

    def test_rejected_add_reports_error_with_sources_and_backup_intact(self):
        self.router.reject_destinations.add('8.8.8.8/32')
        result = self.post('/api/router/ip-routes/move', indices=['1'], interface='new')
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.router.ip[0].interface, 'old')
        self.assertEqual(len(list(Path(self.data.name).glob('backups/*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
