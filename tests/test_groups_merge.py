"""Offline tests for merging DNS lists on the router."""
import unittest

from services.keenetic import MAX_GROUP_ENTRIES, DnsRoute, DomainGroup, KeeneticClient


class FakeRouter:
    merge_groups = KeeneticClient.merge_groups

    def __init__(self, groups, routes):
        self._groups = {g.name: g for g in groups}
        self._routes = list(routes)
        self.added, self.deleted = [], []

    def groups(self):
        return list(self._groups.values())

    def dns_routes(self):
        return list(self._routes)

    def set_group_entries(self, name, entries, description=None):
        g = self._groups[name]
        self._groups[name] = DomainGroup(name, description or g.description, list(entries))
        return self._groups[name]

    def add_dns_route(self, group, interface, auto=True, reject=False):
        self.added.append((group, interface, auto, reject))

    def delete_group(self, name):
        self.deleted.append(name)
        del self._groups[name]


def route(index, group, interface, reject=False):
    return DnsRoute(index=index, group=group, interface=interface, auto=True, reject=reject, enabled=True)


class MergeTests(unittest.TestCase):
    def test_entries_union_rules_moved_sources_deleted(self):
        router = FakeRouter(
            [DomainGroup('domain-list0', 'AI', ['openai.com', 'claude.ai']),
             DomainGroup('domain-list1', 'Claude', ['claude.ai', 'anthropic.com']),
             DomainGroup('domain-list2', 'Chat', ['chatgpt.com'])],
            [route('1', 'domain-list0', 'Wireguard0'), route('2', 'domain-list1', 'Wireguard0'),
             route('3', 'domain-list2', 'Wireguard1', reject=True)])
        result = router.merge_groups('domain-list0', ['domain-list1', 'domain-list2', 'domain-list0'], 'Нейросети')
        self.assertEqual(result['group'].entries, ['openai.com', 'claude.ai', 'anthropic.com', 'chatgpt.com'])
        self.assertEqual(result['group'].description, 'Нейросети')
        self.assertEqual(router.added, [('domain-list0', 'Wireguard1', True, True)])
        self.assertEqual(router.deleted, ['domain-list1', 'domain-list2'])
        self.assertEqual(result['rules_moved'], 1)

    def test_over_limit_changes_nothing(self):
        half = MAX_GROUP_ENTRIES // 2 + 1
        router = FakeRouter([DomainGroup('a', 'A', [f'a{i}.com' for i in range(half)]),
                             DomainGroup('b', 'B', [f'b{i}.com' for i in range(half)])], [])
        with self.assertRaisesRegex(ValueError, 'не больше'):
            router.merge_groups('a', ['b'])
        self.assertEqual((router.deleted, len(router.groups()[0].entries)), ([], half))

    def test_needs_two_existing_lists(self):
        router = FakeRouter([DomainGroup('a', 'A', [])], [])
        with self.assertRaisesRegex(ValueError, 'два списка'):
            router.merge_groups('a', ['a'])
        with self.assertRaisesRegex(ValueError, 'не найден'):
            router.merge_groups('a', ['missing'])


if __name__ == '__main__':
    unittest.main()
