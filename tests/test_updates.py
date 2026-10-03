"""Offline regression tests for GitHub updates and Windows replacement safety."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from services.updates import API_URL, UpdateError, UpdateManager, parse_release, version_tuple
from services.update_install import helper_command

DATA = b'MZverified release binary fixture'


def release(tag='v2.4.6', data=DATA):
    return {'tag_name':tag, 'draft':False, 'prerelease':False,
            'html_url':f'https://github.com/MBudkin/RouteDeck/releases/tag/{tag}', 'body':'Новая версия',
            'assets':[{'name':'RouteDeck.exe','state':'uploaded','size':len(data),
                       'digest':'sha256:'+hashlib.sha256(data).hexdigest(),
                       'browser_download_url':f'https://github.com/MBudkin/RouteDeck/releases/download/{tag}/RouteDeck.exe'}]}


class Response:
    def __init__(self, status=200, data=None, content=None, headers=None, chunks=None):
        self.status_code=status; self.headers=headers or {}; self.closed=False
        self.content=content if content is not None else json.dumps(data).encode('utf-8')
        self.chunks=chunks
    def iter_content(self, _size):
        if self.chunks is not None:
            yield from self.chunks()
        else: yield self.content
    def close(self): self.closed=True


class Session:
    def __init__(self,*responses): self.responses=list(responses); self.calls=[]
    def get(self,url,**options):
        self.calls.append((url,options))
        result=self.responses.pop(0)
        if isinstance(result,Exception): raise result
        return result


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='updates-test-')
        self.addCleanup(self.tmp.cleanup)
        self.now=1000
    def manager(self,*responses,version='2.4.5'):
        session=Session(*responses)
        manager=UpdateManager(version,self.tmp.name,session=session,clock=lambda:self.now)
        return manager,session
    def checked(self,manager):
        manager.check(); manager._check_thread.join(3)
        self.assertFalse(manager._check_thread.is_alive())
        return manager.snapshot()
    def downloaded(self,manager):
        manager.download(); manager._download_thread.join(3)
        self.assertFalse(manager._download_thread.is_alive())
        return manager.snapshot()['download']
    def test_numeric_versions_and_stable_only(self):
        self.assertGreater(version_tuple('v2.4.10'),version_tuple('2.4.9'))
        self.assertEqual(version_tuple('2.4.6+build.1'),(2,4,6))
        for value in ['2.04.6','2.4','2.4.6-beta','../2.4.6',None,2.4,'v99999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999.1.1']:
            with self.subTest(value=value),self.assertRaises(UpdateError): version_tuple(value)
    def test_anonymous_session_does_not_read_netrc(self):
        m=UpdateManager('2.4.6',self.tmp.name)
        with patch('requests.sessions.get_netrc_auth') as netrc:
            request=m.session.prepare_request(requests.Request('GET',API_URL))
        netrc.assert_not_called()
        self.assertNotIn('Authorization',request.headers)
    def test_available_current_and_older(self):
        for installed,latest,expected in [('2.4.5','v2.4.6','available'),('2.4.6','v2.4.6','current'),('2.4.10','v2.4.9','current')]:
            with self.subTest(installed=installed):
                m,s=self.manager(Response(data=release(latest)),version=installed)
                self.assertEqual(self.checked(m)['status'],expected)
                self.assertEqual(len(s.calls),1)
                self.assertEqual(s.calls[0][0],API_URL)
                self.assertNotIn('Authorization',s.calls[0][1]['headers'])
                self.assertEqual(m.snapshot()['download']['status'],'idle')
    def test_offline_and_invalid_json_are_nonfatal(self):
        for reply,code in [(requests.Timeout(),'offline'),(Response(content=b'<html>oops</html>'),'invalid_release'),(Response(data=[]),'invalid_release'),(Response(status=404),'unavailable')]:
            with self.subTest(code=code):
                m,_=self.manager(reply)
                state=self.checked(m)
                self.assertEqual(state['status'],'unavailable')
                self.assertEqual(state['error']['code'],code)
    def test_invalid_assets_drafts_and_untrusted_urls(self):
        changes=[('draft',True),('prerelease',True),('tag_name','v2.4.6-alpha'),('html_url','https://github.com/other/project/releases/tag/v2.4.6')]
        for key,value in changes:
            data=release(); data[key]=value
            with self.subTest(key=key),self.assertRaises(UpdateError): parse_release(data)
        for key,value in [('size',True),('size',9999999999),('digest',None),('digest','sha256:abc'),('state','new'),('browser_download_url','http://github.com/MBudkin/RouteDeck/releases/download/v2.4.6/RouteDeck.exe'),('browser_download_url','https://github.com.evil.invalid/RouteDeck.exe'),('browser_download_url','https://github.com/MBudkin/Other/releases/download/v2.4.6/RouteDeck.exe')]:
            data=release(); data['assets'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(UpdateError): parse_release(data)
        data=release(); data['assets']*=2
        with self.assertRaises(UpdateError): parse_release(data)
    def test_cache_etag_and_repeated_checks(self):
        m,s=self.manager(Response(data=release(),headers={'ETag':'"release"'}),Response(status=304))
        self.checked(m)
        m.check(manual=True); m.check(manual=True)
        self.assertEqual(len(s.calls),1)
        self.now+=61; m.check(manual=True); m._check_thread.join(3)
        self.assertEqual(len(s.calls),2)
        self.assertEqual(s.calls[1][1]['headers']['If-None-Match'],'"release"')
        self.assertEqual(m.snapshot()['status'],'available')
    def test_rate_limit_waits_and_retains_valid_release(self):
        m,s=self.manager(Response(data=release()),Response(status=429,headers={'Retry-After':'300'}),Response(data=release()))
        self.checked(m); self.now+=61; m.check(manual=True); m._check_thread.join(3)
        self.assertEqual(m.snapshot()['error']['code'],'rate_limited')
        self.assertEqual(m.snapshot()['status'],'available')
        self.now+=61; m.check(manual=True); self.assertEqual(len(s.calls),2)
        self.now+=300; m.check(manual=True); m._check_thread.join(3)
        self.assertEqual(len(s.calls),3)
    def test_successful_download_verifies_and_detects_later_tampering(self):
        m,_=self.manager(Response(data=release()),Response(content=DATA,headers={'Content-Length':str(len(DATA))}))
        self.checked(m); self.assertEqual(self.downloaded(m)['status'],'ready')
        path,_=m.verified_file(); self.assertEqual(path.read_bytes(),DATA)
        path.write_bytes(b'MZtampered')
        with self.assertRaises(UpdateError): m.verified_file()
        self.assertEqual(m.snapshot()['download']['status'],'error')
    def test_checksum_size_and_truncated_download_cleanup(self):
        def interrupted():
            yield DATA[:4]
            raise requests.ConnectionError('interrupted')
        for reply in [Response(content=b'MZwrong'),Response(content=DATA,headers={'Content-Length':'123'}),Response(content=DATA[:-1]),Response(chunks=interrupted)]:
            with self.subTest(reply=reply):
                m,_=self.manager(Response(data=release()),reply)
                self.checked(m)
                self.assertEqual(self.downloaded(m)['status'],'error')
                self.assertFalse(list(Path(self.tmp.name).rglob('*.part')))
                self.assertFalse(list(Path(self.tmp.name).rglob('RouteDeck.exe')))
    def test_cancel_and_retry_with_no_parallel_download(self):
        started=threading.Event(); continue_download=threading.Event()
        def chunks():
            started.set(); continue_download.wait(2); yield DATA
        m,s=self.manager(Response(data=release()),Response(chunks=chunks),Response(content=DATA))
        self.checked(m); m.download(); self.assertTrue(started.wait(2))
        m.download(); self.assertEqual(len(s.calls),2)
        m.cancel_download(); continue_download.set(); m._download_thread.join(3)
        self.assertEqual(m.snapshot()['download']['status'],'cancelled')
        self.assertFalse(list(Path(self.tmp.name).rglob('*.part')))
        self.assertEqual(self.downloaded(m)['status'],'ready')
    def test_redirect_validation_before_request(self):
        m,s=self.manager(Response(data=release()),Response(status=302,headers={'Location':'https://evil.invalid/app.exe'}))
        self.checked(m); result=self.downloaded(m)
        self.assertEqual(result['error']['code'],'unsafe_asset')
        self.assertEqual(len(s.calls),2)
        m,s=self.manager(Response(data=release()),Response(status=302,headers={'Location':'https://release-assets.githubusercontent.com/asset?signature=fixture'}),Response(content=DATA))
        self.checked(m); self.assertEqual(self.downloaded(m)['status'],'ready')


@unittest.skipUnless(os.name=='nt','Windows helper')
class InstallerTests(unittest.TestCase):
    def test_replace_waits_for_old_process_and_keeps_backup(self):
        with tempfile.TemporaryDirectory(prefix='install-test-') as directory:
            root=Path(directory); old=root/'RouteDeck.exe'; old.write_bytes(b'MZold version')
            staged=root/'new.exe'; staged.write_bytes(DATA)
            child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(0.2)'])
            cmd=helper_command(root,staged,old,hashlib.sha256(DATA).hexdigest(),'2.4.6',child.pid,start=False,notify=False)
            result=subprocess.run(cmd,capture_output=True,timeout=30)
            child.wait()
            detail=(root/'install-result.json').read_text('utf-8-sig') if (root/'install-result.json').exists() else result.stderr
            self.assertEqual(result.returncode,0,detail)
            self.assertEqual(old.read_bytes(),DATA)
            backups=list(root.glob('RouteDeck.exe.backup-*'))
            self.assertEqual(len(backups),1); self.assertEqual(backups[0].read_bytes(),b'MZold version')
    def test_bad_checksum_never_replaces_original(self):
        with tempfile.TemporaryDirectory(prefix='install-test-') as directory:
            root=Path(directory); old=root/'RouteDeck.exe'; old.write_bytes(b'MZold version')
            staged=root/'new.exe'; staged.write_bytes(DATA)
            child=subprocess.Popen([sys.executable,'-c','pass']); child.wait()
            cmd=helper_command(root,staged,old,'0'*64,'2.4.6',child.pid,start=False,notify=False)
            result=subprocess.run(cmd,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,1)
            self.assertEqual(old.read_bytes(),b'MZold version')


if __name__=='__main__': unittest.main()
