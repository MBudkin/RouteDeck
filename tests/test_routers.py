"""Несколько роутеров: хранение настроек, миграция старого формата и выбор роутера для запроса."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.settings import Settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'settings.json')

    def tearDown(self):
        self.dir.cleanup()

    def stored(self):
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def test_migrates_single_router_settings(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'router_host': '10.0.0.1', 'router_login': 'root', 'router_password': 'b64:c2VjcmV0',
                       'default_interface': 'Wireguard0'}, f)
        s = Settings(self.path)
        self.assertEqual(len(s.public()['routers']), 1)
        self.assertEqual(s.get('router_host'), '10.0.0.1')
        self.assertEqual(s.get('router_password'), 'secret')
        self.assertEqual(s.get('default_interface'), 'Wireguard0')
        self.assertTrue(s.public()['has_password'])

    def test_routers_are_independent_and_switchable(self):
        s = Settings(self.path)
        home = s.save_router(None, name='Дом', router_host='192.168.1.1', router_password='one')
        dacha = s.save_router(None, name='Дача', router_host='192.168.2.1', router_password='two')
        self.assertEqual(s.active_id, dacha)
        s.update(home, default_interface='Wireguard1')
        s.select(home)
        self.assertEqual((s.get('router_host'), s.get('router_password'), s.get('default_interface')),
                         ('192.168.1.1', 'one', 'Wireguard1'))
        self.assertEqual(s.router(dacha)['default_interface'], '')

        reloaded = Settings(self.path)
        self.assertEqual(reloaded.active_id, home)
        self.assertEqual([r['name'] for r in reloaded.public()['routers']], ['Дом', 'Дача'])
        self.assertEqual(reloaded.router(dacha)['router_password'], 'two')
        self.assertNotIn('two', json.dumps(self.stored()), 'пароль не хранится открытым текстом')
        # Старая версия программы читает ключи активного роутера из корня файла.
        self.assertEqual(self.stored()['router_host'], '192.168.1.1')

    def test_delete_active_router_selects_another(self):
        s = Settings(self.path)
        first = s.save_router(None, router_host='a')
        second = s.save_router(None, router_host='b')
        s.delete_router(second)
        self.assertEqual(s.active_id, first)
        s.delete_router(first)
        self.assertEqual((s.active_id, s.public()['routers'], s.public()['has_password']), ('', [], False))
        with self.assertRaises(ValueError):
            s.select('missing')

    def test_public_settings_have_no_passwords(self):
        s = Settings(self.path)
        s.save_router(None, router_host='a', router_password='secret')
        self.assertNotIn('secret', json.dumps(s.public()))


class RouterApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        os.environ['S4R_DATA_DIR'] = cls.dir.name
        import app
        cls.app = app
        app.settings = Settings(os.path.join(cls.dir.name, 'routers-test.json'))
        cls.client = app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        os.chdir(Path(__file__).resolve().parents[1])
        cls.dir.cleanup()

    def put(self, **data):
        return self.client.put('/api/router/settings', json=data).get_json()

    def test_add_select_and_pin_router_per_request(self):
        first = self.put(name='Дом', router_host='10.0.0.1', router_password='p1')
        second = self.put(new=True, name='Дача', router_host='10.0.0.2', router_password='p2')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(second['settings']['active_router'], second['id'])

        res = self.client.post('/api/routers/select', json={'id': first['id']}).get_json()
        self.assertEqual(res['settings']['active_router'], first['id'])
        with self.app.app.test_request_context(headers={'X-Router': second['id']}):
            self.assertEqual(self.app.router().base_url, 'http://10.0.0.2')
        with self.app.app.test_request_context(headers={'X-Router': 'unknown'}):
            self.assertEqual(self.app.router().base_url, 'http://10.0.0.1')
        with self.app.app.test_request_context():
            self.app.remember_interface('Wireguard3')
        self.assertEqual(self.app.settings.router(first['id'])['default_interface'], 'Wireguard3')
        self.assertEqual(self.app.settings.router(second['id'])['default_interface'], '')

        deleted = self.client.post('/api/routers/delete', json={'id': first['id']}).get_json()
        self.assertEqual([r['id'] for r in deleted['settings']['routers']], [second['id']])
        self.assertEqual(deleted['settings']['active_router'], second['id'])

    def test_dns_routes_toggle_many(self):
        calls = []

        class FakeClient:
            def set_dns_routes_enabled(self, indices, enabled):
                calls.append((list(indices), enabled))
                return len(indices)

        original = self.app.router
        self.app.router = lambda: FakeClient()
        try:
            res = self.client.post('/api/router/dns-routes/toggle', json={'indices': ['1', '2'], 'enabled': False}).get_json()
            single = self.client.post('/api/router/dns-routes/toggle', json={'index': '3', 'enabled': True}).get_json()
        finally:
            self.app.router = original
        self.assertEqual(calls, [(['1', '2'], False), (['3'], True)])
        self.assertEqual(res['message'], 'Выключено правил: 2')
        self.assertEqual(single['message'], 'Правило включено')


if __name__ == '__main__':
    unittest.main()
