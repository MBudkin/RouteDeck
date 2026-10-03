"""Запускает проверки интерфейса в Chrome без доступа к настоящему роутеру."""

import functools
import argparse
import http.server
import os
import json
import sys
from pathlib import Path
import re
import subprocess
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[1]
BROWSER = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Google/Chrome/Application/chrome.exe'


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith(('/capture-test', '/dns-test', '/ip-test')):
            html = (ROOT / 'templates/index.html').read_text('utf-8')
            html = html.replace('{{ app_name }}', 'RouteDeck').replace('{{ app_version }}', 'test')
            html = html.replace('{{ app_name|tojson }}', json.dumps('RouteDeck'))
            suite = 'ip' if self.path.startswith('/ip-test') else 'dns' if self.path.startswith('/dns-test') else 'capture'
            html = html.replace('</head>', f'<script src="/tests/browser_{suite}.js"></script></head>')
            content = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            super().do_GET()

    def log_message(self, *_args):
        pass


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=['capture', 'dns', 'ip'], default='capture')
    parser.add_argument('--screenshot', default='')
    parser.add_argument('--view', choices=['results', 'history', 'setup', 'add', 'loading', 'error'], default='results')
    parser.add_argument('--theme', choices=['light', 'dark'], default='dark')
    parser.add_argument('--width', type=int, default=1440)
    parser.add_argument('--height', type=int, default=1000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='routedeck-browser-', ignore_cleanup_errors=True) as profile:
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            extra = [f'--screenshot={args.screenshot}', f'--window-size={args.width},{args.height}'] if args.screenshot else []
            run = subprocess.run([str(BROWSER), '--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                                  f'--user-data-dir={profile}', '--virtual-time-budget=3000', '--dump-dom',
                                  *extra, f'http://127.0.0.1:{server.server_port}/{args.suite}-test' + (f'?preview=1&view={args.view}&theme={args.theme}' if extra else '')], capture_output=True, timeout=45)
            html = run.stdout.decode('utf-8', errors='replace')
            report = re.search(r'<pre id="test-report">(.*?)</pre>', html, re.S)
            if report:
                print(report.group(1))
            elif args.screenshot:
                print('Screenshot: ' + args.screenshot)
            else:
                print(html[-3000:])
            if 'data-test-status="passed"' not in html:
                print('Browser exit:', run.returncode)
                print(run.stderr.decode('utf-8', errors='replace')[-4000:])
                raise SystemExit(f'Browser {args.suite} tests failed')
            print(re.search(r'data-test-count="(\d+)"', html).group(1) + ' browser checks passed')
        finally:
            server.shutdown()
            server.server_close()
