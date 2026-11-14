/* IP-маршруты: запросы, ошибки и задержки воспроизводятся без настоящего роутера. */
const ipFixture = {
    interfaces: [{id: 'Wireguard0', description: 'Основной VPN', tunnel: true, up: true},
                 {id: 'Wireguard1', description: 'Рабочий VPN', tunnel: true, up: true}],
    routes: [{index: '1', destination: '8.8.8.8/32', comment: 'Discord', interface: 'Wireguard1', interface_title: 'Рабочий VPN', enabled: true, auto: true},
             {index: '2', destination: '1.1.1.0/24', comment: 'Discord', interface: 'Wireguard1', interface_title: 'Рабочий VPN', enabled: true, auto: true},
             {index: '3', destination: '9.9.9.9/32', comment: '', interface: 'Wireguard0', interface_title: 'Основной VPN', enabled: true},
             {index: '4', destination: '149.154.160.0/20', comment: 'Смешанная группа', interface: 'Wireguard0', interface_title: 'Основной VPN', enabled: true},
             {index: '5', destination: '91.108.56.0/22', comment: 'Смешанная группа', interface: 'Wireguard1', interface_title: 'Рабочий VPN', enabled: true}],
    groups: null, moveFail: false, requests: [], pending: [], delay: false, fail: false, overviewDelay: false, overviewResolve: null, overviewFail: false,
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
            groups: ipFixture.groups || [], limit: 300, default_interface: 'Wireguard0'});
    } else if (url === '/api/router/ip-routes' && options.method !== 'POST') {
        const routes = structuredClone(ipFixture.routes), fail = ipFixture.fail;
        if (ipFixture.delay) await new Promise((resolve) => ipFixture.pending.push(resolve));
        if (fail) return {ok: false, json: async () => ({success: false, error: 'Роутер временно недоступен'})};
        body.routes = routes;
    } else if (url === '/api/router/ip-routes/toggle') {
        const ids = payload.indices || [payload.index];
        ipFixture.routes.forEach((r) => { if (ids.includes(r.index)) r.enabled = payload.enabled; });
        body.message = 'Готово';
    } else if (url === '/api/router/ip-routes/move' || url === '/api/router/move') {
        if (ipFixture.moveFail) return {ok: false, json: async () => ({success: false, error: 'Перенос остановлен: подключение удалено'})};
        const ids = payload.indices || payload.ip_indices;
        ipFixture.routes.forEach((r) => { if (ids.includes(r.index)) {
            r.interface = payload.interface;
            r.interface_title = ipFixture.interfaces.find((i) => i.id === payload.interface)?.description;
        }});
        if (payload.single_dns_interface && payload.groups?.length) {
            const groups = structuredClone(ipFixture.groups || state.groups);
            for (const g of groups.filter((g) => payload.groups.includes(g.name))) {
                const target = g.routes.find((r) => r.interface === payload.interface)
                    || {...g.routes[0], index: 'dns-target', interface: payload.interface};
                g.routes = [target];
            }
            ipFixture.groups = groups;
        }
        body.message = 'Перенос проверен'; body.moved = ids.length;
    } else if (url === '/api/router/ip-routes/comment') {
        ipFixture.routes.forEach((r) => { if (payload.indices.includes(r.index)) r.comment = payload.comment; });
        body.message = `Описание обновлено у ${payload.indices.length}`;
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

        switchTab('ip');
        await ensureIpRoutes();
        // Выбор галочками: одиночное окно и общий перенос нескольких групп.
        const beforeMoves = structuredClone(ipFixture.routes);
        state.ipSelected.clear(); renderIpRoutes();
        $('[data-action="ipg-select"][data-comment="Discord"]').click();
        const checkedIds = [...state.ipSelected];
        const untouchedBefore = ipFixture.routes.filter((r) => !checkedIds.includes(r.index));
        $('[data-action="ip-move-selected"]').click();
        let moveForm = $('#ipmove-form');
        assert(!!moveForm && checkedIds.length === ipGroupRoutes('Discord').length,
               'Галочка группы передаёт в смену туннеля все её IP-маршруты');
        moveForm.to.value = 'Wireguard0'; moveForm.requestSubmit();
        for (let n = 0; n < 20 && $('#ipmove-form'); n++) await tick();
        await tick();
        const movedCall = ipFixture.requests.filter((r) => r.url === '/api/router/ip-routes/move').at(-1).payload;
        assert(JSON.stringify(movedCall.indices) === JSON.stringify(checkedIds)
               && movedCall.expected_routes.length === checkedIds.length
               && movedCall.expected_routes.every((r) => r.interface === 'Wireguard1'),
               'Перенос передаёт снимок именно отмеченных маршрутов с исходным подключением');
        assert(checkedIds.every((i) => state.ipRoutes.find((r) => r.index === i).interface === 'Wireguard0')
               && untouchedBefore.every((r) => state.ipRoutes.find((x) => x.index === r.index).interface === r.interface)
               && !state.ipSelected.size, 'После переноса адреса и чужие группы сохранены, выбор очищен');
        ipFixture.routes = structuredClone(beforeMoves); await loadIpRoutes();

        await openMove({ipComments: ['Discord', 'Смешанная группа'], from: 'Wireguard1'});
        moveForm = $('#move-form'); moveForm.to.value = 'Wireguard0';
        moveForm.dispatchEvent(new Event('change', {bubbles: true})); moveForm.requestSubmit();
        for (let n = 0; n < 20 && $('#move-form'); n++) await tick();
        await tick();
        const unifiedCall = ipFixture.requests.filter((r) => r.url === '/api/router/move').at(-1).payload;
        const fromIds = beforeMoves.filter((r) => r.interface === 'Wireguard1').map((r) => r.index).sort();
        assert(JSON.stringify([...unifiedCall.ip_indices].sort()) === JSON.stringify(fromIds)
               && unifiedCall.expected_ip_routes.every((r) => r.interface === 'Wireguard1')
               && unifiedCall.expected_dns_routes.length === 0, 'Общий перенос выбранных групп соблюдает фильтр исходного туннеля');
        ipFixture.routes = structuredClone(beforeMoves); await loadIpRoutes();

        const dnsRule = (index, iface) => ({index, group: 'single', interface: iface,
            interface_title: iface, auto: true, reject: true, enabled: true});
        ipFixture.groups = [{name: 'single', title: 'Один туннель', entries: ['example.org'], count: 1,
            routes: [dnsRule('old-one', 'Wireguard1'), dnsRule('old-two', 'LegacyVPN'), dnsRule('target', 'Wireguard0')]}];
        state.groups = structuredClone(ipFixture.groups);
        await openMove({groups: ['single'], from: 'Wireguard1'});
        moveForm = $('#move-form'); moveForm.to.value = 'Wireguard0';
        assert($('#modal-root').textContent.includes('останется только новое подключение'),
               'Форма объясняет, что у выбранного DNS-списка останется только новый туннель');
        moveForm.requestSubmit();
        for (let n = 0; n < 20 && $('#move-form'); n++) await tick();
        await tick();
        const singleCall = ipFixture.requests.filter((r) => r.url === '/api/router/move').at(-1).payload;
        assert(singleCall.single_dns_interface === true && singleCall.expected_dns_routes.length === 3,
               'Смена туннеля отправляет снимок всех привязок DNS-списка, включая третий интерфейс и существующий целевой');
        assert(state.groups[0].routes.length === 1 && state.groups[0].routes[0].interface === 'Wireguard0',
               'После смены туннеля у DNS-списка отображается одно правило с выбранным подключением');
        ipFixture.groups = null; state.groups = [];

        ipFixture.moveFail = true; openIpMove(['1']); moveForm = $('#ipmove-form');
        state.ipRoutes.find((r) => r.index === '1').comment = 'Локальное изменение';
        moveForm.to.value = 'Wireguard0'; moveForm.requestSubmit();
        for (let n = 0; n < 20 && $('#ipmove-form'); n++) await tick();
        await tick();
        const failedCall = ipFixture.requests.filter((r) => r.url === '/api/router/ip-routes/move').at(-1).payload;
        assert(failedCall.expected_routes[0].comment === 'Discord'
               && state.ipRoutes.find((r) => r.index === '1').interface === 'Wireguard1'
               && !$('#ipmove-form'), 'Снимок выбора неизменяем, после ошибки окно закрывается и маршруты перечитываются');
        ipFixture.moveFail = false;

        storage('s4r-ip-collapsed', '[]');
        renderIpRoutes();
        const collapseAll = $('#ip-collapse-all');
        const tip = (el) => el.title || el.dataset.tip;
        assert(!collapseAll.hidden && tip(collapseAll) === 'Свернуть все', 'Есть кнопка «Свернуть все» для IP-групп');
        collapseAll.click();
        assert($$('.ipg').every((g) => g.classList.contains('collapsed')) && tip(collapseAll) === 'Развернуть все', 'Все IP-группы сворачиваются одной кнопкой');
        collapseAll.click();
        assert($$('.ipg').every((g) => !g.classList.contains('collapsed')), 'Все IP-группы разворачиваются одной кнопкой');

        state.ipSelected = new Set(['1']);
        renderIpRoutes();
        assert($('#ip-merge-btn').hidden, 'Объединение недоступно для маршрутов одной группы');
        state.ipSelected = new Set(['2', '4']);
        renderIpRoutes();
        assert(!$('#ip-merge-btn').hidden, 'Объединение доступно для маршрутов из разных групп');
        $('#ip-merge-btn').click();
        const mergeForm = $('#ip-merge-form');
        assert(mergeForm && $$('input[name=pick]', mergeForm).length === 3, 'Окно объединения предлагает названия выбранных групп и своё');
        mergeForm.querySelector('input[value="new"]').checked = true;
        mergeForm.dispatchEvent(new Event('change', {bubbles: true}));
        mergeForm.comment.value = 'Общая группа';
        mergeForm.requestSubmit();
        for (let n = 0; n < 20 && $('#ip-merge-form'); n++) await tick();
        await tick();
        const merged = ipFixture.requests.filter((r) => r.url === '/api/router/ip-routes/comment').pop().payload;
        assert(JSON.stringify(merged) === JSON.stringify({indices: ['2', '4'], comment: 'Общая группа'}) && !state.ipSelected.size
               && ipGroupRoutes('Общая группа').length === 2, 'Выбранные маршруты переходят в одну группу');

        state.ipSelected.clear();
        $('#ip-search').value = 'discord';
        renderIpRoutes();
        const ipMaster = $('#ip-check-all');
        assert(!ipMaster.closest('.select-all').hidden && !ipMaster.checked, 'В IP-маршрутах есть флажок «Выбрать все»');
        ipMaster.click();
        assert(state.ipSelected.size === ipGroupRoutes('Discord').length && [...state.ipSelected].every((i) => ipGroupRoutes('Discord').some((r) => r.index === i)),
               'Флажок выбирает только маршруты, подходящие под фильтр');
        $('#ip-search').value = '';
        renderIpRoutes();
        assert(ipMaster.indeterminate && !$('#ip-select-all-link').hidden, 'Без фильтра выбор частичный, предложено выбрать все');
        $('#ip-select-all-link').click();
        assert(state.ipSelected.size === state.ipRoutes.length && ipMaster.checked && $('#ip-select-all-link').hidden, 'Ссылка выбирает все маршруты');
        assert($('[data-action="ip-enable-selected"]').disabled && !$('[data-action="ip-disable-selected"]').disabled, 'Включить недоступно, когда все выбранные маршруты включены');
        ipMaster.click();
        assert(!state.ipSelected.size && $('#ip-bulk').hidden, 'Снятие флажка снимает выбор');

        // Повторы IP: точные повторы, адреса внутри подсетей и один адрес через разные подключения.
        const savedRoutes = ipFixture.routes;
        const r = (index, destination, comment, iface, extra = {}) => ({index, destination, comment, interface: iface,
            interface_title: ipFixture.interfaces.find((i) => i.id === iface).description, enabled: true, auto: true, ...extra});
        ipFixture.routes = [
            r('11', '91.108.4.0/22', 'Telegram', 'Wireguard0'),
            r('12', '91.108.4.10/32', 'Telegram', 'Wireguard0'),        // внутри /22 с тем же VPN - лишний
            r('13', '91.108.4.20/32', 'Discord', 'Wireguard1'),         // внутри /22, но через другой VPN - нужен
            r('14', '149.154.160.0/20', 'Telegram', 'Wireguard0'),
            r('15', '149.154.160.0/20', 'Старые', 'Wireguard1'),        // тот же адрес через другой VPN - конфликт
            r('16', '5.5.5.5/32', '', 'Wireguard0'),                    // точный повтор 17 без описания
            r('17', '5.5.5.5/32', 'Discord', 'Wireguard0'),
            r('18', '91.108.5.30/32', 'Telegram', 'Wireguard0'),        // ближайшая подсеть /24 идёт через другой VPN
            r('19', '91.108.5.0/24', 'Discord', 'Wireguard1'),
            r('20', '8.8.8.0/24', 'Telegram', 'Wireguard0', {enabled: false}),
            r('21', '8.8.8.8/32', 'Telegram', 'Wireguard0'),            // широкая подсеть выключена - нужен
        ];
        state.ipSelected.clear();
        invalidateIpRoutes();
        await loadIpRoutes();
        const notes = ipDuplicates();
        const kinds = Object.fromEntries([...notes].map(([i, n]) => [i, n.kind]));
        assert(JSON.stringify(kinds) === JSON.stringify({16: 'same', 14: 'conflict', 15: 'conflict', 12: 'covered'}),
               'Повторы определены точно: лишние, конфликты подключений и нужные узкие маршруты различаются');
        const dupsBtn = $('#ip-dups-btn');
        assert(!dupsBtn.hidden && dupsBtn.textContent.includes('Повторы: 3'), 'На панели IP видна кнопка с числом повторов');
        assert(!!$('[data-ip="12"] .ip-dup-mark.covered') && !!$('[data-ip="15"] .ip-dup-mark.conflict') && $('[data-ip="16"] .ip-dest').title.includes('повтор')
               && !$('[data-ip="13"] .ip-dup-mark') && !$('[data-ip="21"] .ip-dup-mark'), 'Строки маршрутов отмечены метками повторов с пояснением');
        const cleanLink = $('[data-action="ipg-clean"][data-comment="Telegram"]');
        assert(cleanLink?.textContent.includes('убрать лишние: 1') && $('.ipg[data-comment="Старые"] .ipg-meta').textContent.includes('повторов: 1'),
               'В заголовке группы есть «убрать лишние» и число конфликтов');

        dupsBtn.click();
        const dupForm = $('#ip-dups-form');
        const dupSubmit = $('#modal-root .modal-foot .danger');
        assert(dupForm && $$('.ip-dup-set', dupForm).length === 1 && $('.ip-dup-set input[value="14"]', dupForm).checked
               && $$('input[name="x"]:checked', dupForm).length === 2 && dupSubmit.textContent.includes('Удалить 3 маршрута'),
               'Окно повторов предлагает оставить маршрут основной группы и удалить лишние');
        $('.ip-dup-set input[value=""]', dupForm).click();
        assert(dupSubmit.textContent.includes('Удалить 2 маршрута'), '«Оставить все» исключает конфликт из удаления');
        $('.ip-dup-set input[value="15"]', dupForm).click();
        dupForm.requestSubmit();
        for (let n = 0; n < 20 && $('#ip-dups-form'); n++) await tick();
        await tick();
        const dropped = ipFixture.requests.filter((x) => x.url === '/api/router/ip-routes/delete').pop().payload.indices;
        assert(JSON.stringify([...dropped].sort()) === JSON.stringify(['12', '14', '16']) && !$('#ip-dups-form'),
               'Удаляются выбранный конфликтующий маршрут и лишние повторы');
        assert($('#ip-dups-btn').hidden && !ipDuplicates().size && $$('.ip-row').length === 8, 'После удаления повторов кнопка скрывается');

        ipFixture.routes.push(r('12', '91.108.4.10/32', 'Telegram', 'Wireguard0'));
        invalidateIpRoutes();
        await loadIpRoutes();
        const realConfirm = confirmDialog;
        confirmDialog = async () => true;
        $('[data-action="ipg-clean"][data-comment="Telegram"]').click();
        for (let n = 0; n < 20 && ipGroupRoutes('Telegram').some((x) => x.index === '12'); n++) await tick();
        assert(JSON.stringify(ipFixture.requests.filter((x) => x.url === '/api/router/ip-routes/delete').pop().payload.indices) === JSON.stringify(['12'])
               && !$('[data-action="ipg-clean"]'), '«Убрать лишние» в группе удаляет только её лишние маршруты');
        confirmDialog = realConfirm;

        $('[data-action="ip-new"]').click();
        const addForm = $('#ip-form');
        addForm.dest.value = '91.108.4.10\n91.108.6.1\n149.154.160.0/20\n1.2.3.4\n1.2.3.4/32';
        addForm.dest.dispatchEvent(new Event('input', {bubbles: true}));
        const addPreview = $('#ip-preview').textContent;
        assert(addPreview.includes('3 новых адреса') && addPreview.includes('уже на роутере: 1') && addPreview.includes('входят в подсети на роутере: 2'),
               'При добавлении видно, какие адреса уже есть на роутере или входят в его подсети');
        closeModal();
        ipFixture.routes = savedRoutes;
        invalidateIpRoutes();
        await loadIpRoutes();

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
