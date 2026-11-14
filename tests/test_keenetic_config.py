"""Real-client transport regressions: running state differs from saved GET snapshots."""
import copy
import unittest
from unittest.mock import patch

import requests
from services import keenetic
from services.keenetic import KeeneticClient, KeeneticError, KeeneticWriteUncertainError


class Clock:
    def __init__(self): self.now = 0
    def monotonic(self): return self.now
    def sleep(self, delay): self.now += delay


class Response:
    status_code = 200
    content = b'json'
    def __init__(self, data): self.data = data
    def json(self): return copy.deepcopy(self.data)


class ConfigSession:
    """HTTP-level fixture. Unsaved commands never change the saved GET view."""
    def __init__(self):
        self.dns = [dict(index='source', group='group1', interface='Wireguard2', auto=True, reject=True),
                    dict(index='other', group='group2', interface='Wireguard2', auto=True, reject=False)]
        self.ip = [dict(index='ip-source', host='198.51.100.10', interface='Wireguard2', auto=True,
                        comment='Selected', disable=True, metric=7),
                   dict(index='ip-other', host='203.0.113.20', interface='Wireguard2', auto=True, comment='Other')]
        self.groups = {'group1': {'description': 'Example', 'include': [{'address': 'example.org'}]},
                       'group2': {'description': 'Other', 'include': [{'address': 'other.example.org'}]}}
        self.saved = self.config()
        self.calls, self.mutations, self.checkpoints = [], [], []
        self.counter = 0
        self.fail_save = False
        self.fail_read = False
        self.freeze_running_view = False

    def config(self):
        return copy.deepcopy({'dns-proxy': {'route': self.dns}, 'ip': {'route': self.ip},
                              'object-group': {'fqdn': self.groups}})

    @staticmethod
    def at_path(data, parts):
        for part in parts: data = data.get(part, {}) if isinstance(data, dict) else {}
        return data

    def request(self, method, url, json=None, **kwargs):
        self.calls.append((method, url, copy.deepcopy(json)))
        if method == 'GET':
            endpoint = url.split('/rci/', 1)[1]
            if endpoint == 'show/interface':
                return Response({i: {'type': 'Wireguard', 'state': 'up', 'description': i}
                                 for i in ('Wireguard1', 'Wireguard2')})
            if endpoint == 'show/version': return Response({'model': 'Test', 'title': '5.2'})
            if endpoint.startswith('show/sc/'):
                return Response(self.at_path(self.saved, endpoint.split('/')[2:]))
            raise AssertionError('Unexpected GET ' + endpoint)
        if all('show' in command for command in json):
            if self.fail_read: raise requests.ReadTimeout('Read-only query timed out')
            output = []
            for command in json:
                kind, path = next(iter(command['show'].items()))
                data = self.saved if kind == 'sc' or self.freeze_running_view else self.config()
                def select(query, actual):
                    return {k: select(v, actual.get(k, {})) if v else copy.deepcopy(actual.get(k, {}))
                            for k, v in query.items()}
                output.append({'show': {kind: select(path, data)}})
            return Response(output)
        self.mutations.append(copy.deepcopy(json))
        output = []
        for command in json:
            if 'system' in command:
                if self.fail_save:
                    self.fail_save = False
                    output.append({'system': {'configuration': {'save': {'status': [
                        {'status': 'error', 'message': 'Save rejected'}]}}}})
                    continue
                self.saved = self.config(); self.checkpoints.append(copy.deepcopy(self.saved))
                output.append({'system': {'configuration': {'save': {}}}})
                continue
            kind = 'dns-proxy' if 'dns-proxy' in command else 'ip'
            data = command[kind]['route']
            rows = self.dns if kind == 'dns-proxy' else self.ip
            if data.get('no'):
                if 'index' in data: rows[:] = [r for r in rows if r['index'] != data['index']]
                else:
                    selectors = ('group', 'interface') if kind == 'dns-proxy' else ('host', 'network', 'mask', 'interface', 'gateway', 'metric')
                    rows[:] = [r for r in rows if not all(r.get(k) == data[k] for k in selectors if k in data)]
            else:
                self.counter += 1
                rows.append(dict(data, index='added-' + str(self.counter)))
            output.append({kind: {'route': {'status': [{'status': 'message', 'message': 'Applied'}]}}})
        return Response(output)


def client(session=None, cls=KeeneticClient):
    c = cls('router.invalid', 'test', '')
    c._session = session or ConfigSession()
    c._authenticated = True
    return c


class RunningConfigTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.patch = patch.object(keenetic, 'time', self.clock)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def test_explicit_running_query_sees_unsaved_dns_while_get_remains_old(self):
        session = ConfigSession()
        session.dns.append(dict(index='pending', group='group1', interface='Wireguard1', auto=True, reject=True))
        c = client(session)
        self.assertEqual(len(c.get('show/sc/dns-proxy/route')), 2)
        self.assertEqual(len(c.dns_routes()), 3)
        self.assertEqual(session.mutations, [])
        self.assertEqual(session.calls[-1][2], [{'show': {'rc': {'dns-proxy': {'route': {}}}}}])

    def test_dns_move_uses_running_state_and_saves_copies_before_removal(self):
        c = client(); session = c._session; other = copy.deepcopy(session.dns[1]); ips = copy.deepcopy(session.ip)
        self.assertEqual(c.move_dns_routes(['source'], 'Wireguard1'), 1)
        self.assertEqual([r['interface'] for r in session.dns if r['group'] == 'group1'], ['Wireguard1'])
        self.assertIn(other, session.dns); self.assertEqual(session.ip, ips)
        self.assertEqual(len(session.mutations), 2)
        self.assertEqual(len(session.checkpoints[0]['dns-proxy']['route']), 3)
        self.assertEqual(len(session.checkpoints[-1]['dns-proxy']['route']), 2)
        self.assertTrue(all('system' in batch[-1] for batch in session.mutations))
        self.assertFalse(any('/show/sc/' in url for _, url, _ in session.calls))
        self.assertEqual(self.clock.now, 0)

    def test_pending_target_from_failed_previous_attempt_is_preserved(self):
        session = ConfigSession()
        session.dns.append(dict(index='pending', group='group1', interface='Wireguard1', auto=True, reject=True))
        c = client(session); c.move_dns_routes(['source'], 'Wireguard1')
        self.assertEqual(len(session.mutations), 1)
        self.assertEqual([r['index'] for r in session.dns if r['group'] == 'group1'], ['pending'])

    def test_ip_move_preserves_other_routes_and_disabled_metric(self):
        c = client(); session = c._session; other = copy.deepcopy(session.ip[1])
        self.assertEqual(c.move_ip_routes(['ip-source'], 'Wireguard1'), 1)
        moved = next(r for r in c.ip_routes() if r.destination == '198.51.100.10/32')
        self.assertEqual((moved.interface, moved.enabled, moved.metric, moved.comment), ('Wireguard1', False, 7, 'Selected'))
        self.assertIn(other, session.ip)
        self.assertTrue(all('system' in batch[-1] for batch in session.mutations))

    def test_real_client_single_group_mode_keeps_only_selected_interface(self):
        c = client(); session = c._session
        session.dns.extend([dict(index='third', group='group1', interface='Wireguard0', auto=True, reject=True),
                            dict(index='target', group='group1', interface='Wireguard1', auto=True, reject=True)])
        ips = copy.deepcopy(session.ip); other = copy.deepcopy(session.dns[1])
        snapshots = [r.to_dict() for r in c.dns_routes() if r.group == 'group1']
        self.assertEqual(c.move_dns_routes([r['index'] for r in snapshots], 'Wireguard1', snapshots, True), 2)
        self.assertEqual([r['index'] for r in session.dns if r['group'] == 'group1'], ['target'])
        self.assertIn(other, session.dns); self.assertEqual(session.ip, ips)
        self.assertEqual(len(session.mutations), 1)

    def test_options_are_verified_from_live_flags(self):
        c = client(); result = c.update_dns_route('source', auto=False, reject=False)
        self.assertEqual((result.auto, result.reject, result.enabled), (False, False, True))
        self.assertIn('system', c._session.mutations[0][-1])
        self.assertEqual(self.clock.now, 0)

    def test_groups_are_read_from_running_configuration(self):
        c = client(); c._session.groups['new-group'] = {'description': 'Unsaved', 'include': [{'address': 'new.example.org'}]}
        self.assertIn('new-group', [g.name for g in c.groups()])
        self.assertEqual(c._session.mutations, [])
        self.assertEqual(c._session.calls[-1][2], [{'show': {'rc': {'object-group': {'fqdn': {}}}}}])

    def test_save_failure_stops_before_any_source_deletion(self):
        c = client(); c._session.fail_save = True
        with self.assertRaisesRegex(KeeneticError, 'Save rejected'): c.move_dns_routes(['source'], 'Wireguard1')
        self.assertTrue(any(r['index'] == 'source' for r in c._session.dns))
        self.assertEqual(len(c._session.mutations), 1)
        self.assertFalse(any(c.get('dns-proxy', {}).get('route', {}).get('no') for c in c._session.mutations[0]))

    def test_unconfirmed_running_target_still_does_not_delete_source(self):
        c = client(); session = c._session; session.freeze_running_view = True
        # Even save acknowledgements can be unreliable: keep the read view frozen.
        original_config = session.config()
        session.config = lambda: copy.deepcopy(original_config)
        with self.assertRaisesRegex(KeeneticError, 'Исходные правила не удалялись'):
            c.move_dns_routes(['source'], 'Wireguard1')
        self.assertEqual(len(session.mutations), 1)
        self.assertTrue(any(r['index'] == 'source' for r in session.dns))

    def test_read_only_post_timeout_is_not_an_uncertain_write(self):
        c = client(); c._session.fail_read = True
        with self.assertRaises(KeeneticError) as caught: c.dns_routes()
        self.assertNotIsInstance(caught.exception, KeeneticWriteUncertainError)
        self.assertEqual(c._session.mutations, [])

    def test_malformed_or_saved_envelope_is_not_accepted_as_live_config(self):
        for payload in ({}, [], [{'show': {'sc': {}}}], [{'show': {'rc': []}}]):
            with self.subTest(payload=payload):
                c = client(); c._request = lambda *a, **k: payload
                with self.assertRaises(KeeneticError): c.dns_routes()

    def test_unconfigured_running_section_is_empty(self):
        c = client(); c._request = lambda *a, **k: [{'show': {'rc': {}}}]
        self.assertEqual(c.dns_routes(), [])
        self.assertEqual(c.ip_routes(), [])
        self.assertEqual(c.groups(), [])


if __name__ == '__main__':
    unittest.main()
