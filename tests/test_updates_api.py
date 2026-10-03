"""Update HTTP API uses the same JSON/origin guard as router operations."""
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.updates import UpdateManager
from test_updates import DATA, Response, Session, release


class UpdateApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cwd=Path.cwd()
        cls.appdata=tempfile.TemporaryDirectory(prefix='update-api-')
        cls.old_env=os.environ.get('S4R_DATA_DIR')
        os.environ['S4R_DATA_DIR']=cls.appdata.name
        import app
        cls.module=app
        cls.client=app.app.test_client()
    @classmethod
    def tearDownClass(cls):
        cls.module.capture.shutdown()
        logging.shutdown()
        os.chdir(cls.cwd)
        cls.appdata.cleanup()
        if cls.old_env is None: os.environ.pop('S4R_DATA_DIR',None)
        else: os.environ['S4R_DATA_DIR']=cls.old_env
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='update-api-download-')
        self.addCleanup(self.tmp.cleanup)
        self.manager=UpdateManager('2.4.5',self.tmp.name,session=Session(Response(data=release()),Response(content=DATA)))
        self.replacement=patch.object(self.module,'updates',self.manager)
        self.replacement.start(); self.addCleanup(self.replacement.stop)
    def test_origin_and_json_guard(self):
        self.assertEqual(self.client.post('/api/updates/check',data='manual=1').status_code,415)
        response=self.client.post('/api/updates/check',json={},headers={'Origin':'https://evil.invalid'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(len(self.manager.session.calls),0)
    def test_check_status_and_verified_file(self):
        self.assertEqual(self.client.post('/api/updates/check',json={'manual':True}).status_code,200)
        self.manager._check_thread.join(3)
        data=self.client.get('/api/updates').get_json()
        self.assertEqual(data['status'],'available')
        self.assertFalse(data['can_install'])
        self.assertEqual(self.client.post('/api/updates/download',json={}).status_code,200)
        self.manager._download_thread.join(3)
        response=self.client.get('/api/updates/file')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data,DATA)
        self.assertIn('RouteDeck.exe',response.headers['Content-Disposition'])
        response.close()
    def test_unverified_download_is_not_served(self):
        response=self.client.get('/api/updates/file')
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.get_json()['code'],'not_ready')
        self.assertEqual(self.client.post('/api/updates/download',json={}).status_code,400)


if __name__=='__main__': unittest.main()
