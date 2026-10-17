"""Offline tests for the AmneziaVPN split-tunneling export."""
import json
import unittest

from services import amnezia


class AmneziaExportTests(unittest.TestCase):
    def test_networks_and_domains_without_resolving_match_amnezia_format(self):
        result = amnezia.build_sites(['91.108.4.0/22', '149.154.167.99', 'telegram.org',
                                      'https://www.YouTube.com/watch?v=1'])
        self.assertEqual(result['sites'], [
            {'hostname': '91.108.4.0/22', 'ip': ''},
            {'hostname': '149.154.167.99/32', 'ip': ''},
            {'hostname': 'telegram.org', 'ip': ''},
            {'hostname': 'youtube.com', 'ip': ''},
        ])
        json.dumps(result['sites'])  # сериализуется без ошибок

    def test_networks_sorted_numerically_and_deduplicated(self):
        sites = amnezia.build_sites(['104.16.0.0/12', '8.8.8.8', '104.16.0.0/13',
                                     '8.8.8.8/32', '104.16.5.0/12'])['sites']
        self.assertEqual([s['hostname'] for s in sites],
                         ['8.8.8.8/32', '104.16.0.0/12', '104.16.0.0/13'])

    def test_skips_ipv6_local_default_and_garbage(self):
        result = amnezia.build_sites(['2001:db8::/32', '::1', '192.168.1.0/24', '10.0.0.1',
                                      '0.0.0.0/0', 'localhost', '', 'not a domain', '1.1.1.1'])
        self.assertEqual(result['sites'], [{'hostname': '1.1.1.1/32', 'ip': ''}])
        s = result['summary']
        self.assertEqual((s['skipped_ipv6'], s['skipped_local'], s['skipped_invalid']), (2, 3, 2))

    def test_resolved_domains_get_ip_and_ips_including_www(self):
        answers = {'example.com': {'93.184.216.34', '2606:2800::1'},
                   'www.example.com': {'93.184.216.35', '192.168.0.1'},
                   'down.example': set()}

        def resolve(name):
            if name == 'www.down.example':
                raise RuntimeError('timeout')
            return answers.get(name, set())

        result = amnezia.build_sites(['example.com', 'down.example', '1.2.3.0/24'], resolve)
        self.assertEqual(result['sites'], [
            {'hostname': '1.2.3.0/24', 'ip': ''},
            {'hostname': 'down.example', 'ip': '', 'ips': []},
            {'hostname': 'example.com', 'ip': '93.184.216.34',
             'ips': ['93.184.216.34', '93.184.216.35']},
        ])
        self.assertEqual((result['summary']['resolved'], result['summary']['unresolved']), (1, 1))

    def test_idn_domain_is_punycode(self):
        sites = amnezia.build_sites(['пример.рф'])['sites']
        self.assertEqual(sites, [{'hostname': 'xn--e1afmkfd.xn--p1ai', 'ip': ''}])


if __name__ == '__main__':
    unittest.main()
