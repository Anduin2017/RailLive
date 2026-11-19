'use strict';

const liveTimeline = (() => {
  const clock = document.querySelector('#live-clock');
  const toggle=document.querySelector('#live-toggle');
  function setClockVisible(visible) {
    clock.hidden=!visible;
    toggle.setAttribute('aria-expanded',String(visible));
  }
  toggle.onclick=()=>setClockVisible(clock.hidden);
  document.querySelector('#live-minimize').onclick=()=>{
    setClockVisible(false);
    toggle.focus({preventScroll:true});
  };
  const liveButton = document.querySelector('#live-now');
  const dateInput = document.querySelector('#live-date');
  const timeInput = document.querySelector('#live-time');
  const slider = document.querySelector('#live-range');
  const speedInput = document.querySelector('#live-speed');
  const directionButtons = [...clock.querySelectorAll('[data-rate]')];
  const day = 86400000;
  const beijingOffset = 8 * 3600000;
  let base = Date.now();
  let origin = performance.now();
  let direction = 1;
  let resumeDirection = 1;
  let speed = 1;
  let live = true;
  let card;
  let board;
  let animationFrame, previousFrame, selectedFrame=0, panelSecond, panelUpdated=0;

  // Elapsed monotonic time drives the clock, even after background-tab throttling.
  function now() {
    if (live) return Date.now();
    return base + (performance.now() - origin) * direction * speed;
  }

  function setClock(time, rate, followNow = false) {
    live = followNow;
    liveButton.setAttribute('aria-pressed', String(live));
    base = time;
    origin = performance.now();
    direction = rate;
    if (rate !== 0) resumeDirection = rate;
    render();
  }

  function iso(time) {
    return new Date(time + beijingOffset).toISOString();
  }

  function midnight(date) {
    return Date.parse(date + 'T00:00:00+08:00');
  }

  function runStatus(run, t) {
    if (t < run.start) return '待发';
    if (t >= run.end) return '已终到';
    return '在途';
  }

  function renderRuns(t, date) {
    if (!card || !card.host.isConnected) return;
    const {host, trip, runs} = card;
    const midnightMs = midnight(date);
    const today = runs.filter(run => run.start < midnightMs + day && run.end > midnightMs);
    const active = today.filter(run => runStatus(run, t) === '在途');
    if (card.initial || (!card.selected && !card.overlapping && today.length)) {
      if (today.length === 1) card.selected = today[0].key;
      else if (active.length === 1) card.selected = active[0].key;
      else if (!card.overlapping && today.length) card.selected=(today.find(run=>t<run.start) || today.at(-1)).key;
      card.initial = false;
    }
    const selected = runs.find(run => run.key === card.selected);
    card.motion.update(selected);
    const single = selected && (!card.overlapping || today.length===0 || (today.length===1 && today[0]===selected));
    host.hidden=Boolean(single);
    if (single) { if (host.firstChild) host.replaceChildren(); card.signature=null; return; }
    const visible = selected ? [selected] : today;
    const descriptions = visible.map(run => {
      const state = runStatus(run, t);
      if (state !== '在途') return state;
      const seconds = (t - run.serviceMidnight) / 1000;
      const next = card.calls.find(stop => stop.departure > seconds);
      if (!next) return '在途';
      return next.arrival <= seconds ? '在途 · 停靠' + next.name : '在途 · 下一站' + next.name;
    });
    const signature = JSON.stringify([date, card.selected, visible.map(run => run.key), descriptions]);
    if (signature === card.signature) return;
    card.signature = signature;
    host.replaceChildren();
    el('h3', selected ? '所选班次' : '选择始发班次', host);
    if (!visible.length) {
      el('p', date + ' · 快照无覆盖此日的班次', host);
      const nearest = runs.find(run => run.start > t) || runs.at(-1);
      if (nearest) action(host, '跳到 ' + iso(nearest.start).slice(0,10) + ' 始发', () => setClock(nearest.start, 0));
      return;
    }
    for (const [index, run] of visible.entries()) {
      const daysAgo = Math.round((midnightMs - midnight(iso(run.start).slice(0,10))) / day);
      const relative = ['当日', '前一日', '前两日'][daysAgo];
      const title = trip.number + ' · ' + (relative ? relative + '始发' : '始发班次');
      const row = selected ? el('strong', title, host) : action(host, title, () => {
        if (!route.features.length) setRoute(trip);
        card.selected = run.key;
        render();
      }, 'result');
      const detail = el('small', iso(run.start).slice(0,10) + ' ' + iso(run.start).slice(11,16) +
        ' ' + trip.origin + '始发 · ' + descriptions[index], selected ? host : row);
      detail.style.display = 'block';
      row.dataset.instance = run.key;
    }
    if (selected) {
      if (t < selected.start || t >= selected.end)
        action(host, '跳到始发', () => setClock(selected.start, 0));
      action(host, '切换班次', () => {
        card.selected = null;
        render();
      });
    }
  }

  function renderPanel(t) {
    panelSecond=t;
    panelUpdated=performance.now();
    const local = iso(t);
    const date = local.slice(0,10);
    stationSymbols.setDay(date);
    const time = local.slice(11,19);
    if (document.activeElement !== dateInput) dateInput.value = date;
    if (document.activeElement !== timeInput) timeInput.value = time;
    slider.value = (t - midnight(date)) / 1000;
    slider.setAttribute('aria-valuetext', date + ' ' + time + ' 北京时间');
    for (const button of directionButtons) {
      button.setAttribute('aria-pressed', Number(button.dataset.rate) === direction);
    }
    if (board?.host.isConnected) board.update(t, date);
    else board = null;
    renderRuns(t, date);

    if (card && !card.host.isConnected) detachTrip();
  }

  // Sample the timetable curve between panel updates. Drop missed frames on
  // slow devices rather than queuing intermediate positions in the map worker.
  function animate(stamp) {
    animationFrame=null;
    if (!direction) return;
    // At a wide zoom and 1×, even a fast train takes many frames to
    // cross a pixel. Save worker/layout time until a quarter pixel can move.
    const metresPerPixel=map ? 40030228*Math.cos(map.getCenter().lat*Math.PI/180)/(512*2**map.getZoom()) : Infinity;
    const interval=Math.min(120,Math.max(1000/30,250*metresPerPixel/(100*speed)));
    const time=now(), second=Math.floor(time/1000)*1000;
    if (second!==panelSecond && stamp-panelUpdated>=100) renderPanel(second);
    else if (card && stamp-selectedFrame>=1000/60-1) {
      selectedFrame=stamp;
      card.motion.draw(false);
    }
    if (previousFrame==null || stamp-previousFrame>=interval-1) {
      previousFrame=stamp;
      regionLive.tick(time);
    }
    animationFrame=requestAnimationFrame(animate);
  }

  function render() {
    const time=now();
    renderPanel(Math.floor(time/1000)*1000);
    regionLive.tick(time,true);
    if (direction && animationFrame==null) animationFrame=requestAnimationFrame(animate);
  }

  function attachTrip(trip, motionHost, serviceDate) {
    const host = el('div', null, motionHost, 'live-runs');
    const start = timetableSeconds(trip.stops[0]?.departure);
    const end = timetableSeconds(trip.stops.at(-1)?.arrival);
    const runs = [];
    if (start !== null && end !== null && end > start) {
      for (const compact of trip.dates) {
        const serviceDate = compact.slice(0,4) + '-' + compact.slice(4,6) + '-' + compact.slice(6);
        const offset = midnight(serviceDate);
        runs.push({
          key: JSON.stringify([trip.snapshot, trip.number, trip.id, serviceDate]),
          serviceMidnight: offset,
          start: offset + start * 1000,
          end: offset + end * 1000
        });
      }
    }
    runs.sort((a,b)=>a.start-b.start);
    const overlapping=runs.some((run,i)=>i>0 && runs[i-1].end>run.start);
    const calls = trip.stops.map((stop, index) => ({
      name: stop.name, ...scheduledStopTimes(stop, index === trip.stops.length - 1)
    })).filter(stop => stop.arrival !== null && stop.departure !== null);
    detachTrip();
    const locked = runs.find(run => iso(run.serviceMidnight).slice(0,10) === serviceDate);
    card = {trip, host, runs, calls, overlapping, initial: !locked, selected: locked?.key ?? null, motion:trainMotionView(trip,motionHost,host)};
    render();
  }

  function detachTrip() {
    card?.motion.clear();
    card = null;
  }

  for (const button of directionButtons) {
    button.onclick = () => {
      const rate = Number(button.dataset.rate);
      setClock(now(), rate, live && rate === 1);
    };
  }
  document.addEventListener('keydown', event => {
    if (event.key !== ' ' || event.defaultPrevented || event.isComposing ||
        event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
    const target = event.target;
    if (!(target instanceof Element) || target.isContentEditable || target.closest('dialog')) return;
    const control = target.closest('input, textarea, select, button, a[href], summary, [role="button"], [role="textbox"]');
    if (control && control !== slider && !directionButtons.includes(control)) return;
    event.preventDefault();
    if (!event.repeat) setClock(now(), direction === 0 ? resumeDirection : 0);
  });
  speedInput.onchange = () => {
    const time = now();
    speed = Number(speedInput.value);
    setClock(time, direction);
  };
  liveButton.onclick = () => {
    speed = 1;
    speedInput.value = '1';
    setClock(Date.now(), 1, true);
  };
  for (const input of [dateInput, timeInput]) {
    input.onfocus = () => setClock(now(), 0);
    input.onchange = () => {
      const t = Date.parse(dateInput.value + 'T' + timeInput.value + '+08:00');
      if (Number.isFinite(t)) setClock(t, 0);
    };
    input.onblur = () => requestAnimationFrame(render);
  }
  // A drag keeps its starting date even if fast playback crosses midnight.
  let dragDate;
  slider.onpointerdown = () => { dragDate = dateInput.value; };
  const endDrag = () => { dragDate = null; };
  window.addEventListener('pointerup', endDrag);
  window.addEventListener('pointercancel', endDrag);
  window.addEventListener('blur', endDrag);
  slider.oninput = () => setClock(midnight(dragDate || dateInput.value) + Number(slider.value) * 1000, direction);
  new ResizeObserver(() => {
    document.body.style.setProperty('--live-height', clock.offsetHeight + 'px');
  }).observe(clock);
  document.addEventListener('DOMContentLoaded', () => {
    render();

  }, {once:true});
  return {
    attachTrip, detachTrip, iso, midnight, time: now,
    get trainCamera() { return card?.motion; },
    refreshPausedMap() { if (!direction) render(); },
    clearTripSelection() {
      if (!card) return;
      detachTrip();
      section('已清除所选车次');
      render();
    },
    get playbackRate() { return direction*speed; },
    get playing() { return direction !== 0; },
    jump(time) { setClock(time, 0); },
    replay(time) { speed = 8; speedInput.value = '8'; setClock(time, 1); },
    attachBoard(host, update) {
      board = {host, update};
      render();
    }
  };
})();
