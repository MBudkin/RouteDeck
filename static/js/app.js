/* Sites4Router v2 — фронтенд */
'use strict';

// ============================================================ утилиты

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ICONS = {
    x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
    info: '<svg viewBox="0 0 24 24"><path d="M12 16v-4M12 8h.01"/></svg>',
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    arrow: '<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    chevron: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
    edit: '<svg viewBox="0 0 24 24"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    download: '<svg viewBox="0 0 24 24"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
    refresh: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6"/></svg>',
    globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/></svg>',
    list: '<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h10"/></svg>',
    router: '<svg viewBox="0 0 24 24"><rect x="2" y="13" width="20" height="8" rx="2"/><path d="M6 17h.01M10 17h.01M15 13V9M8.5 6.5a9 9 0 0 1 13 0M11.3 9.3a5 5 0 0 1 7.4 0"/></svg>',
    link: '<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    bolt: '<svg viewBox="0 0 24 24"><path d="M13 2 3 14h9l-1 8 10-12h-9z"/></svg>',
    swap: '<svg viewBox="0 0 24 24"><path d="M7 7h13M16 3l4 4-4 4M17 17H4M8 13l-4 4 4 4"/></svg>',
};

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const plural = (n, one, few, many) => {
    const m10 = n % 10, m100 = n % 100;
    return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many;
};
const isIp = (s) => /^\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?$/.test(s);

function hue(str) {
    let h = 0;
    for (const ch of str) h = (h * 31 + ch.codePointAt(0)) % 360;
    return h;
}

function groupIcon(title) {
    const letter = (title.trim()[0] || '?').toUpperCase();
    const h = hue(title);
    return `<div class="group-icon" style="background:linear-gradient(135deg,hsl(${h} 75% 55%),hsl(${(h + 40) % 360} 70% 45%))">${esc(letter)}</div>`;
}

function relTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    const diff = (Date.now() - date) / 1000;
    if (diff < 60) return 'только что';
    if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} дн назад`;
    return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function normalizeEntry(raw) {
    let v = String(raw || '').trim().toLowerCase();
    v = v.replace(/^[a-z][a-z0-9+.-]*:\/\//, '').split(/[/?#]/)[0].replace(/:\d+$/, '').replace(/^\*\./, '').replace(/^www\./, '').replace(/\.$/, '');
    return v;
}

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        toast('Скопировано в буфер обмена', 'success');
    } catch {
        toast('Не удалось скопировать', 'error');
    }
}

function downloadText(filename, text) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: filename });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function storage(key, value) {
    try {
        if (value === undefined) return localStorage.getItem(key);
        localStorage.setItem(key, value);
    } catch { /* приватный режим */ }
    return null;
}

// ============================================================ API

async function api(url, method = 'GET', body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    let res, data;
    try {
        res = await fetch(url, opts);
    } catch {
        throw Object.assign(new Error(`${window.APP_NAME || 'Программа'}: сервер не отвечает — перезапустите приложение`), { code: 'offline' });
    }
    try { data = await res.json(); } catch { data = {}; }
    if (!res.ok || data.success === false) {
        throw Object.assign(new Error(data.error || `Ошибка ${res.status}`), { code: data.code, status: res.status });
    }
    return data;
}

async function withButton(btn, fn) {
    btn?.classList.add('loading');
    btn && (btn.disabled = true);
    try { return await fn(); }
    finally {
        btn?.classList.remove('loading');
        btn && (btn.disabled = false);
    }
}

// ============================================================ уведомления

// Уведомление висит 6 с (ошибка — 12 с), при наведении мыши таймер замирает, клик закрывает.
function toast(text, type = 'info', { loading = false, timeout = 6000 } = {}) {
    const el = document.createElement('div');
    const icon = () => (el.classList.contains('loading') ? '' : type === 'success' ? ICONS.check : type === 'error' ? ICONS.x : ICONS.info);
    el.className = `toast ${type}${loading ? ' loading' : ''}`;
    el.innerHTML = `<div class="ico">${icon()}</div><div class="msg"></div>`;
    $('.msg', el).textContent = text;
    $('#toasts').append(el);

    let timer, remaining = 0, startedAt = 0;
    const close = () => {
        clearTimeout(timer);
        el.classList.add('hide');
        setTimeout(() => el.remove(), 200);
    };
    const schedule = (ms) => {
        clearTimeout(timer);
        remaining = ms;
        startedAt = Date.now();
        timer = setTimeout(close, ms);
    };
    el.addEventListener('mouseenter', () => {
        if (!remaining) return;
        clearTimeout(timer);
        remaining = Math.max(1500, remaining - (Date.now() - startedAt));
    });
    el.addEventListener('mouseleave', () => { if (remaining) schedule(remaining); });
    el.addEventListener('click', () => { if (!el.classList.contains('loading')) close(); });
    if (!loading) schedule(type === 'error' ? 12000 : timeout);

    return {
        update(newText, newType = 'success') {
            type = newType;
            el.className = `toast ${newType}`;
            $('.ico', el).innerHTML = icon();
            $('.msg', el).textContent = newText;
            schedule(newType === 'error' ? 12000 : timeout);
        },
        close,
    };
}

async function task(text, fn, successText) {
    const t = toast(text, 'info', { loading: true });
    try {
        const result = await fn();
        t.update(typeof successText === 'function' ? successText(result) : (successText || result?.message || 'Готово'), 'success');
        return result;
    } catch (e) {
        t.update(e.message, 'error');
        if (e.code === 'auth') setConn('err', 'Нет доступа');
        throw e;
    }
}

// ============================================================ модальные окна

function openModal({ title, body, foot = '', size = '', onMount }) {
    const root = $('#modal-root');
    const wrap = document.createElement('div');
    wrap.className = 'backdrop';
    wrap.innerHTML = `
        <div class="modal ${size}" role="dialog" aria-modal="true">
            <div class="modal-head"><h2>${esc(title)}</h2>
                <button class="icon-btn sm plain" data-close title="Закрыть">${ICONS.x}</button></div>
            <div class="modal-body">${body}</div>
            ${foot ? `<div class="modal-foot">${foot}</div>` : ''}
        </div>`;
    root.append(wrap);

    const prevFocus = document.activeElement;
    const close = () => {
        wrap.remove();
        document.removeEventListener('keydown', onKey, true);
        prevFocus?.focus?.();
    };
    const onKey = (e) => {
        if (e.key === 'Escape' && root.lastElementChild === wrap) {
            e.stopPropagation();
            close();
        }
    };
    document.addEventListener('keydown', onKey, true);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(); });
    wrap.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) close(); });

    const modal = { el: wrap, close };
    onMount?.(modal);
    setTimeout(() => $('[autofocus]', wrap)?.focus(), 30);
    return modal;
}

function confirmDialog(text, { title = 'Подтверждение', ok = 'Удалить', danger = true } = {}) {
    return new Promise((resolve) => {
        let answered = false;
        const m = openModal({
            title,
            size: 'sm',
            body: `<p>${text}</p>`,
            foot: `<button class="btn ghost" data-close>Отмена</button>
                   <button class="btn ${danger ? 'danger' : 'primary'}" data-ok autofocus>${esc(ok)}</button>`,
            onMount: ({ el, close }) => {
                $('[data-ok]', el).onclick = () => { answered = true; close(); resolve(true); };
            },
        });
        const obs = new MutationObserver(() => {
            if (!m.el.isConnected) { obs.disconnect(); if (!answered) resolve(false); }
        });
        obs.observe($('#modal-root'), { childList: true });
    });
}

/** Возвращает введённую строку или null при отмене (пустая строка допустима, если allowEmpty). */
function promptDialog({ title, label, value = '', ok = 'Сохранить', allowEmpty = false }) {
    return new Promise((resolve) => {
        let done = false;
        const m = openModal({
            title, size: 'sm',
            body: `<form id="prompt-form"><label class="field"><span>${esc(label)}</span>
                   <input type="text" name="v" value="${esc(value)}" autofocus ${allowEmpty ? '' : 'required'}></label></form>`,
            foot: `<button class="btn ghost" data-close>Отмена</button>
                   <button class="btn primary" type="submit" form="prompt-form">${esc(ok)}</button>`,
            onMount: ({ el, close }) => {
                $('form', el).onsubmit = (e) => {
                    e.preventDefault();
                    done = true;
                    const v = e.target.v.value.trim();
                    close();
                    resolve(v || (allowEmpty ? '' : null));
                };
            },
        });
        const obs = new MutationObserver(() => {
            if (!m.el.isConnected) { obs.disconnect(); if (!done) resolve(null); }
        });
        obs.observe($('#modal-root'), { childList: true });
    });
}

// ============================================================ состояние

const state = {
    settings: null,
    connected: false,
    overviewLoading: false,
    system: null,
    interfaces: [],
    groups: [],
    limit: 300,
    defaultInterface: '',
    loadingGroups: false,
    expanded: new Set(),
    fresh: new Map(),       // имя списка -> Set недавно добавленных записей
    ipRoutes: null,
    ipLoading: false,
    ipError: '',
    ipSelected: new Set(),
    sites: [],
    sitesSelected: new Set(),
};

function setConn(kind, text) {
    const el = $('#conn');
    el.className = `conn ${kind}`;
    $('.conn-text', el).textContent = text;
}

function ifaceTitle(id) {
    const i = state.interfaces.find((x) => x.id === id);
    return i ? (i.description || i.id) : id;
}

function ifaceOptions(selected) {
    const sel = selected || state.defaultInterface || state.interfaces.find((i) => i.tunnel)?.id || '';
    const opt = (i) => `<option value="${esc(i.id)}" ${i.id === sel ? 'selected' : ''}>${i.up ? '● ' : '○ '}${esc(i.description || i.id)}${i.description ? ` — ${esc(i.id)}` : ''}</option>`;
    const tunnels = state.interfaces.filter((i) => i.tunnel);
    // Локальные сегменты (Bridge — «Домашняя сеть» и т.п.) выходом в интернет не являются.
    const others = state.interfaces.filter((i) => !i.tunnel && i.description && !/^bridge/i.test(i.type || i.id));
    return (tunnels.length ? `<optgroup label="VPN и туннели">${tunnels.map(opt).join('')}</optgroup>` : '')
        + (others.length ? `<optgroup label="Другие подключения">${others.map(opt).join('')}</optgroup>` : '');
}

// ============================================================ вкладки

function switchTab(name) {
    $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
    $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
    storage('s4r-tab', name);
    if (name === 'ip') {
        if (state.connected) loadIpRoutes();
        else if (state.settings?.has_password && !state.overviewLoading) loadOverview({quiet: true});
        else renderIpRoutes();
    }
    if (name === 'sites' && !state.sites.length) loadSites();
    if (name === 'capture') loadCapture();
    else clearTimeout(cap.timer);
}

// ============================================================ роутер: подключение

async function loadSettings() {
    const data = await api('/api/router/settings');
    state.settings = data.settings;
    return data.settings;
}

async function loadOverview({ quiet = false } = {}) {
    if (!state.settings?.has_password) {
        state.connected = false;
        setConn('', 'Не подключено');
        renderGroups();
        if ($('#view-ip').classList.contains('active')) renderIpRoutes();
        return;
    }
    state.loadingGroups = !state.groups.length;
    state.overviewLoading = true;
    if (state.loadingGroups) renderGroups();
    setConn('busy', 'Подключение…');
    if ($('#view-ip').classList.contains('active')) renderIpRoutes();
    try {
        const data = await api('/api/router/overview');
        Object.assign(state, {
            connected: true,
            overviewLoading: false,
            error: '',
            system: data.system,
            interfaces: data.interfaces,
            groups: data.groups,
            limit: data.limit,
            defaultInterface: data.default_interface || '',
            loadingGroups: false,
        });
        setConn('ok', data.system.model);
        $('#router-caption').textContent = `${data.system.model} · KeeneticOS ${data.system.release}`.trim();
        if (!quiet) toast('Данные роутера обновлены', 'success', { timeout: 1800 });
    } catch (e) {
        state.connected = false;
        state.overviewLoading = false;
        state.loadingGroups = false;
        state.error = e.message;
        setConn('err', e.code === 'auth' ? 'Нет доступа' : 'Ошибка');
        toast(e.message, 'error');
    }
    renderGroups();
    if ($('#view-ip').classList.contains('active')) await loadIpRoutes();
    if (capVisible()) {
        if (state.connected) await ensureIpRoutes();
        renderCapResults();
    }
}

function openSettings() {
    const s = state.settings || {};
    openModal({
        title: 'Подключение к роутеру',
        body: `
            <div class="notice">${ICONS.info}<div>Программа работает с роутером через тот же API, что и его веб-интерфейс.
            Пароль хранится локально в <code>settings.json</code>, зашифрованный средствами Windows (DPAPI).</div></div>
            <form id="settings-form" autocomplete="off">
                <label class="field"><span>Адрес роутера</span>
                    <input type="text" name="router_host" value="${esc(s.router_host || '192.168.1.1')}" placeholder="192.168.1.1 или my.keenetic.net" required autofocus></label>
                <div class="row">
                    <label class="field"><span>Логин</span>
                        <input type="text" name="router_login" value="${esc(s.router_login || 'admin')}" required></label>
                    <label class="field"><span>Пароль</span>
                        <input type="password" name="router_password" placeholder="${s.has_password ? '•••••••• (сохранён)' : 'Пароль администратора'}" ${s.has_password ? '' : 'required'}></label>
                </div>
            </form>`,
        foot: `<button class="btn ghost" data-close>Отмена</button>
               <button class="btn primary" type="submit" form="settings-form">Подключиться</button>`,
        onMount: ({ el, close }) => {
            $('form', el).onsubmit = async (e) => {
                e.preventDefault();
                const f = e.target;
                const btn = $('.modal-foot .primary', el);
                await withButton(btn, async () => {
                    try {
                        const saved = await api('/api/router/settings', 'PUT', {
                            router_host: f.router_host.value.trim(),
                            router_login: f.router_login.value.trim(),
                            router_password: f.router_password.value,
                        });
                        state.settings = saved.settings;
                        const res = await api('/api/router/test', 'POST', {});
                        toast(`Подключено: ${res.system.model} (${res.system.release})`, 'success');
                        close();
                        state.groups = [];
                        invalidateIpRoutes();
                        await loadOverview({ quiet: true });
                    } catch (err) {
                        toast(err.message, 'error');
                    }
                });
            };
        },
    });
}

// ============================================================ DNS-списки: отрисовка

const CHIP_LIMIT = 48;

function searchQuery() {
    return normalizeEntry($('#dns-search').value);
}

/** Запись списка покрывает домен q, если q совпадает с ней или является её поддоменом. */
function covers(entry, q) {
    return q === entry || q.endsWith('.' + entry);
}

function renderGroups() {
    const box = $('#groups');
    const q = searchQuery();
    renderStats();

    if (!state.settings?.has_password || (!state.connected && !state.groups.length && !state.loadingGroups)) {
        const needSetup = !state.settings?.has_password;
        box.innerHTML = `
            <div class="placeholder card">
                <div class="big">${ICONS.router}</div>
                <h2>${needSetup ? 'Подключите роутер Keenetic' : 'Роутер недоступен'}</h2>
                <p>${needSetup
                    ? 'Укажите адрес и пароль администратора — и управляйте списками доменов прямо отсюда: добавляйте сайты в один клик, без bat-файлов.'
                    : esc(state.error || 'Проверьте адрес роутера и пароль.')}</p>
                <button class="btn primary" data-action="settings">${needSetup ? 'Подключить' : 'Настройки подключения'}</button>
                ${needSetup ? '' : `<button class="btn ghost" data-action="dns-reload">Повторить</button>`}
            </div>`;
        $('#dns-search-hint').innerHTML = '';
        return;
    }

    if (state.loadingGroups) {
        layoutColumns(box, Array.from({ length: 4 }, () => '<div class="skeleton"></div>'));
        return;
    }

    if (!state.groups.length) {
        box.innerHTML = `
            <div class="placeholder card">
                <div class="big">${ICONS.list}</div>
                <h2>Списков пока нет</h2>
                <p>Создайте первый список доменов и выберите интерфейс, через который пойдёт трафик.</p>
                <button class="btn primary" data-action="group-new">${ICONS.plus} Новый список</button>
            </div>`;
        return;
    }

    renderGroupCards(box, q);
    renderSearchHint(q);
}

/** Обновляем списки на месте, сохраняя колонки, прокрутку и незавершённый ввод. */
function renderGroupCards(box, q) {
    const scroll = {x: window.scrollX, y: window.scrollY};
    const previous = new Map($$('.group', box).map((card) => [card.dataset.name, {
        card, scrollTop: $('.chips', card).scrollTop, draft: $('.group-add input', card).value,
    }]));
    const cardsHtml = state.groups.map((g) => groupCard(g, q));
    const sameLayout = +box.dataset.cols === columnCount(box)
        && previous.size === state.groups.length && state.groups.every((g) => previous.has(g.name));
    if (sameLayout) {
        const template = document.createElement('template');
        template.innerHTML = cardsHtml.join('');
        for (const next of template.content.children) {
            const card = previous.get(next.dataset.name).card;
            card.className = next.className;
            renderHtml(card, next.innerHTML);
        }
    } else {
        layoutColumns(box, cardsHtml);
    }
    for (const card of $$('.group', box)) {
        const old = previous.get(card.dataset.name);
        if (!old) continue;
        $('.chips', card).scrollTop = old.scrollTop;
        const input = $('.group-add input', card);
        if (input.value !== old.draft) input.value = old.draft;
    }
    if (window.scrollX !== scroll.x || window.scrollY !== scroll.y) window.scrollTo(scroll.x, scroll.y);
}

const CARD_MIN_WIDTH = 380;
const CARD_GAP = 16;

function columnCount(box) {
    return Math.max(1, Math.floor((box.clientWidth + CARD_GAP) / (CARD_MIN_WIDTH + CARD_GAP)));
}

/** Раскладывает карточки по колонкам: каждая следующая — в самую короткую колонку. */
function layoutColumns(box, cardsHtml) {
    const cols = columnCount(box);
    box.dataset.cols = cols;
    box.innerHTML = '<div class="groups-col"></div>'.repeat(cols);
    const columns = $$('.groups-col', box);
    const tpl = document.createElement('template');
    tpl.innerHTML = cardsHtml.join('');
    for (const card of [...tpl.content.children]) {
        // Самая короткая колонка; при почти равной высоте (±48px) - та, что левее,
        // чтобы карточка не уезжала в дальний край из-за пары пикселей.
        const heights = columns.map((c) => c.offsetHeight);
        const min = Math.min(...heights);
        columns[heights.findIndex((h) => h <= min + 48)].append(card);
    }
}

new ResizeObserver(() => {
    const box = $('#groups');
    if ($('.groups-col', box) && +box.dataset.cols !== columnCount(box)) renderGroups();
}).observe($('#groups'));

function renderStats() {
    const el = $('#dns-stats');
    if (!state.connected) { el.innerHTML = ''; return; }
    const entries = state.groups.reduce((n, g) => n + g.count, 0);
    const routes = state.groups.flatMap((g) => g.routes);
    const active = routes.filter((r) => r.enabled).length;
    const orphan = state.groups.filter((g) => !g.routes.length).length;
    const tunnels = state.interfaces.filter((i) => i.tunnel);
    const upTunnels = tunnels.filter((i) => i.up).length;
    el.innerHTML = [
        ['Списков', state.groups.length, orphan ? `<small>${orphan} без правила</small>` : ''],
        ['Записей', entries, ''],
        ['Активных правил', active, routes.length > active ? `<small>из ${routes.length}</small>` : ''],
        ['Туннели онлайн', upTunnels, `<small>из ${tunnels.length}</small>`],
    ].map(([label, value, extra]) => `
        <div class="stat card"><div class="stat-label">${label}</div>
        <div class="stat-value">${value} ${extra}</div></div>`).join('');
}

/**
 * Подсказка к записи списка:
 *  - лишняя: её уже покрывает другая запись этого списка (api.z.ai при z.ai);
 *  - www-only: www.site.com без site.com покрывает только поддомен www;
 *  - дубль: та же запись есть в другом списке.
 */
function entryNote(g, e) {
    if (isIp(e)) return null;
    const parent = g.entries.find((o) => o !== e && covers(o, e));
    if (parent) return { cls: 'redundant', text: `лишняя запись: уже покрывается ${parent}` };
    if (e.startsWith('www.') && !g.entries.includes(e.slice(4))) {
        return { cls: 'partial', text: `покрывает только www — добавьте ${e.slice(4)}, чтобы охватить весь сайт` };
    }
    const twin = state.groups.find((o) => o.name !== g.name && o.entries.some((x) => covers(x, e)));
    if (twin) return { cls: 'dup', text: `также входит в список «${twin.title}»` };
    return null;
}

function groupCard(g, q) {
    const expanded = state.expanded.has(g.name);
    const fresh = state.fresh.get(g.name) || new Set();
    const hits = q ? g.entries.filter((e) => e.includes(q) || covers(e, q)) : [];
    const dim = q && !hits.length && !g.title.toLowerCase().includes(q);
    const pct = Math.min(100, Math.round((g.count / g.limit) * 100));
    const meterCls = pct >= 100 ? 'full' : pct >= 85 ? 'warn' : '';
    const redundant = g.entries.filter((e) => entryNote(g, e)?.cls === 'redundant').length;

    let shown = g.entries;
    if (q && hits.length) shown = hits;
    else if (!expanded) shown = g.entries.slice(0, CHIP_LIMIT);
    const hidden = g.entries.length - shown.length;

    const chips = shown.length
        ? shown.map((e) => {
            const note = entryNote(g, e);
            return `
            <span class="chip${isIp(e) ? ' ip' : ''}${hits.includes(e) ? ' hit' : ''}${fresh.has(e) ? ' new' : ''}${note ? ` ${note.cls}` : ''}" data-render-key="${esc(e)}" title="${esc(note ? `${e} — ${note.text}` : e)}">
                ${note ? '<i class="chip-mark"></i>' : ''}<span>${esc(e)}</span>
                <button data-action="entry-remove" data-group="${esc(g.name)}" data-entry="${esc(e)}" title="Удалить из списка">${ICONS.x}</button>
            </span>`;
        }).join('')
        : '<div class="chips-empty">Список пуст — добавьте домены ниже</div>';

    const routes = g.routes.length
        ? g.routes.map((r) => `
            <div class="route${r.enabled ? '' : ' off'}">
                <label class="switch" title="${r.enabled ? 'Выключить правило' : 'Включить правило'}">
                    <input type="checkbox" data-action="route-toggle" data-index="${esc(r.index)}" ${r.enabled ? 'checked' : ''}><span></span>
                </label>
                <span class="arrow">${ICONS.arrow}</span>
                <button class="route-iface" data-action="route-move" data-group="${esc(g.name)}" data-iface="${esc(r.interface)}" title="${esc(r.interface_title)} — сменить подключение для этого списка"><span class="dot${r.interface_up ? ' up' : ''}"></span><span class="route-iface-title">${esc(r.interface_title)}</span></button>
                <div class="route-tags">${r.auto ? '<span class="tag" title="Автоматическое добавление маршрутов">авто</span>' : ''}${r.reject ? '<span class="tag warn" title="Эксклюзивный маршрут">Экскл.</span>' : ''}</div>
                <button class="route-opts" data-action="route-edit" data-index="${esc(r.index)}" title="Настроить правило: эксклюзивный маршрут, автодобавление">
                    <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></svg>
                </button>
                <button class="icon-btn sm plain danger" data-action="route-delete" data-index="${esc(r.index)}" title="Удалить правило">${ICONS.trash}</button>
            </div>`).join('')
        : `<div class="route-none">${ICONS.info}<span>Нет правила — домены никуда не направляются</span>
            <button class="btn sm primary" data-action="route-new" data-group="${esc(g.name)}">Направить</button></div>`;

    return `
    <article class="group card${dim ? ' dim' : ''}${q && hits.length ? ' match' : ''}${expanded ? ' expanded' : ''}" data-name="${esc(g.name)}">
        <div class="group-head">
            ${groupIcon(g.title)}
            <div class="group-title">
                <h2 data-action="group-rename" data-group="${esc(g.name)}" title="${esc(g.title)} — нажмите, чтобы переименовать">${esc(g.title)}</h2>
                <div class="group-meta" title="Имя на роутере: ${esc(g.name)}">
                    <span>${g.count} из ${g.limit}</span>
                    <span class="meter ${meterCls}"><i style="width:${pct}%"></i></span>
                    ${redundant ? `<button class="link-btn" data-action="group-clean" data-group="${esc(g.name)}" title="Записи, которые уже покрываются другими доменами этого списка">убрать лишние: ${redundant}</button>` : ''}
                </div>
            </div>
            <div class="group-menu">
                ${g.routes.length ? `<button class="icon-btn sm plain" data-action="route-new" data-group="${esc(g.name)}" title="Добавить ещё правило">${ICONS.plus}</button>` : ''}
                <button class="icon-btn sm plain" data-action="group-edit" data-group="${esc(g.name)}" title="Редактировать списком">${ICONS.edit}</button>
                <button class="icon-btn sm plain" data-action="group-copy" data-group="${esc(g.name)}" title="Копировать все записи">${ICONS.copy}</button>
                <button class="icon-btn sm plain" data-action="group-export" data-group="${esc(g.name)}" title="Скачать .txt">${ICONS.download}</button>
                <button class="icon-btn sm plain danger" data-action="group-delete" data-group="${esc(g.name)}" title="Удалить список">${ICONS.trash}</button>
            </div>
        </div>
        <div class="routes">${routes}</div>
        <div class="chips">${chips}</div>
        ${hidden > 0 && !q ? `<button class="chips-more" data-action="group-expand" data-group="${esc(g.name)}">Показать ещё ${hidden}</button>` : ''}
        ${expanded && !q && g.entries.length > CHIP_LIMIT ? `<button class="chips-more" data-action="group-expand" data-group="${esc(g.name)}">Свернуть</button>` : ''}
        <form class="group-add" data-render-key="add" data-group="${esc(g.name)}">
            <input type="text" placeholder="Добавить домен, URL или IP…" title="Можно вставить сразу несколько — через пробел, запятую или с новой строки" autocomplete="off" spellcheck="false">
            <button class="btn sm primary" type="submit">${ICONS.plus}</button>
        </form>
    </article>`;
}

function renderSearchHint(q) {
    const hint = $('#dns-search-hint');
    if (!q) { hint.innerHTML = ''; return; }
    const covering = state.groups.filter((g) => g.entries.some((e) => covers(e, q)));
    const looksDomain = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(q) || isIp(q);
    if (covering.length) {
        const g = covering[0];
        const route = g.routes.find((r) => r.enabled);
        const entry = g.entries.find((e) => covers(e, q));
        hint.innerHTML = `✓ <b>${esc(q)}</b> входит в «${esc(g.title)}»${entry !== q ? ` (через <span class="mono">${esc(entry)}</span>)` : ''}`
            + (route ? ` → идёт через <b>${esc(route.interface_title)}</b>` : ' — но у списка нет активного правила');
    } else if (looksDomain) {
        hint.innerHTML = `<b>${esc(q)}</b> не входит ни в один список · <button class="btn sm primary" data-action="search-add">${ICONS.plus} Добавить в список…</button>`;
    } else {
        hint.innerHTML = '';
    }
}

// ============================================================ DNS-списки: действия

function findGroup(name) {
    return state.groups.find((g) => g.name === name);
}

function applyGroup(updated, fresh = []) {
    const g = findGroup(updated.name);
    if (!g) return;
    Object.assign(g, { entries: updated.entries, count: updated.count, title: updated.title, description: updated.description });
    if (fresh.length) {
        state.fresh.set(g.name, new Set(fresh));
        setTimeout(() => state.fresh.delete(g.name), 1500);
    }
}

async function addEntries(name, text) {
    const data = await api(`/api/router/groups/${encodeURIComponent(name)}/entries`, 'POST', { entries: text });
    applyGroup(data.group, data.added);
    renderGroups();
    let msg = data.message;
    if (data.invalid?.length) msg += ` · не распознано: ${data.invalid.slice(0, 3).join(', ')}${data.invalid.length > 3 ? '…' : ''}`;
    toast(msg, data.added.length ? 'success' : 'info');
    return data;
}

async function removeEntry(name, entry) {
    const g = findGroup(name);
    if (!g) return;
    const before = [...g.entries];
    g.entries = g.entries.filter((e) => e !== entry);
    g.count = g.entries.length;
    renderGroups();
    try {
        const data = await api(`/api/router/groups/${encodeURIComponent(name)}/entries`, 'DELETE', { entries: [entry] });
        applyGroup(data.group);
        renderGroups();
        toast(`${entry} удалён из «${g.title}»`, 'success', { timeout: 2000 });
    } catch (e) {
        g.entries = before;
        g.count = before.length;
        renderGroups();
        toast(e.message, 'error');
    }
}

function entriesPreview(textarea, out) {
    const update = () => {
        const items = textarea.value.split(/[\s,;]+/).filter(Boolean);
        const valid = new Set(items.map(normalizeEntry).filter((e) => /^[a-z0-9_.-]+\.[a-z0-9-]{2,}$/.test(e) || isIp(e)));
        const bad = items.length - [...items].filter((i) => valid.has(normalizeEntry(i))).length;
        out.innerHTML = items.length
            ? `<span class="good">${valid.size} ${plural(valid.size, 'запись', 'записи', 'записей')}</span>${bad ? ` · <span class="bad">${bad} не распознано</span>` : ''} · www. и https:// отбрасываются автоматически`
            : 'Домены, URL, IPv4-адреса или подсети — по одному в строке или через пробел. Поддомены включаются автоматически.';
    };
    textarea.addEventListener('input', update);
    update();
}

function openGroupCreate(prefill = '', suggestedTitle = '') {
    if (!state.connected) return openSettings();
    openModal({
        title: 'Новый список доменов',
        body: `
            <form id="group-form">
                <label class="field"><span>Название</span>
                    <input type="text" name="title" value="${esc(suggestedTitle)}" placeholder="Например: YouTube" required autofocus></label>
                <label class="field"><span>Домены</span>
                    <textarea name="entries" placeholder="youtube.com&#10;googlevideo.com&#10;ytimg.com" spellcheck="false">${esc(prefill)}</textarea></label>
                <div class="preview" id="group-preview"></div>
                <label class="field"><span>Направлять через</span>
                    <select name="interface"><option value="">— не создавать правило —</option>${ifaceOptions()}</select></label>
                <div class="options">
                    <label class="switch-label"><span class="switch"><input type="checkbox" name="reject"><span></span></span>Эксклюзивный маршрут</label>
                    <small>Если туннель недоступен, сайты из списка не откроются напрямую (защита от «утечки» мимо VPN).</small>
                </div>
            </form>`,
        foot: `<button class="btn ghost" data-close>Отмена</button>
               <button class="btn primary" type="submit" form="group-form">Создать список</button>`,
        onMount: ({ el, close }) => {
            const f = $('form', el);
            entriesPreview(f.entries, $('#group-preview', el));
            f.onsubmit = async (e) => {
                e.preventDefault();
                await withButton($('.modal-foot .primary', el), async () => {
                    try {
                        const data = await api('/api/router/groups', 'POST', {
                            title: f.title.value.trim(),
                            entries: f.entries.value,
                            interface: f.interface.value,
                            auto: true,
                            reject: f.reject.checked,
                        });
                        toast(data.message, 'success');
                        close();
                        await loadOverview({ quiet: true });
                    } catch (err) {
                        toast(err.message, 'error');
                    }
                });
            };
        },
    });
}

function openGroupEdit(name) {
    const g = findGroup(name);
    if (!g) return;
    openModal({
        title: `Редактирование «${g.title}»`,
        body: `
            <form id="edit-form">
                <label class="field"><span>Название</span>
                    <input type="text" name="title" value="${esc(g.title)}" required></label>
                <label class="field"><span>Записи</span>
                    <textarea name="entries" rows="14" spellcheck="false" autofocus>${esc(g.entries.join('\n'))}</textarea></label>
                <div class="preview" id="edit-preview"></div>
            </form>`,
        foot: `<button class="btn ghost" data-close>Отмена</button>
               <button class="btn primary" type="submit" form="edit-form">Сохранить на роутер</button>`,
        onMount: ({ el, close }) => {
            const f = $('form', el);
            entriesPreview(f.entries, $('#edit-preview', el));
            f.onsubmit = async (e) => {
                e.preventDefault();
                await withButton($('.modal-foot .primary', el), async () => {
                    try {
                        const data = await api(`/api/router/groups/${encodeURIComponent(name)}`, 'PUT', {
                            title: f.title.value.trim(),
                            entries: f.entries.value.split(/[\s,;]+/).filter(Boolean),
                        });
                        applyGroup(data.group);
                        renderGroups();
                        toast('Список сохранён', 'success');
                        close();
                    } catch (err) {
                        toast(err.message, 'error');
                    }
                });
            };
        },
    });
}

function openRouteEdit(index) {
    const g = state.groups.find((x) => x.routes.some((r) => r.index === index));
    const r = g?.routes.find((x) => x.index === index);
    if (!r) return;
    openModal({
        title: 'Настройки правила',
        size: 'sm',
        body: `
            <p>«${esc(g.title)}» → <b>${esc(r.interface_title)}</b></p>
            <form id="route-edit-form" class="options">
                <label class="switch-label"><span class="switch"><input type="checkbox" name="reject" ${r.reject ? 'checked' : ''}><span></span></span>Эксклюзивный маршрут</label>
                <small>Если туннель недоступен, сайты из списка не откроются напрямую — трафик не «утечёт» мимо VPN.</small>
                <label class="switch-label"><span class="switch"><input type="checkbox" name="auto" ${r.auto ? 'checked' : ''}><span></span></span>Добавлять автоматически</label>
                <small>Маршрут действует, только пока подключение активно. Обычно оставляют включённым.</small>
            </form>`,
        foot: `<button class="btn ghost" data-close>Отмена</button>
               <button class="btn primary" type="submit" form="route-edit-form">Сохранить</button>`,
        onMount: ({ el, close }) => {
            $('form', el).onsubmit = async (e) => {
                e.preventDefault();
                const f = e.target;
                if (f.reject.checked === r.reject && f.auto.checked === r.auto) return close();
                await withButton($('.modal-foot .primary', el), async () => {
                    try {
                        await api('/api/router/dns-routes/update', 'POST', { index, auto: f.auto.checked, reject: f.reject.checked });
                        toast(`Правило «${g.title}» обновлено`, 'success');
                        close();
                        await loadOverview({ quiet: true });
                    } catch (err) {
                        toast(err.message, 'error');
                        await loadOverview({ quiet: true });
                    }
                });
            };
        },
    });
}

function openRouteCreate(name) {
    const g = findGroup(name);
    openModal({
        title: 'Правило маршрутизации',
        size: 'sm',
        body: `
            <p>Куда направлять трафик доменов из «${esc(g?.title || name)}»?</p>
            <form id="route-form">
                <label class="field"><span>Интерфейс</span><select name="interface" autofocus>${ifaceOptions()}</select></label>
                <div class="options">
                    <label class="switch-label"><span class="switch"><input type="checkbox" name="reject"><span></span></span>Эксклюзивный маршрут</label>
                </div>
            </form>`,
        foot: `<button class="btn ghost" data-close>Отмена</button>
               <button class="btn primary" type="submit" form="route-form">Добавить правило</button>`,
        onMount: ({ el, close }) => {
            const f = $('form', el);
            f.onsubmit = async (e) => {
                e.preventDefault();
                await withButton($('.modal-foot .primary', el), async () => {
                    try {
                        await api('/api/router/dns-routes', 'POST', {
                            group: name, interface: f.interface.value, auto: true, reject: f.reject.checked,
                        });
                        close();
                        toast('Правило добавлено', 'success');
                        await loadOverview({ quiet: true });
                    } catch (err) {
                        toast(err.message, 'error');
                    }
                });
            };
        },
    });
}

// ============================================================ смена туннеля

/** Ключ группы IP-маршрутов: описание без учёта регистра и пробелов по краям. */
const ckey = (s) => String(s || '').trim().toLowerCase();

/** IP-маршруты, сгруппированные по описанию: [{comment, title, routes}] */
function ipGroups(routes = state.ipRoutes || []) {
    const map = new Map();
    for (const r of routes) {
        const key = ckey(r.comment);
        if (!map.has(key)) map.set(key, { comment: (r.comment || '').trim(), routes: [] });
        map.get(key).routes.push(r);
    }
    return [...map.values()]
        .map((g) => ({ ...g, title: g.comment || 'Без описания' }))
        .sort((a, b) => (!a.comment) - (!b.comment) || a.title.localeCompare(b.title, 'ru'));
}

async function ensureIpRoutes() {
    if (state.connected && (state.ipRoutes === null || state.ipError || state.ipLoading)) await loadIpRoutes();
    return state.ipRoutes || [];
}

let moveLoadingModal = null;

/**
 * Смена туннеля: выбранные DNS-списки и IP-группы переводятся на другое подключение.
 * preset: {groups: [имена списков], from: интерфейс, ipComments: [описания]}
 */
async function openMove(preset = {}) {
    if (!state.connected) return openSettings();
    if (moveLoadingModal?.el.isConnected) return;
    if (state.ipRoutes === null || state.ipError || state.ipLoading) {
        const loading = openModal({
            title: 'Сменить туннель', size: 'lg',
            body: '<div class="move-loading" role="status"><span class="loading-spinner" aria-hidden="true"></span><div><b>Загружаем маршруты</b><p class="muted">Получаем сведения о маршрутах и подключениях…</p></div></div>',
            foot: '<button class="btn ghost" data-close>Отмена</button>',
        });
        moveLoadingModal = loading;
        const loaded = await loadIpRoutes();
        if (moveLoadingModal === loading) moveLoadingModal = null;
        if (!loading.el.isConnected) return;
        if (!loaded) {
            $('.modal-body', loading.el).innerHTML = `<div class="notice">${ICONS.info}<div>Не удалось загрузить маршруты.<br>${esc(state.ipError || 'Проверьте подключение к роутеру.')}</div></div>`;
            $('.modal-foot', loading.el).innerHTML = '<button class="btn ghost" data-close>Отмена</button><button class="btn primary" data-move-retry>Повторить</button>';
            $('[data-move-retry]', loading.el).onclick = () => { loading.close(); openMove(preset); };
            return;
        }
        loading.close();
    }
    const routes = state.groups.flatMap((g) => g.routes);
    const ipg = ipGroups();
    const used = new Map();
    routes.forEach((r) => used.set(r.interface, r.interface_title));
    (state.ipRoutes || []).forEach((r) => r.interface && used.set(r.interface, r.interface_title));
    if (!used.size) return toast('Нечего переводить: правил и IP-маршрутов нет', 'info');

    const counts = (iface) => routes.filter((r) => r.interface === iface).length
        + (state.ipRoutes || []).filter((r) => r.interface === iface).length;
    const from = preset.from ?? [...used.keys()].sort((a, b) => counts(b) - counts(a))[0];
    const ifaceNames = (list) => [...new Set(list.map((r) => r.interface_title))].join(', ') || 'без правила';

    openModal({
        title: 'Сменить туннель',
        size: 'lg',
        body: `
            <form id="move-form">
                <div class="row">
                    <label class="field"><span>Откуда</span>
                        <select name="from"><option value="">Любое подключение</option>
                        ${[...used.entries()].map(([id, t]) => `<option value="${esc(id)}" ${id === from ? 'selected' : ''}>${esc(t)} — ${counts(id)}</option>`).join('')}
                        </select></label>
                    <label class="field"><span>Куда</span>
                        <select name="to">${ifaceOptions(state.interfaces.find((i) => i.tunnel && i.id !== from)?.id)}</select></label>
                </div>
                <div class="move-cols">
                    <div>
                        <h3>DNS-списки</h3>
                        <div class="checks scroll" id="mv-dns">
                            ${state.groups.filter((g) => g.routes.length).map((g) => `
                                <label data-ifaces="${esc(g.routes.map((r) => r.interface).join(' '))}">
                                    <input type="checkbox" name="g" value="${esc(g.name)}"> ${esc(g.title)}
                                    <span class="count">${esc(ifaceNames(g.routes))}</span></label>`).join('') || '<span class="muted">Нет списков с правилами</span>'}
                        </div>
                    </div>
                    <div>
                        <h3>IP-группы</h3>
                        <div class="checks scroll" id="mv-ip">
                            ${ipg.map((g, i) => `
                                <label data-ifaces="${esc(g.routes.map((r) => r.interface).join(' '))}">
                                    <input type="checkbox" name="ip" value="${i}"> ${esc(g.title)}
                                    <span class="count">${g.routes.length} · ${esc(ifaceNames(g.routes))}</span></label>`).join('') || '<span class="muted">Статических маршрутов нет</span>'}
                        </div>
                    </div>
                </div>
                <div class="summary" id="mv-summary"></div>
            </form>`,
        foot: `<button class="btn ghost" data-close>Отмена</button>
               <button class="btn primary" type="submit" form="move-form">Перевести</button>`,
        onMount: ({ el, close }) => {
            const f = $('form', el);
            const selection = () => {
                const src = f.from.value;
                const groups = $$('input[name=g]:checked', f).map((c) => c.value);
                const dnsCount = routes.filter((r) => groups.includes(r.group) && (!src || r.interface === src) && r.interface !== f.to.value).length;
                const ipIdx = $$('input[name=ip]:checked', f)
                    .flatMap((c) => ipg[+c.value].routes)
                    .filter((r) => (!src || r.interface === src) && r.interface !== f.to.value)
                    .map((r) => r.index);
                return { src, groups, dnsCount, ipIdx };
            };
            const summary = () => {
                const s = selection();
                $('#mv-summary', el).innerHTML = s.dnsCount || s.ipIdx.length
                    ? `Будет переведено на <b>${esc(ifaceTitle(f.to.value))}</b>: ${s.dnsCount} ${plural(s.dnsCount, 'правило', 'правила', 'правил')} DNS-списков, ${s.ipIdx.length} IP-${plural(s.ipIdx.length, 'маршрут', 'маршрута', 'маршрутов')}`
                    : 'Отметьте списки или IP-группы, которые нужно перевести.';
            };
            // Отметить всё, что идёт через выбранный источник (или пресет).
            const autoCheck = () => {
                const src = f.from.value;
                $$('#mv-dns label, #mv-ip label', el).forEach((l) => {
                    const box = $('input', l);
                    const ifaces = l.dataset.ifaces.split(' ');
                    if (preset.groups && box.name === 'g') box.checked = preset.groups.includes(box.value);
                    else if (preset.ipComments && box.name === 'ip') box.checked = preset.ipComments.map(ckey).includes(ckey(ipg[+box.value].comment));
                    else if (preset.groups || preset.ipComments) box.checked = false;
                    else box.checked = !src || ifaces.includes(src);
                    l.classList.toggle('dim', !!src && !ifaces.includes(src));
                });
                summary();
            };
            autoCheck();
            f.from.onchange = () => { preset = {}; autoCheck(); };
            f.addEventListener('change', (e) => { if (e.target.name !== 'from') summary(); });

            f.onsubmit = async (e) => {
                e.preventDefault();
                const s = selection();
                if (!s.dnsCount && !s.ipIdx.length) return toast('Нечего переводить', 'error');
                await withButton($('.modal-foot .primary', el), async () => {
                    try {
                        const res = await api('/api/router/move', 'POST', {
                            groups: s.groups, from: s.src, ip_indices: s.ipIdx, interface: f.to.value,
                        });
                        toast(`${res.message} → ${ifaceTitle(f.to.value)}`, 'success');
                        close();
                        invalidateIpRoutes();
                        await loadOverview({ quiet: true });
                    } catch (err) {
                        toast(err.message, 'error');
                    }
                });
            };
        },
    });
}

function openIpMove(indices = [...state.ipSelected]) {
    const selected = state.ipRoutes.filter((r) => indices.includes(r.index));
    const current = [...new Set(selected.map((r) => r.interface_title))];
    openModal({
        title: 'Сменить интерфейс',
        size: 'sm',
        body: `
            <p>${selected.length} ${plural(selected.length, 'маршрут', 'маршрута', 'маршрутов')} сейчас через: <b>${esc(current.join(', '))}</b></p>
            <form id="ipmove-form">
                <label class="field"><span>Новое подключение</span>
                    <select name="to" autofocus>${ifaceOptions(state.interfaces.find((i) => i.tunnel && !selected.some((r) => r.interface === i.id))?.id)}</select></label>
            </form>`,
        foot: `<button class="btn ghost" data-close>Отмена</button>
               <button class="btn primary" type="submit" form="ipmove-form">Перевести</button>`,
        onMount: ({ el, close }) => {
            $('form', el).onsubmit = async (e) => {
                e.preventDefault();
                const to = e.target.to.value;
                await withButton($('.modal-foot .primary', el), async () => {
                    try {
                        const data = await api('/api/router/ip-routes/move', 'POST', { indices, interface: to });
                        toast(`${data.message} → ${ifaceTitle(to)}`, 'success');
                        close();
                        invalidateIpRoutes();
                        await loadIpRoutes();
                    } catch (err) {
                        toast(err.message, 'error');
                    }
                });
            };
        },
    });
}

// ============================================================ перенос: экспорт / импорт

const ICON_UP = '<svg viewBox="0 0 24 24"><path d="M12 15V3M8 7l4-4 4 4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>';
const ICON_DOWN = '<svg viewBox="0 0 24 24"><path d="M12 3v12M8 11l4 4 4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>';

function slug(s) {
    return String(s || '').trim().replace(/[\\/:*?"<>|\s]+/g, '-').replace(/-+/g, '-').slice(0, 40) || 'export';
}

/**
 * Скачивает файл экспорта. Отбор делается на клиенте:
 * groups — названия DNS-списков (null = все), ip — фильтр IP-маршрутов (null = все).
 */
async function exportFile({ groups = null, ip = null, name = '' } = {}) {
    const res = await api('/api/router/export');
    const d = res.data;
    d.groups = groups === null ? d.groups : d.groups.filter((g) => groups.includes(g.title));
    d.ip_routes = ip === null ? d.ip_routes : d.ip_routes.filter(ip);
    const used = new Set([...d.groups.flatMap((g) => g.routes.map((r) => r.interface)), ...d.ip_routes.map((r) => r.interface)]);
    d.interfaces = d.interfaces.filter((i) => used.has(i.id));
    if (!d.groups.length && !d.ip_routes.length) throw new Error('Нечего экспортировать');
    const date = new Date().toISOString().slice(0, 10);
    downloadText(`routedeck-${slug(name || d.source.model || 'keenetic').toLowerCase()}-${date}.json`, JSON.stringify(d, null, 2));
    const n = d.groups.length, m = d.ip_routes.length;
    toast(`Экспортировано: ${n} ${plural(n, 'список', 'списка', 'списков')}, ${m} IP-${plural(m, 'маршрут', 'маршрута', 'маршрутов')}`, 'success');
}

async function openTransfer(tab = 'export') {
    if (!state.connected) return openSettings();
    await ensureIpRoutes();
    const ipg = ipGroups();
    openModal({
        title: 'Перенос настроек',
        size: 'lg',
        body: `
            <div class="seg" role="tablist">
                <button class="seg-btn" data-seg="export">${ICON_DOWN} Экспорт</button>
                <button class="seg-btn" data-seg="import">${ICON_UP} Импорт</button>
            </div>

            <div data-pane="export">
                <p>Отметьте, что сохранить в файл. Файл можно открыть в RouteDeck на другом компьютере и загрузить на другой Keenetic. Пароль роутера в файл не попадает.</p>
                <div class="move-cols">
                    <div>
                        <h3><label><input type="checkbox" data-all="ex-g" checked> DNS-списки</label></h3>
                        <div class="checks scroll">
                            ${state.groups.map((g) => `<label><input type="checkbox" name="ex-g" value="${esc(g.title)}" checked> ${esc(g.title)}<span class="count">${g.count}</span></label>`).join('') || '<span class="muted">Нет списков</span>'}
                        </div>
                    </div>
                    <div>
                        <h3><label><input type="checkbox" data-all="ex-ip" checked> IP-группы</label></h3>
                        <div class="checks scroll">
                            ${ipg.map((g, i) => `<label><input type="checkbox" name="ex-ip" value="${i}" checked> ${esc(g.title)}<span class="count">${g.routes.length}</span></label>`).join('') || '<span class="muted">Нет статических маршрутов</span>'}
                        </div>
                    </div>
                </div>
            </div>

            <div data-pane="import">
                <p>Загрузите файл экспорта RouteDeck — дальше можно выбрать, что импортировать и как: дополнить или перезаписать. Подходят и списки адресов <span class="mono">.txt</span> / <span class="mono">.bat</span>.</p>
                <label class="file-drop" id="drop">
                    ${ICON_UP}<b>Выберите файл</b><span>или перетащите его сюда</span>
                    <input type="file" accept=".json,.txt,.bat,.csv,application/json,text/plain" hidden>
                </label>
            </div>`,
        foot: `<button class="btn ghost" data-close>Закрыть</button>
               <button class="btn primary" data-export>${ICON_DOWN} Скачать файл</button>`,
        onMount: ({ el, close }) => {
            const setTab = (t) => {
                $$('.seg-btn', el).forEach((b) => b.classList.toggle('active', b.dataset.seg === t));
                $$('[data-pane]', el).forEach((p) => { p.hidden = p.dataset.pane !== t; });
                $('[data-export]', el).hidden = t !== 'export';
            };
            setTab(tab);
            el.addEventListener('click', (e) => {
                const seg = e.target.closest('[data-seg]');
                if (seg) setTab(seg.dataset.seg);
            });
            bindCheckAll(el);

            $('[data-export]', el).onclick = async (e) => {
                const groups = $$('input[name=ex-g]:checked', el).map((c) => c.value);
                const comments = new Set($$('input[name=ex-ip]:checked', el).map((c) => ckey(ipg[+c.value].comment)));
                await withButton(e.currentTarget, async () => {
                    try {
                        await exportFile({ groups, ip: (r) => comments.has(ckey(r.comment)) });
                        close();
                    } catch (err) { toast(err.message, 'error'); }
                });
            };

            const drop = $('#drop', el);
            const read = async (file) => {
                if (!file) return;
                const text = await file.text();
                close();
                handleImportFile(file.name, text);
            };
            $('input[type=file]', el).onchange = (e) => read(e.target.files[0]);
            drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
            drop.addEventListener('dragleave', () => drop.classList.remove('over'));
            drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); read(e.dataTransfer.files[0]); });
        },
    });
}

/** «Выбрать все» для групп чекбоксов: <input data-all="имя"> управляет input[name=имя]. */
function bindCheckAll(root) {
    const sync = (name) => {
        const all = $(`[data-all="${name}"]`, root);
        const boxes = $$(`input[name="${name}"]`, root);
        if (!all) return;
        all.checked = boxes.length > 0 && boxes.every((c) => c.checked);
        all.indeterminate = !all.checked && boxes.some((c) => c.checked);
    };
    root.addEventListener('change', (e) => {
        const name = e.target.dataset.all;
        if (name) $$(`input[name="${name}"]`, root).forEach((c) => { c.checked = e.target.checked; });
        else if (e.target.name) sync(e.target.name);
    });
    $$('[data-all]', root).forEach((a) => sync(a.dataset.all));
}

function handleImportFile(fileName, text) {
    let data = null;
    try { data = JSON.parse(text); } catch { /* не JSON - разбираем как список адресов */ }
    if (data) {
        if (data.format !== 1 || !Array.isArray(data.groups)) return toast('Это не файл экспорта RouteDeck', 'error');
        return openImport(data, fileName);
    }
    const ips = parseRouteText(text);
    const domains = [...new Set(text.split(/[\s,;]+/).map(normalizeEntry)
        .filter((d) => /^[a-z0-9_.-]+\.[a-z0-9-]{2,}$/.test(d) && !isIp(d)))];
    const stem = fileName.replace(/\.[^.]+$/, '').replace(/[-_]?ipv4$/i, '');
    if (ips.length) return openIpCreate(ips.join('\n'), null, stem);
    if (domains.length) return openPickGroup(domains, `Домены из ${fileName}`);
    toast('В файле не найдено ни IP-адресов, ни доменов', 'error');
}

function guessInterface(src) {
    const title = (src.title || '').toLowerCase();
    return state.interfaces.find((i) => (i.description || '').toLowerCase() === title)?.id
        || state.interfaces.find((i) => i.id === src.id && i.tunnel)?.id
        || state.defaultInterface
        || state.interfaces.find((i) => i.tunnel)?.id
        || '';
}

function openImport(data, fileName) {
    const entries = data.groups.reduce((n, g) => n + (g.entries?.length || 0), 0);
    const when = data.exported_at ? new Date(data.exported_at).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '';
    const ifaces = data.interfaces || [];
    const ipg = ipGroups(data.ip_routes || []);

    openModal({
        title: 'Импорт на этот роутер',
        size: 'lg',
        body: `
            <div class="notice">${ICONS.info}<div><b>${esc(fileName)}</b><br>
                ${esc(data.source?.model || 'Keenetic')}${when ? ` · ${esc(when)}` : ''} ·
                ${data.groups.length} ${plural(data.groups.length, 'список', 'списка', 'списков')} (${entries} записей) ·
                ${data.ip_routes?.length || 0} IP-маршрутов</div></div>
            <form id="import-form">
                <h3>Как импортировать</h3>
                <div class="modes">
                    <label><input type="radio" name="mode" value="merge" checked><div><b>Только добавить</b>
                        <small>Добавляется то, чего на роутере ещё нет. Ничего не удаляется. Пример: в вашем «Telegram» 5 адресов, в файле 3 других — станет 8.</small></div></label>
                    <label><input type="radio" name="mode" value="replace"><div><b>Обновить как в файле</b>
                        <small>Списки и IP-группы, которые есть и в файле, и на роутере, становятся в точности как в файле — лишнее из них удаляется. Остальные не трогаются. Пример: «Telegram» станет ровно 3 адреса из файла.</small></div></label>
                    <label class="danger"><input type="radio" name="mode" value="replace_all"><div><b>Заменить всё</b>
                        <small>Все списки, правила и статические маршруты роутера удаляются, загружается только файл. Маршрут по умолчанию не трогается.</small></div></label>
                </div>

                ${ifaces.length ? `<h3>Подключения</h3>
                <p class="muted" style="margin:0 0 10px">Через какое подключение этого роутера направлять трафик вместо исходного?</p>
                ${ifaces.map((i) => `
                    <div class="map-row">
                        <div class="src"><b>${esc(i.title)}</b><small>${esc(i.id)}</small></div>
                        <span class="arrow">${ICONS.arrow}</span>
                        <select data-src="${esc(i.id)}"><option value="">— не создавать правила —</option>${ifaceOptions(guessInterface(i))}</select>
                    </div>`).join('')}` : ''}

                <div class="move-cols">
                    <div>
                        <h3><label><input type="checkbox" data-all="group" checked> DNS-списки</label></h3>
                        <div class="checks scroll">
                            ${data.groups.map((g) => `<label><input type="checkbox" name="group" value="${esc(g.title)}" checked> ${esc(g.title)}<span class="count">${g.entries.length}</span></label>`).join('') || '<span class="muted">В файле нет списков</span>'}
                        </div>
                    </div>
                    <div>
                        <h3><label><input type="checkbox" data-all="ipc" checked> IP-группы</label></h3>
                        <div class="checks scroll">
                            ${ipg.map((g) => `<label><input type="checkbox" name="ipc" value="${esc(g.comment)}" checked> ${esc(g.title)}<span class="count">${g.routes.length}</span></label>`).join('') || '<span class="muted">В файле нет IP-маршрутов</span>'}
                        </div>
                    </div>
                </div>
                <div class="summary" id="imp-summary">Подсчёт изменений…</div>
            </form>`,
        foot: `<button class="btn ghost" data-close>Отмена</button>
               <button class="btn primary" type="submit" form="import-form">Импортировать</button>`,
        onMount: ({ el, close }) => {
            const f = $('form', el);
            bindCheckAll(f);
            const payload = () => {
                const ipComments = $$('input[name=ipc]:checked', f).map((c) => c.value);
                return {
                    data,
                    mode: f.mode.value,
                    interface_map: Object.fromEntries($$('select[data-src]', f).map((s) => [s.dataset.src, s.value])),
                    groups: $$('input[name=group]:checked', f).map((c) => c.value),
                    include_ip: ipComments.length > 0,
                    ip_comments: ipComments,
                };
            };

            let timer, seq = 0;
            const preview = () => {
                clearTimeout(timer);
                timer = setTimeout(async () => {
                    const my = ++seq;
                    const box = $('#imp-summary', el);
                    try {
                        const res = await api('/api/router/import/preview', 'POST', payload());
                        if (my === seq) box.innerHTML = renderImportSummary(res.summary, true);
                    } catch (err) {
                        if (my === seq) box.innerHTML = `<span class="minus">${esc(err.message)}</span>`;
                    }
                }, 250);
            };
            f.addEventListener('change', preview);
            preview();

            f.onsubmit = async (e) => {
                e.preventDefault();
                const p = payload();
                if (p.mode === 'replace_all' && !await confirmDialog(
                    'Все текущие списки доменов, правила и статические маршруты этого роутера будут <b>удалены</b> и заменены данными из файла.<br><br>Перед импортом программа сохранит резервную копию.',
                    { title: 'Заменить всё?', ok: 'Заменить всё' })) return;
                await withButton($('.modal-foot .primary', el), async () => {
                    try {
                        const res = await api('/api/router/import', 'POST', p);
                        close();
                        openModal({
                            title: 'Импорт выполнен',
                            size: 'sm',
                            body: `${renderImportSummary(res.summary, false)}
                                   <p class="muted" style="margin-top:14px">Резервная копия прежней конфигурации:<br><span class="mono">${esc(res.backup)}</span><br>
                                   Чтобы откатиться, импортируйте этот файл в режиме «Заменить всё».</p>`,
                            foot: '<button class="btn primary" data-close autofocus>Готово</button>',
                        });
                        invalidateIpRoutes();
                        await loadOverview({ quiet: true });
                    } catch (err) {
                        toast(err.message, 'error');
                    }
                });
            };
        },
    });
}

function renderImportSummary(s, future) {
    const n = (v, one, few, many) => `${v} ${plural(v, one, few, many)}`;
    const items = [];
    if (s.lists_removed) items.push(`<span class="minus">${future ? 'Будет удалено' : 'Удалено'}: ${n(s.lists_removed, 'список', 'списка', 'списков')}</span>`);
    if (s.lists_created) items.push(`<span class="plus">${future ? 'Будет создано' : 'Создано'}: ${n(s.lists_created, 'список', 'списка', 'списков')}</span>`);
    if (s.lists_updated) items.push(`${future ? 'Изменится' : 'Изменено'}: ${n(s.lists_updated, 'список', 'списка', 'списков')}`);
    if (s.entries_added) items.push(`<span class="plus">+${n(s.entries_added, 'запись', 'записи', 'записей')}</span>`);
    if (s.rules_added || s.rules_removed) items.push(`Правила: <span class="plus">+${s.rules_added}</span>${s.rules_removed ? ` / <span class="minus">−${s.rules_removed}</span>` : ''}`);
    if (s.ip_added || s.ip_removed) items.push(`IP-маршруты: <span class="plus">+${s.ip_added}</span>${s.ip_removed ? ` / <span class="minus">−${s.ip_removed}</span>` : ''}`);
    if (s.ip_updated) items.push(`Описание ${future ? 'будет дополнено' : 'дополнено'} у ${n(s.ip_updated, 'IP-маршрута', 'IP-маршрутов', 'IP-маршрутов')}`);
    if (s.skipped_entries) items.push(`<span class="warn">Пропущено некорректных записей: ${s.skipped_entries}</span>`);
    (s.warnings || []).forEach((w) => items.push(`<span class="warn">${esc(w)}</span>`));
    if (!items.length) return 'Изменений нет — всё уже есть на роутере.';
    return `<ul>${items.map((i) => `<li>${i}</li>`).join('')}</ul>`;
}

/** Выбор списка, в который добавить записи (или создание нового). */
function openPickGroup(entries, title = 'Добавить в список', suggestedTitle = '') {
    if (!state.connected) return openSettings();
    const text = Array.isArray(entries) ? entries.join('\n') : entries;
    openModal({
        title,
        body: `
            <p>Записи: <span class="mono">${esc(text.split(/\s+/).slice(0, 6).join(', '))}${text.split(/\s+/).length > 6 ? '…' : ''}</span></p>
            <div class="pick-list">
                ${state.groups.map((g) => `
                    <button class="pick" data-pick="${esc(g.name)}">
                        ${groupIcon(g.title)}
                        <div><b>${esc(g.title)}</b><small>${g.count} из ${g.limit} · ${g.routes.map((r) => esc(r.interface_title)).join(', ') || 'без правила'}</small></div>
                    </button>`).join('')}
            </div>
            <button class="btn ghost" data-new>${ICONS.plus} Создать новый список</button>`,
        onMount: ({ el, close }) => {
            el.addEventListener('click', async (e) => {
                const pick = e.target.closest('[data-pick]');
                if (pick) {
                    close();
                    try { await addEntries(pick.dataset.pick, text); } catch (err) { toast(err.message, 'error'); }
                    return;
                }
                if (e.target.closest('[data-new]')) {
                    close();
                    openGroupCreate(text, suggestedTitle);
                }
            });
        },
    });
}

// ============================================================ IP-маршруты

let ipLoadPromise = null;
let ipLoadRevision = 0;

function invalidateIpRoutes({keepData = false} = {}) {
    ipLoadRevision++;
    ipLoadPromise = null;
    if (!keepData) state.ipRoutes = null;
    state.ipLoading = false;
    state.ipError = '';
}

function loadIpRoutes() {
    if (!state.connected) {
        renderIpRoutes();
        return Promise.resolve(false);
    }
    if (ipLoadPromise) return ipLoadPromise;
    const revision = ipLoadRevision;
    state.ipLoading = true;
    state.ipError = '';
    renderIpRoutes();
    const request = (async () => {
        try {
            const data = await api('/api/router/ip-routes');
            if (revision !== ipLoadRevision || !state.connected) return false;
            const alive = new Set(data.routes.map((r) => r.index));
            state.ipRoutes = data.routes;
            state.ipSelected = new Set([...state.ipSelected].filter((i) => alive.has(i)));
            return true;
        } catch (e) {
            if (revision === ipLoadRevision && state.connected) {
                state.ipError = e.message;
                if (!$('#view-ip').classList.contains('active')) toast(e.message, 'error');
            }
            return false;
        } finally {
            if (revision === ipLoadRevision) {
                state.ipLoading = false;
                if (ipLoadPromise === request) ipLoadPromise = null;
                renderIpRoutes();
            }
        }
    })();
    ipLoadPromise = request;
    return request;
}

function ipCollapsed() {
    try { return new Set(JSON.parse(storage('s4r-ip-collapsed') || '[]')); } catch { return new Set(); }
}

function renderIpRoutes() {
    const box = $('#ip-groups');
    const empty = $('#ip-empty');
    const status = $('#ip-status');
    const q = $('#ip-search').value.trim().toLowerCase();
    const collapsed = ipCollapsed();
    const all = state.ipRoutes || [];

    const n = state.ipSelected.size;
    $('#ip-bulk').hidden = !n;
    $('#ip-bulk-count').textContent = `Выбрано: ${n}`;
    box.setAttribute('aria-busy', String(state.ipLoading));
    $('[data-action="ip-reload"]').disabled = state.ipLoading || state.overviewLoading;

    if (!state.connected) {
        status.innerHTML = '';
        box.innerHTML = '';
        empty.hidden = false;
        empty.innerHTML = state.overviewLoading
            ? '<div class="ip-connect-status" role="status"><span class="loading-spinner" aria-hidden="true"></span>Подключаемся к роутеру…</div>'
            : state.settings?.has_password
                ? `<p>Не удалось подключиться к роутеру.${state.error ? `<br><span class="muted">${esc(state.error)}</span>` : ''}</p><button class="btn sm primary" data-action="ip-reload">Повторить подключение</button>`
                : 'Подключите роутер, чтобы увидеть маршруты. <button class="btn sm primary" data-action="settings">Подключить</button>';
        return;
    }

    status.innerHTML = state.ipLoading
        ? '<div class="ip-load-status" role="status"><span class="loading-spinner" aria-hidden="true"></span>Загружаем IP-маршруты с роутера…</div>'
        : state.ipError ? `<div class="ip-load-status error">${ICONS.info}<span>${esc(state.ipError)}${state.ipRoutes !== null ? '<small>Показаны ранее загруженные маршруты.</small>' : ''}</span><button class="btn sm ghost" data-action="ip-reload">Повторить</button></div>` : '';
    if (state.ipRoutes === null) {
        box.innerHTML = state.ipLoading ? '<div class="skeleton" style="height:160px"></div>' : '';
        empty.hidden = true;
        return;
    }

    const groups = ipGroups(all).map((g) => {
        const titleHit = q && g.title.toLowerCase().includes(q);
        const routes = !q || titleHit ? g.routes : g.routes.filter((r) =>
            [r.destination, r.interface, r.interface_title].some((v) => String(v).toLowerCase().includes(q)));
        return { ...g, shown: routes };
    }).filter((g) => g.shown.length);

    if (!groups.length) {
        box.innerHTML = '';
        empty.hidden = false;
        empty.textContent = q ? 'Ничего не найдено' : 'Статических маршрутов нет';
        return;
    }
    empty.hidden = true;

    renderHtml(box, groups.map((g) => {
        const key = g.comment;
        const isCollapsed = collapsed.has(ckey(key)) && !q;
        const sel = g.routes.filter((r) => state.ipSelected.has(r.index)).length;
        const ifaces = [...new Set(g.routes.map((r) => r.interface_title))];
        const off = g.routes.filter((r) => !r.enabled).length;
        return `
        <section class="ipg card${isCollapsed ? ' collapsed' : ''}${off === g.routes.length ? ' all-off' : ''}" data-comment="${esc(key)}" data-render-key="ip-group:${esc(ckey(key))}">
            <header class="ipg-head">
                <input type="checkbox" data-action="ipg-select" data-comment="${esc(key)}" ${sel && sel === g.routes.length ? 'checked' : ''} ${sel && sel < g.routes.length ? 'data-partial="1"' : ''} title="Выбрать всю группу">
                <label class="switch${off && off < g.routes.length ? ' partial' : ''}" title="${off === g.routes.length ? 'Включить всю группу' : 'Выключить всю группу'}${off && off < g.routes.length ? ` (сейчас выключено ${off} из ${g.routes.length})` : ''}">
                    <input type="checkbox" data-action="ipg-enable" data-comment="${esc(key)}" ${off < g.routes.length ? 'checked' : ''}><span></span></label>
                <button class="ipg-toggle" data-action="ipg-toggle" data-comment="${esc(key)}">
                    <svg class="chev" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>
                    <b class="${g.comment ? '' : 'muted'}">${esc(g.title)}</b>
                    <span class="badge">${g.routes.length}</span>
                    <span class="muted ipg-meta">${esc(ifaces.join(', '))}${off ? ` · выключено ${off}` : ''}</span>
                </button>
                <div class="ipg-actions">
                    <button class="icon-btn sm plain ipg-add" data-action="ipg-add" data-comment="${esc(key)}" title="Добавить IP в эту группу" aria-label="Добавить IP в группу ${esc(g.title)}">${ICONS.plus}</button>
                    <button class="icon-btn sm plain" data-action="ipg-rename" data-comment="${esc(key)}" title="${g.comment ? 'Переименовать группу' : 'Задать описание'}">${ICONS.edit}</button>
                    <button class="icon-btn sm plain" data-action="ipg-move" data-comment="${esc(key)}" title="Сменить туннель для группы">${ICONS.swap}</button>
                    <button class="icon-btn sm plain" data-action="ipg-copy" data-comment="${esc(key)}" title="Копировать адреса">${ICONS.copy}</button>
                    <button class="icon-btn sm plain" data-action="ipg-export" data-comment="${esc(key)}" title="Экспорт группы в файл">${ICONS.download}</button>
                    <button class="icon-btn sm plain danger" data-action="ipg-delete" data-comment="${esc(key)}" title="Удалить группу маршрутов">${ICONS.trash}</button>
                </div>
            </header>
            <div class="ipg-body">
                ${g.shown.map((r) => `
                <div class="ip-row${state.ipSelected.has(r.index) ? ' selected' : ''}${r.enabled ? '' : ' off'}" data-ip="${esc(r.index)}" data-render-key="ip-route:${esc(r.index)}">
                    <input type="checkbox" data-action="ip-select" data-index="${esc(r.index)}" ${state.ipSelected.has(r.index) ? 'checked' : ''}>
                    <label class="switch" title="${r.enabled ? 'Выключить' : 'Включить'}"><input type="checkbox" data-action="ip-toggle" data-index="${esc(r.index)}" ${r.enabled ? 'checked' : ''}><span></span></label>
                    <span class="mono ip-dest">${esc(r.destination)}</span>
                    <span class="ip-iface">${esc(r.interface_title || r.gateway)}</span>
                    <span class="tags">${r.auto ? '<span class="tag">авто</span>' : ''}${r.reject ? '<span class="tag warn">эксклюзивный</span>' : ''}</span>
                </div>`).join('')}
            </div>
        </section>`;
    }).join(''));
    $$('[data-action="ipg-select"]', box).forEach((c) => { c.indeterminate = c.hasAttribute('data-partial'); });
}

function ipGroupRoutes(comment) {
    return (state.ipRoutes || []).filter((r) => ckey(r.comment) === ckey(comment));
}

/** Включает или выключает маршруты пачкой: они остаются в настройках роутера, но не действуют. */
async function setIpEnabled(indices, enabled) {
    const ids = indices.filter((i) => state.ipRoutes.find((r) => r.index === i)?.enabled !== enabled);
    if (!ids.length) {
        toast(enabled ? 'Эти маршруты уже включены' : 'Эти маршруты уже выключены', 'info', { timeout: 2500 });
        return renderIpRoutes();
    }
    try {
        const res = await api('/api/router/ip-routes/toggle', 'POST', { indices: ids, enabled });
        invalidateIpRoutes({keepData: true});
        if (state.ipRoutes) state.ipRoutes.forEach((r) => { if (ids.includes(r.index)) r.enabled = enabled; });
        else await loadIpRoutes();
        toast(res.message, 'success', { timeout: 2500 });
    } catch (e) {
        toast(e.message, 'error');
    }
    renderIpRoutes();
}

async function setIpComment(indices, current = '') {
    const comment = await promptDialog({
        title: 'Описание маршрутов',
        label: `Чьи это адреса? (${indices.length} ${plural(indices.length, 'маршрут', 'маршрута', 'маршрутов')})`,
        value: current,
        ok: 'Сохранить',
        allowEmpty: true,
    });
    if (comment === null) return;
    await task('Сохранение описания…', () => api('/api/router/ip-routes/comment', 'POST', { indices, comment }));
    invalidateIpRoutes();
    await loadIpRoutes();
}

function openIpGroupCreate(comment) {
    const routes = ipGroupRoutes(comment);
    const interfaces = [...new Set(routes.map((r) => r.interface).filter(Boolean))];
    const single = interfaces.length === 1 ? interfaces[0] : '';
    openIpCreate('', null, comment, {group: true, interface: single,
        interfaceTitle: routes.find((r) => r.interface === single)?.interface_title,
        chooseInterface: interfaces.length !== 1});
}

function openIpCreate(prefill = '', siteIds = null, comment = '', preset = {}) {
    if (!state.connected) return openSettings();
    const known = [...new Set((state.ipRoutes || []).map((r) => r.comment).filter(Boolean))];
    openModal({
        title: preset.group ? `Добавить IP в «${comment || 'Без описания'}»` : siteIds ? 'IP-адреса сайтов → роутер' : 'Добавить статические маршруты',
        body: `
            <form id="ip-form">
                ${siteIds ? `<div class="notice">${ICONS.info}<div>IP-адреса выбранных сайтов (${siteIds.length}) будут добавлены как маршруты /32, описанием станет домен сайта. Учтите: IP у крупных сайтов меняются — для них надёжнее DNS-списки.</div></div>`
                    : `<label class="field"><span>Адреса и подсети</span>
                        <textarea name="dest" placeholder="91.108.56.0/22&#10;149.154.160.0/20&#10;95.161.64.0/20" spellcheck="false" autofocus>${esc(prefill)}</textarea>
                        <small>Понимает CIDR, одиночные IP и строки bat-файлов: route add 1.2.3.0 mask 255.255.255.0 0.0.0.0</small></label>
                       <div class="preview" id="ip-preview"></div>`}
                <label class="field"><span>Описание (группа)</span>
                    <input type="text" name="comment" list="ip-comments" value="${esc(comment)}" ${preset.group ? 'readonly' : ''} placeholder="${preset.group ? 'Без описания' : 'Например: Telegram — по нему маршруты группируются'}">
                    <datalist id="ip-comments">${known.map((c) => `<option value="${esc(c)}">`).join('')}</datalist>
                    <small>${preset.group ? 'Адреса будут добавлены в эту же группу.' : 'Если адрес уже есть на роутере без описания — описание будет дополнено.'}</small></label>
                <label class="field"><span>Интерфейс</span><select name="interface" required>${preset.chooseInterface ? '<option value="">Выберите подключение…</option>' : ''}${ifaceOptions(preset.interface)}${preset.interface && !state.interfaces.some((i) => i.id === preset.interface) ? `<option value="${esc(preset.interface)}" selected>${esc(preset.interfaceTitle || preset.interface)}</option>` : ''}</select>${preset.chooseInterface ? '<small>В группе нет единственного подключения — выберите, через какое направить новые IP.</small>' : ''}</label>
            </form>`,
        foot: `<button class="btn ghost" data-close>Отмена</button>
               <button class="btn primary" type="submit" form="ip-form">Добавить</button>`,
        onMount: ({ el, close }) => {
            const f = $('form', el);
            if (preset.chooseInterface) f.interface.value = '';
            if (f.dest) {
                const upd = () => {
                    const n = parseRouteText(f.dest.value).length;
                    $('#ip-preview', el).innerHTML = n ? `<span class="good">${n} ${plural(n, 'адрес', 'адреса', 'адресов')}</span>` : '';
                };
                f.dest.addEventListener('input', upd);
                upd();
            }
            f.onsubmit = async (e) => {
                e.preventDefault();
                const payload = { interface: f.interface.value, comment: f.comment.value.trim() };
                if (!payload.interface) return toast('Выберите подключение для новых IP', 'error');
                if (siteIds) payload.site_ids = siteIds;
                else payload.destinations = parseRouteText(f.dest.value);
                if (!siteIds && !payload.destinations.length) return toast('Не найдено ни одного IPv4-адреса', 'error');
                await withButton($('.modal-foot .primary', el), async () => {
                    try {
                        const data = await api('/api/router/ip-routes', 'POST', payload);
                        toast(data.message, 'success');
                        close();
                        invalidateIpRoutes();
                        if ($('#view-ip').classList.contains('active')) loadIpRoutes();
                        if (capVisible()) { await ensureIpRoutes(); renderCapResults(); }
                    } catch (err) {
                        toast(err.message, 'error');
                    }
                });
            };
        },
    });
}

/** Достаёт адреса из текста: CIDR, одиночные IP и строки «route add X mask M». */
function parseRouteText(text) {
    const out = [];
    const maskToPrefix = (m) => m.split('.').reduce((n, o) => n + (+o).toString(2).split('1').length - 1, 0);
    for (const line of text.split(/\n/)) {
        const m = line.match(/route\s+add\s+(\d+\.\d+\.\d+\.\d+)\s+mask\s+(\d+\.\d+\.\d+\.\d+)/i);
        if (m) { out.push(`${m[1]}/${maskToPrefix(m[2])}`); continue; }
        for (const token of line.split(/[\s,;]+/)) if (isIp(token)) out.push(token);
    }
    return [...new Set(out)];
}

// ============================================================ Сайты → bat

async function loadSites() {
    try {
        const data = await api('/api/sites');
        state.sites = data.sites;
        const ids = new Set(state.sites.map((s) => s.id));
        state.sitesSelected = new Set([...state.sitesSelected].filter((id) => ids.has(id)));
    } catch (e) {
        toast(`Ошибка загрузки сайтов: ${e.message}`, 'error');
    }
    renderSites();
}

function renderSites() {
    const tbody = $('#sites-table tbody');
    const empty = $('#sites-empty');
    empty.hidden = state.sites.length > 0;
    empty.textContent = 'Сайтов пока нет — добавьте первый в поле выше';
    tbody.innerHTML = state.sites.map((s) => `
        <tr class="${state.sitesSelected.has(s.id) ? 'selected' : ''}">
            <td class="col-check"><input type="checkbox" data-action="site-select" data-id="${s.id}" ${state.sitesSelected.has(s.id) ? 'checked' : ''}></td>
            <td class="cell-domain">${esc(s.domain)}</td>
            <td class="cell-url hide-sm"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" title="${esc(s.url)}">${esc(s.url.replace(/^https?:\/\//, ''))}</a></td>
            <td class="num"><span class="badge${s.ip_count ? '' : ' zero'}">${s.ip_count}</span></td>
            <td class="hide-sm muted">${relTime(s.last_ip_update || s.updated_at)}</td>
            <td class="col-actions"><div class="actions">
                <button class="icon-btn sm plain" data-action="site-to-list" data-id="${s.id}" title="Добавить домен в DNS-список роутера">${ICONS.list}</button>
                <button class="icon-btn sm plain" data-action="site-refresh" data-id="${s.id}" title="Обновить IP">${ICONS.refresh}</button>
                <button class="icon-btn sm plain" data-action="site-refresh-html" data-id="${s.id}" title="Обновить IP вместе с доменами со страницы">${ICONS.globe}</button>
                <button class="icon-btn sm plain" data-action="site-bat" data-id="${s.id}" title="Скачать bat-файл" ${s.ip_count ? '' : 'disabled'}>${ICONS.download}</button>
                <button class="icon-btn sm plain danger" data-action="site-delete" data-id="${s.id}" title="Удалить">${ICONS.trash}</button>
            </div></td>
        </tr>`).join('');

    const n = state.sitesSelected.size;
    $('#sites-bulk').hidden = !n;
    $('#sites-bulk-count').textContent = `Выбрано: ${n}`;
    $('#bat-scope').textContent = n ? `(выбрано: ${n})` : '(все сайты)';
    const all = $('#sites-check-all');
    all.checked = state.sites.length > 0 && n === state.sites.length;
    all.indeterminate = n > 0 && n < state.sites.length;
}

async function refreshSites(ids, withHtml) {
    const label = withHtml ? 'Обновление IP с доменами со страницы' : 'Обновление IP';
    await task(`${label}: ${ids.length} ${plural(ids.length, 'сайт', 'сайта', 'сайтов')}…`,
        () => api(withHtml ? '/api/sites/update-ips-with-html' : '/api/sites/update-ips', 'POST', { site_ids: ids }),
        (d) => {
            const failed = d.results.filter((r) => !r.success);
            return failed.length ? `${d.message}. Ошибки: ${failed.map((r) => r.domain).join(', ')}` : d.message;
        });
    await loadSites();
}

async function deleteSites(ids) {
    const names = state.sites.filter((s) => ids.includes(s.id)).map((s) => s.domain);
    if (!await confirmDialog(`Удалить ${ids.length === 1 ? `сайт <b>${esc(names[0])}</b>` : `${ids.length} ${plural(ids.length, 'сайт', 'сайта', 'сайтов')}`} из списка?`)) return;
    await task('Удаление…', () => api('/api/sites', 'DELETE', { site_ids: ids }));
    ids.forEach((id) => state.sitesSelected.delete(id));
    await loadSites();
}

function downloadFile(name) {
    const a = Object.assign(document.createElement('a'), { href: `/api/bat-files/${encodeURIComponent(name)}`, download: name });
    document.body.append(a);
    a.click();
    a.remove();
}

async function downloadBats() {
    const ids = [...state.sitesSelected];
    const merge = $('#merge-all').checked || !ids.length;
    const data = await task('Генерация bat-файлов…',
        () => api('/api/generate', 'POST', { site_ids: ids, merge_all: merge }));
    for (const file of data.files) {
        downloadFile(file);
        await new Promise((r) => setTimeout(r, 250));
    }
}

// ============================================================ анализ программ

const cap = {
    apps: null,             // запущенные программы
    showSystem: false,
    source: 'running',      // running - выбрать запущенную, exe - запустить файл
    picks: new Map(),       // ключ -> выбранная программа; выбор переживает обновление списка
    names: new Map(),       // ключ -> отдельное название программы
    exe: '',
    filter: '',
    session: null,          // текущий или последний анализ
    history: null,
    hist: null,             // отдельно раскрытая сохранённая история
    histName: '',
    histLoading: false,
    histError: '',
    histRequest: 0,
    archive: capResultState(),
    setup: true,            // показывать форму запуска
    selDomains: new Set(),
    selIps: new Set(),
    exact: false,           // добавлять найденные поддомены, а не основной домен
    agg24: false,           // объединять IP в подсети /24
    noDomainOnly: false,
    query: '',
    newOnly: false,
    timer: null,
    starting: false,
    program: '',            // результаты только этой программы
    agent: { status: 'off' },
    pointerDown: false,
    refreshPending: false,
};

function capResultState() {
    return { selDomains: new Set(), selIps: new Set(), exact: false, agg24: false,
             noDomainOnly: false, query: '', newOnly: false };
}

function capResultView(scope = 'live') {
    return scope === 'history' ? cap.archive : cap;
}

function capScope(el) {
    return el?.dataset.capScope || 'live';
}

const isIpv4 = (s) => /^\d{1,3}(\.\d{1,3}){3}$/.test(s) && s.split('.').every((v) => +v <= 255);
const ipInt = (ip) => ip.split('.').reduce((n, o) => n * 256 + (+o), 0);

/** Обновляем существующие узлы: кнопка и checkbox не исчезают между нажатием и кликом. */
function renderHtml(box, html) {
    const template = document.createElement('template');
    template.innerHTML = html;
    const key = (n) => n.nodeType === 1 ? (n.id || n.dataset.renderKey || n.dataset.capKey || '') : '';
    const same = (a, b) => a.nodeType === b.nodeType && a.nodeName === b.nodeName && key(a) === key(b);
    function patch(parent, next) {
        let current = parent.firstChild;
        for (const desired of [...next.childNodes]) {
            if (!current || !same(current, desired)) {
                const found = key(desired) && [...parent.childNodes].find((n) => same(n, desired));
                const node = found || desired.cloneNode(true);
                parent.insertBefore(node, current);
                current = node;
            }
            if (current.nodeType === 1) {
                for (const attr of [...current.attributes]) {
                    if (!desired.hasAttribute(attr.name) && !(current.tagName === 'DETAILS' && attr.name === 'open')) current.removeAttribute(attr.name);
                }
                for (const attr of [...desired.attributes]) {
                    if (current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
                }
                if (current.tagName === 'INPUT') {
                    current.checked = desired.checked;
                    if (document.activeElement !== current) current.value = desired.value;
                }
                if (!desired.hasAttribute('data-cap-preserve')) patch(current, desired);
            } else if (current.nodeValue !== desired.nodeValue) current.nodeValue = desired.nodeValue;
            current = current.nextSibling;
        }
        while (current) { const after = current.nextSibling; current.remove(); current = after; }
    }
    patch(box, template.content);
}

function capResetSelection(scope = 'live') {
    const view = capResultView(scope);
    view.selDomains.clear();
    view.selIps.clear();
}

function capProgramNames() {
    const target = cap.session?.target;
    return [...new Set(target?.apps?.map((a) => a.name) || (target ? [target.name] : []))];
}

/** Домен -> основной домен (api.discord.gg -> discord.gg). Та же логика, что на сервере, в упрощённом виде. */
function baseDomain(name) {
    const multi = /\.(co|com|net|org|ac|gov|ne|or|msk|spb|pp)\.[a-z]{2}$/;
    const shared = /\.(amazonaws\.com|cloudfront\.net|azureedge\.net|azurefd\.net|azurewebsites\.net|blob\.core\.windows\.net|trafficmanager\.net|cloudapp\.net|akamaized\.net|akamaiedge\.net|akamai\.net|edgekey\.net|edgesuite\.net|fastly\.net|fastlylb\.net|googleusercontent\.com|githubusercontent\.com|appspot\.com|herokuapp\.com|web\.app|firebaseapp\.com|vercel\.app|netlify\.app|pages\.dev|workers\.dev|r2\.dev|b-cdn\.net)$/;
    const labels = name.split('.');
    const m = name.match(shared);
    if (m) return labels.slice(-(m[1].split('.').length + 1)).join('.');
    return labels.slice(multi.test(name) ? -3 : -2).join('.');
}

function domainCoveredBy(name) {
    return state.groups.find((g) => g.entries.some((e) => !isIp(e) && covers(e, name)));
}

/** Через какой маршрут уже идёт IPv4: «IP-маршрут Telegram» или DNS-список с этим адресом/подсетью. */
function ipCoveredBy(ip) {
    if (!isIpv4(ip)) return null;
    const n = ipInt(ip);
    const inNet = (cidr) => {
        const [addr, bits = '32'] = cidr.split('/');
        if (!isIpv4(addr) || +bits === 0) return false;
        const size = 2 ** (32 - +bits);
        return Math.floor(n / size) === Math.floor(ipInt(addr) / size);
    };
    const route = (state.ipRoutes || []).filter((r) => inNet(r.destination))
        .sort((a, b) => +(b.destination.split('/')[1] || 32) - +(a.destination.split('/')[1] || 32))[0];
    if (route) return `IP-маршрут «${route.comment || route.destination}» · ${route.interface_title || route.interface || 'без интерфейса'}`;
    const group = state.groups.find((g) => g.entries.some((e) => isIp(e) && inNet(e)));
    return group ? `список «${group.title}»` : null;
}

function capElapsed(sec) {
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + `:${String(s).padStart(2, '0')}`;
}

function capVisible() {
    return $('#view-capture').classList.contains('active');
}

async function loadCapture() {
    try {
        const [st, hist] = await Promise.all([api('/api/capture/state'), api('/api/capture/history')]);
        cap.session = st.session;
        cap.agent = st.agent;
        cap.history = hist.programs;
        if (cap.session?.running) cap.setup = false;
    } catch (e) {
        toast(e.message, 'error');
    }
    if (state.connected) ensureIpRoutes().then(() => capVisible() && renderCapResults());
    renderCapture();
    if (cap.setup && !cap.apps) loadCapApps();
    scheduleCapPoll();
}

async function loadCapApps() {
    try {
        cap.apps = (await api(`/api/capture/apps${cap.showSystem ? '?system=1' : ''}`)).apps;
        cap.apps.forEach((a) => { if (cap.picks.has(a.key)) cap.picks.set(a.key, a); });
    } catch (e) {
        cap.apps = [];
        toast(e.message, 'error');
    }
    renderCapApps();
}

function scheduleCapPoll() {
    clearTimeout(cap.timer);
    if (cap.session?.running && capVisible()) cap.timer = setTimeout(pollCapture, 1000);
}

async function pollCapture() {
    const wasRunning = cap.session?.running;
    const wasPending = ['pending', 'starting'].includes(cap.agent?.status);
    try {
        const st = await api('/api/capture/state');
        cap.session = st.session;
        cap.agent = st.agent;
    } catch { /* сервер занят - попробуем снова */ }
    if (wasPending && !['pending', 'starting'].includes(cap.agent?.status)) {
        try { await window.pywebview?.api?.restore_window?.(); } catch { /* WinAPI также восстанавливает окно */ }
    }
    if (wasRunning && !cap.session?.running) {
        try { cap.history = (await api('/api/capture/history')).programs; } catch { /* не критично */ }
    }
    if (capVisible()) {
        if (!cap.pointerDown) {
            renderCapLive();
            renderCapResults();
        } else cap.refreshPending = true;
        if (wasRunning && !cap.session?.running) renderCapHistory();
    }
    scheduleCapPoll();
}

function renderCapture() {
    $('#cap-new-btn').hidden = cap.setup;
    $('#cap-new-btn').disabled = !!cap.session?.running;
    renderCapSetup();
    renderCapLive();
    renderCapHistory();
    renderCapResults();
}

function renderCapSetup() {
    const box = $('#cap-setup');
    if (!cap.setup) { box.innerHTML = ''; return; }
    const canPick = !!window.pywebview?.api?.pick_exe;
    const full = storage('s4r-cap-full') !== '0';
    const children = storage('s4r-cap-children') !== '0';
    box.innerHTML = `
        <div class="card cap-setup">
            <div class="cap-section-heading"><div><span class="cap-eyebrow">Новый анализ</span><h2>Выберите программы</h2><p class="muted">Отметьте одну или несколько программ, затем пользуйтесь ими как обычно.</p></div>
                ${cap.session && !cap.session.running ? '<button class="btn sm ghost" data-action="cap-setup-close">Вернуться к результатам</button>' : ''}</div>
            <div class="seg">
                <button class="seg-btn${cap.source === 'running' ? ' active' : ''}" data-action="cap-source" data-source="running">${ICONS.list} Запущенные программы</button>
                <button class="seg-btn${cap.source === 'exe' ? ' active' : ''}" data-action="cap-source" data-source="exe">${ICONS.bolt} Запустить файл</button>
            </div>
            <div ${cap.source === 'running' ? '' : 'hidden'}>
                <div class="cap-apps-tools">
                    <label class="search sm">
                        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
                        <input type="search" id="cap-filter" placeholder="Найти программу…" value="${esc(cap.filter)}" autocomplete="off">
                    </label>
                    <label class="switch-label"><span class="switch"><input type="checkbox" id="cap-system" ${cap.showSystem ? 'checked' : ''}><span></span></span>Системные</label>
                    <button class="icon-btn sm" data-action="cap-apps-reload" title="Обновить список">${ICONS.refresh}</button>
                </div>
                <div class="cap-apps" id="cap-apps"></div>
                <div id="cap-picked"></div>
            </div>
            <div ${cap.source === 'exe' ? '' : 'hidden'}>
                <label class="field"><span>Файл программы</span>
                    <div class="cap-exe">
                        <input type="text" id="cap-exe" value="${esc(cap.exe)}" placeholder="C:\\Users\\…\\AppData\\Local\\Discord\\Update.exe" spellcheck="false">
                        ${canPick ? `<button class="btn ghost" data-action="cap-browse">Обзор…</button>` : ''}
                    </div>
                    <small>RouteDeck запустит программу и будет следить за её процессами. Несколько уже открытых программ можно выбрать во вкладке «Запущенные программы».</small>
                </label>
                <label class="field"><span>Название программы</span><input type="text" id="cap-exe-name" value="${esc(cap.names.get('exe') || '')}" placeholder="Автоматически из файла программы"></label>
            </div>
            <div class="cap-options">
                <label class="switch-label"><span class="switch"><input type="checkbox" id="cap-full" ${full ? 'checked' : ''}><span></span></span>
                    <span><b>Полный режим</b> — UDP (звонки, игры, QUIC) и домены программ. ${['ready', 'capturing'].includes(cap.agent?.status) ? 'Права уже подтверждены: повторный запрос не нужен.' : 'Windows запросит права один раз, пока RouteDeck открыт.'}</span></label>
                <label class="switch-label"><span class="switch"><input type="checkbox" id="cap-children" ${children ? 'checked' : ''}><span></span></span>
                    <span>Учитывать дочерние процессы — например, git и расширения у VS Code</span></label>
            </div>
            <div class="cap-start">
                <span class="muted" id="cap-pick-hint"></span>
                <button class="btn primary" data-action="cap-start">${ICONS.bolt} Начать анализ</button>
            </div>
        </div>`;
    renderCapApps();
}

function renderCapApps() {
    const box = $('#cap-apps');
    if (!box) return;
    if (!cap.apps) {
        box.innerHTML = '<div class="skeleton" style="height:180px"></div>';
        return;
    }
    const q = cap.filter.trim().toLowerCase();
    const apps = cap.apps.filter((a) => !q || [a.title, a.folder, ...a.exe_names].some((v) => v.toLowerCase().includes(q)));
    box.innerHTML = apps.length ? apps.map((a) => `
        <button class="cap-app${cap.picks.has(a.key) ? ' active' : ''}" data-action="cap-pick" data-key="${esc(a.key)}" aria-pressed="${cap.picks.has(a.key)}" title="${esc(a.exe_names.join(', '))}">
            <span class="cap-check">${cap.picks.has(a.key) ? ICONS.check : ''}</span>
            ${groupIcon(a.title)}
            <div class="cap-app-main"><b>${esc(a.title)}</b><small>${esc(a.folder)}</small></div>
            <span class="cap-app-meta">${a.processes} ${plural(a.processes, 'процесс', 'процесса', 'процессов')}${a.connections ? ` · <span class="good">${a.connections} ${plural(a.connections, 'соединение', 'соединения', 'соединений')}</span>` : ''}</span>
        </button>`).join('') : `<div class="empty">${q ? 'Ничего не найдено' : 'Нет запущенных программ'}</div>`;
    renderCapPicked();
}

function renderCapPicked() {
    const box = $('#cap-picked');
    if (box) box.innerHTML = cap.picks.size ? `
        <p class="cap-note muted">Название каждой программы используется отдельно в истории, DNS-списках и описании IP-маршрутов.</p>
        <div class="cap-picked">${[...cap.picks].map(([key, a]) => `
            <div class="cap-picked-item">
                ${groupIcon(a.title)}
                <label class="field cap-picked-name"><span title="${esc(a.title)}">${esc(a.title)}</span>
                    <input type="text" data-cap-name="${esc(key)}" value="${esc(cap.names.get(key) || a.title)}" placeholder="${esc(a.title)}" maxlength="100" aria-label="Название ${esc(a.title)} в истории и списках">
                </label>
                <button type="button" class="icon-btn sm plain danger cap-picked-remove" data-action="cap-remove" data-key="${esc(key)}" title="Убрать из анализа" aria-label="Убрать ${esc(a.title)} из анализа">${ICONS.x}</button>
            </div>`).join('')}</div>` : '';
    const hint = $('#cap-pick-hint');
    if (hint) hint.textContent = cap.source === 'running' ? (cap.picks.size ? `Выбрано программ: ${cap.picks.size}` : 'Выберите одну или несколько программ') : '';
}

const AGENT_TEXT = {
    off: 'Обычный режим: только TCP, домены — из кэша DNS Windows. Программы на Chromium (Discord, VS Code, браузеры) резолвят домены сами — их домены и UDP видны только в полном режиме.',
    pending: 'Подтвердите запрос прав администратора в окне Windows…',
    starting: 'Запуск сборщика…',
    ready: 'Права администратора подтверждены. Сборщик готов — повторный запрос в этом запуске RouteDeck не нужен.',
    capturing: 'Полный режим: TCP, UDP и домены программ. Права подтверждены до закрытия RouteDeck.',
};

function renderCapLive() {
    const box = $('#cap-live');
    const s = cap.session;
    if (!s || (!s.running && cap.setup)) { box.innerHTML = ''; return; }
    const a = s.agent || cap.agent;
    const canElevate = s.running && (!s.full || ['off', 'denied', 'error'].includes(a.status));
    const agentCls = ['ready', 'capturing'].includes(a.status) ? 'ok' : ['denied', 'error'].includes(a.status) ? 'warn' : '';
    const procs = new Map();
    s.processes.forEach((p) => {
        const item = procs.get(p.name) || { name: p.name, n: 0, alive: 0, hits: 0 };
        item.n++; item.alive += p.alive ? 1 : 0; item.hits += p.hits;
        procs.set(p.name, item);
    });
    const r = s.results;
    const nDomains = capDomainGroups(r).length;
    renderHtml(box, `
        <div class="card cap-live${s.running ? ' running' : ''}">
            <div class="cap-live-head">
                <span class="cap-pulse"></span>
                <div class="cap-live-title">
                    <span class="cap-eyebrow">${s.running ? 'Текущий анализ' : 'Последний анализ'}</span><b>${esc(s.target.name)}</b>
                    <small>${s.running ? 'Идёт анализ' : 'Анализ завершён · результаты сохранены'} · ${capElapsed(s.elapsed)}</small>
                </div>
                <div class="cap-counters">
                    <span><b>${nDomains}</b> ${plural(nDomains, 'домен', 'домена', 'доменов')}</span>
                    <span><b>${r.ips.length}</b> IP</span>
                </div>
                ${s.running ? `<button class="btn danger" data-action="cap-stop">Остановить</button>` : ''}
            </div>
            ${s.running ? `<div class="cap-agent ${agentCls}">
                ${ICONS.info}<span>${esc(a.error || AGENT_TEXT[a.status] || '')}${a.status === 'capturing' && a.stats?.events ? ` <span class="muted">· событий: ${a.stats.events.toLocaleString('ru-RU')}</span>` : ''}</span>
                ${canElevate ? `<button class="btn sm primary" data-action="cap-elevate">${!s.full || a.status === 'off' ? 'Включить полный режим' : 'Повторить'}</button>` : ''}
            </div>` : ''}
            <details class="cap-details" data-cap-key="process-details"><summary>Процессы программы (${s.processes.length})</summary>
            <div class="cap-procs">
                ${procs.size ? [...procs.values()].map((p) => `<span class="chip${p.alive ? '' : ' redundant'}" title="${p.alive ? 'работает' : 'завершён'} · активность: ${p.hits}"><span>${esc(p.name)}${p.n > 1 ? ` ×${p.n}` : ''}</span></span>`).join('')
                    : `<span class="muted">${s.running ? 'Ждём процессы программы — запустите её, если она ещё не открыта.' : 'Процессы программы не встретились.'}</span>`}
            </div>
            </details>
            ${s.running ? `<p class="muted cap-tip">Пользуйтесь программой как обычно: откройте нужные разделы, войдите в аккаунт, сделайте звонок — адреса появляются по мере работы. Когда закончите, нажмите «Остановить».</p>` : ''}
            ${!s.running ? '<p class="muted cap-tip">Выберите программу ниже и добавьте нужные адреса на роутер. Прошлые анализы доступны в истории.</p>' : ''}
        </div>`);
}

/** Текущий анализ и история используют разные данные, фильтры и выбор. */
function capData(scope = 'live') {
    if (scope === 'history') return cap.hist ? { name: cap.hist.name, ips: cap.hist.ips, domains: cap.hist.domains, history: true } : null;
    if (cap.session) {
        const names = capProgramNames();
        if (!names.includes(cap.program)) cap.program = names[0] || cap.session.target.name;
        const belongs = (row) => row.apps?.length ? row.apps.includes(cap.program) : names.length === 1;
        return { name: cap.program, ips: cap.session.results.ips.filter(belongs), domains: cap.session.results.domains.filter(belongs), history: false };
    }
    return null;
}

function capDomainGroups(data) {
    const map = new Map();
    for (const d of data.domains) {
        const base = baseDomain(d.domain);
        const g = map.get(base) || { base, names: [], ips: new Set(), queried: false, count: 0 };
        g.names.push(d.domain);
        d.ips.forEach((ip) => g.ips.add(ip));
        g.queried = g.queried || d.queried;
        g.count += d.count;
        map.set(base, g);
    }
    return [...map.values()].sort((a, b) => a.base.localeCompare(b.base));
}

function capShownResults(data, scope = 'live') {
    const view = capResultView(scope);
    const query = view.query.trim().toLowerCase();
    const match = (values) => !query || values.some((v) => String(v || '').toLowerCase().includes(query));
    const allGroups = capDomainGroups(data);
    const coverage = new Map();
    const coveredIp = (ip) => {
        if (!coverage.has(ip)) coverage.set(ip, ipCoveredBy(ip));
        return coverage.get(ip);
    };
    const groups = allGroups.filter((g) => match([g.base, ...g.names, ...g.ips])
        && (!view.newOnly || !state.connected || !domainCoveredBy(g.base)));
    const ips = data.ips
        .filter((i) => (!view.noDomainOnly || !i.domains.length)
            && (!view.newOnly || !state.connected || !coveredIp(i.ip))
            && match([i.ip, ...i.domains, ...i.procs, coveredIp(i.ip)]))
        .sort((a, b) => a.ip.localeCompare(b.ip, undefined, { numeric: true }));
    return { groups, ips, allGroups, coveredIp, coveredIps: data.ips.filter((i) => coveredIp(i.ip)).length };
}

function renderCapResults(scope) {
    if (scope === undefined) { renderCapResults('live'); renderCapResults('history'); return; }
    const box = $(scope === 'history' ? '#cap-history-results' : '#cap-results');
    if (!box) return;
    const data = capData(scope);
    const view = capResultView(scope);
    if (!data || (scope === 'live' && cap.setup && !cap.session?.running)) { box.innerHTML = ''; return; }
    if (cap.pointerDown) { cap.refreshPending = true; return; }
    const prefix = scope === 'history' ? 'cap-hist' : 'cap';
    const attr = `data-cap-scope="${scope}"`;
    const scroll = [`#${prefix}-dom-list`, `#${prefix}-ip-list`].map((s) => $(s)?.scrollTop || 0);
    const { groups, ips, allGroups, coveredIp, coveredIps } = capShownResults(data, scope);
    const alive = new Set(allGroups.map((g) => g.base));
    view.selDomains = new Set([...view.selDomains].filter((d) => alive.has(d)));
    const ipSet = new Set(data.ips.map((i) => i.ip));
    view.selIps = new Set([...view.selIps].filter((i) => ipSet.has(i)));

    const head = data.history ? '' : `<div class="cap-section-heading cap-res-head"><div><h2>Результаты ${cap.session?.running ? 'текущего' : 'последнего'} анализа</h2><p class="muted">Выберите программу и отметьте адреса для добавления на роутер.</p></div></div>
        <div class="cap-programs" role="group" aria-label="Результаты по программе">${capProgramNames().map((name) => {
            const target = cap.session.results;
            const names = capProgramNames();
            const belongs = (row) => row.apps?.length ? row.apps.includes(name) : names.length === 1;
            const nDom = capDomainGroups({ domains: target.domains.filter(belongs) }).length;
            const nIp = target.ips.filter(belongs).length;
            return `<button class="cap-program${cap.program === name ? ' active' : ''}" data-action="cap-program" data-name="${esc(name)}" data-cap-key="program:${esc(name)}" aria-pressed="${cap.program === name}">${groupIcon(name)}<span><b>${esc(name)}</b><small>${nDom} ${plural(nDom, 'домен', 'домена', 'доменов')} · ${nIp} IP</small></span></button>`;
        }).join('')}</div>`;

    const domRows = groups.map((g) => {
        const covered = domainCoveredBy(g.base);
        const subs = g.names.filter((n) => n !== g.base);
        return `
        <label class="cap-row${view.selDomains.has(g.base) ? ' selected' : ''}" data-cap-key="domain:${esc(g.base)}">
            <input type="checkbox" data-cap-dom="${esc(g.base)}" ${attr} ${view.selDomains.has(g.base) ? 'checked' : ''}>
            <div class="cap-main">
                <div class="cap-line"><b class="mono">${esc(g.base)}</b>
                    ${g.queried ? '<span class="tag accent" title="Программа сама запрашивала этот домен">запрос программы</span>' : '<span class="tag" title="Домен найден по IP-адресу, к которому подключалась программа. Такой адрес может принадлежать и другим сайтам на том же CDN">по IP</span>'}
                    ${covered ? `<span class="tag ok" title="Уже маршрутизируется">в списке «${esc(covered.title)}»</span>` : ''}</div>
                ${subs.length ? `<div class="cap-sub mono" title="${esc(subs.join('\n'))}">${esc(subs.slice(0, 6).join(', '))}${subs.length > 6 ? ` и ещё ${subs.length - 6}` : ''}</div>` : ''}
            </div>
            <span class="cap-meta" title="${esc([...g.ips].map((ip) => `${ip}${coveredIp(ip) ? ` — ${coveredIp(ip)}` : ''}`).join('\n'))}">${g.ips.size} IP${[...g.ips].some((ip) => coveredIp(ip)) ? ' · есть на роутере' : ''}</span>
        </label>`;
    }).join('');

    const ipRows = ips.map((i) => {
        const v4 = isIpv4(i.ip);
        const covered = coveredIp(i.ip);
        const ports = i.ports.slice(0, 4).join(', ') + (i.ports.length > 4 ? '…' : '');
        return `
        <label class="cap-row${view.selIps.has(i.ip) ? ' selected' : ''}${v4 ? '' : ' v6'}" data-cap-key="ip:${esc(i.ip)}" ${v4 ? '' : 'title="IPv6: роутер принимает только IPv4-маршруты — для таких адресов используйте DNS-список"'}>
            <input type="checkbox" data-cap-ip="${esc(i.ip)}" ${attr} ${view.selIps.has(i.ip) ? 'checked' : ''} ${v4 ? '' : 'disabled'}>
            <div class="cap-main">
                <div class="cap-line"><b class="mono">${esc(i.ip)}</b>
                    <span class="tag${i.protos.includes('udp') ? ' warn' : ''}">${i.protos.map((p) => p.toUpperCase()).join('+')}${ports ? ` :${ports}` : ''}</span>
                    ${covered ? `<span class="tag ok">${esc(covered)}</span>` : ''}</div>
                <div class="cap-sub">${i.domains.length ? `<span class="mono">${esc(i.domains.slice(0, 3).join(', '))}${i.domains.length > 3 ? '…' : ''}</span>` : '<span class="warn-text">без домена</span>'}
                    <span class="muted"> · ${esc(i.procs.join(', '))}</span></div>
            </div>
            <span class="cap-meta" title="Активность: соединения и пакеты">${i.count.toLocaleString('ru-RU')}</span>
        </label>`;
    }).join('');

    const nd = groups.filter((g) => view.selDomains.has(g.base)).length;
    const ni = ips.filter((i) => isIpv4(i.ip) && view.selIps.has(i.ip)).length;
    const empty = (text) => `<div class="empty">${text}</div>`;
    const waiting = cap.session?.running && !data.history;
    const filtered = view.query.trim() || view.newOnly;
    const hiddenSelection = (total, shown) => total > shown ? ` · ещё ${total - shown} вне фильтра` : '';
    renderHtml(box, `${head}
        <div class="cap-result-toolbar">
            <label class="search sm"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg><input type="search" id="${prefix}-result-search" data-cap-query ${attr} value="${esc(view.query)}" placeholder="Найти домен, IP или процесс…" aria-label="Поиск в ${data.history ? 'истории' : 'результатах анализа'}"></label>
            <button class="icon-btn sm" data-action="cap-search-clear" ${attr} ${view.query ? '' : 'hidden'} title="Очистить поиск" aria-label="Очистить поиск">${ICONS.x}</button>
            <label class="switch-label" title="Скрыть домены и IP, которые уже добавлены на подключённый роутер"><span class="switch"><input type="checkbox" id="${prefix}-newonly" data-cap-option="newOnly" ${attr} ${view.newOnly ? 'checked' : ''} ${state.connected ? '' : 'disabled'}><span></span></span>Только новые</label>
            ${state.connected && state.ipRoutes !== null ? `<span class="tag ok">Уже на роутере: ${coveredIps} IP</span>` : ''}
        </div>
        <p class="cap-summary muted">${data.history ? 'Сохранённые адреса' : esc(data.name)} · ${allGroups.length} ${plural(allGroups.length, 'домен', 'домена', 'доменов')} · ${data.ips.length} IP${state.connected ? (state.ipRoutes === null ? ' · сведения об IP-маршрутах пока не загружены' : '') : ' · подключите роутер для проверки добавленных адресов'}</p>
        <div class="cap-results">
            <section class="card cap-card" data-cap-key="domain-card">
                <header class="cap-card-head">
                    <span class="cap-card-icon">${ICONS.globe}</span><div><h2>Домены <span class="badge">${groups.length}${groups.length !== allGroups.length ? ` / ${allGroups.length}` : ''}</span></h2><small>Для DNS-списка · роутер сам обновляет IP</small></div>
                </header>
                <div class="cap-selection-bar"><button class="link-btn" data-action="cap-dom-all" ${attr} ${groups.length ? '' : 'disabled'}>Выбрать все</button><button class="link-btn" data-action="cap-dom-none" ${attr} ${view.selDomains.size ? '' : 'disabled'}>Снять</button><span>${nd ? `Выбрано: ${nd}` : 'Ничего не выбрано'}${hiddenSelection(view.selDomains.size, nd)}</span></div>
                <div class="cap-list" id="${prefix}-dom-list">${domRows || empty(filtered ? 'По этому фильтру домены не найдены' : waiting ? 'Пользуйтесь программой — домены появятся здесь' : 'Домены не найдены')}</div>
                <footer class="cap-card-foot"><details class="cap-add-options" data-cap-key="domain-options"><summary>Параметры добавления</summary><label class="switch-label"><span class="switch"><input type="checkbox" id="${prefix}-exact" data-cap-option="exact" ${attr} ${view.exact ? 'checked' : ''}><span></span></span>Добавлять отдельные поддомены</label><small>Обычно достаточно основного домена: он включает поддомены.</small></details><button class="btn primary" data-action="cap-dom-add" ${attr} ${nd ? '' : 'disabled'}>${ICONS.plus} В DNS-список${nd ? ` (${nd})` : ''}</button></footer>
            </section>
            <section class="card cap-card" data-cap-key="ip-card">
                <header class="cap-card-head">
                    <span class="cap-card-icon ip">${ICONS.router}</span><div><h2>IP-адреса <span class="badge">${ips.length}${ips.length !== data.ips.length ? ` / ${data.ips.length}` : ''}</span></h2><small>Для IP-маршрутов · полезно для адресов без домена</small></div>
                </header>
                <div class="cap-selection-bar"><button class="link-btn" data-action="cap-ip-all" ${attr} ${ips.some((i) => isIpv4(i.ip)) ? '' : 'disabled'}>Выбрать все</button><button class="link-btn" data-action="cap-ip-none" ${attr} ${view.selIps.size ? '' : 'disabled'}>Снять</button><span>${ni ? `Выбрано: ${ni}` : 'Ничего не выбрано'}${hiddenSelection(view.selIps.size, ni)}</span><label class="switch-label"><span class="switch"><input type="checkbox" id="${prefix}-nodomain" data-cap-option="noDomainOnly" ${attr} ${view.noDomainOnly ? 'checked' : ''}><span></span></span>Без домена</label></div>
                <div class="cap-list" id="${prefix}-ip-list">${ipRows || empty(filtered ? 'По этому фильтру IP не найдены' : waiting ? 'Пользуйтесь программой — IP появятся здесь' : view.noDomainOnly ? 'Адресов без домена не найдено' : 'Адреса не найдены')}</div>
                <footer class="cap-card-foot"><details class="cap-add-options" data-cap-key="ip-options"><summary>Параметры добавления</summary><label class="switch-label"><span class="switch"><input type="checkbox" id="${prefix}-agg" data-cap-option="agg24" ${attr} ${view.agg24 ? 'checked' : ''}><span></span></span>Объединять IP в подсети /24</label><small>Включайте, если хотите направлять и соседние адреса серверов.</small></details><button class="btn primary" data-action="cap-ip-add" ${attr} ${ni ? '' : 'disabled'}>${ICONS.plus} В IP-маршруты${ni ? ` (${ni})` : ''}</button></footer>
            </section>
        </div>${waiting ? '<p class="cap-update-note muted">Адреса обновляются автоматически. Выбранные строки сохраняются; новые строки можно отметить отдельно.</p>' : ''}`);
    [`#${prefix}-dom-list`, `#${prefix}-ip-list`].forEach((s, i) => { const el = $(s); if (el) el.scrollTop = scroll[i]; });
}

function renderCapHistory() {
    const box = $('#cap-history');
    const list = cap.history || [];
    if (!list.length) { box.innerHTML = ''; return; }
    renderHtml(box, `
        <section class="cap-history-section" aria-label="Сохранённая история">
            <header class="cap-section-heading"><div><span class="cap-eyebrow">Сохранённые данные</span><h2>История по программам <span class="badge">${list.length}</span></h2><p class="muted">Откройте программу: все её сеансы появятся ниже, под этой строкой.</p></div></header>
            <div class="card cap-history" data-cap-key="history">
            ${list.map((p) => {
                const open = cap.histName === p.name;
                return `<div class="cap-history-entry${open ? ' open' : ''}" data-cap-key="history-entry:${esc(p.name)}">
                    <button class="cap-hist${open ? ' active' : ''}" data-action="cap-hist-open" data-name="${esc(p.name)}" data-cap-key="history:${esc(p.name)}" aria-expanded="${open}">
                        ${groupIcon(p.name)}<div class="cap-app-main"><b>${esc(p.name)}</b><small>${p.sessions} ${plural(p.sessions, 'сеанс', 'сеанса', 'сеансов')} · ${p.domains} ${plural(p.domains, 'домен', 'домена', 'доменов')} · ${p.ips} IP</small></div>
                        <span class="cap-app-meta">${open && cap.histLoading ? 'Загрузка…' : relTime(p.last)}</span><span class="cap-history-chevron" aria-hidden="true">${ICONS.chevron}</span>
                    </button>
                    ${open ? `<div class="cap-history-detail" id="cap-history-detail" role="region" aria-label="История ${esc(p.name)}" data-cap-key="history-detail:${esc(p.name)}">
                        <div class="cap-archive-head"><div><span class="cap-eyebrow">История программы</span><h3>${esc(p.name)}</h3><p class="muted">${cap.hist ? `${cap.hist.sessions.length} ${plural(cap.hist.sessions.length, 'сеанс', 'сеанса', 'сеансов')} · результаты объединены` : 'Загружаем сохранённые адреса'}</p></div>
                            <div class="cap-archive-actions"><button class="btn sm ghost" data-action="cap-hist-refresh" ${cap.histLoading ? 'disabled' : ''}>${ICONS.refresh} Обновить</button><button class="btn sm ghost cap-history-delete" data-action="cap-hist-delete" data-name="${esc(p.name)}">${ICONS.trash} Удалить историю</button><button class="icon-btn" data-action="cap-hist-close" title="Свернуть историю" aria-label="Свернуть историю ${esc(p.name)}">${ICONS.x}</button></div>
                        </div>
                        ${cap.histError ? `<div class="notice cap-history-error">${ICONS.info}<span>${esc(cap.histError)}</span><button class="btn sm ghost" data-action="cap-hist-refresh">Повторить</button></div>` : ''}
                        ${cap.histLoading && !cap.hist ? '<div class="skeleton" style="height:180px" aria-label="Загрузка истории"></div>' : ''}
                        <div id="cap-history-results" data-cap-preserve></div>
                    </div>` : ''}
                </div>`;
            }).join('')}</div>
        </section>`);
    renderCapResults('history');
}

async function startCapture(btn) {
    const full = $('#cap-full').checked, children = $('#cap-children').checked;
    storage('s4r-cap-full', full ? '1' : '0');
    storage('s4r-cap-children', children ? '1' : '0');
    let payload;
    if (cap.source === 'running') {
        if (!cap.picks.size) return toast('Выберите одну или несколько программ', 'error');
        const apps = [...cap.picks].map(([key, app]) => ({ name: (cap.names.get(key) || app.title).trim(), folder: app.folder, pids: app.pids }));
        if (new Set(apps.map((a) => a.name.toLowerCase())).size !== apps.length) return toast('Названия программ должны различаться — у каждой своя история', 'error');
        payload = { apps };
    } else {
        cap.exe = ($('#cap-exe').value || '').trim().replace(/^"|"$/g, '');
        if (!cap.exe) return toast('Укажите файл программы', 'error');
        payload = { name: (cap.names.get('exe') || '').trim(), exe: cap.exe };
    }
    await withButton(btn, async () => {
        try {
            const data = await api('/api/capture/start', 'POST', { ...payload, full, children });
            cap.session = data.session;
            cap.agent = data.session.agent;
            cap.program = data.session.target.apps?.[0]?.name || data.session.target.name;
            cap.setup = false;
            cap.query = '';
            cap.selDomains.clear();
            cap.selIps.clear();
            renderCapture();
            scheduleCapPoll();
            if (full && ['pending', 'starting'].includes(cap.agent.status)) toast('Анализ начат. Подтвердите запрос прав администратора в окне Windows', 'info');
            else toast('Анализ начат', 'success', { timeout: 2500 });
        } catch (e) {
            toast(e.message, 'error');
        }
    });
}

async function openCapHistory(name, { force = false } = {}) {
    if (cap.histName === name && !force) { closeCapHistory(); return; }
    const changed = cap.histName !== name;
    if (changed) { cap.hist = null; cap.archive = capResultState(); }
    const revision = ++cap.histRequest;
    cap.histName = name;
    cap.histLoading = true;
    cap.histError = '';
    renderCapHistory();
    try {
        const data = await api(`/api/capture/program?name=${encodeURIComponent(name)}`);
        if (revision !== cap.histRequest || cap.histName !== name) return;
        cap.hist = data;
    } catch (e) {
        if (revision !== cap.histRequest) return;
        cap.histError = e.message;
    } finally {
        if (revision === cap.histRequest) { cap.histLoading = false; renderCapHistory(); }
    }
}

function closeCapHistory() {
    cap.histRequest++;
    cap.histName = '';
    cap.hist = null;
    cap.histLoading = false;
    cap.histError = '';
    renderCapHistory();
}

function capAddDomains(scope = 'live') {
    const data = capData(scope);
    if (!data) return;
    const view = capResultView(scope);
    const groups = capShownResults(data, scope).groups.filter((g) => view.selDomains.has(g.base));
    const entries = view.exact ? [...new Set(groups.flatMap((g) => g.names))] : groups.map((g) => g.base);
    if (entries.length) openPickGroup(entries, `${data.name}: домены → DNS-список`, data.name);
}

function capAddIps(scope = 'live') {
    const data = capData(scope);
    if (!data) return;
    const view = capResultView(scope);
    let list = capShownResults(data, scope).ips.filter((i) => view.selIps.has(i.ip) && isIpv4(i.ip)).map((i) => i.ip);
    if (view.agg24) list = [...new Set(list.map((ip) => ip.split('.').slice(0, 3).join('.') + '.0/24'))];
    list.sort((a, b) => ipInt(a.split('/')[0]) - ipInt(b.split('/')[0]));
    if (list.length) openIpCreate(list.join('\n'), null, data.name);
}

// ============================================================ буфер обмена и контекстное меню
// В окне программы (WebView2) стандартное меню отключено - рисуем своё.

async function readClipboard() {
    try {
        if (window.pywebview?.api?.clipboard_read) return await window.pywebview.api.clipboard_read();
    } catch { /* нет API окна - пробуем браузерный */ }
    try {
        return await navigator.clipboard.readText();
    } catch {
        toast('Нет доступа к буферу обмена — нажмите Ctrl+V', 'error');
        return null;
    }
}

async function writeClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        const ta = Object.assign(document.createElement('textarea'), { value: text });
        document.body.append(ta);
        ta.select();
        const ok = document.execCommand('copy');
        ta.remove();
        return ok;
    }
}

const isTextField = (el) => el && (el.tagName === 'TEXTAREA'
    || (el.tagName === 'INPUT' && /^(text|search|password|url|email|number|)$/i.test(el.getAttribute('type') || '')));

/** Вставка текста в поле с сохранением истории отмены (Ctrl+Z). */
function insertText(field, text, range) {
    if (field.tagName === 'INPUT') text = text.replace(/\s*[\r\n]+\s*/g, ' ').trim();
    field.focus();
    if (range) field.setSelectionRange(range[0], range[1]);
    if (!document.execCommand('insertText', false, text)) {
        const [s, e] = range || [field.selectionStart, field.selectionEnd];
        field.setRangeText(text, s, e, 'end');
        field.dispatchEvent(new Event('input', { bubbles: true }));
    }
}

// Многострочный текст в однострочное поле: строки превращаются в пробелы,
// чтобы список доменов из буфера не склеивался в одно слово.
document.addEventListener('paste', (e) => {
    const field = e.target;
    if (field.tagName !== 'INPUT' || !isTextField(field)) return;
    const text = e.clipboardData?.getData('text') || '';
    if (!/[\r\n]/.test(text)) return;
    e.preventDefault();
    insertText(field, text);
});

let ctxMenu = null;

function closeMenu() {
    ctxMenu?.remove();
    ctxMenu = null;
}

function showMenu(x, y, items) {
    closeMenu();
    const menu = document.createElement('div');
    menu.className = 'ctx';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = items.map((it, i) => it === '-'
        ? '<div class="ctx-sep"></div>'
        : `<button class="ctx-item${it.danger ? ' danger' : ''}" data-i="${i}" ${it.disabled ? 'disabled' : ''} role="menuitem">
               <span>${esc(it.label)}</span>${it.hint ? `<kbd>${esc(it.hint)}</kbd>` : ''}</button>`).join('');
    document.body.append(menu);
    const { innerWidth: w, innerHeight: h } = window;
    const r = menu.getBoundingClientRect();
    menu.style.left = `${Math.min(x, w - r.width - 6)}px`;
    menu.style.top = `${Math.min(y, h - r.height - 6)}px`;
    menu.addEventListener('mousedown', (e) => e.preventDefault()); // не терять выделение в поле
    menu.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-i]');
        if (!btn) return;
        closeMenu();
        Promise.resolve(items[+btn.dataset.i].run()).catch((err) => toast(err.message, 'error'));
    });
    ctxMenu = menu;
}

document.addEventListener('mousedown', (e) => { if (ctxMenu && !ctxMenu.contains(e.target)) closeMenu(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); }, true);
window.addEventListener('blur', closeMenu);
window.addEventListener('resize', closeMenu);
document.addEventListener('scroll', closeMenu, true);

function fieldMenu(field) {
    const range = [field.selectionStart ?? 0, field.selectionEnd ?? 0];
    const selected = field.value.slice(range[0], range[1]);
    const readonly = field.readOnly || field.disabled;
    const secret = field.type === 'password';
    return [
        { label: 'Вырезать', hint: 'Ctrl+X', disabled: !selected || readonly || secret,
          run: async () => { await writeClipboard(selected); insertText(field, '', range); } },
        { label: 'Копировать', hint: 'Ctrl+C', disabled: !selected || secret, run: () => writeClipboard(selected) },
        { label: 'Вставить', hint: 'Ctrl+V', disabled: readonly,
          run: async () => { const t = await readClipboard(); if (t) insertText(field, t, range); } },
        '-',
        { label: 'Выделить всё', hint: 'Ctrl+A', disabled: !field.value,
          run: () => { field.focus(); field.select(); } },
        ...(field.value && !readonly ? [{ label: 'Очистить', run: () => { field.focus(); field.select(); insertText(field, ''); } }] : []),
    ];
}

function chipMenu(chip) {
    const btn = $('[data-action="entry-remove"]', chip);
    const name = btn.dataset.group, entry = btn.dataset.entry;
    const others = state.groups.filter((g) => g.name !== name);
    return [
        { label: 'Копировать', run: () => writeClipboard(entry).then(() => toast('Скопировано', 'success', { timeout: 1500 })) },
        { label: 'Проверить в поиске', run: () => { $('#dns-search').value = entry; renderGroups(); } },
        ...(others.length ? [{ label: 'Переместить в другой список…', run: () => openMoveEntry(name, entry) }] : []),
        '-',
        { label: 'Удалить из списка', danger: true, run: () => removeEntry(name, entry) },
    ];
}

function ipRowMenu(row) {
    const r = (state.ipRoutes || []).find((x) => x.index === row.dataset.ip);
    if (!r) return [];
    const ids = state.ipSelected.has(r.index) && state.ipSelected.size > 1 ? [...state.ipSelected] : [r.index];
    const many = ids.length > 1 ? ` (${ids.length})` : '';
    return [
        { label: 'Копировать адрес', run: () => writeClipboard(r.destination).then(() => toast('Скопировано', 'success', { timeout: 1500 })) },
        { label: `Описание…${many}`, run: () => setIpComment(ids, ids.length === 1 ? r.comment : '') },
        r.enabled
            ? { label: `Выключить${many}`, run: () => setIpEnabled(ids, false) }
            : { label: `Включить${many}`, run: () => setIpEnabled(ids, true) },
        { label: `Сменить интерфейс…${many}`, run: () => openIpMove(ids) },
        { label: `Экспорт${many}`, run: () => exportFile({ ip: (x) => ids.some((i) => state.ipRoutes.find((y) => y.index === i)?.destination === x.destination), name: r.comment || r.destination }) },
        '-',
        { label: `Удалить${many}`, danger: true, run: () => deleteIpRoutes(ids) },
    ];
}

document.addEventListener('contextmenu', (e) => {
    const field = isTextField(e.target) ? e.target : null;
    const chip = !field && e.target.closest('.chip');
    const row = !field && e.target.closest('[data-ip]');
    const selection = String(window.getSelection() || '');
    let items = [];
    if (field) items = fieldMenu(field);
    else if (chip) items = chipMenu(chip);
    else if (row) items = ipRowMenu(row);
    else if (selection.trim()) items = [{ label: 'Копировать', hint: 'Ctrl+C', run: () => writeClipboard(selection) }];
    e.preventDefault();
    if (items.length) showMenu(e.clientX, e.clientY, items);
    else closeMenu();
});

function openMoveEntry(from, entry) {
    const src = findGroup(from);
    openModal({
        title: 'Переместить запись',
        body: `<p><span class="mono">${esc(entry)}</span> из «${esc(src.title)}» в:</p>
            <div class="pick-list">
                ${state.groups.filter((g) => g.name !== from).map((g) => `
                    <button class="pick" data-pick="${esc(g.name)}">${groupIcon(g.title)}
                        <div><b>${esc(g.title)}</b><small>${g.count} из ${g.limit}${g.entries.includes(entry) ? ' · уже содержит эту запись' : ''}</small></div>
                    </button>`).join('')}
            </div>`,
        onMount: ({ el, close }) => {
            el.addEventListener('click', async (e) => {
                const pick = e.target.closest('[data-pick]');
                if (!pick) return;
                close();
                try {
                    await addEntries(pick.dataset.pick, entry);
                    await removeEntry(from, entry);
                } catch (err) { toast(err.message, 'error'); }
            });
        },
    });
}

async function deleteIpRoutes(ids) {
    if (!await confirmDialog(`Удалить ${ids.length} ${plural(ids.length, 'маршрут', 'маршрута', 'маршрутов')} с роутера?`)) return;
    await task('Удаление маршрутов…', () => api('/api/router/ip-routes/delete', 'POST', { indices: ids }));
    ids.forEach((i) => state.ipSelected.delete(i));
    invalidateIpRoutes();
    await loadIpRoutes();
}

// ============================================================ обработчики событий

const actions = {
    theme() {
        const root = document.documentElement;
        const dark = root.dataset.theme ? root.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
        root.dataset.theme = dark ? 'light' : 'dark';
        storage('s4r-theme', root.dataset.theme);
    },
    settings: openSettings,
    'dns-reload': () => loadOverview(),
    'group-new': () => openGroupCreate(),
    'group-edit': (el) => openGroupEdit(el.dataset.group),
    'group-expand': (el) => {
        const n = el.dataset.group;
        state.expanded.has(n) ? state.expanded.delete(n) : state.expanded.add(n);
        renderGroups();
    },
    'group-copy': (el) => copyText(findGroup(el.dataset.group).entries.join('\n')),
    'group-export': (el) => {
        const g = findGroup(el.dataset.group);
        downloadText(`${g.title.replace(/[\\/:*?"<>|]+/g, '_')}.txt`, g.entries.join('\n') + '\n');
    },
    async 'group-rename'(el) {
        const g = findGroup(el.dataset.group);
        const title = await promptDialog({ title: 'Переименовать список', label: 'Название', value: g.title });
        if (!title || title === g.title) return;
        try {
            const data = await api(`/api/router/groups/${encodeURIComponent(g.name)}`, 'PUT', { title, entries: g.entries });
            applyGroup(data.group);
            renderGroups();
            toast('Список переименован', 'success');
        } catch (e) { toast(e.message, 'error'); }
    },
    async 'group-delete'(el) {
        const g = findGroup(el.dataset.group);
        if (!await confirmDialog(`Удалить список <b>«${esc(g.title)}»</b> (${g.count} ${plural(g.count, 'запись', 'записи', 'записей')}) и его правила маршрутизации?`)) return;
        await task('Удаление списка…', () => api(`/api/router/groups/${encodeURIComponent(g.name)}`, 'DELETE', {}));
        state.groups = state.groups.filter((x) => x.name !== g.name);
        renderGroups();
    },
    'entry-remove': (el) => removeEntry(el.dataset.group, el.dataset.entry),
    'route-new': (el) => openRouteCreate(el.dataset.group),
    'route-edit': (el) => openRouteEdit(el.dataset.index),
    'route-move': (el) => openMove({ groups: [el.dataset.group], from: el.dataset.iface }),
    'dns-move': () => openMove(),
    transfer: () => openTransfer(),
    async 'group-clean'(el) {
        const g = findGroup(el.dataset.group);
        const keep = g.entries.filter((e) => entryNote(g, e)?.cls !== 'redundant');
        const n = g.entries.length - keep.length;
        if (!await confirmDialog(`Убрать из «${esc(g.title)}» ${n} ${plural(n, 'лишнюю запись', 'лишние записи', 'лишних записей')}? Их уже покрывают родительские домены этого списка — работа сайтов не изменится.`, { ok: 'Убрать', danger: false })) return;
        const data = await task('Очистка списка…', () => api(`/api/router/groups/${encodeURIComponent(g.name)}`, 'PUT', { entries: keep }));
        applyGroup(data.group);
        renderGroups();
    },

    'ip-move-selected': () => openIpMove([...state.ipSelected]),
    'ip-comment-selected': () => setIpComment([...state.ipSelected]),
    'ip-export-selected': () => {
        const dests = new Set(state.ipRoutes.filter((r) => state.ipSelected.has(r.index)).map((r) => r.destination));
        return exportFile({ ip: (r) => dests.has(r.destination), groups: [], name: 'ip' }).catch((e) => toast(e.message, 'error'));
    },
    'ipg-toggle'(el) {
        const set = ipCollapsed();
        const key = ckey(el.dataset.comment);
        set.has(key) ? set.delete(key) : set.add(key);
        storage('s4r-ip-collapsed', JSON.stringify([...set]));
        renderIpRoutes();
    },
    'ipg-select'(el) {
        ipGroupRoutes(el.dataset.comment).forEach((r) => (el.checked ? state.ipSelected.add(r.index) : state.ipSelected.delete(r.index)));
        renderIpRoutes();
    },
    'ipg-enable': (el) => setIpEnabled(ipGroupRoutes(el.dataset.comment).map((r) => r.index), el.checked),
    'ip-enable-selected': () => setIpEnabled([...state.ipSelected], true),
    'ip-disable-selected': () => setIpEnabled([...state.ipSelected], false),
    'ipg-rename': (el) => setIpComment(ipGroupRoutes(el.dataset.comment).map((r) => r.index), el.dataset.comment),
    'ipg-add': (el) => openIpGroupCreate(el.dataset.comment),
    'ipg-move': (el) => openMove({ ipComments: [el.dataset.comment], groups: [], from: '' }),
    'ipg-copy': (el) => copyText(ipGroupRoutes(el.dataset.comment).map((r) => r.destination).join('\n')),
    'ipg-export': (el) => exportFile({ groups: [], ip: (r) => ckey(r.comment) === ckey(el.dataset.comment), name: el.dataset.comment || 'ip' })
        .catch((e) => toast(e.message, 'error')),
    'ipg-delete': (el) => deleteIpRoutes(ipGroupRoutes(el.dataset.comment).map((r) => r.index)),
    async 'route-delete'(el) {
        if (!await confirmDialog('Удалить правило маршрутизации? Сам список доменов останется.')) return;
        await task('Удаление правила…', () => api('/api/router/dns-routes/delete', 'POST', { index: el.dataset.index }));
        await loadOverview({ quiet: true });
    },
    async 'route-toggle'(el) {
        const enabled = el.checked;
        try {
            await api('/api/router/dns-routes/toggle', 'POST', { index: el.dataset.index, enabled });
            state.groups.flatMap((g) => g.routes).filter((r) => r.index === el.dataset.index).forEach((r) => { r.enabled = enabled; });
            renderGroups();
            toast(enabled ? 'Правило включено' : 'Правило выключено', 'success', { timeout: 1800 });
        } catch (e) {
            el.checked = !enabled;
            toast(e.message, 'error');
        }
    },
    'search-add': () => openPickGroup(searchQuery()),

    'ip-reload': () => state.connected ? loadIpRoutes() : loadOverview({quiet: true}),
    'ip-new': () => openIpCreate(),
    'ip-select'(el) {
        el.checked ? state.ipSelected.add(el.dataset.index) : state.ipSelected.delete(el.dataset.index);
        renderIpRoutes();
    },
    'ip-clear-selection'() { state.ipSelected.clear(); renderIpRoutes(); },
    'ip-delete-selected': () => deleteIpRoutes([...state.ipSelected]),
    async 'ip-toggle'(el) {
        const enabled = el.checked;
        try {
            await api('/api/router/ip-routes/toggle', 'POST', { index: el.dataset.index, enabled });
            invalidateIpRoutes({keepData: true});
            const r = (state.ipRoutes || []).find((x) => x.index === el.dataset.index);
            if (r) r.enabled = enabled;
            if (state.ipRoutes === null) await loadIpRoutes();
            else renderIpRoutes();
        } catch (e) {
            el.checked = !enabled;
            toast(e.message, 'error');
        }
    },

    'site-select'(el) {
        const id = +el.dataset.id;
        el.checked ? state.sitesSelected.add(id) : state.sitesSelected.delete(id);
        renderSites();
    },
    'site-refresh': (el) => refreshSites([+el.dataset.id], false),
    'site-refresh-html': (el) => refreshSites([+el.dataset.id], true),
    'site-delete': (el) => deleteSites([+el.dataset.id]),
    'site-bat': (el) => downloadFile(state.sites.find((s) => s.id === +el.dataset.id).bat_name),
    'site-to-list': (el) => openPickGroup([state.sites.find((s) => s.id === +el.dataset.id).domain], 'Домен → DNS-список'),
    'sites-refresh': () => refreshSites([...state.sitesSelected], false),
    'sites-refresh-html': () => refreshSites([...state.sitesSelected], true),
    'sites-delete': () => deleteSites([...state.sitesSelected]),
    'sites-to-list': () => openPickGroup([...new Set(state.sites.filter((s) => state.sitesSelected.has(s.id)).map((s) => s.domain))], 'Домены → DNS-список'),
    'sites-to-routes': () => openIpCreate('', [...state.sitesSelected]),
    'bat-download': () => downloadBats().catch(() => {}),

    'cap-source'(el) {
        cap.source = el.dataset.source;
        renderCapSetup();
    },
    'cap-apps-reload': () => { cap.apps = null; renderCapApps(); return loadCapApps(); },
    'cap-pick'(el) {
        const key = el.dataset.key;
        if (cap.picks.has(key)) cap.picks.delete(key);
        else { const app = cap.apps.find((a) => a.key === key); if (app) cap.picks.set(key, app); }
        renderCapApps();
    },
    'cap-remove'(el) { cap.picks.delete(el.dataset.key); renderCapApps(); },
    async 'cap-browse'() {
        try {
            const path = await window.pywebview.api.pick_exe();
            if (path) { cap.exe = path; $('#cap-exe').value = path; }
        } catch (e) { toast(e.message || 'Не удалось открыть окно выбора файла', 'error'); }
    },
    'cap-start': (el) => startCapture(el),
    async 'cap-stop'(el) {
        await withButton(el, async () => {
            try {
                cap.session = (await api('/api/capture/stop', 'POST', {})).session;
                cap.agent = cap.session?.agent || cap.agent;
                cap.history = (await api('/api/capture/history')).programs;
                const r = cap.session?.results;
                const n = r ? capDomainGroups(r).length : 0;
                toast(r ? `Анализ завершён: ${n} ${plural(n, 'домен', 'домена', 'доменов')}, ${r.ips.length} IP` : 'Анализ завершён', 'success');
            } catch (e) { toast(e.message, 'error'); }
        });
        renderCapture();
    },
    async 'cap-elevate'(el) {
        await withButton(el, async () => {
            try {
                cap.session = (await api('/api/capture/elevate', 'POST', {})).session;
                cap.agent = cap.session.agent;
                toast(['pending', 'starting'].includes(cap.agent.status) ? 'Подтвердите запрос прав администратора в окне Windows' : 'Полный режим включается — права уже подтверждены', 'info');
            } catch (e) { toast(e.message, 'error'); }
        });
        renderCapLive();
        scheduleCapPoll();
    },
    'cap-new'() {
        if (cap.session?.running) return toast('Сначала остановите текущий анализ', 'info');
        cap.setup = true;
        capResetSelection();
        if (!cap.apps) loadCapApps();
        renderCapture();
        $('#cap-setup').scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
    'cap-hist-open': (el) => openCapHistory(el.dataset.name),
    'cap-program'(el) {
        if (cap.program === el.dataset.name) return;
        cap.program = el.dataset.name;
        capResetSelection();
        renderCapResults();
    },
    'cap-setup-close'() { cap.setup = false; renderCapture(); },
    'cap-hist-close': () => closeCapHistory(),
    'cap-hist-refresh': () => cap.histName && openCapHistory(cap.histName, { force: true }),
    async 'cap-hist-delete'(el) {
        const name = el.dataset.name;
        if (!await confirmDialog(`Удалить всю историю анализа <b>«${esc(name)}»</b>? На роутере ничего не изменится.`)) return;
        await task('Удаление истории…', () => api('/api/capture/program/delete', 'POST', { name }));
        cap.history = (await api('/api/capture/history')).programs;
        if (cap.histName === name) closeCapHistory();
        else renderCapHistory();
    },
    'cap-dom-all'(el) {
        const scope = capScope(el), view = capResultView(scope), data = capData(scope);
        if (!data) return;
        capShownResults(data, scope).groups.forEach((g) => view.selDomains.add(g.base));
        renderCapResults(scope);
    },
    'cap-ip-all'(el) {
        const scope = capScope(el), view = capResultView(scope), data = capData(scope);
        if (!data) return;
        capShownResults(data, scope).ips.filter((i) => isIpv4(i.ip)).forEach((i) => view.selIps.add(i.ip));
        renderCapResults(scope);
    },
    'cap-dom-none'(el) { const scope = capScope(el); capResultView(scope).selDomains.clear(); renderCapResults(scope); },
    'cap-ip-none'(el) { const scope = capScope(el); capResultView(scope).selIps.clear(); renderCapResults(scope); },
    'cap-dom-add': (el) => capAddDomains(capScope(el)),
    'cap-ip-add': (el) => capAddIps(capScope(el)),
    'cap-search-clear'(el) {
        const scope = capScope(el);
        capResultView(scope).query = '';
        renderCapResults(scope);
        $(scope === 'history' ? '#cap-hist-result-search' : '#cap-result-search')?.focus();
    },
};

document.addEventListener('pointerdown', (e) => {
    if (e.target.closest('#cap-live, #cap-results, #cap-history')) cap.pointerDown = true;
});
function capReleasePointer() {
    // Выполняем после click/change: пользователь успевает закончить действие до обновления списка.
    setTimeout(() => {
        cap.pointerDown = false;
        if (cap.refreshPending && capVisible()) {
            cap.refreshPending = false;
            renderCapLive();
            renderCapResults();
        }
    }, 0);
}
document.addEventListener('pointerup', capReleasePointer);
document.addEventListener('pointercancel', capReleasePointer);
window.addEventListener('blur', capReleasePointer);

document.addEventListener('click', (e) => {
    const tab = e.target.closest('.tab');
    if (tab) return switchTab(tab.dataset.tab);
    const el = e.target.closest('[data-action]');
    if (!el || el.matches('input[type=checkbox]')) return;
    const fn = actions[el.dataset.action];
    if (fn) {
        e.preventDefault();
        Promise.resolve(fn(el)).catch(() => {});
    }
});

document.addEventListener('change', (e) => {
    const el = e.target;
    if (el.matches('input[type=checkbox][data-action]')) {
        Promise.resolve(actions[el.dataset.action]?.(el)).catch(() => {});
    } else if (el.dataset.capDom !== undefined) {
        const scope = capScope(el), view = capResultView(scope);
        el.checked ? view.selDomains.add(el.dataset.capDom) : view.selDomains.delete(el.dataset.capDom);
        renderCapResults(scope);
    } else if (el.dataset.capIp !== undefined) {
        const scope = capScope(el), view = capResultView(scope);
        el.checked ? view.selIps.add(el.dataset.capIp) : view.selIps.delete(el.dataset.capIp);
        renderCapResults(scope);
    } else if (el.dataset.capOption !== undefined) {
        const scope = capScope(el);
        capResultView(scope)[el.dataset.capOption] = el.checked;
        renderCapResults(scope);
    } else if (el.id === 'cap-system') {
        cap.showSystem = el.checked;
        cap.apps = null;
        renderCapApps();
        loadCapApps();
    } else if (el.id === 'cap-full' || el.id === 'cap-children') {
        storage(el.id === 'cap-full' ? 's4r-cap-full' : 's4r-cap-children', el.checked ? '1' : '0');
    } else if (el.id === 'sites-check-all') {
        state.sitesSelected = el.checked ? new Set(state.sites.map((s) => s.id)) : new Set();
        renderSites();
    }
});

document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (form.matches('.group-add')) {
        e.preventDefault();
        const input = $('input', form);
        const text = input.value.trim();
        if (!text) return;
        await withButton($('button', form), async () => {
            try {
                await addEntries(form.dataset.group, text);
                const fresh = $(`.group-add[data-group="${CSS.escape(form.dataset.group)}"] input`);
                if (fresh?.value.trim() === text) fresh.value = '';
                fresh?.focus();
            } catch (err) {
                toast(err.message, 'error');
            }
        });
    } else if (form.id === 'site-form') {
        e.preventDefault();
        const input = $('#site-url');
        const url = input.value.trim();
        if (!url) return;
        await withButton($('button', form), async () => {
            try {
                await task('Добавление сайта и поиск IP…', () => api('/api/sites', 'POST', { url, generate_bat: true }));
                input.value = '';
                await loadSites();
            } catch { /* toast уже показан */ }
        });
    }
});

let searchTimer;
$('#dns-search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(renderGroups, 120);
});
$('#ip-search').addEventListener('input', renderIpRoutes);
document.addEventListener('input', (e) => {
    if (e.target.dataset.capQuery !== undefined) {
        const scope = capScope(e.target);
        capResultView(scope).query = e.target.value;
        renderCapResults(scope);
    } else if (e.target.id === 'cap-filter') { cap.filter = e.target.value; renderCapApps(); }
    else if (e.target.dataset.capName !== undefined) cap.names.set(e.target.dataset.capName, e.target.value);
    else if (e.target.id === 'cap-exe-name') cap.names.set('exe', e.target.value);
    else if (e.target.id === 'cap-exe') cap.exe = e.target.value;
});

document.addEventListener('keydown', (e) => {
    if (e.key === '/' && !e.target.closest('input, textarea, select') && !$('#modal-root').children.length) {
        e.preventDefault();
        switchTab('dns');
        $('#dns-search').focus();
    }
    if (e.key === 'Escape' && e.target.id === 'dns-search') {
        e.target.value = '';
        renderGroups();
    }
});

// ============================================================ старт

(async function init() {
    const tab = storage('s4r-tab');
    if (tab && $(`#view-${tab}`)) switchTab(tab);
    renderSites();
    try {
        await loadSettings();
    } catch (e) {
        toast(e.message, 'error');
    }
    await loadOverview({ quiet: true });
    if (tab !== 'sites') loadSites();
})();
