// Stable releases; downloads and restart always require a user action.
const updateUI = { data: null, modal: null, request: null, poll: null };

function renderUpdateBadge() {
    const el = $('#updates-button');
    if (!el) return;
    const available = updateUI.data?.status === 'available';
    el.classList.toggle('has-update', available);
    el.title = available ? `Доступна версия RouteDeck ${updateUI.data.release.version}` : 'Проверить обновления RouteDeck';
    el.setAttribute('aria-label', el.title);
}

function updateBody() {
    const d = updateUI.data;
    if (!d) return '<p class="muted">Проверяем обновления…</p>';
    const r = d.release;
    const download = d.download || {};
    const available = d.status === 'available';
    const checking = d.status === 'checking';
    let headline = checking ? 'Проверяем GitHub…' : available ? `Доступна версия ${r.version}` : d.status === 'current' ? 'Новая версия не найдена' : 'Проверка временно недоступна';
    const progress = download.total ? Math.min(100, Math.floor(download.bytes/download.total*100)) : 0;
    const downloading = download.status === 'downloading';
    const ready = download.status === 'ready';
    const native = Boolean(d.can_install && window.pywebview?.api?.install_update);
    return `<div class="update-summary"><span class="update-symbol">${ICONS.download}</span><div><h3>${esc(headline)}</h3><p class="muted">Установлена версия ${esc(d.current_version)}${d.checked_at ? ` · Проверено ${esc(new Date(d.checked_at).toLocaleTimeString('ru-RU', {hour:'2-digit',minute:'2-digit'}))}` : ''}</p></div></div>
        ${d.error ? `<p class="update-error" role="status">${esc(d.error.message)}</p>` : ''}
        ${r && available ? `<div class="update-notes">${esc(r.notes || 'Описание изменений доступно на GitHub.')}</div><p class="muted">Windows EXE · ${(r.size/1048576).toFixed(1)} МБ · проверка SHA-256</p>` : ''}
        ${downloading ? `<div class="update-progress"><progress value="${progress}" max="100"></progress><span>${progress}%</span></div><p class="muted">Проверяем файл после скачивания. Приложение продолжает работать.</p>` : ''}
        ${download.error ? `<p class="update-error" role="status">${esc(download.error.message)}</p>` : ''}
        ${ready ? '<p class="update-ready">Файл скачан. Размер и SHA-256 подтверждены.</p>' : ''}
        <div class="update-actions">
            <button class="btn ghost" data-update-check ${checking || downloading ? 'disabled' : ''}>${ICONS.refresh}Проверить снова</button>
            ${available && !ready && !downloading ? `<button class="btn primary" data-update-download ${checking ? 'disabled' : ''}>${ICONS.download}${download.status === 'error' || download.status === 'cancelled' ? 'Скачать заново' : 'Скачать обновление'}</button>` : ''}
            ${downloading ? '<button class="btn ghost" data-update-cancel>Отменить скачивание</button>' : ''}
            ${ready && native ? '<button class="btn primary" data-update-install>Обновить и перезапустить</button>' : ''}
            ${ready ? '<a class="btn ghost" href="/api/updates/file" download="RouteDeck.exe">Сохранить EXE</a>' : ''}
        </div>
        ${ready && !native ? '<p class="muted">Сохраните EXE, закройте RouteDeck и замените файл программы вручную. Настройки и база останутся в папке данных.</p>' : ''}
        <p class="muted update-footnote">Проверяем только стабильные релизы. Скачивание и перезапуск запускаете вы. <a href="https://github.com/MBudkin/RouteDeck/releases" target="_blank" rel="noopener noreferrer">Релизы GitHub</a></p>`;
}

function renderUpdateModal() {
    const modal = updateUI.modal;
    if (!modal?.el.isConnected) return;
    const focus = ['check','download','cancel','install'].find(name => document.activeElement?.hasAttribute(`data-update-${name}`));
    const notesScroll = $('.update-notes',modal.el)?.scrollTop || 0;
    $('.modal-body', modal.el).innerHTML = updateBody();
    const el = modal.el;
    const check = $('[data-update-check]', el);
    if (check) check.onclick = () => checkUpdates(true);
    $('[data-update-download]', el)?.addEventListener('click', () => updateDownload());
    $('[data-update-cancel]', el)?.addEventListener('click', async () => {
        try { await api('/api/updates/cancel', 'POST', {}); } catch (e) { toast(e.message,'error'); }
    });
    $('[data-update-install]', el)?.addEventListener('click', async () => {
        if (!await confirmDialog('RouteDeck закроется, заменит свой EXE проверенной новой версией и запустится снова. Настройки и база сохранятся.', {title:'Установить обновление?',ok:'Обновить и перезапустить',danger:false})) return;
        try {
            const result = await window.pywebview.api.install_update();
            if (!result.success) throw new Error(result.error || 'Не удалось установить обновление');
        } catch (e) { toast(e.message,'error'); }
    });
    if (focus) $(`[data-update-${focus}]`,el)?.focus();
    const notes = $('.update-notes',el); if (notes) notes.scrollTop=notesScroll;
}

function applyUpdateState(data) {
    updateUI.data = data;
    renderUpdateBadge();
    renderUpdateModal();
    const working = data.status === 'checking' || data.download?.status === 'downloading';
    if (!working && updateUI.poll) { clearInterval(updateUI.poll); updateUI.poll = null; }
    if (working && !updateUI.poll) {
        updateUI.poll = setInterval(async () => {
            if (updateUI.request) return;
            updateUI.request = api('/api/updates');
            try { applyUpdateState(await updateUI.request); } catch { clearInterval(updateUI.poll); updateUI.poll=null; }
            finally { updateUI.request=null; }
        },1000);
    }
}

async function checkUpdates(manual=false) {
    if (updateUI.request) return updateUI.request;
    updateUI.request = api('/api/updates/check','POST',{manual});
    try { applyUpdateState(await updateUI.request); }
    catch (e) { if (manual) toast(e.message,'error'); }
    finally { updateUI.request=null; }
}

async function updateDownload() {
    if (updateUI.data?.download?.status === 'downloading') return;
    try { applyUpdateState(await api('/api/updates/download','POST',{})); }
    catch (e) { toast(e.message,'error'); }
}

function openUpdates() {
    if (updateUI.modal?.el.isConnected) return;
    updateUI.modal = openModal({title:'Обновления RouteDeck',size:'',body:updateBody()});
    renderUpdateModal();
    checkUpdates(true);
}

setTimeout(() => checkUpdates(false), 1500);
setInterval(() => checkUpdates(false), 6*60*60*1000);
$('#updates-button')?.addEventListener('click',openUpdates);
