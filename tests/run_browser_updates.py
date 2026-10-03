"""Real UI regression with explicit offline GitHub fixtures; no screenshots."""
import json, logging, os, sys, tempfile, threading
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.updates import UpdateManager
from test_updates import DATA, Response, Session, release
import requests

with tempfile.TemporaryDirectory(prefix='updates-ui-') as data:
    os.environ['S4R_DATA_DIR']=data
    import app
    from werkzeug.serving import make_server
    from playwright.sync_api import sync_playwright
    server=make_server('127.0.0.1',0,app.app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}'
    checked=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(url+'/') else route.abort())
            metadata=release('v2.4.7'); metadata['body']='<img src=x onerror=alert(1)> Тестовое описание'
            app.updates=UpdateManager('2.4.6',data,session=Session(Response(data=metadata),Response(content=DATA)))
            page.goto(url,wait_until='networkidle')
            page.locator('#updates-button').click()
            page.get_by_text('Доступна версия 2.4.7',exact=True).wait_for()
            assert page.locator('#updates-button').evaluate('(e)=>e.classList.contains("has-update")')
            assert page.locator('.update-notes img').count()==0
            assert '<img' in page.locator('.update-notes').inner_text()
            checked.extend(['available badge','escaped notes','early manual check'])
            page.get_by_role('button',name='Скачать обновление',exact=True).click()
            page.get_by_text('Файл скачан. Размер и SHA-256 подтверждены.',exact=True).wait_for()
            assert page.get_by_role('button',name='Обновить и перезапустить',exact=True).count()==0
            with page.expect_download() as download:
                page.get_by_role('link',name='Сохранить EXE',exact=True).click()
            target=Path(data)/'saved.exe'; download.value.save_as(target)
            assert target.read_bytes()==DATA
            page.get_by_role('button',name='Проверить снова',exact=True).click()
            assert len(app.updates.session.calls)==2
            checked.extend(['verified download','manual EXE in browser','repeat uses cache'])
            page.locator('[data-close]').click()
            app.updates=UpdateManager('2.4.6',data,session=Session(Response(data=release('v2.4.6'))))
            page.reload(wait_until='networkidle'); page.locator('#updates-button').click()
            page.get_by_text('Новая версия не найдена',exact=True).wait_for()
            assert page.get_by_role('button',name='Скачать обновление',exact=True).count()==0
            checked.append('current version')
            page.locator('[data-close]').click()
            app.updates=UpdateManager('2.4.6',data,session=Session(requests.Timeout()))
            page.reload(wait_until='networkidle'); page.locator('#updates-button').click()
            page.get_by_text('Нет связи с GitHub. Проверить обновления можно позже.',exact=True).wait_for()
            assert page.locator('#view-dns').is_visible()
            assert not errors,errors
            checked.extend(['offline does not block router UI','no browser exceptions'])
            browser.close()
    finally:
        app.capture.shutdown(); server.shutdown(); server.server_close(); logging.shutdown(); os.chdir(ROOT)
    print(json.dumps({'passed':len(checked),'checks':checked},ensure_ascii=False))
