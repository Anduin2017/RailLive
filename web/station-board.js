'use strict';

function openStationBoard(station) {
  const boardWindow = el('aside', null, sidebar, 'station-board-window');
  boardWindow.id = 'station-board';
  boardWindow.setAttribute('aria-label', station.name + '到发时刻表');
  const host = el('div', null, boardWindow, 'station-live');
  if (!station.hasTimetable) {
    el('p', '本站尚未关联本地时刻表。', host, 'meta');
    return;
  }
  async function load() {
    host.textContent = '正在读取时刻表…';
    try {
      const data = await get('/api/station-calendar/' + encodeURIComponent(station.id));
      if (host.isConnected) showStationBoard(host, station, data);
    } catch {
      if (!host.isConnected) return;
      host.textContent = '时刻表读取失败。';
      action(host, '重试', load);
    }
  }
  load();
}

function showStationBoard(host, station, data) {
  const limit = 10;
  const day = 86400000;
  const calendars = new Map(Object.entries(data.calendars).map(([id, dates]) => [
    id, dates.split(',').sort().map(value =>
      liveTimeline.midnight(value.slice(0,4) + '-' + value.slice(4,6) + '-' + value.slice(6)))
  ]));
  const visits = station.trains.map(train => ({
    train, ...scheduledStopTimes(train)
  }));
  let cachedDate, lastTime;
  host.replaceChildren();
  const heading = el('div', null, host, 'board-clock');
  el('span', '北京时间 · 按时刻表推演', heading);
  const stamp = el('time', null, heading);
  const notice = el('p', null, host, 'meta');

  function lowerBound(length, before) {
    let lo = 0, hi = length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (before(mid)) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  function countdown(seconds) {
    const pad = n => String(n).padStart(2, '0');
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    return (hours ? pad(hours) + ':' : '') + pad(minutes % 60) + ':' + pad(seconds % 60);
  }

  const columns = el('div', null, host, 'board-columns');
  function queue(title, field, style) {
    const column = el('section', null, columns, 'board-column');
    el('h3', title, column);
    const list = el('div', null, column, style);
    let events = [], first, rows = [];

    function prepare(start) {
      events = [];
      first = undefined;
      for (const visit of visits) {
        const offset = visit[field];
        const bases = calendars.get(visit.train.service);
        if (offset === null || !bases) continue;
        const ms = offset * 1000;
        const i = lowerBound(bases.length, j => bases[j] + ms < start);
        // Keep today's calls and enough later service dates to fill a sparse board,
        // even after the last train of the day or across several days without service.
        let later = 0;
        for (let j = i; j < bases.length; j++) {
          const base = bases[j], at = base + ms;
          if (at >= start + day && ++later > limit) break;
          events.push({train:visit.train, arrival:visit.arrival, base, at});
        }
      }
      events.sort((a,b) => a.at-b.at || a.train.number.localeCompare(b.train.number) ||
        a.train.id.localeCompare(b.train.id) || a.train.sequence-b.train.sequence);
      events.length = Math.min(events.length, lowerBound(events.length, i => events[i].at < start + day) + limit);
    }

    function draw(t, date) {
      const index = lowerBound(events.length, i => events[i].at < t);
      if (index !== first) {
        first = index;
        const focused = list.contains(document.activeElement) ? document.activeElement.dataset.event : null;
        list.replaceChildren();
        rows = events.slice(index, index + limit).map(({train, arrival, base, at}) => {
          const row = action(list, '', () => liveTimeline.jump(at), 'board-row');
          row.dataset.event = [train.id, base, train.sequence].join('/');
          const scheduled = liveTimeline.iso(at);
          row.title = '计划' + (field === 'arrival' ? '到站' : '发车') + ' ' +
            scheduled.slice(0,10) + ' ' + scheduled.slice(11,19) + ' · 点击跳到此刻';
          const title = el('span', null, row);
          el('strong', train.number, title);
          if (field === 'departure') {
            const label = train.kind === 'origin' ? '本站始发' : '经停';
            el('span', label, title, 'board-call-kind');
          }
          const timer = el('time', null, row, 'board-countdown');
          const clockText = ms => {
            const iso = liveTimeline.iso(ms);
            return (iso.slice(0,10) === date ? '' : iso.slice(5,10) + ' ') + iso.slice(11,16);
          };
          const times = field === 'departure' && arrival !== null ? clockText(base+arrival*1000) + ' 到 · ' : '';
          el('span', times + clockText(at) + (field === 'arrival' ? ' 到' : ' 开'), row, 'board-scheduled');
          timer.title = field === 'arrival' ? '距到达' : '距出发';
          const serviceDay = liveTimeline.iso(base).slice(0,10);
          el('small', train.origin + ' → ' + train.destination +
            (serviceDay === date ? '' : ' · ' + serviceDay.slice(5) + ' 始发'), row);
          if (focused === row.dataset.event) row.focus({preventScroll:true});
          return {timer,at};
        });
        if (!rows.length) el('p', '已收录时刻中没有后续' + (field === 'arrival' ? '到站' : '发车') + '。', list, 'meta');
      }
      for (const {timer,at} of rows) {
        const seconds = Math.ceil((at-t)/1000);
        timer.textContent = countdown(seconds);
        timer.classList.toggle('imminent', seconds < 60);
      }
    }
    return {prepare,draw};
  }
  const boards = [queue('即将到达', 'arrival', 'board-arrivals'), queue('即将出发', 'departure', 'board-departures')];
  liveTimeline.attachBoard(host, (t,date) => {
    if (lastTime === t) return;
    lastTime = t;
    if (cachedDate !== date) {
      cachedDate = date;
      boards.forEach(board => board.prepare(liveTimeline.midnight(date)));
      const compact = date.replaceAll('-', '');
      notice.hidden = compact >= data.start && compact <= data.end;
      notice.textContent = notice.hidden ? '' : '当前日期在快照覆盖范围外，仅展示已收录班次。';
    }
    stamp.textContent = date + ' ' + liveTimeline.iso(t).slice(11,19);
    boards.forEach(board => board.draw(t,date));
  });
}
