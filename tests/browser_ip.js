/* IP-маршруты: запросы, ошибки и задержки воспроизводятся без настоящего роутера. */
const ipFixture = {
    interfaces: [{id: 'Wireguard0', description: 'Основной VPN', tunnel: true, up: true},
                 {id: 'Wireguard1', description: 'Рабочий VPN', tunnel: true, up: true}],
    routes: [{index: '1', destination: '8.8.8.8/32', comment: 'Discord', interface: 'Wireguard1', interface_title: 'Рабочий VPN', enabled: true, auto: true},
             {index: '2', destination: '1.1.1.0/24', comment: 'Discord', interface: 'Wireguard1', interface_title: 'Рабочий VPN', enabled: true, auto: true},
             {index: '3', destination: '9.9.9.9/32', comment: '', interface: 'Wireguard0', interface_title: 'Основной VPN', enabled: true},
             {index: '4', destination: '149.154.160.0/20', comment: 'Смешанная группа', interface: 'Wireguard0', interface_title: 'Основной VPN', enabled: true},
             {index: '5', destination: '91.108.56.0/22', comment: 'Смешанная группа', interface: 'Wireguard1', interface_title: 'Рабочий VPN', enabled: true}],
    requests: [], pending: [], delay: false, fail: false, overviewDelay: false, overviewResolve: null, overviewFail: false,
};
window.fetch = async (url, options = {}) => {
    const payload = options.body ? JSON.parse(options.body) : null;
    ipFixture.requests.push({url, method: options.method || 'GET', payload});
    let body = {success: true};
    if (url === '/api/router/settings') body.settings = {has_password: false};
    else if (url === '/api/sites') body.sites = [];
    else if (url === '/api/router/overview') {
        if (ipFixture.overviewDelay) await new Promise((resolve) => { ipFixture.overviewResolve = resolve; });
        if (ipFixture.overviewFail) return {ok: false, json: async () => ({success: false, error: 'Ошибка подключения к роутеру'})};
        Object.assign(body, {system: {model: 'Тестовый роутер', release: '5.0'}, interfaces: ipFixture.interfaces,
            groups: [], limit: 300, default_interface: 'Wireguard0'});
    } else if (url === '/api/router/ip-routes' && options.method !== 'POST') {
        const routes = structuredClone(ipFixture.routes), fail = ipFixture.fail;
        if (ipFixture.delay) await new Promise((resolve) => ipFixture.pending.push(resolve));
        if (fail) return {ok: false, json: async () => ({success: false, error: 'Роутер временно недоступен'})};
        body.routes = routes;
    } else if (url === '/api/router/ip-routes/toggle') {
        const ids = payload.indices || [payload.index];
        ipFixture.routes.forEach((r) => { if (ids.includes(r.index)) r.enabled = payload.enabled; });
        body.message = 'Готово';
    } else if (url === '/api/router/ip-routes/delete') {
        ipFixture.routes = ipFixture.routes.filter((r) => !payload.indices.includes(r.index));
        body.message = 'Удалено';
    } else if (url === '/api/router/ip-routes' && options.method === 'POST') {
        for (const destination of payload.destinations) ipFixture.routes.push({index: String(100 + ipFixture.routes.length),
            destination: destination.includes('/') ? destination : destination + '/32', comment: payload.comment,
            interface: payload.interface, interface_title: ipFixture.interfaces.find((i) => i.id === payload.interface)?.description,
            enabled: true, auto: true});
        body.message = 'Адреса добавлены';
    }
    return {ok: true, json: async () => body};
};

window.addEventListener('load', async () => {
    const report = [];
    const assert = (condition, name) => { if (!condition) throw new Error(name); report.push(name); };
    const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
    const gets = () => ipFixture.requests.filter((r) => r.url === '/api/router/ip-routes' && r.method === 'GET').length;
    const plus = (comment) => $$('[data-action="ipg-add"]').find((b) => b.dataset.comment === comment);
    const closeModal = () => $('#modal-root [data-close]')?.click();
    const finish = () => { ipFixture.delay = false; ipFixture.pending.splice(0).forEach((resolve) => resolve()); };
    try {
        await tick();
        state.settings = {has_password: true};
        state.connected = false;
        invalidateIpRoutes();
        ipFixture.overviewDelay = true;
        const connection = loadOverview({quiet: true});
        switchTab('ip');
        assert(gets() === 0 && state.ipRoutes === null, 'Ранний вход в IP не сохраняет ложный пустой список');
        ipFixture.overviewResolve();
        await connection;
        ipFixture.overviewDelay = false;
        assert(gets() === 1 && $$('.ip-row').length === 5, 'После подключения данные загружаются в уже открытый раздел');

        invalidateIpRoutes();
        ipFixture.fail = true;
        await loadIpRoutes();
        assert(state.ipRoutes === null && !!$('#ip-status [data-action="ip-reload"]') && $('#ip-empty').hidden,
               'Ошибка первой загрузки показана с повтором, без ложного сообщения об отсутствии маршрутов');
        ipFixture.fail = false;
        switchTab('dns');
        const failedGets = gets();
        switchTab('ip');
        await tick();
        assert(gets() === failedGets + 1 && $$('.ip-row').length === 5 && !state.ipError,
               'Повторный вход после ошибки заново загружает маршруты');
        const nextGets = gets();
        switchTab('dns'); switchTab('ip'); await tick();
        assert(gets() === nextGets + 1, 'При следующем входе сведения о маршрутах обновляются');

        state.connected = false;
        ipFixture.overviewFail = true;
        switchTab('dns'); switchTab('ip'); await tick();
        assert(!state.connected && !!$('#ip-empty [data-action="ip-reload"]'), 'При ошибке подключения к роутеру доступен повтор подключения');
        ipFixture.overviewFail = false;
        $('#ip-empty [data-action="ip-reload"]').click(); await tick();
        assert(state.connected && $$('.ip-row').length === 5 && $('#ip-empty').hidden, 'Повтор подключения из IP-раздела восстанавливает соединение и маршруты');

        state.ipSelected.add('1');
        renderIpRoutes();
        const previous = state.ipRoutes, row = $('[data-ip="1"]');
        ipFixture.fail = true;
        await loadIpRoutes();
        assert(state.ipRoutes === previous && state.ipSelected.has('1') && $('[data-ip="1"]') === row,
               'Ошибка обновления сохраняет маршруты, выбранные строки и DOM');
        assert($('#ip-status').textContent.includes('ранее загруженные'), 'Ранее полученные данные явно отмечены при ошибке');
        ipFixture.fail = false;
        $('#ip-status [data-action="ip-reload"]').click(); await tick();
        assert(!state.ipError && !$('#ip-status').textContent && state.ipSelected.has('1'), 'Повтор из сообщения убирает ошибку и сохраняет выбор');
        const groupSelect = $('[data-action="ipg-select"][data-comment="Discord"]');
        assert(groupSelect.indeterminate, 'Частичный выбор группы отображается корректно');
        groupSelect.checked = true; groupSelect.dispatchEvent(new Event('change', {bubbles: true}));
        assert(groupSelect.checked && !groupSelect.indeterminate, 'При полном выборе группы частичная отметка убирается');

        invalidateIpRoutes(); ipFixture.delay = true;
        const concurrentGets = gets();
        const one = loadIpRoutes(), two = ensureIpRoutes(), three = loadIpRoutes();
        assert(gets() === concurrentGets + 1 && state.ipLoading && !!$('#ip-status .loading-spinner') && $('#ip-groups').getAttribute('aria-busy') === 'true',
               'Одновременные загрузки используют один запрос и показывают индикатор');
        finish(); await Promise.all([one, two, three]);
        assert(!state.ipLoading && $$('.ip-row').length === 5, 'После ответа индикатор исчезает и отображаются маршруты');

        ipFixture.delay = true;
        const obsolete = loadIpRoutes();
        invalidateIpRoutes();
        const originalRoutes = ipFixture.routes;
        ipFixture.routes = [{...originalRoutes[0], destination: '4.4.4.4/32'}];
        ipFixture.delay = false;
        await loadIpRoutes();
        finish(); await obsolete;
        assert(state.ipRoutes.length === 1 && state.ipRoutes[0].destination === '4.4.4.4/32',
               'Поздний ответ старого запроса не заменяет данные после сброса кэша');
        ipFixture.routes = originalRoutes;
        await loadIpRoutes();

        ipFixture.delay = true;
        const beforeToggle = loadIpRoutes();
        await setIpEnabled(['1'], false);
        finish(); await beforeToggle;
        assert(state.ipRoutes.find((r) => r.index === '1').enabled === false && $('[data-ip="1"]').classList.contains('off'),
               'Поздний ответ обновления не отменяет выключение маршрута');
        await setIpEnabled(['1'], true);

        ipFixture.delay = true;
        const beforeDelete = loadIpRoutes();
        ipFixture.delay = false;
        const originalConfirm = confirmDialog;
        confirmDialog = async () => true;
        await deleteIpRoutes(['3']);
        finish(); await beforeDelete;
        assert(!state.ipRoutes.some((r) => r.index === '3') && !$('[data-ip="3"]'),
               'Удаление во время обновления запрашивает новые данные, старый ответ не возвращает удалённый IP');
        confirmDialog = originalConfirm;
        ipFixture.routes.push(originalRoutes.find((r) => r.index === '3'));
        await loadIpRoutes();

        $('[data-action="ipg-toggle"][data-comment="Discord"]').click();
        assert(plus('Discord')?.closest('.ipg').classList.contains('collapsed'), 'Плюс доступен и у свёрнутой группы');
        plus('Discord').click();
        let form = $('#ip-form');
        assert(form.comment.value === 'Discord' && form.comment.readOnly && form.interface.value === 'Wireguard1',
               'Плюс подставляет точную группу и её VPN, а не подключение по умолчанию');
        form.dest.value = '8.8.4.4\n208.67.222.0/24';
        await form.onsubmit({preventDefault() {}}); await tick();
        const sent = ipFixture.requests.find((r) => r.method === 'POST' && r.url === '/api/router/ip-routes').payload;
        assert(sent.comment === 'Discord' && sent.interface === 'Wireguard1' && sent.destinations.join(',') === '8.8.4.4,208.67.222.0/24',
               'Добавление отправляет новые адреса в нужную группу через нужное подключение');
        assert(ipGroupRoutes('Discord').length === 4 && !$('#ip-form'), 'Новые IP появились в существующей группе после сохранения');

        plus('Смешанная группа').click(); form = $('#ip-form');
        assert(form.interface.value === '' && form.interface.required && form.comment.value === 'Смешанная группа',
               'Для группы с несколькими VPN подключение выбирается явно');
        closeModal(); plus('').click(); form = $('#ip-form');
        assert(form.comment.value === '' && form.comment.readOnly && form.interface.value === 'Wireguard0',
               'Безымянная группа остаётся без описания и сохраняет своё подключение');
        closeModal();

        state.groups = [{name: 'domain-list1', title: 'Рабочие сайты', entries: ['example.org'], count: 1, limit: 300,
            routes: [{index: 'dns1', group: 'domain-list1', interface: 'Wireguard0', interface_title: 'Основной VPN', interface_up: true, enabled: true}]}];
        switchTab('dns'); renderGroups();
        invalidateIpRoutes(); ipFixture.delay = true;
        const moving = actions['route-move']($('.route-iface'));
        assert(!!$('#modal-root .move-loading .loading-spinner') && !$('#move-form'),
               'Смена подключения сразу открывает окно с анимацией загрузки');
        await openMove({groups: ['domain-list1'], from: 'Wireguard0'});
        assert($('#modal-root').children.length === 1, 'Повторное нажатие во время загрузки не создаёт второе окно');
        closeModal(); finish(); await moving;
        assert(!$('#modal-root').children.length, 'Закрытое окно загрузки не открывается снова после ответа');

        invalidateIpRoutes(); ipFixture.fail = true;
        await openMove({groups: ['domain-list1'], from: 'Wireguard0'});
        assert(!!$('[data-move-retry]') && !$('#move-form') && $('#modal-root').textContent.includes('Не удалось загрузить'),
               'Ошибка загрузки смены подключения показана с кнопкой повтора');
        ipFixture.fail = false;
        $('[data-move-retry]').click(); await tick();
        assert(!!$('#move-form') && $('#move-form input[name="g"]').checked && !$$('#move-form input[name="ip"]:checked').length,
               'После повтора открыт диалог с выбранным DNS-списком');
        closeModal();
        const cachedGets = gets();
        await openMove({groups: ['domain-list1'], from: 'Wireguard0'});
        assert(gets() === cachedGets && !!$('#move-form'), 'С загруженными маршрутами диалог открывается без ожидания запроса');
        closeModal();

        const preview = new URLSearchParams(location.search);
        if (preview.has('preview')) {
            const still = document.createElement('style');
            still.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; }';
            document.head.append(still);
            switchTab('ip'); await tick();
            storage('s4r-ip-collapsed', '[]'); renderIpRoutes();
            state.ipSelected.clear(); renderIpRoutes();
            $('#toasts').innerHTML = '';
            document.documentElement.dataset.theme = preview.get('theme') || 'dark';
            if (preview.get('view') === 'add') plus('Discord').click();
            if (preview.get('view') === 'loading') { invalidateIpRoutes(); ipFixture.delay = true; openMove({ipComments: ['Discord'], groups: [], from: ''}); }
            if (preview.get('view') === 'error') { ipFixture.fail = true; await loadIpRoutes(); }
        }
        document.body.dataset.testStatus = 'passed';
        document.body.dataset.testCount = String(report.length);
    } catch (error) {
        document.body.dataset.testStatus = 'failed';
        report.push(error.stack);
    }
    const output = document.createElement('pre');
    output.id = 'test-report'; output.textContent = report.join('\n');
    output.hidden = new URLSearchParams(location.search).has('preview');
    document.body.append(output);
});
