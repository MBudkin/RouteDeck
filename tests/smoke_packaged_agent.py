"""Проверка сборщика внутри EXE без UAC и запуска системной трассировки."""

import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[1]
received = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == '/api/capture/agent'
        assert self.headers['X-Capture-Token'] == 'packaged-test'
        received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        payload = b'{"stop": true}'  # трассировку не включать
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(ROOT / 'dist/RouteDeck.exe'))
    for relative in ['static/js/app.js', 'static/css/styles.css', 'templates/index.html']:
        entry = next(name for name in archive.toc if name.replace('\\', '/') == relative)
        assert archive.extract(entry) == (ROOT / relative).read_bytes(), f'Outdated packaged resource: {relative}'
    print('Packaged UI resources match the verified sources')
    with tempfile.TemporaryDirectory(dir=ROOT / 'build', prefix='agent-test-') as tmp:
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            log = Path(tmp) / 'agent.log'
            result = subprocess.run([str(ROOT / 'dist/RouteDeck.exe'), '--capture-agent',
                '--url', f'http://127.0.0.1:{server.server_port}', '--token', 'packaged-test', '--log', str(log)],
                timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode == 0, f'Agent exit code: {result.returncode}'
            assert received == [{'capturing': False}], received
            assert 'Сборщик завершён' in log.read_text('utf-8')
            print('Packaged agent starts, contacts local API and exits successfully')
        finally:
            server.shutdown()
            server.server_close()
