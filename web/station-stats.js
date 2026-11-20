const stationLineSides = {
  either: '进站或离站',
  incoming: '从该线进站',
  outgoing: '从该线离站'
};

function stationStatsSelection(trains, attribution, state) {
  if (!state.line) return {trains, unknown: 0};
  const visits = new Map(attribution.visits.map(visit => [`${visit.id}:${visit.sequence}`, visit]));
  const confirmed = [];
  let unknown = 0;
  for (const train of trains) {
    const visit = visits.get(`${train.id}:${train.sequence}`);
    const sides = [];
    if (state.side !== 'outgoing' && train.kind !== 'origin') sides.push(visit?.incomingId);
    if (state.side !== 'incoming' && train.kind !== 'terminal') sides.push(visit?.outgoingId);
    if (sides.includes(state.line)) confirmed.push(train);
    else if (sides.some(side => !side)) unknown++;
  }
  return {trains: confirmed, unknown};
}

function stationStatsGroup(trains, key) {
  const groups = new Map();
  for (const train of trains) {
    const label = key(train);
    if (label === null) continue;
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(train);
  }
  return groups;
}

function stationStatsTop(trains, key) {
  return [...stationStatsGroup(trains, key)]
    .sort(([a, rowsA], [b, rowsB]) => rowsB.length - rowsA.length || a.localeCompare(b, 'zh'))
    .slice(0, 8);
}

function stationStatsCard(parent, title, detail) {
  const card = el('section', null, parent, 'stats-card');
  el('h3', title, card);
  if (detail) el('p', detail, card, 'stats-description');
  return card;
}

function stationStatsBars(parent, title, detail, groups, drill) {
  const card = stationStatsCard(parent, title, detail);
  const max = Math.max(0, ...groups.map(([, rows]) => rows.length));
  if (!max) {
    el('p', '没有符合条件的记录。', card, 'stats-description');
    return;
  }
  for (const [label, rows] of groups) {
    const button = action(card, '', () => drill(`${title} · ${label}`, rows), 'stats-bar-row');
    button.disabled = !rows.length;
    el('span', label, button, 'stats-bar-label');
    const track = el('span', null, button, 'stats-track');
    el('span', null, track, 'stats-bar').style.width = `${100 * rows.length / max}%`;
    el('strong', String(rows.length), button, 'stats-value');
  }
}

function stationStatsHourly(parent, trains, drill) {
  const series = [
    {kind:'through', label:'经停'},
    {kind:'origin', label:'始发'},
    {kind:'terminal', label:'终到'}
  ];
  const hours = Array.from({length:24}, () => ({through:[], origin:[], terminal:[]}));
  let max = 0;
  for (const train of trains) {
    const seconds = timetableSeconds(train.kind === 'origin' ? train.departure : train.arrival);
    if (seconds === null) continue;
    const rows = hours[Math.floor(seconds / 3600) % 24][train.kind];
    if (rows) max = Math.max(max, rows.push(train));
  }
  const card = stationStatsCard(parent, '24 小时到发分布', '按本站计划时刻统计。经停和终到按到站小时，始发按发车小时，每次停靠只计一次。');
  if (!max) {
    el('p', '没有可统计的到发时刻。', card, 'stats-description');
    return;
  }
  const legend = el('div', null, card, 'stats-hour-legend');
  for (const {kind,label} of series) el('span', label, legend, 'stats-kind-' + kind);
  el('span', `单柱最高 ${max} 次`, legend, 'stats-hour-max');

  const grid = el('div', null, card, 'stats-hour-grid');
  for (let i = 0; i < 24; i++) {
    const groups = hours[i];
    const time = String(i).padStart(2, '0');
    const counts = series.map(({kind,label}) => label + ' ' + groups[kind].length);
    const label = [time + ':00–' + time + ':59', ...counts].join(' · ');
    const rows = series.flatMap(({kind}) => groups[kind]);
    const button = action(grid, '', () => drill(label, rows), 'stats-hour');
    button.disabled = !rows.length;
    button.title = label;
    button.setAttribute('aria-label', label);
    const bars = el('span', null, button, 'stats-hour-bars');
    bars.setAttribute('aria-hidden', 'true');
    for (const {kind} of series) {
      el('span', null, bars, 'stats-kind-' + kind).style.height = `${100 * groups[kind].length / max}%`;
    }
    el('span', time, button);
  }
}

function stationStatsDonut(parent, trains, drill) {
  const card = stationStatsCard(parent, '本站停靠类型');
  const groups = stationStatsGroup(trains, train => train.kind);
  const layout = el('div', null, card, 'stats-donut-layout');
  const ring = el('div', null, layout, 'stats-donut');
  ring.setAttribute('aria-hidden', 'true');
  const center = el('span', null, ring);
  el('strong', String(trains.length), center);
  el('small', '停靠记录', center);
  const legend = el('div', null, layout, 'stats-donut-legend');
  const segments = [];
  let total = 0;
  for (const [kind, label] of Object.entries(stationCallLabels)) {
    const rows = groups.get(kind) ?? [];
    const start = 100 * total / trains.length;
    total += rows.length;
    const end = 100 * total / trains.length;
    if (rows.length) segments.push(`var(--stats-${kind}) ${start}% ${end}%`);
    const button = action(legend, '', () => drill(label, rows), `stats-donut-key stats-kind-${kind}`);
    button.disabled = !rows.length;
    el('span', label, button);
    el('strong', String(rows.length), button);
    el('small', `${(100 * rows.length / trains.length).toFixed(1)}%`, button);
  }
  if (segments.length) ring.style.background = `conic-gradient(${segments.join(', ')})`;
}

function stationStatsDwell(train) {
  if (train.kind !== 'through') return null;
  const arrival = timetableSeconds(train.arrival);
  const departure = timetableSeconds(train.departure);
  if (arrival === null || departure === null || departure < arrival) return null;
  const minutes = Math.floor((departure - arrival) / 60);
  if (minutes < 1) return '不足 1 分钟';
  if (minutes <= 2) return '1–2 分钟';
  if (minutes <= 5) return '3–5 分钟';
  if (minutes <= 10) return '6–10 分钟';
  return '11 分钟及以上';
}

function renderStationStats(station, body, state, drill) {
  const filters = el('div', null, body, 'stats-filters');
  function select(label, choices, current) {
    const field = el('label', label, filters);
    const input = el('select', null, field);
    for (const [value, name] of choices) el('option', name, input).value = value;
    input.value = current;
    return input;
  }
  const line = select('线路', [['', '全部线路']], '');
  line.disabled = true;
  const side = select('进出方向', Object.entries(stationLineSides), state.side);
  side.disabled = true;
  const summary = el('div', null, body, 'stats-summary');
  summary.setAttribute('role', 'status');
  const charts = el('div', null, body, 'stats-charts');
  let attribution = null;

  function redraw() {
    const {trains, unknown} = stationStatsSelection(station.trains, attribution, state);
    charts.replaceChildren();
    summary.replaceChildren();
    el('strong', state.line ? `${trains.length} 条停靠记录可归入该线` : `${trains.length} 条停靠记录`, summary);
    if (state.line) el('span', `${unknown} 条停靠记录的线路待确认，未计入下方图表。`, summary);
    el('span', '按时刻表快照统计，含始发、经停和终到。', summary);
    if (!trains.length) {
      el('p', state.line ? '该线路和方向暂无已确认的停靠记录。' : '快照中没有本站停靠记录。', charts, 'meta');
      return;
    }
    const outgoing = trains.filter(train => train.kind !== 'terminal');
    const incoming = trains.filter(train => train.kind !== 'origin');
    stationStatsBars(charts, '目的地分布', '从本站出发的列车最常开往的 8 个终到站。', stationStatsTop(outgoing, train => train.destination), drill);
    stationStatsBars(charts, '来源分布', '到达本站的列车最常来自的 8 个始发站。', stationStatsTop(incoming, train => train.origin), drill);
    stationStatsHourly(charts, trains, drill);
    stationStatsDonut(charts, trains, drill);
    stationStatsBars(charts, '始发 → 终到组合前 8', '按列车全程的起终点统计。', stationStatsTop(trains, train => `${train.origin} → ${train.destination}`), drill);
    const dwell = stationStatsGroup(trains, stationStatsDwell);
    const buckets = ['不足 1 分钟', '1–2 分钟', '3–5 分钟', '6–10 分钟', '11 分钟及以上'];
    const count = [...dwell.values()].reduce((sum, rows) => sum + rows.length, 0);
    stationStatsBars(charts, '经停车停留时长', `按计划到发时间差统计，共 ${count} 条有效记录。`,
      buckets.map(label => [label, dwell.get(label) ?? []]), drill);
  }

  line.addEventListener('change', () => {
    state.line = line.value;
    state.lineName = line.selectedOptions[0].textContent;
    side.disabled = !state.line;
    redraw();
  });
  side.addEventListener('change', () => {
    state.side = side.value;
    redraw();
  });

  if (!state.line) redraw();
  const status = el('p', '正在读取线路归属…', filters, 'stats-filter-status');
  state.request ??= get('/api/station-lines/' + encodeURIComponent(station.id)).catch(error => {
    state.request = null;
    throw error;
  });
  state.request.then(data => {
    if (!filters.isConnected) return;
    attribution = data;
    for (const option of data.options) el('option', option.name, line).value = option.id;
    line.disabled = !data.lines.length;
    line.value = state.line;
    side.disabled = !state.line;
    if (data.lines.length) status.remove();
    else status.textContent = '快照中没有可筛选的线路。';
    if (state.line) redraw();
  }).catch(() => {
    if (!filters.isConnected) return;
    status.textContent = '暂时无法读取线路归属。';
    action(status, '重试', () => {
      body.replaceChildren();
      renderStationStats(station, body, state, drill);
    }, 'stats-retry');
  });
}
