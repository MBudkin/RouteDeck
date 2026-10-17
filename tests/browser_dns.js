/* Проверки размеров контролов и прокрутки. Все обращения к API изолированы от роутера. */
window.dnsTestRequests = [];
window.fetch = async (url, opts = {}) => {
    let body = {success: true};
    if (url === '/api/router/settings') body.settings = {has_password: false};
    else if (url === '/api/sites') body.sites = [];
    else if (url === '/api/router/overview') Object.assign(body, {system: {model: 'Тестовый роутер', release: '5.0'}, interfaces: [],
        groups: state.groups, limit: 300, default_interface: ''});
    else if (url === '/api/router/groups/merge') {
        const payload = JSON.parse(opts.body);
        window.dnsTestRequests.push({url, payload});
        state.groups = state.groups.filter((g) => !payload.sources.includes(g.name));
        body.message = 'Объединено';
    }
    else if (opts.method === 'DELETE' && url.endsWith('/entries')) {
        const payload = JSON.parse(opts.body);
        window.dnsTestRequests.push({url, payload});
        if (window.dnsTestDelay) await new Promise((resolve) => { window.dnsTestResolve = resolve; });
        if (window.dnsTestFail) return {ok: false, json: async () => ({success: false, error: 'Ошибка тестового роутера'})};
        const name = decodeURIComponent(url.split('/')[4]);
        const group = state.groups.find((g) => g.name === name);
        body.group = {...group, entries: [...group.entries]};
    }
    return {ok: true, json: async () => body};
};

window.addEventListener('load', async () => {
    const report = [];
    let failures = 0;
    const check = (condition, name) => { report.push(`${condition ? 'OK' : 'FAIL'} ${name}`); if (!condition) failures++; };
    const tick = () => new Promise((resolve) => setTimeout(resolve, 20));
    const route = (index, auto = true, reject = true) => ({index, enabled: true, auto, reject,
        interface: 'Wireguard0', interface_up: true,
        interface_title: 'Очень длинное название VPN подключения · Wireguard для всех приложений'});
    const group = (index, count = 90) => ({name: `domain-list${index}`, title: `Список ${index}`, description: '',
        entries: Array.from({length: count}, (_, n) => `domain-${index}-${String(n).padStart(3, '0')}.example.org`),
        count, limit: 300, routes: [route(index)]});
    const card = (name) => $(`.group[data-name="${name}"]`);
    const removeButton = (name, entry) => $(`[data-action="entry-remove"][data-entry="${entry}"]`, card(name));
    try {
        await tick();
        $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-dns'));
        state.settings = {has_password: true};
        state.connected = true;
        state.groups = [group(1, 8), group(2, 8)];
        state.groups[1].routes = [route(2, false, false), {...route(3, true, false), interface_title: 'VPN'}];
        const box = $('#groups');
        for (const width of [320, 380, 430, 620, 780, 1180]) {
            box.style.width = `${width}px`;
            renderGroups();
            await tick();
            let inside = true, separate = true;
            for (const row of $$('.route', box)) {
                const bounds = row.getBoundingClientRect();
                const controls = $$('button, label.switch', row).map((el) => el.getBoundingClientRect());
                inside &&= controls.every((r) => r.left >= bounds.left && r.right <= bounds.right && r.top >= bounds.top && r.bottom <= bounds.bottom);
                for (let a = 0; a < controls.length; a++) for (let b = a + 1; b < controls.length; b++) {
                    const x = controls[a], y = controls[b];
                    separate &&= !(x.left < y.right - 1 && x.right > y.left + 1 && x.top < y.bottom - 1 && x.bottom > y.top + 1);
                }
                const trash = $('[data-action="route-delete"]', row);
                const rect = trash.getBoundingClientRect();
                inside &&= rect.width >= 28 && rect.height >= 28;
            }
            check(inside, `Все кнопки правила доступны внутри строки при ширине ${width}px`);
            check(separate, `Название и кнопки не перекрываются при ширине ${width}px`);
        }

        box.style.width = '1180px';
        state.groups = Array.from({length: 15}, (_, n) => group(n + 1));
        renderGroups();
        await tick();
        const target = state.groups[7];
        let targetCard = card(target.name);
        let chips = $('.chips', targetCard);
        window.scrollTo(0, targetCard.offsetTop - 140);
        chips.scrollTop = 430;
        const entry = target.entries[14];
        const draft = $('.group-add input', targetCard);
        draft.value = 'unfinished.example.net';
        const beforeY = window.scrollY, beforeInner = chips.scrollTop;
        const beforePositions = new Map($$('.group', box).map((c) => [c.dataset.name, {node: c, parent: c.parentElement}]));
        check(beforeY > 200 && beforeInner > 0, 'Перед удалением страница и список доменов прокручены');
        window.dnsTestDelay = true;
        removeButton(target.name, entry).click();
        await tick();
        check(!target.entries.includes(entry) && !removeButton(target.name, entry), 'Клик удаляет нужный домен до ответа API');
        check(Math.abs(window.scrollY - beforeY) <= 1, 'Удаление сохраняет прокрутку страницы');
        check(Math.abs($('.chips', card(target.name)).scrollTop - beforeInner) <= 1, 'Удаление сохраняет прокрутку внутри списка доменов');
        check([...beforePositions].every(([name, old]) => card(name) === old.node && card(name).parentElement === old.parent), 'Карточки остаются в прежних колонках и сохраняют DOM');
        check($('.group-add input', card(target.name)).value === 'unfinished.example.net', 'Удаление сохраняет незавершённый ввод домена');
        window.dnsTestResolve();
        await tick();
        window.dnsTestDelay = false;
        check(Math.abs(window.scrollY - beforeY) <= 1 && Math.abs($('.chips', card(target.name)).scrollTop - beforeInner) <= 1,
              'Ответ API не меняет прокрутку');
        check(window.dnsTestRequests[0].payload.entries[0] === entry, 'API получает удаляемый домен');

        chips = $('.chips', card(target.name));
        const failedEntry = target.entries[14];
        const failY = window.scrollY, failInner = chips.scrollTop;
        window.dnsTestFail = true;
        await removeEntry(target.name, failedEntry);
        check(target.entries.includes(failedEntry) && !!removeButton(target.name, failedEntry), 'При ошибке роутера домен восстанавливается');
        check(Math.abs(window.scrollY - failY) <= 1 && Math.abs($('.chips', card(target.name)).scrollTop - failInner) <= 1,
              'Откат после ошибки сохраняет обе прокрутки');
        window.dnsTestFail = false;

        // Раскрытый список превышает высоту окна: удаление не должно сворачивать его.
        state.expanded.add(target.name);
        renderGroups();
        targetCard = card(target.name);
        const expandedEntry = target.entries[45];
        removeButton(target.name, expandedEntry).scrollIntoView({block: 'center'});
        const expandedY = window.scrollY;
        await removeEntry(target.name, expandedEntry);
        check(card(target.name).classList.contains('expanded') && Math.abs(window.scrollY - expandedY) <= 1,
              'Раскрытый список остаётся раскрытым и сохраняет положение страницы');

        // Удаление последней записи переключает пустое состояние без скачка страницы.
        target.entries = ['last.example.net']; target.count = 1;
        state.expanded.delete(target.name);
        renderGroups();
        window.scrollTo(0, card(target.name).offsetTop - 140);
        const lastY = window.scrollY;
        await removeEntry(target.name, 'last.example.net');
        check(!!$('.chips-empty', card(target.name)) && Math.abs(window.scrollY - lastY) <= 1,
              'Удаление последнего домена показывает пустой список без скачка страницы');

        // Вид «списком» и объединение списков.
        state.groups = [group(1, 6), group(2, 4), group(3, 3)];
        state.expanded.clear();
        box.style.width = '1180px';
        actions['dns-view']({dataset: {view: 'list'}});
        const row = (name) => $(`.glist-row[data-name="${name}"]`, box);
        check(!!$('.glist', box) && $$('.glist-row', box).length === 3 && !$('.group', box) && storage('s4r-dns-view') === 'list', 'Списки отображаются строками');
        check(!$('.chips', row('domain-list1')), 'Строка по умолчанию свёрнута');
        $('[data-action="group-row-toggle"]', row('domain-list1')).click();
        check($$('.chip', row('domain-list1')).length === 6 && !!$('.route', row('domain-list1')), 'Строка раскрывается и показывает правила и записи');
        $('.group-add input', row('domain-list1')).value = 'draft.example.com';
        renderGroups();
        check($('.group-add input', row('domain-list1')).value === 'draft.example.com', 'Обновление сохраняет незавершённый ввод в строке');
        for (const name of ['domain-list1', 'domain-list2']) {
            const box2 = $('[data-action="group-select"]', row(name));
            box2.checked = true;
            box2.dispatchEvent(new Event('change', {bubbles: true}));
        }
        check(!$('#dns-bulk').hidden && $('#dns-bulk-count').textContent.includes('2') && !$('[data-action="dns-merge"]').disabled,
              'Выбранные списки показывают панель объединения');
        $('[data-action="dns-merge"]').click();
        const mergeForm = $('#dns-merge-form');
        check(!!mergeForm && $$('input[name=target]', mergeForm).length === 2 && $('.notice b', mergeForm).textContent === '10',
              'Окно объединения показывает выбранные списки и итоговое число записей');
        mergeForm.querySelector('input[value="domain-list2"]').click();
        check(mergeForm.title.value === 'Список 2', 'Название берётся из выбранного основного списка');
        mergeForm.requestSubmit();
        for (let n = 0; n < 25 && $('#dns-merge-form'); n++) await tick();
        const mergeCall = window.dnsTestRequests.find((r) => r.url === '/api/router/groups/merge');
        check(mergeCall?.payload.target === 'domain-list2' && JSON.stringify(mergeCall.payload.sources) === '["domain-list1"]'
              && !state.groupSelected.size && $$('.glist-row', box).length === 2, 'Объединение отправляет основной и удаляемые списки');
        actions['dns-view']({dataset: {view: 'cards'}});
        check(!!$('.group', box) && !$('.glist', box) && storage('s4r-dns-view') === 'cards', 'Можно вернуться к карточкам');

        const params = new URLSearchParams(location.search);
        if (params.has('preview')) {
            state.groups = [group(1, 10), group(2, 14), group(3, 10)];
            state.groups[0].title = 'Рабочие приложения';
            state.groups[1].title = 'Видео и музыка';
            state.groups[2].title = 'Без меток правила';
            state.groups[2].routes = [route(3, false, false)];
            box.style.width = '';
            renderGroups();
            $('#toasts').innerHTML = '';
            document.documentElement.dataset.theme = params.get('theme') || 'dark';
            window.scrollTo(0, 0);
        }
    } catch (error) {
        failures++;
        report.push(`ERROR ${error.stack}`);
    }
    document.body.dataset.testStatus = failures ? 'failed' : 'passed';
    document.body.dataset.testCount = String(report.length);
    const output = document.createElement('pre');
    output.id = 'test-report';
    output.textContent = report.join('\n');
    if (new URLSearchParams(location.search).has('preview')) output.hidden = true;
    document.body.append(output);
});
