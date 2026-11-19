const catalogViews = {
  lines: {
    title: '所有线路', search: '搜索线路名', open: id => showLine(id, '', null, true), sort: 'name', order: 'asc',
    tags: [['high', '高速'], ['fast', '快速'], ['regular', '普速'], ['mixed', '混合'], ['link', '联络线'], ['ferry', '铁路渡轮'], ['unknown', '类型未知']],
    columns: [
      ['name', '线路'], ['type', '类型'], ['origin', '起点'], ['destination', '终点'],
      ['stations', '沿线站点', true], ['length', '里程跨度', true], ['speed', '设计时速', true],
      ['tracks', '股道', true], ['electric', '电气化']
    ],
    note: '里程优先用已核实的全线长度，其余取首末里程差。分段时速和股道按最大值排序。'
  },
  trains: {
    title: '所有车次', search: '搜索车次号，如 G12', open: id => showTrip(id, undefined, 'catalog'), sort: 'name', order: 'asc',
    tags: [...'GDCSZTKY'].map(prefix => [prefix, prefix]).concat([['numeric', '纯数字']]),
    columns: [
      ['name', '车次'], ['origin', '始发站'], ['destination', '终到站'], ['departure', '始发时间', true],
      ['arrival', '终到时间', true], ['duration', '全程耗时', true], ['stops', '停靠站', true],
      ['distanceKm', '里程', true], ['averageKmh', '表速', true], ['peakKmh', '最高速度', true]
    ],
    note: '按推演径路估算。表速 = 全程里程 ÷ 全程耗时（含停站）。最高速度取全程推演峰值。'
  },
  stations: {
    title: '所有车站', search: '搜索站名，如 石家', open: id => showStation(id, true), sort: 'total', order: 'desc',
    tags: [['origin', '有始发'], ['through', '有经停'], ['terminal', '有终到']],
    columns: [
      ['name', '车站'], ['total', '停靠总数', true], ['origin', '始发', true], ['through', '经停', true],
      ['terminal', '终到', true], ['lines', '接入线路', true], ['code', '电报码 / 站点代码']
    ],
    note: '停靠总数按本地时刻表快照统计。接入线路来自本地线路资料。'
  }
};

function setupCatalog() {
  const dialog = document.querySelector('#catalog');
  const search = dialog.querySelector('.catalog-search');
  const filters = dialog.querySelector('.catalog-filters');
  const scroll = dialog.querySelector('.catalog-scroll');
  const head = dialog.querySelector('thead');
  const body = dialog.querySelector('tbody');
  const empty = dialog.querySelector('.catalog-empty');
  const count = dialog.querySelector('.catalog-count');
  const page = dialog.querySelector('.catalog-page');
  const pagination = [...dialog.querySelectorAll('[data-step]')];
  const dock = [...document.querySelectorAll('[data-catalog]')];
  const number = new Intl.NumberFormat('zh-CN');
  const metric = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 1});
  const states = Object.fromEntries(Object.entries(catalogViews).map(([key, view]) => [key, {
    q: '', tags: new Set(), multi: false, sort: view.sort, order: view.order, page: 1, scroll: 0
  }]));
  const typeNames = Object.fromEntries(catalogViews.lines.tags);
  const tabs = {};
  const icons = {lines: '━', trains: '▤', stations: '◆'};
  let active;
  let controller;
  let timer;

  for (const button of dock) {
    const kind = button.dataset.catalog;
    const icon = el('span', icons[kind], null, 'catalog-icon');
    icon.setAttribute('aria-hidden', 'true');
    button.prepend(icon);
    button.onclick = () => open(kind);
    tabs[kind] = action(dialog.querySelector('.catalog-tabs'), catalogViews[kind].title, () => open(kind));
  }

  function stopRequest() {
    clearTimeout(timer);
    controller?.abort();
  }

  function close() {
    stopRequest();
    states[active].scroll = scroll.scrollTop;
    dialog.close();
  }

  dialog.querySelector('.catalog-close').onclick = close;
  dialog.addEventListener('cancel', event => {
    event.preventDefault();
    close();
  });
  dialog.addEventListener('click', event => {
    if (event.target === dialog) close();
  });

  async function openDetail(kind, item) {
    close();
    const pending = catalogViews[kind].open(item.id);
    const version = panelVersion;
    await pending;
    if (panelVersion !== version || dialog.open) return;
    const back = action(panel, `← 返回${catalogViews[kind].title}`, () => open(kind), 'catalog-back');
    panel.prepend(back);
    panel.scrollTop = 0;
  }

  function open(kind) {
    if (dialog.open) states[active].scroll = scroll.scrollTop;
    active = kind;
    dialog.dataset.view = kind;
    const view = catalogViews[kind];
    const state = states[kind];
    search.value = state.q;
    search.placeholder = view.search;
    search.setAttribute('aria-label', view.search);
    for (const [key, tab] of Object.entries(tabs)) {
      tab.setAttribute('aria-pressed', String(key === kind));
    }

    filters.replaceChildren();
    filters.hidden = false;
    for (const [tag, label] of view.tags) {
      const button = action(filters, label, () => {
        if (state.tags.has(tag)) state.tags.delete(tag);
        else state.tags.add(tag);
        button.setAttribute('aria-pressed', String(state.tags.has(tag)));
        firstPage();
      }, `catalog-chip catalog-tone-${tag}`);
      button.setAttribute('aria-pressed', String(state.tags.has(tag)));
    }
    if (kind === 'stations') {
      const multi = action(filters, '多线接入', () => {
        state.multi = !state.multi;
        multi.setAttribute('aria-pressed', String(state.multi));
        firstPage();
      }, 'catalog-chip catalog-multi');
      multi.setAttribute('aria-pressed', String(state.multi));
    }

    head.replaceChildren();
    const headers = el('tr', null, head);
    for (const [field, label, numeric] of view.columns) {
      const th = el('th', null, headers, numeric ? 'catalog-number' : '');
      th.scope = 'col';
      th.dataset.field = field;
      const button = action(th, label, () => {
        state.order = state.sort === field ? (state.order === 'asc' ? 'desc' : 'asc') : (numeric ? 'desc' : 'asc');
        state.sort = field;
        setSortHeader();
        firstPage();
      });
      el('span', '↕', button, 'catalog-sort-arrow').setAttribute('aria-hidden', 'true');
    }
    setSortHeader();
    if (!dialog.open) {
      dialog.showModal();
      dialog.querySelector('.catalog-close').focus();
    }
    load();
  }

  function setSortHeader() {
    const state = states[active];
    for (const th of head.querySelectorAll('th')) {
      const sorted = state.sort === th.dataset.field;
      if (sorted) th.setAttribute('aria-sort', state.order === 'asc' ? 'ascending' : 'descending');
      else th.removeAttribute('aria-sort');
      th.querySelector('span').textContent = sorted ? (state.order === 'asc' ? '↑' : '↓') : '↕';
    }
  }

  function firstPage(delay = 0) {
    Object.assign(states[active], {page: 1, scroll: 0});
    schedule(delay);
  }

  function schedule(delay = 0) {
    stopRequest();
    scroll.setAttribute('aria-busy', 'true');
    body.inert = true;
    empty.hidden = true;
    count.replaceChildren(loadingIndicator());
    for (const button of pagination) button.disabled = true;
    if (delay) timer = setTimeout(load, delay);
    else load();
  }

  search.oninput = () => {
    states[active].q = search.value;
    firstPage(160);
  };

  for (const button of pagination) {
    button.onclick = () => {
      states[active].page += Number(button.dataset.step);
      states[active].scroll = 0;
      schedule();
    };
  }

  function renderCell(cell, item, field, kind) {
    const value = item[field];
    if (field === 'name') {
      const button = action(cell, value, () => openDetail(kind, item), 'catalog-row-link');
      if (kind === 'trains') button.classList.add(`catalog-tone-${item.prefix}`);
      if (kind === 'lines' && item.id.startsWith('map:')) el('small', ' 底图轨道', cell);
      if (item.aliases?.length) button.title = item.aliases.join('、');
      return;
    }
    if (field === 'type') {
      el('span', typeNames[value], cell, `catalog-type catalog-tone-${value}`);
      return;
    }
    if (value === null || value === '') {
      el('span', '—', cell, 'catalog-missing');
      return;
    }
    if (field === 'distanceKm') cell.textContent = `${metric.format(value)} km`;
    else if (field === 'averageKmh' || field === 'peakKmh') cell.textContent = `${metric.format(value)} km/h`;
    else if (field === 'length') cell.textContent = `${number.format(value)} km`;
    else if (field === 'duration') cell.textContent = formatDuration(value);
    else if (field === 'arrival' || field === 'departure') cell.textContent = showTime(value);
    else cell.textContent = typeof value === 'number' ? number.format(value) : value;
    if (field === 'peakKmh' && item.peakSection) cell.title = '推演峰值 · '+item.peakSection.join(' → ');
  }

  async function load() {
    stopRequest();
    const kind = active;
    const state = states[kind];
    const view = catalogViews[kind];
    const request = new AbortController();
    controller = request;
    const params = new URLSearchParams({q: state.q, sort: state.sort, order: state.order, page: state.page});
    for (const tag of state.tags) params.append('tag', tag);
    if (state.multi) params.set('multi', '1');

    scroll.setAttribute('aria-busy', 'true');
    body.inert = true;
    empty.hidden = true;
    count.replaceChildren(loadingIndicator());
    for (const button of pagination) button.disabled = true;
    try {
      const data = await get(`/api/catalog/${kind}?${params}`, {signal: request.signal});
      if (request.signal.aborted) return;
      body.replaceChildren();
      for (const item of data.rows) {
        const row = el('tr', null, body);
        for (const [field, , numeric] of view.columns) {
          renderCell(el('td', null, row, numeric ? 'catalog-number' : ''), item, field, kind);
        }
      }
      empty.hidden = data.total > 0;
      empty.textContent = '没有匹配的结果。';
      state.page = data.page;
      page.textContent = `${number.format(data.page)} / ${number.format(data.pages)}`;
      count.textContent = `共 ${number.format(data.total)} 条 · ${view.note}`;
      pagination[0].disabled = data.page <= 1;
      pagination[1].disabled = data.page >= data.pages;
      scroll.scrollTop = state.scroll;
    } catch (error) {
      if (request.signal.aborted) return;
      body.replaceChildren();
      empty.hidden = false;
      empty.textContent = '本地索引读取失败。';
      action(empty, '重试', load);
      page.textContent = '';
      count.textContent = '';
    } finally {
      if (!request.signal.aborted) {
        scroll.setAttribute('aria-busy', 'false');
        body.inert = false;
      }
    }
  }
}

setupCatalog();
