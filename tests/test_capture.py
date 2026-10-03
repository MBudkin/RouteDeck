"""Регрессии совместного анализа, истории и повторного использования сборщика."""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from services.capture.session import AgentLink, CaptureManager, CaptureSession, Target, TargetApp
from services.capture.store import CaptureStore
from services.capture.winapi import ProcInfo


def results():
    def ip(address, apps):
        return dict(ip=address, apps=apps, protos=['tcp'], ports=[443], procs=['app.exe'],
                    domains=[], count=1, first='2026-10-03', last='2026-10-03')
    def domain(name, apps):
        return dict(domain=name, apps=apps, ips=[], queried=True, count=1,
                    first='2026-10-03', last='2026-10-03')
    return {'ips': [ip('8.8.8.8', ['Discord']), ip('1.1.1.1', ['Code']), ip('9.9.9.9', ['Discord', 'Code'])],
            'domains': [domain('discord.com', ['Discord']), domain('code.visualstudio.com', ['Code']),
                        domain('shared.example.com', ['Discord', 'Code'])]}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'history.db')
        self.store = CaptureStore(self.db)
        self.target = {'name': 'Discord + Code', 'apps': [{'name': 'Discord'}, {'name': 'Code'}]}
        self.id = self.store.create_session('Discord + Code', self.target, 'basic', '2026-10-03')
        self.store.save_results(self.id, results(), 'full', '2026-10-03')

    def tearDown(self):
        self.tmp.cleanup()

    def test_history_has_separate_programs_and_counts(self):
        history = {p['name']: p for p in self.store.programs()}
        self.assertEqual(set(history), {'Discord', 'Code'})
        for p in history.values():
            self.assertEqual((p['sessions'], p['ips'], p['domains'], p['full']), (1, 2, 2, True))

    def test_results_do_not_mix_programs(self):
        data = self.store.program_results('Discord')
        self.assertEqual({i['ip'] for i in data['ips']}, {'8.8.8.8', '9.9.9.9'})
        self.assertEqual({d['domain'] for d in data['domains']}, {'discord.com', 'shared.example.com'})

    def test_separate_and_joint_sessions_merge(self):
        id2 = self.store.create_session('Discord', {'apps': [{'name': 'Discord'}]}, 'basic', '2026-10-04')
        self.store.save_results(id2, {'ips': results()['ips'][:1], 'domains': []}, 'basic')
        data = self.store.program_results('Discord')
        self.assertEqual(len(data['sessions']), 2)
        self.assertEqual(len(data['ips']), 2)
        self.assertEqual(next(i for i in data['ips'] if i['ip'] == '8.8.8.8')['count'], 2)

    def test_delete_one_program_preserves_other_and_shared_rows(self):
        self.assertEqual(self.store.delete_program('Discord'), 1)
        self.assertEqual([p['name'] for p in self.store.programs()], ['Code'])
        self.assertEqual({i['ip'] for i in self.store.program_results('Code')['ips']}, {'1.1.1.1', '9.9.9.9'})
        self.assertEqual(self.store.program_results('Discord')['sessions'], [])
        # Миграция при следующем запуске не должна восстановить удалённую программу.
        self.assertEqual([p['name'] for p in CaptureStore(self.db).programs()], ['Code'])

    def test_delete_last_program_removes_session(self):
        self.store.delete_program('Discord')
        self.store.delete_program('Code')
        self.assertEqual(self.store.programs(), [])
        with self.store._conn() as c:
            for table in ['capture_sessions', 'capture_ips', 'capture_domains', 'capture_session_apps']:
                self.assertEqual(c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    def test_migration_of_legacy_single_program(self):
        with self.store._conn() as c:
            c.execute('DELETE FROM capture_session_apps')
            c.execute('UPDATE capture_sessions SET name = ?, target = ?', ('Legacy', json.dumps({'name': 'Legacy', 'folder': 'C:/Legacy'})))
            c.execute('UPDATE capture_ips SET apps = NULL')
            c.execute('UPDATE capture_domains SET apps = NULL')
        old = CaptureStore(self.db)
        self.assertEqual(old.programs()[0]['name'], 'Legacy')
        self.assertEqual(len(old.program_results('Legacy')['ips']), 3)


class ManagerTests(unittest.TestCase):
    def test_multiple_analyses_reuse_one_privileged_agent(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch('services.capture.session.threading.Thread') as thread, \
                patch('services.capture.session.winapi.process_details', return_value=('', 100)), \
                patch('services.capture.session.winapi.is_admin', return_value=False):
            manager = CaptureManager(CaptureStore(os.path.join(tmp, 'test.db')), {})
            try:
                manager.start({'apps': [{'name': 'Discord', 'pids': [1]}], 'full': True}, 'http://127.0.0.1')
                self.assertEqual(thread.call_count, 3)  # два опроса и один запуск сборщика
                manager.agent.handle({'capturing': False})
                token = manager.agent.token
                manager.stop()
                manager.start({'apps': [{'name': 'Code', 'pids': [2]}], 'full': True}, 'http://127.0.0.1')
                self.assertEqual(thread.call_count, 5)  # добавились только два опроса
                self.assertEqual(manager.agent.token, token)
            finally:
                manager.shutdown()

    def test_stop_accepts_last_agent_events_before_saving(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(CaptureSession, '_poll_loop'), patch.object(CaptureSession, '_dns_loop'), \
                patch('services.capture.session.winapi.process_details', return_value=('', 100)):
            manager = CaptureManager(CaptureStore(os.path.join(tmp, 'test.db')), {})
            session = manager.start({'apps': [{'name': 'Discord', 'pids': [1]}]}, 'http://127.0.0.1')
            manager.agent.session = session
            manager.agent.status = 'capturing'
            def final_batch(**_kwargs):
                manager.agent.handle({'paused': True, 'capturing': False,
                    'procs': [ProcInfo(1, 0, 'Discord.exe', created=100).to_dict()],
                    'net': [[1, 'udp', '8.8.8.8', 443, 1]]})
                return True
            with patch.object(manager.agent.paused, 'wait', side_effect=final_batch):
                manager.stop()
            self.assertEqual(manager.store.program_results('Discord')['ips'][0]['ip'], '8.8.8.8')
            self.assertEqual(manager.agent.status, 'ready')

    def test_accepts_multiple_programs(self):
        with patch('services.capture.session.winapi.process_details', return_value=('', 123)):
            apps = CaptureManager._parse_apps({'apps': [{'name': 'Discord', 'pids': [1]}, {'name': 'Code', 'pids': [2]}]})
        self.assertEqual([(a.name, a.roots) for a in apps], [('Discord', {1: 123}), ('Code', {2: 123})])

    def test_accepts_legacy_payload(self):
        with patch('services.capture.session.winapi.process_details', return_value=('', 123)):
            apps = CaptureManager._parse_apps({'name': 'Discord', 'pids': [1]})
        self.assertEqual(apps[0].name, 'Discord')

    def test_custom_exe_name(self):
        with patch('os.path.isfile', return_value=True):
            apps = CaptureManager._parse_apps({'exe': r'C:\Apps\Discord\Discord.exe', 'name': 'Мой Discord'})
        self.assertEqual(apps[0].name, 'Мой Discord')

    def test_rejects_duplicate_names(self):
        with patch('services.capture.session.winapi.process_details', return_value=('', 0)):
            with self.assertRaisesRegex(ValueError, 'отдельное название'):
                CaptureManager._parse_apps({'apps': [{'name': 'Code', 'pids': [1]}, {'name': 'code', 'pids': [2]}]})

    def test_process_events_keep_program_ownership(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(CaptureSession, '_poll_loop'), patch.object(CaptureSession, '_dns_loop'):
            session = CaptureSession(Target('Discord + Code', [TargetApp('Discord', roots={1: 100}), TargetApp('Code', roots={2: 100})]),
                                     CaptureStore(os.path.join(tmp, 'test.db')), False)
            try:
                session.ingest({'capturing': True, 'procs': [ProcInfo(1, 0, 'Discord.exe', created=100).to_dict(),
                                                             ProcInfo(2, 0, 'Code.exe', created=100).to_dict()],
                                'dns': [[1, 'discord.com', ['8.8.8.8']], [2, 'code.visualstudio.com', ['1.1.1.1']]],
                                'net': [[1, 'udp', '8.8.8.8', 443, 1], [2, 'tcp', '1.1.1.1', 443, 1]]})
                self.assertEqual({i['ip']: i['apps'] for i in session.results()['ips']}, {'8.8.8.8': ['Discord'], '1.1.1.1': ['Code']})
            finally:
                session.finish()


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = AgentLink({'command': lambda: ('pythonw.exe', 'desktop.py'), 'window': 'RouteDeck', 'data_dir': '.', 'cwd': '.'})

    def test_ready_agent_is_reused_without_uac(self):
        self.agent.handle({'capturing': False})
        original_token = self.agent.token = 'existing-token'
        with patch('services.capture.session.threading.Thread') as thread:
            self.agent.ensure('http://127.0.0.1:5000')
            thread.assert_not_called()
        self.assertEqual(self.agent.token, original_token)
        self.assertEqual(self.agent.status, 'ready')

    def test_lost_agent_can_be_restarted(self):
        self.agent.status = 'ready'
        self.agent.contact = time.time() - 60
        with patch('services.capture.session.threading.Thread') as thread:
            self.agent.ensure('http://127.0.0.1:5000')
            thread.return_value.start.assert_called_once()
        self.assertEqual(self.agent.status, 'pending')

    def test_cancelled_uac_restores_window(self):
        from services.capture.winapi import ElevationCancelled
        with patch('services.capture.session.winapi.find_window', return_value=123), \
                patch('services.capture.session.winapi.run_elevated', side_effect=ElevationCancelled), \
                patch('services.capture.session.winapi.bring_to_front') as restore:
            self.agent._elevate('http://localhost:5000')
            restore.assert_called_once_with(123)
        self.assertEqual(self.agent.status, 'denied')

    def test_confirmed_uac_restores_window_and_waits_for_agent(self):
        self.agent.status = 'pending'
        with patch('services.capture.session.winapi.find_window', return_value=123), \
                patch('services.capture.session.winapi.run_elevated') as launch, \
                patch('services.capture.session.winapi.bring_to_front') as restore:
            self.agent._elevate('http://localhost:5000')
            self.assertIn('http://127.0.0.1:5000', launch.call_args.args[1])
            restore.assert_called_once_with(123)
        self.assertEqual(self.agent.status, 'starting')


if __name__ == '__main__':
    unittest.main()
