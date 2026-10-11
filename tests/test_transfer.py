"""Offline tests for importing an exported configuration onto a router."""
import unittest

from services import transfer
from services.keenetic import MAX_GROUP_ENTRIES, DnsRoute, DomainGroup, IpRoute


class FakeRouter:
    def __init__(self, groups=(), routes=(), ip_routes=()):
        self._groups, self._routes, self._ip = list(groups), list(routes), list(ip_routes)

    def interfaces(self):
        return [{'id': 'Wireguard0', 'description': 'VPN'}, {'id': 'Wireguard1', 'description': 'VPN 2'}]

    def groups(self):
        return [DomainGroup(g.name, g.description, list(g.entries)) for g in self._groups]

    def dns_routes(self):
        return list(self._routes)

    def ip_routes(self):
        return [IpRoute(**r.to_dict()) for r in self._ip]


def export(groups=(), ip_routes=()):
    return {'app': 'RouteDeck', 'format': 1, 'interfaces': [{'id': 'wg', 'title': 'VPN'}],
            'groups': list(groups), 'ip_routes': list(ip_routes)}


def ip(destination, comment='', interface='wg'):
    return {'destination': destination, 'interface': interface, 'auto': True,
            'reject': False, 'enabled': True, 'comment': comment}


class ImportTests(unittest.TestCase):
    def plan(self, router, data, mode='merge'):
        return transfer.plan_import(router, data, mode, {'wg': 'Wireguard0'})

    def test_existing_ip_is_skipped_but_description_updated(self):
        router = FakeRouter(ip_routes=[
            IpRoute('1', '1.1.1.0/24', 'Wireguard0', comment='Старое'),
            IpRoute('2', '2.2.2.2/32', 'Wireguard0', comment='Discord'),
            IpRoute('3', '3.3.3.3/32', 'Wireguard1'),
        ])
        plan = self.plan(router, export(ip_routes=[
            ip('1.1.1.0/24', 'Discord'), ip('2.2.2.2/32', 'Discord'),
            ip('3.3.3.3/32', 'Discord'), ip('4.4.4.4/32', 'Discord')]))
        s = plan['summary']
        self.assertEqual((s['ip_added'], s['ip_existing'], s['ip_updated'], s['ip_removed']), (1, 3, 2, 0))
        self.assertEqual(s['ip_other_interface'], 1)
        routes = [c['ip']['route'] for c in plan['commands']]
        self.assertFalse(any(r.get('no') for r in routes), 'обычный импорт ничего не удаляет')
        self.assertEqual(sum(1 for r in routes if r.get('comment') == 'Discord'), 3)

    def test_lists_are_extended_without_removing_entries(self):
        router = FakeRouter(groups=[DomainGroup('domain-list0', 'Telegram', ['a.com', 'b.com'])],
                            routes=[DnsRoute('r0', 'domain-list0', 'Wireguard0', auto=True)])
        plan = self.plan(router, export(groups=[
            {'title': 'Telegram', 'entries': ['b.com', 'c.com'],
             'routes': [{'interface': 'wg', 'auto': True, 'reject': False, 'enabled': True}]},
            {'title': 'Новый', 'entries': ['d.com'], 'routes': []}]))
        s = plan['summary']
        self.assertEqual((s['lists_updated'], s['lists_created'], s['entries_added'], s['entries_existing']), (1, 1, 2, 1))
        self.assertEqual(s['rules_added'], 0, 'правило через то же подключение уже есть')
        self.assertIn({'object-group': {'fqdn': {'domain-list0': {'include': [{'address': 'c.com'}]}}}}, plan['commands'])
        self.assertFalse(any('no' in str(c) for c in plan['commands']))

    def test_full_list_continues_in_next_list(self):
        full = [f'site{i}.com' for i in range(MAX_GROUP_ENTRIES)]
        router = FakeRouter(groups=[DomainGroup('domain-list0', 'Big', full)])
        plan = self.plan(router, export(groups=[{'title': 'Big', 'entries': full + ['extra.com'], 'routes': []}]))
        s = plan['summary']
        self.assertEqual((s['lists_created'], s['entries_added'], s['entries_existing']), (1, 1, MAX_GROUP_ENTRIES))
        created = plan['commands'][0]['object-group']['fqdn']['domain-list1']
        self.assertEqual(created['description'], 'Big (2)')

    def test_replace_all_clears_router_first(self):
        router = FakeRouter(groups=[DomainGroup('domain-list0', 'Old', ['a.com'])],
                            routes=[DnsRoute('r0', 'domain-list0', 'Wireguard0')],
                            ip_routes=[IpRoute('1', '0.0.0.0/0', 'ISP'), IpRoute('2', '1.1.1.1/32', 'Wireguard0')])
        plan = self.plan(router, export(groups=[{'title': 'New', 'entries': ['b.com'], 'routes': []}],
                                        ip_routes=[ip('1.1.1.1/32')]), mode='replace_all')
        s = plan['summary']
        self.assertEqual((s['lists_removed'], s['rules_removed'], s['ip_removed'], s['ip_added']), (1, 1, 1, 1))

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(FakeRouter(), export(), mode='replace')


if __name__ == '__main__':
    unittest.main()
