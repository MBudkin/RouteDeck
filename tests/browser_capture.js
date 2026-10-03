/* Браузерные регрессии на реальном DOM. API подменён: роутер и UAC не затрагиваются. */
window.testRequests = [];
window.testSession = null;
const testHistory = [{name: 'Discord', sessions: 2, domains: 2, ips: 3, full: true, last: '2026-10-03T12:00:00'},
                     {name: 'Code', sessions: 1, domains: 1, ips: 1, full: false, last: '2026-10-03T12:00:00'}];
const testRoute = {destination: '8.8.8.0/24', comment: 'Discord', interface: 'Wireguard0', interface_title: 'VPN'};
window.fetch = async (url, opts = {}) => {
    const payload = opts.body ? JSON.parse(opts.body) : null;
    window.testRequests.push({url, payload});
    let body = {success: true};
    if (url === '/api/router/settings') body.settings = {has_password: false};
    else if (url === '/api/router/overview') Object.assign(body, {system: {model: 'Тестовый роутер', release: '5.0'}, interfaces: [], groups: [], limit: 300});
    else if (url === '/api/router/ip-routes') body.routes = [testRoute];
    else if (url === '/api/sites') body.sites = [];
    else if (url === '/api/capture/history') body.programs = testHistory;
    else if (url.startsWith('/api/capture/program?')) {
        const name = new URL(url, location.href).searchParams.get('name');
        if (window.testDelayedHistory === name) await new Promise((resolve) => { window.testHistoryResolve = resolve; });
        if (window.testHistoryFailure === name) return {ok: false, json: async () => ({success: false, error: 'Тестовая ошибка загрузки'})};
        Object.assign(body, {name, sessions: [{id: 1}, {id: 2}],
            domains: [{domain: name === 'Discord' ? 'old.discord.com' : 'archive.code.example.org', queried: true, ips: [], apps: [name], count: 1},
                      {domain: 'archive.example.net', queried: true, ips: [], apps: [name], count: 1}],
            ips: [{ip: name === 'Discord' ? '8.8.8.8' : '13.107.6.158', apps: [name], domains: [], protos: ['udp'], ports: [443], procs: ['archive.exe'], count: 1}]});
    }
    else if (url === '/api/capture/program/delete') {
        const index = testHistory.findIndex((h) => h.name === payload.name);
        if (index !== -1) testHistory.splice(index, 1);
    }
    else if (url === '/api/capture/state') Object.assign(body, {session: window.testSession, agent: window.testSession?.agent || {status: 'ready'}});
    else if (url === '/api/capture/start') {
        window.testSession = {id: 9, running: true, full: payload.full, mode: 'full', started: '2026-10-03T12:00:00', elapsed: 65,
            target: {name: payload.apps.map((a) => a.name).join(' + '), apps: payload.apps},
            processes: [], results: {ips: [], domains: []}, agent: {status: 'ready'}};
        body.session = window.testSession;
    }
    return {ok: true, json: async () => body};
};

window.addEventListener('load', async () => {
    const report = [];
    const assert = (condition, name) => { if (!condition) throw new Error(name); report.push(name); };
    const tick = () => new Promise((resolve) => setTimeout(resolve, 15));
    try {
        await tick();
        $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-capture'));
        $$('.tab').forEach((v) => v.classList.toggle('active', v.dataset.tab === 'capture'));
        cap.apps = [{key: 'discord', title: 'Discord', folder: 'C:\\Apps\\Discord', pids: [1], processes: 1, exe_names: ['Discord.exe']},
                    {key: 'code', title: 'Code', folder: 'C:\\Apps\\Code', pids: [2], processes: 1, exe_names: ['Code.exe']}];
        cap.agent = {status: 'ready'};
        renderCapture();
        $('[data-key="discord"]').click();
        $('[data-key="code"]').click();
        assert(cap.picks.size === 2 && $$('[data-cap-name]').length === 2, 'Несколько программ выбираются одновременно');
        const nameInput = $('[data-cap-name="code"]');
        nameInput.value = 'VS Code';
        nameInput.dispatchEvent(new Event('input', {bubbles: true}));
        await startCapture($('[data-action="cap-start"]'));
        clearTimeout(cap.timer);
        const payload = window.testRequests.find((r) => r.url === '/api/capture/start').payload;
        assert(payload.apps.length === 2 && payload.apps[1].name === 'VS Code', 'API получает отдельные названия программ');

        const ip = (address, apps, domains = []) => ({ip: address, apps, domains, protos: ['tcp'], ports: [443], procs: ['app.exe'], count: 1});
        const domain = (name, apps, ips = []) => ({domain: name, apps, ips, queried: true, count: 1});
        cap.session.results = {
            ips: [ip('8.8.8.8', ['Discord'], ['discord.com']), ip('1.1.1.1', ['Discord']), ip('9.9.9.9', ['VS Code']), ip('2606:4700:4700::1111', ['Discord'])],
            domains: [domain('discord.com', ['Discord'], ['8.8.8.8']), domain('example.com', ['Discord']), domain('visualstudio.com', ['VS Code'])]};
        state.connected = true;
        state.ipRoutes = [testRoute];
        cap.setup = false;
        renderCapture();
        cap.session.full = false;
        renderCapLive();
        assert(!!$('[data-action="cap-elevate"]'), 'Полный режим можно включить в обычном анализе при готовом сборщике');
        cap.session.full = true;
        assert($('[data-cap-ip="8.8.8.8"]').closest('label').textContent.includes('IP-маршрут «Discord» · VPN'), 'Найденный добавленный IP отмечен группой и туннелем');
        assert(!$('[data-cap-ip="9.9.9.9"]'), 'Результаты разных программ разделены');
        const all = $('[data-action="cap-dom-all"]');
        const checkbox = $('[data-cap-dom="discord.com"]');
        all.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));
        cap.session.results.domains.push(domain('new.example.net', ['Discord']));
        renderCapResults();
        assert(all === $('[data-action="cap-dom-all"]'), 'Кнопка остаётся в DOM во время нажатия и обновления');
        all.click();
        document.dispatchEvent(new PointerEvent('pointerup', {bubbles: true}));
        await tick();
        assert(cap.selDomains.size === 3 && checkbox.checked, 'Выбрать всё работает при одновременном обновлении');
        checkbox.focus();
        for (let n = 0; n < 40; n++) { cap.session.results.ips[0].count++; renderCapResults(); }
        assert(checkbox === $('[data-cap-dom="discord.com"]') && document.activeElement === checkbox && checkbox.checked,
               'Обновления сохраняют checkbox, фокус и выбор');
        $('[data-action="cap-dom-none"]').click();
        assert(cap.selDomains.size === 0 && !checkbox.checked, 'Снять всё работает после обновлений');
        $('[data-action="cap-ip-all"]').click();
        assert(cap.selIps.size === 2 && !cap.selIps.has('2606:4700:4700::1111'), 'Выбрать IP отмечает IPv4, IPv6 остаётся недоступным');
        cap.noDomainOnly = true;
        renderCapResults();
        let addedIps;
        openIpCreate = (...args) => { addedIps = args; };
        capAddIps();
        assert(addedIps[0] === '1.1.1.1' && addedIps[2] === 'Discord', 'Добавляются только видимые выбранные IP с названием программы');
        cap.noDomainOnly = false;
        renderCapResults();
        $('[data-action="cap-program"][data-name="VS Code"]').click();
        assert(capData().ips.length === 1 && cap.selIps.size === 0 && $('[data-cap-ip="9.9.9.9"]'), 'Смена программы показывает её результаты и очищает выбор');
        $('[data-action="cap-program"][data-name="Discord"]').click();
        $('[data-action="cap-dom-all"]').click();
        let domainArgs;
        openPickGroup = (...args) => { domainArgs = args; };
        capAddDomains();
        assert(domainArgs[2] === 'Discord', 'Новый DNS-список получает название выбранной программы');

        const details = $('.cap-details');
        details.open = true;
        renderCapLive();
        assert(details === $('.cap-details') && details.open, 'Раскрытые процессы не сворачиваются при обновлении');
        cap.history = testHistory;
        renderCapHistory();
        const history = $('.cap-history');
        assert(!$('#cap-history-detail') && !!$('[data-action="cap-hist-open"]'), 'Список истории виден, подробности по умолчанию свёрнуты');
        const liveBeforeHistory = $('#cap-results').innerHTML;
        const selectedBeforeHistory = [...cap.selDomains].join(',');
        const scrollBeforeHistory = window.scrollY;
        await openCapHistory('Discord');
        const archived = $('#cap-history-results');
        assert(archived && $('#cap-history-detail').previousElementSibling?.dataset.name === 'Discord', 'История раскрывается непосредственно под выбранной программой');
        assert($('#cap-results').innerHTML === liveBeforeHistory && [...cap.selDomains].join(',') === selectedBeforeHistory && cap.program === 'Discord',
               'Открытие истории сохраняет текущие результаты, программу и выбор');
        assert(window.scrollY === scrollBeforeHistory, 'Открытие истории не переносит прокрутку наверх');
        $('[data-action="cap-ip-all"]', archived).click();
        const archiveAggregate = $('#cap-hist-agg');
        archiveAggregate.checked = true;
        archiveAggregate.dispatchEvent(new Event('change', {bubbles: true}));
        capAddIps('history');
        assert(addedIps[0] === '8.8.8.0/24' && addedIps[2] === 'Discord' && !cap.agg24 && !cap.selIps.size,
               'IP и параметры добавления истории независимы от текущего анализа');
        $('[data-action="cap-dom-all"]', archived).click();
        assert(cap.archive.selDomains.size === 2 && [...cap.selDomains].join(',') === selectedBeforeHistory, 'Выбор доменов истории независим от текущего анализа');
        capAddDomains('history');
        assert(domainArgs[0].includes('discord.com') && !domainArgs[0].includes('example.com') && domainArgs[2] === 'Discord', 'Добавление из истории использует сохранённые адреса');
        const archiveSearch = $('#cap-hist-result-search');
        archiveSearch.value = 'archive.example.net';
        archiveSearch.dispatchEvent(new Event('input', {bubbles: true}));
        assert($$('[data-cap-dom]', archived).length === 1 && $('#cap-dom-list').children.length === 3 && !cap.query,
               'Поиск в истории фильтрует только сохранённые результаты');
        $('[data-action="cap-dom-none"]', archived).click();
        assert(!cap.archive.selDomains.size && cap.selDomains.size === 3, 'Снятие выбора истории не затрагивает текущий выбор');
        archiveSearch.focus();
        renderCapHistory();
        assert(history === $('.cap-history') && archiveSearch === $('#cap-hist-result-search') && document.activeElement === archiveSearch,
               'Обновление списка истории сохраняет её поиск, фокус и раскрытый блок');
        cap.session.results.domains.push(domain('arrived.example.org', ['Discord']));
        await pollCapture();
        clearTimeout(cap.timer);
        assert($('#cap-dom-list').textContent.includes('example.org') && cap.archive.query === 'archive.example.net' && cap.selDomains.size === 3,
               'Текущий анализ обновляется при раскрытой истории и сохраняет оба выбора');
        closeCapHistory();
        assert(!$('#cap-history-detail') && cap.selDomains.size === 3 && cap.program === 'Discord', 'Закрытие истории не сбрасывает текущий анализ');

        window.testDelayedHistory = 'Discord';
        const delayed = openCapHistory('Discord');
        await openCapHistory('Code');
        window.testHistoryResolve();
        await delayed;
        window.testDelayedHistory = '';
        assert(cap.hist.name === 'Code' && $('.cap-archive-head h3').textContent === 'Code',
               'Запоздавший ответ первой программы не заменяет выбранную историю');
        window.testHistoryFailure = 'Code';
        await openCapHistory('Code', {force: true});
        assert(!!$('.cap-history-error') && !!$('#cap-history-results [data-cap-ip]'), 'Ошибка обновления истории сохраняет уже загруженные результаты');
        window.testHistoryFailure = '';
        await openCapHistory('Code', {force: true});
        assert(!$('.cap-history-error'), 'Историю можно повторно загрузить после ошибки');
        closeCapHistory();
        window.testDelayedHistory = 'Discord';
        const closedPending = openCapHistory('Discord');
        closeCapHistory();
        window.testHistoryResolve();
        await closedPending;
        window.testDelayedHistory = '';
        assert(!$('#cap-history-detail') && !cap.histName, 'История не открывается снова после закрытия незавершённой загрузки');

        const liveSearch = $('#cap-result-search');
        liveSearch.value = 'discord.com';
        liveSearch.dispatchEvent(new Event('input', {bubbles: true}));
        assert($$('[data-cap-dom]', $('#cap-results')).length === 1 && $$('[data-cap-ip]', $('#cap-results')).length === 1,
               'Поиск результатов находит и домен, и связанные IP');
        $('[data-action="cap-search-clear"]', $('#cap-results')).click();
        const onlyNew = $('#cap-newonly');
        onlyNew.checked = true;
        onlyNew.dispatchEvent(new Event('change', {bubbles: true}));
        assert(!$('[data-cap-ip="8.8.8.8"]', $('#cap-results')) && !!$('[data-cap-ip="1.1.1.1"]', $('#cap-results')),
               'Фильтр новых адресов скрывает IP, уже добавленные на роутер');
        onlyNew.checked = false;
        onlyNew.dispatchEvent(new Event('change', {bubbles: true}));

        let restored = 0;
        window.pywebview = {api: {restore_window: async () => { restored++; }}};
        cap.agent = {status: 'pending'};
        cap.session.agent = {status: 'capturing'};
        await pollCapture();
        clearTimeout(cap.timer);
        assert(restored === 1 && $('.cap-agent').textContent.includes('Права подтверждены'), 'После UAC окно восстанавливается и виден актуальный статус');
        state.settings = {has_password: true};
        state.ipRoutes = null;
        await loadOverview({quiet: true});
        assert(state.ipRoutes.length === 1 && $('#cap-results .cap-result-toolbar').textContent.includes('Уже на роутере: 1 IP'), 'Добавленные IP загружаются после подключения роутера');
        cap.session.running = false;
        renderCapture();
        assert(!$('.cap-agent') && $('.cap-live-title').textContent.includes('результаты сохранены'), 'После завершения виден итог без панели работающего сборщика');
        await openCapHistory('Code');
        const beforeDelete = $('#cap-results').innerHTML;
        confirmDialog = async () => true;
        await actions['cap-hist-delete']({dataset: {name: 'Code'}});
        assert($('#cap-results').innerHTML === beforeDelete && !!cap.session && cap.selDomains.size === 3,
               'Удаление истории сохраняет текущие результаты и выбор');
        actions['cap-new']();
        assert(!$('#cap-live').children.length && !$('#cap-results').children.length && !!$('#cap-apps'), 'Новый анализ убирает карточки прежнего результата');
        await openCapHistory('Discord');
        assert(!!$('#cap-apps') && !!$('#cap-history-detail') && cap.setup, 'Историю можно просматривать во время выбора программ для нового анализа');
        closeCapHistory();
        actions['cap-setup-close']();
        assert(!!$('#cap-dom-list') && !cap.setup, 'Из выбора программ можно вернуться к последним результатам');
        const resultOptions = $('#cap-results .cap-add-options');
        resultOptions.open = true;
        renderCapResults();
        assert(resultOptions === $('#cap-results .cap-add-options') && resultOptions.open, 'Параметры добавления сохраняют раскрытие при обновлении');
        document.body.dataset.testStatus = 'passed';
        document.body.dataset.testCount = report.length;
    } catch (error) {
        document.body.dataset.testStatus = 'failed';
        report.push(error.stack);
    }
    clearTimeout(cap.timer);
    if (location.search.includes('preview')) {
        cap.setup = false;
        cap.session.running = false;
        renderCapture();
        $('.cap-details').open = false;
        $('#cap-results .cap-add-options')?.removeAttribute('open');
        $('#toasts').innerHTML = '';
        const preview = new URLSearchParams(location.search);
        document.documentElement.dataset.theme = preview.get('theme') || 'dark';
        if (preview.get('view') === 'history') await openCapHistory('Discord');
        if (preview.get('view') === 'setup') { cap.setup = true; renderCapture(); }
        return;
    }
    const output = document.createElement('pre');
    output.id = 'test-report';
    output.textContent = report.join('\n');
    document.body.append(output);
});
