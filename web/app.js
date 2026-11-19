'use strict';

const panel = document.querySelector('#panel');
const form = document.querySelector('#search');
const query = document.querySelector('#query');
const sidebar = document.querySelector('#sidebar');
const sidebarToggle = document.querySelector('#sidebar-toggle');
const national = [[69, 18], [135, 56]];
const coordinateFit = {
  xx: 1.015972842, xy: -0.000070497, x0: -5.116568470,
  yx: 0.000009795, yy: 1.012049241, y0: 1.113596968
};
let panelVersion = 0;
let route = {type: 'FeatureCollection', features: []};
let selectedStation = null;
const emptyStation = {type:'FeatureCollection',features:[]};

function clearStationHighlight() {
  document.querySelector('#station-board')?.remove();
  if (!selectedStation) return;
  selectedStation=null;
  stationSymbols.select();
}
let map;
const followView = {line: true, station: true, trip: true, autoFollow: false};

const trainLabels = {next:true, destination:false};

// Keep captions upright and above the carriage as traffic density changes.
const liveTrainLabelPlacement = {
  'text-anchor':'bottom','text-offset':[0,-0.85],
  'text-allow-overlap':true,'text-ignore-placement':true
};

function trainLabel(number, next, destination) {
  let label=number;
  if (trainLabels.next && next) label+='\n-> '+next;
  if (trainLabels.destination && destination && destination!==next) label+='\n--> '+destination;
  return label;
}

function runLabel(run, seconds) {
  return trainLabel(run.number,run.arrivals.find(([arrival])=>arrival>seconds)?.[1],run.destination);
}

const trainClasses = {
  G: ['高速动车', '#bf2949'],
  D: ['动车', '#1769ac'],
  C: ['城际', '#008476'],
  S: ['市郊', '#59801d'],
  Z: ['直达特快', '#513580'],
  T: ['特快', '#ad5909'],
  K: ['快速', '#814b36'],
  Y: ['旅游', '#a53684']
};
const selectedTrainScale = 1.5;
const ordinaryTrainColor = '#536170';

function trainClassStyle(sprite=false) {
  const value=color=>sprite ? trainBodyIcon(color) : color;
  const expression=['match',['get','prefix']];
  for (const [prefix,[,color]] of Object.entries(trainClasses)) expression.push(prefix,value(color));
  expression.push(value(ordinaryTrainColor));
  return expression;
}

function trainBodyIcon(color) {
  const id='train-body-'+color.slice(1);
  if (map.hasImage(id)) return id;
  const canvas=document.createElement('canvas');
  const ratio=3, width=14, length=11;
  canvas.width=width*ratio;
  canvas.height=length*ratio;
  const ctx=canvas.getContext('2d');
  ctx.scale(ratio,ratio);
  ctx.beginPath();
  ctx.roundRect(0.5,0.5,width-1,length-1,2);
  ctx.fillStyle=color;
  ctx.fill();
  ctx.strokeStyle='white';
  ctx.lineWidth=1;
  ctx.stroke();
  // The sprite points north. Map rotation aligns the tips with the train's path.
  ctx.beginPath();
  ctx.moveTo(3,7.5);
  ctx.lineTo(width/2,3.2);
  ctx.lineTo(width-3,7.5);
  ctx.lineWidth=1.4;
  ctx.lineJoin='round';
  ctx.stroke();
  map.addImage(id,ctx.getImageData(0,0,canvas.width,canvas.height),{pixelRatio:ratio});
  return id;
}

function indexedTrackPoint(path,at) {
  let i=0,j=path.length-1;
  while (i+1<j) {
    const mid=(i+j)>>>1;
    if (path[mid][0]<=at) i=mid; else j=mid;
  }
  const [a,x,y]=path[i],[b,u,v]=path[j];
  const f=Math.max(0,Math.min(1,(at-a)/(b-a||1)));
  return {coord:toMapCoordinates([x+(u-x)*f,y+(v-y)*f]),i};
}

// Both the area map and the selected run sample the same offline display leg.
function displayMotion(leg,t) {
  const duration=leg.end-leg.start, elapsed=t-leg.start;
  if (elapsed<0 || elapsed>=duration) return null;
  const ramp=Math.min(60,duration/4), left=duration-elapsed;
  const length=leg.length ?? leg.path.at(-1)[0], cruise=length/(duration-ramp);
  const at=elapsed<ramp ? cruise*elapsed*elapsed/(2*ramp)
    : left<ramp ? length-cruise*left*left/(2*ramp) : cruise*(elapsed-ramp/2);
  return {at,speed:3.6*cruise*Math.min(1,elapsed/ramp,left/ramp),display:leg,
    coord:s=>indexedTrackPoint(leg.path,s).coord};
}

function trainViewport() {
  const box=map.getBounds(), canvas=map.getCanvas();
  const west=box.getWest(), east=box.getEast(), south=box.getSouth(), north=box.getNorth();
  // Keep tails, labels and trains approaching the edge inside the draw buffer.
  const dx=(east-west)*96/Math.max(1,canvas.clientWidth);
  const dy=(north-south)*96/Math.max(1,canvas.clientHeight);
  return ([x,y])=>x>=west-dx && x<=east+dx && y>=south-dy && y<=north+dy;
}

// Align the first metres of a departure with the platform on the basemap.
function platformExit(pointAt,start,station,at) {
  if (!station || at-start>5000) return pointAt;
  const p=pointAt(start), dx=station[0]-p[0], dy=station[1]-p[1];
  const blend=Math.max(300,2*111200*Math.hypot(dx*Math.cos(p[1]*Math.PI/180),dy));
  if (at-start>blend+1500) return pointAt;
  return s=>{
    const q=pointAt(s), f=Math.max(0,Math.min(1,1-(s-start)/blend));
    return [q[0]+f*dx,q[1]+f*dy];
  };
}

function visibleTailStart(run, seconds) {
  let first=run.path[0][0];
  for (const [departure,at] of run.departures) {
    if (departure>seconds) break;
    first=Math.max(first,at);
  }
  return first;
}

function trainCarriages(head,at,pointAt,first=0,scale=1) {
  const parts=[head];
  const latitude=head.geometry.coordinates[1];
  // Five 11 px coaches at 11.25 px pitch form a 56 px train. Distances
  // are measured on the timetable path; its basemap grid scales by ~1.014.
  const pitch=11.25*scale*40030228*Math.cos(latitude*Math.PI/180)/(512*2**map.getZoom()*1.014);
  for (let car=1;car<5;car++) {
    const s=at-car*pitch;
    if (s<first) break;
    const coord=pointAt(s), ahead=pointAt(s+pitch*.45), behind=pointAt(Math.max(first,s-pitch*.45));
    const bearing=Math.atan2((ahead[0]-behind[0])*Math.cos(coord[1]*Math.PI/180),ahead[1]-behind[1])*180/Math.PI;
    const {id,day,state,parked,prefix}=head.properties;
    parts.push({type:'Feature',geometry:{type:'Point',coordinates:coord},properties:{car,id,day,state,parked,bearing,prefix}});
  }
  return parts;
}

function setupMapSettings() {
  const dialog = document.querySelector('#map-settings');
  const legend=document.querySelector('#train-class-legend');
  for (const [prefix,[name,color]] of Object.entries(trainClasses)) {
    el('span',prefix+' · '+name,legend).style.setProperty('--traffic',color);
  }
  el('span','纯数字车次',legend).style.setProperty('--traffic',ordinaryTrainColor);
  const storageKey = 'rails.followView';
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey));
    for (const key of Object.keys(followView)) {
      if (typeof saved?.[key] === 'boolean') followView[key] = saved[key];
    }
  } catch {
    // Browsers can disable storage. Keep the switches usable for this page.
  }
  for (const input of dialog.querySelectorAll('[data-follow]')) {
    const key = input.dataset.follow;
    input.checked = followView[key];
    input.addEventListener('change', () => {
      followView[key] = input.checked;
      try {
        localStorage.setItem(storageKey, JSON.stringify(followView));
      } catch {
        // The in-memory preference already applies.
      }
    });
  }
  for (const input of dialog.querySelectorAll('[data-train-label]')) {
    const key=input.dataset.trainLabel;
    try {
      const saved=localStorage.getItem('rails.trainLabels.'+key);
      if (saved!==null) trainLabels[key]=saved==='true';
    } catch {}
    input.checked=trainLabels[key];
    input.onchange=()=>{
      trainLabels[key]=input.checked;
      try { localStorage.setItem('rails.trainLabels.'+key,String(input.checked)); } catch {}
      const t=liveTimeline.time();
      regionLive.tick(t,true);
    };
  }
  const boardSwitch=dialog.querySelector('#map-departure-board');
  const boardKey='rails.showDepartureBoard';
  try { boardSwitch.checked=localStorage.getItem(boardKey)!=='false'; } catch {}
  boardSwitch.onchange=()=>{
    try { localStorage.setItem(boardKey,String(boardSwitch.checked)); } catch {}
    regionLive.tick(liveTimeline.time(),true);
  };
  document.querySelector('#open-map-settings').onclick = () => dialog.showModal();
  dialog.addEventListener('click', event => {
    if (event.target === dialog) dialog.close();
  });
}

setupMapSettings();

function el(tag, text, parent, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  if (parent) parent.append(node);
  return node;
}

function action(parent, text, callback, className) {
  const button = el('button', text, parent, className);
  button.type = 'button';
  button.onclick = callback;
  return button;
}

async function get(path, options) {
  if (window.staticRailData && path.startsWith('/api/')) return staticRailData.get(path);
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function mapPadding() {
  return panel.hasChildNodes() && !sidebar.classList.contains('collapsed') && window.innerWidth > 680
    ? {left: 410, right: 36, top: 30, bottom: 36}
    : 24;
}

function setSidebarVisible(visible) {
  sidebar.classList.toggle('collapsed', !visible);
  const label = visible ? '收起面板' : '展开面板';
  sidebarToggle.setAttribute('aria-expanded', String(visible));
  sidebarToggle.setAttribute('aria-label', label);
  sidebarToggle.title = label;
  if (map) map.resize();
  (visible && !panel.hasChildNodes() ? query : sidebarToggle).focus({preventScroll: true});
}

sidebarToggle.onclick = () => setSidebarVisible(sidebar.classList.contains('collapsed'));

function fit(bounds, maxZoom = 8) {
  if (map) map.fitBounds(bounds, {padding: mapPadding(), maxZoom, duration: 450});
}

function refreshRoute() {
  const source = map && map.getSource('selected-route');
  if (source) source.setData(route);
}

function hasPosition(stop) {
  return Number.isFinite(stop.lon) && Number.isFinite(stop.lat) && (!stop.coordinateQuality || stop.coordinateQuality === 'reference');
}

function toMapCoordinates([lon, lat]) {
  // The CNRail tiles place features on a shifted and scaled grid. This
  // transform was fitted against stations shared by the tiles and GTFS.
  const {xx, xy, x0, yx, yy, y0} = coordinateFit;
  return [xx * lon + xy * lat + x0, yx * lon + yy * lat + y0];
}

function fromMapCoordinates(lon, lat) {
  const {xx, xy, x0, yx, yy, y0} = coordinateFit;
  const determinant = xx * yy - xy * yx;
  return [(yy * (lon - x0) - xy * (lat - y0)) / determinant,
          (xx * (lat - y0) - yx * (lon - x0)) / determinant];
}

const eoxService = 'https://tiles.maps.eox.at/wms?';
const eoxTiles = eoxService + 'SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=s2cloudless_3857'
  + '&STYLES=&SRS=EPSG:3857&FORMAT=image/jpeg&WIDTH=256&HEIGHT=256&BBOX={bbox-epsg-3857}';
const mercatorRadius = 6378137;
const radians = Math.PI / 180;

function alignSatelliteRequest(url) {
  if (!url.startsWith(eoxService)) return {url};
  const request = new URL(url);
  const bbox = request.searchParams.get('BBOX')?.split(',').map(Number);
  if (!bbox || bbox.length !== 4 || !bbox.every(Number.isFinite)) return {url};
  function reproject(x, y) {
    const [lon, lat] = fromMapCoordinates(x / mercatorRadius / radians,
      Math.atan(Math.sinh(y / mercatorRadius)) / radians);
    return [mercatorRadius * lon * radians,
      mercatorRadius * Math.asinh(Math.tan(lat * radians))];
  }
  const [west, south, east, north] = bbox;
  request.searchParams.set('BBOX', [...reproject(west, south), ...reproject(east, north)]
    .map(value => value.toFixed(2)).join(','));
  return {url: request.href};
}

const blankPreference='rails.blankBasemap';
let baseMode='map';
try { if (localStorage.getItem(blankPreference)==='true') baseMode='blank'; } catch {}
const railButton = document.querySelector('#rail-basemap');
const satelliteButton = document.querySelector('#satellite-basemap');
const blankButton = document.querySelector('#blank-basemap');

function setupBasemaps(layers, scenerySource) {
  const status = document.querySelector('#basemap-status');
  const satelliteId = 'eox-sentinel-satellite';
  const scenery = layers.filter(layer=>layer.source==='scenery');
  let loadingTimer, fallbackMode;
  let showingImagery = false;

  function syncButtons() {
    for (const [mode,button] of [['map',railButton],['satellite',satelliteButton],['blank',blankButton]])
      button.setAttribute('aria-pressed',mode===baseMode);
  }

  function showScenery(mode) {
    if (mode==='blank') {
      if (map.getSource('scenery')) {
        for (const layer of scenery) map.removeLayer(layer.id);
        map.removeSource('scenery');
      }
      return;
    }
    if (!map.getSource('scenery')) {
      map.addSource('scenery',scenerySource);
      // Restore the original stacking order beneath the railway overlays.
      let before='rail-detail-track';
      for (const layer of [...layers].reverse()) {
        if (layer.source==='rail-detail' || ['railpoi0','railpoi1'].includes(layer['source-layer'])) continue;
        if (layer.source==='scenery') map.addLayer(layer,before);
        before=layer.id;
      }
    }
    for (const layer of scenery) {
      const hide=mode==='satellite' && ['fill','line'].includes(layer.type);
      const visibility=hide ? 'none' : (layer.layout?.visibility ?? 'visible');
      if ((map.getLayoutProperty(layer.id,'visibility') ?? 'visible')!==visibility)
        map.setLayoutProperty(layer.id,'visibility',visibility);
    }
  }

  function setStatus(message) {
    status.textContent = message;
    status.hidden = !message;
  }

  function showOffline(mode, message='') {
    baseMode=mode;
    try { localStorage.setItem(blankPreference,String(mode==='blank')); } catch {}
    clearTimeout(loadingTimer);
    showingImagery = false;
    if (map.getLayer(satelliteId)) map.removeLayer(satelliteId);
    if (map.getSource(satelliteId)) map.removeSource(satelliteId);
    showScenery(mode);
    syncButtons();
    setStatus(message);
  }

  function showSatellite() {
    if (baseMode==='satellite') return;
    if (!navigator.onLine) {
      setStatus('卫星影像需要联网。');
      return;
    }
    fallbackMode=baseMode;
    baseMode='satellite';
    syncButtons();
    setStatus('正在加载卫星影像…');
    map.addSource(satelliteId, {
      type: 'raster', tiles: [eoxTiles], tileSize: 256, minzoom: 2, maxzoom: 13,
      bounds: [30, 0, 150, 65],
      attribution: '<a href="https://cloudless.eox.at/" target="_blank" rel="noopener">EOxCloudless</a> by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2016) · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener">CC BY 4.0</a>'
    });
    map.addLayer({id: satelliteId, type: 'raster', source: satelliteId,
      paint: {'raster-fade-duration': 0}}, layers.find(layer=>layer.type!=='background' && map.getLayer(layer.id)).id);
    loadingTimer = setTimeout(() => showOffline(fallbackMode,'卫星影像未加载，已恢复原底图。'), 12000);
  }

  map.on('sourcedata', event => {
    if (baseMode!=='satellite' || showingImagery || event.sourceId !== satelliteId || event.tile?.state !== 'loaded') return;
    clearTimeout(loadingTimer);
    showingImagery = true;
    showScenery('satellite');
    setStatus('');
  });
  map.on('error', event => {
    if (event.sourceId === satelliteId) {
      const source=map.getSource(satelliteId);
      if (baseMode==='satellite') queueMicrotask(() => {
        if (baseMode==='satellite' && map.getSource(satelliteId)===source)
          showOffline(fallbackMode,'卫星影像暂不可用，已恢复原底图。');
      });
    } else {
      console.error(event.error);
    }
  });
  window.addEventListener('offline', () => {
    if (baseMode==='satellite') showOffline(fallbackMode,'网络已断开，已恢复原底图。');
  });
  railButton.onclick = () => showOffline('map');
  satelliteButton.onclick = showSatellite;
  blankButton.onclick = () => showOffline('blank');
  railButton.disabled = satelliteButton.disabled = blankButton.disabled = false;
  showOffline(baseMode);
}

function setRoute(trip) {
  const stops = trip.stops.map((stop, index) => ({...stop, number: index + 1,
    position: trip.matchedPath?.stops[index].at != null
      ? toMapCoordinates(trip.matchedPath.path.point(trip.matchedPath.stops[index].at))
      : stop.mapPosition || (hasPosition(stop) && toMapCoordinates([stop.lon, stop.lat]))})).filter(stop => stop.position);
  route = {type: 'FeatureCollection', features: []};
  function addLine(coordinates,estimated) {
    if (coordinates.length>=2) route.features.push({type:'Feature',properties:{estimated},
      geometry:{type:'LineString',coordinates}});
  }
  if (trip.displayLegs?.length || trip.inferredLegs) {
    const overrides=new Map((trip.displayLegs || []).map(leg=>[leg.seq,leg]));
    for (let seq=0;seq<trip.stops.length-1;seq++) {
      const leg=overrides.get(seq);
      if (leg) {
        addLine(leg.path.map(([,x,y])=>toMapCoordinates([x,y])),true);
      } else if (trip.inferredLegs) {
        const span=trip.inferredLegs[seq];
        if (span) addLine(trip.geometry.slice(span[0],span[1]+1).map(toMapCoordinates),true);
      } else if (trip.matchedPath) {
        const {path,stops:matched}=trip.matchedPath;
        const a=matched[seq].at,b=matched[seq+1].at;
        if (a!==null && b!==null && b>a) addLine(path.between(a,b).map(toMapCoordinates),false);
      }
    }
  } else if (trip.matchedPath?.stops[0].at!=null && trip.matchedPath.stops.at(-1).at!=null) {
    const {path,stops:matched}=trip.matchedPath;
    addLine(path.between(matched[0].at,matched.at(-1).at).map(toMapCoordinates),false);
  } else {
    addLine(trip.geometry ? trip.geometry.map(toMapCoordinates) : stops.map(s=>s.position),!trip.geometry);
  }
  const line=route.features.flatMap(f=>f.geometry.coordinates);
  for (const stop of stops) {
    route.features.push({type: 'Feature', properties: {name: stop.name, id: stop.id, number: stop.number},
      geometry: {type: 'Point', coordinates: stop.position}});
  }
  refreshRoute();
  if (map && (trip.liveLine==='catalog' || followView.trip) && trip.liveLine!=='region' && line.length) {
    const bounds = line.concat(stops.map(stop=>stop.position)).reduce((box, point) => box.extend(point), new mapboxgl.LngLatBounds());
    fit(bounds);
  }
}

function showTime(value) {
  if (!value) return '—';
  const [hour, minute] = value.split(':');
  const day = Math.floor(Number(hour) / 24);
  const clock = String(Number(hour) % 24).padStart(2, '0') + ':' + minute;
  return day ? `${clock} (+${day})` : clock;
}

function timetableSeconds(value) {
  const match = /^(\d+):([0-5]\d):([0-5]\d)$/.exec(value ?? '');
  if (!match) return null;
  // GTFS keeps counting hours after midnight, so 25:00:00 is next-day 01:00.
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
}

function tripDuration(stops) {
  if (stops.length < 2) return null;
  const departure = timetableSeconds(stops[0].departure);
  const arrival = timetableSeconds(stops.at(-1).arrival);
  if (departure === null || arrival === null || arrival < departure) return null;
  const elapsed = arrival - departure;
  return formatDuration(elapsed);
}

function formatDuration(elapsed) {
  const hours = Math.floor(elapsed / 3600);
  const minutes = Math.floor(elapsed / 60) % 60;
  const seconds = elapsed % 60;
  return [
    hours ? `${hours} 小时` : '',
    minutes || !hours && !seconds ? `${minutes} 分钟` : '',
    seconds ? `${seconds} 秒` : ''
  ].filter(Boolean).join(' ');
}

function section(text) {
  panel.replaceChildren();
  el('h2', text, panel);
}

async function updatePanel(load, render) {
  clearStationHighlight();
  liveTimeline.detachTrip();
  const version = ++panelVersion;
  panel.replaceChildren(el('p', '正在读取本地数据…'));
  if (sidebar.classList.contains('collapsed')) setSidebarVisible(true);
  try {
    const result = await load();
    if (version === panelVersion) render(result);
  } catch (error) {
    if (version === panelVersion) {
      panel.replaceChildren(el('p', `读取失败（${error.message}）`));
    }
  }
}

function timetable(stops, parent, renderStop) {
  const table = el('table', null, parent);
  const head = el('tr', null, table.createTHead());
  for (const text of ['车站', '到达', '出发']) el('th', text, head);
  const body = table.createTBody();
  for (const [index, stop] of stops.entries()) {
    const row = el('tr', null, body);
    renderStop(stop, el('td', null, row), index + 1);
    el('td', showTime(stop.arrival), row);
    el('td', showTime(stop.departure), row);
  }
}

function showTrip(id, serviceDate, liveLine=false) {
  return updatePanel(async () => {
    const key=encodeURIComponent(id);
    const [trip,displayLegs]=await Promise.all([get('/api/trip/'+key),get('/api/region-live?trip='+key)]);
    return {...trip,displayLegs};
  }, trip => {
    section(trip.number);
    trip.liveLine=liveLine;
    el('p', [trip.origin, trip.destination].filter(Boolean).join(' → '), panel);
    if (trip.inferredLegs || trip.displayLegs.length) el('small','轨迹可能和实际不符，仅供参考。',panel,'meta');
    trip.matchedPath = matchTrainPath(trip);
    setRoute(trip);
    liveTimeline.attachTrip(trip, el('div', null, panel), serviceDate);
    const duration = tripDuration(trip.stops);
    if (duration) {
      const summary = el('p', '全程用时 ', panel, 'trip-duration');
      el('strong', duration, summary);
    }
    if (!trip.geometry && !trip.displayLegs.length) el('p', '该车次没有线路几何，地图用虚线连接有坐标的经停站。', panel, 'meta');
    if (trip.dates.length) {
      const dates = el('details', null, panel);
      el('summary', `快照记录 ${trip.dates.length} 个开行日期`, dates, 'meta');
      el('p', trip.dates.map(d => `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6)}`).join('、'), dates);
    }
    const schedule=el('details',null,panel,'trip-extra');
    el('summary','完整时刻表',schedule);
    timetable(trip.stops, schedule, (stop, cell, number) => {
      el('span', number, cell, 'stop-number');
      action(cell, stop.name, () => showStation(stop.id));
    });
  });
}

const railOrange = '#bf510b';
const selectionColor = ['case', ['has', 'railway'], railOrange, '#7643b5'];

function setLineRoute(line, points) {
  const features = (line.tracks || []).map(coordinates => ({type:'Feature',
    properties:{railway:true, estimated:false}, geometry:{type:'LineString',coordinates}}));
  if (line.supplement) features.push({type:'Feature', properties:{railway:true, estimated:true},
    geometry:{type:'LineString',coordinates:line.supplement.coordinates}});
  points.forEach((point,index) => {
    const position=point.mapPosition || (hasPosition(point) && toMapCoordinates([point.lon,point.lat]));
    if (position) features.push({type:'Feature', geometry:{type:'Point',coordinates:position},
      properties:{railway:true, id:point.stationId || point.kid, name:point.name, number:index+1,
        endpoint:index===0 || index===points.length-1}});
  });
  route = {type:'FeatureCollection',features};
  refreshRoute();
}

function addStopBadges() {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 52;
  const context = canvas.getContext('2d');
  for (const [id,color] of [['stop-badge','#7643b5'],['rail-stop-badge',railOrange]]) {
    context.clearRect(0,0,52,52);
    context.beginPath();
    context.arc(26, 26, 23, 0, Math.PI * 2);
    context.fillStyle = color;
    context.fill();
    context.lineWidth = 4;
    context.strokeStyle = '#fff';
    context.stroke();
    map.addImage(id, context.getImageData(0, 0, 52, 52), {pixelRatio: 2});
  }
  // Put consecutive badges on opposite sides so nearby stops can both be read.
  const badgeOffset = distance => ['case', ['==', ['%', ['get', 'number'], 2], 0],
    ['literal', [0, distance]], ['literal', [0, -distance]]];
  map.addLayer({
    id: 'trip-stop-number', type: 'symbol', source: 'selected-route',
    filter: ['==', '$type', 'Point'],
    layout: {
      'icon-image': ['case',['has','railway'],'rail-stop-badge','stop-badge'],
      'icon-offset': badgeOffset(20),
      'text-offset': badgeOffset(20 / 13),
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
      'text-allow-overlap': true,
      'text-ignore-placement': true,
      'symbol-sort-key': ['get', 'number'],
      'text-field': ['to-string', ['get', 'number']],
      'text-font': ['Noto Sans Regular'],
      'text-size': 13
    },
    paint: {'text-color': '#fff'}
  });
  map.addLayer({
    id: 'trip-stop-name', type: 'symbol', source: 'selected-route',
    filter: ['==', '$type', 'Point'],
    layout: {
      'text-field': ['get', 'name'],
      'text-font': ['Noto Sans Regular'],
      'text-size': 14,
      'text-variable-anchor': ['left', 'right', 'top', 'bottom', 'top-left', 'top-right', 'bottom-left', 'bottom-right'],
      'text-radial-offset': 1.5,
      'text-justify': 'auto',
      'symbol-sort-key': ['case', ['==', ['get', 'endpoint'], true], 0, ['get', 'number']]
    },
    paint: {'text-color': selectionColor, 'text-halo-color': '#fff', 'text-halo-width': 1.8}
  }, 'trip-stop-number');
}

function showLine(name, origin = '', station = null, focusView = followView.line) {
  if (sidebar.classList.contains('collapsed')) setSidebarVisible(true);
  clearMapSelection();
  const fetchLine = (id, from = '') => get('/api/line/' + encodeURIComponent(id) + (from ? '?from=' + from : ''));
  return updatePanel(async () => {
    const line = await fetchLine(name, origin);
    if (!line.mapUnresolved) return line;
    const candidates = line.candidates || [];
    if (candidates.length === 1) {
      const detail = await fetchLine(candidates[0].id);
      return {...detail, alternativeGtfs:line.gtfsAvailable ? line.name : null,
        tracks:line.tracks?.length ? line.tracks : detail.tracks, mapBounds:line.mapBounds || detail.mapBounds, mapDetail:line.mapDetail || detail.mapDetail};
    }
    if (!candidates.length && line.gtfsAvailable) {
      return {...await fetchLine(line.name, 'gtfs'), tracks:line.tracks, mapBounds:line.mapBounds, mapDetail:line.mapDetail};
    }
    return line;
  }, line => {
    section(line.name);
    if (!line.points?.length) setLineRoute(line, []);
    if (line.mapUnresolved) {
      if (focusView && line.mapBounds) fit(line.mapBounds);
      el('p', line.candidates.length ? '请选择要查看的线路区段。' : '本地尚未找到这段轨道对应的线路详情。', panel, 'meta');
      for (const candidate of line.candidates) {
        const button = action(panel, candidate.name, () => showLine(candidate.id, '', station, focusView), 'result');
        if (candidate.origin && candidate.destination) el('small', candidate.origin + ' → ' + candidate.destination, button);
      }
      if (line.gtfsAvailable) action(panel, '查看 GTFS 同名站序', () => showLine(line.name, 'gtfs', station, focusView));
      return;
    }
    if (line.alternativeGtfs) action(panel, '查看 GTFS 同名站序', () => showLine(line.alternativeGtfs, 'gtfs', station, focusView));
    if (line.sourceStale) el('p', '原站刷新失败，当前展示上一次成功取得的线路详情。', panel, 'meta');
    const points = line.points;
    if (points.length) el('p', `${points[0].name} → ${points.at(-1).name}`, panel);
    if (station?.kid && line.infrastructure && !points.some(point => point.kid === station.kid)) {
      el('p', `原站的${station.name}站卡列出了这条线路，线路详情的站序中未列出本站。`, panel, 'meta');
    }
    const details = line.infrastructure;
    const facts = el('dl', null, panel, 'station-facts line-facts');
    for (const [label, value] of [
      ['铁路类型', line.railType === 'LINK' ? '联络线' : line.railType || '未收录'],
      ['线路总数', details?.lines ?? '未收录'],
      ['电气化', details?.electrification ?? '未收录'],
      ['设计时速', line.facts?.designSpeed || details?.design_speed || '未收录'],
      [line.facts?.lengthKm ? '全线长度' : '线路表末端里程',
        line.facts?.lengthKm ? `${line.facts.lengthKm.toLocaleString()} km` : points.at(-1)?.mileage],
      ['沿线站点', points.length || null]
    ]) {
      if (!value) continue;
      const item = el('div', null, facts);
      el('dt', label, item);
      el('dd', value, item);
    }
    if (line.facts?.sourceUrl) {
      const source = el('a', line.facts.sourceName, el('p', '资料来源：', panel, 'meta'));
      source.href = line.facts.sourceUrl;
      source.target = '_blank';
      source.rel = 'noopener noreferrer';
    }
    if (details) {
      if (line.sourceArchived) archivedRecord(el('p', null, panel, 'meta'), 'feature', details.id);
      const source = el('a', '中国铁路地图 · 原线路详情', el('p', null, panel, 'meta'));
      source.href = `http://cnrail.geogv.org/zhcn/rail/${encodeURIComponent(details.id)}`;
      source.target = '_blank';
      source.rel = 'noopener noreferrer';
    }
    if (line.additionalStations?.length) {
      const extra = el('details', null, panel);
      el('summary', '站卡另有接入 · ' + line.additionalStations.length + ' 站', extra);
      el('p', '这些站卡列出了本线，线路详情未给出它们的站序。', extra, 'meta');
      for (const point of line.additionalStations) action(extra, point.name, () => showStation(point.kid));
    }
    el('h3', '沿线站点', panel);
    if (!points.length) {
      if (focusView && line.mapBounds) fit(line.mapBounds);
      el('p', line.missingEid ? '原站站卡列出了这条线路，本地尚未取得该线路 ID 的详情。' : '本地线路资料尚未收录站序。', panel, 'meta');
      return;
    }
    if (line.gtfsPoints.length) {
      const gtfs = line.gtfsPoints;
      el('p', `原站站序：${points[0].name} → ${points.at(-1).name}。GTFS 站序：${gtfs[0].name} → ${gtfs.at(-1).name}。可切换比较。`, panel, 'meta');
    }
    for (const section of line.sections || []) {
      action(panel, section.label, () => showLine(section.id), 'station-filter');
    }
    if (!line.tracks?.length) el('p','此线路暂无已匹配的底图轨道，地图标出可定位的沿线站点。',panel,'meta');
    if (line.supplement) el('p', '橙色虚线按 ' + line.supplement.train + ' 的径路补绘。', panel,'meta');
    const split = points.some(point => point.section);
    el('p', split ? '按区段显示站序，里程各段独立起算。' : line.sequenceSource === 'gtfs' ? '按 GTFS 配套线路表的站序显示。' :
      '按线路里程排列，包含线路所。点站名可打开站卡。', panel, 'meta');
    const choices = line.gtfsPoints.length ? el('div', null, panel, 'station-filters') : null;
    if (focusView && map) {
      const bounds=new mapboxgl.LngLatBounds(line.mapBounds || undefined);
      for (const point of points) {
        const position=point.mapPosition || (hasPosition(point) && toMapCoordinates([point.lon,point.lat]));
        if (position) bounds.extend(position);
      }
      if (!bounds.isEmpty()) fit(bounds,line.mapDetail ? 12 : 8);
    }
    const lists = el('div', null, panel);
    function renderPoints(items) {
      setLineRoute(line, items);
      lists.replaceChildren();
      let list;
      for (const [index, point] of items.entries()) {
        if (!list || point.section !== items[index - 1].section) {
          if (point.section) el('h4', point.section, lists, 'line-section-heading');
          list = el('ol', null, lists, 'line-station-list');
          list.start = index + 1;
        }
        const item = el('li', null, list);
        el('span', index + 1, item, 'stop-number');
        const detail = el('div', null, item);
        if (point.stationId) action(detail, point.name, () => showStation(point.stationId));
        else el('strong', point.name, detail);
        if (point.region) el('small', point.region, detail);
        if (point.mileage) el('span', point.mileage, item, 'line-mileage');
      }
    }
    if (choices) {
      choices.setAttribute('role', 'group');
      choices.setAttribute('aria-label', '站序来源');
      for (const [label, items] of [['原站站序', points], ['GTFS 站序', line.gtfsPoints]]) {
        const button = action(choices, `${label} ${items.length}`, () => {
          for (const option of choices.children) option.setAttribute('aria-pressed', String(option === button));
          renderPoints(items);
        });
        button.setAttribute('aria-pressed', String(items === points));
      }
    }
    renderPoints(points);
  });
}

function archivedRecord(parent, kind, identifier) {
  const link = el('a', '查看离线原始资料', parent);
  link.href = window.staticRailData
    ? './records/source/' + kind + '/' + encodeURIComponent(identifier) + '.json'
    : '/api/source/' + kind + '/' + encodeURIComponent(identifier);
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
}

function stationOverview(station, body) {
  const facts = el('dl', null, body, 'station-facts');
  const bureaus = [...new Set(station.lines.map(line => line.bureau).filter(Boolean))];
  const regions = [...new Set(station.lines.map(line => line.region).filter(Boolean))];
  for (const [label, value] of [
    ['电报码', station.teleCode || (station.hasTimetable ? station.id : null)],
    ['同站时刻表编号', station.alternateCodes?.join('、')],
    ['运营', station.operators?.join('、') || bureaus.map(bureau => bureau === '香港' ? bureau : bureau + '局').join('、')],
    ['所在地区', station.location || regions.join('、')]
  ]) {
    if (!value) continue;
    const item = el('div', null, facts);
    el('dt', label, item);
    el('dd', value, item);
  }
  if (station.coordinateQuality === 'interpolated') {
    el('p', station.mapPosition ? '时刻表坐标由邻站估算，地图已使用原站点位。' : '时刻表坐标由邻站估算，本站的准确点位待核实。', body, 'meta');
  }
  if (station.positionDifferenceKm) {
    el('p', `原站与时刻表的点位相距约 ${station.positionDifferenceKm} 公里，地图使用原站位置。`, body, 'meta');
  }
  el('h3', '接入线路', body);
  if (station.otherSourceLines?.length) {
    const extra = el('details', null, body);
    el('summary', '原站线路站序另有收录', extra);
    for (const edge of station.otherSourceLines) action(extra, edge.line, () => showLine(edge.eid));
  }
  if (station.gtfsDifferences?.length) {
    const extra = el('details', null, body, 'gtfs-connections');
    el('summary', '对照 GTFS 配套线路表（' + station.gtfsDifferences.length + ' 条差异）', extra);
    el('p', '以下记录使用 GTFS 配套站序；点击可查看该表的完整范围。', extra, 'meta');
    for (const row of station.gtfsDifferences) {
      const item = el('p', null, extra);
      action(item, row.line, () => showLine(row.line, 'gtfs'));
      el('small', ' ' + [row.previous,station.name,row.next].filter(Boolean).join(' → '), item);
    }
  }
  if (!station.lines.length) {
    el('p', station.sourceCard ? '原站站卡未列出接入线路。' : '未取得本站的接入线路资料。', body, 'meta');
    return;
  }
  if (!station.sourceCard) el('p', station.relationSource === 'line-sequence' ? '原站站卡未提供线路关系。以下线路的原站站序收录了本站，邻站按表中顺序显示。' : '原站站卡关系未收录，以下按 GTFS 配套线路表站序显示。', body, 'meta');
  for (const line of station.lines) {
    const card = el('article', null, body, 'station-line');
    const lineType = line.railType === 'LINK' ? '联络线' : line.railType || '类型未知';
    if (lineType === '高速铁路') card.classList.add('station-line-high-speed');
    if (lineType === '快速铁路') card.classList.add('station-line-fast');
    if (lineType === '普速铁路') card.classList.add('station-line-conventional');
    const heading = el('h4', null, card);
    const lineButton = action(heading, null, () => showLine(line.eid || line.line, line.source === 'gtfs' ? 'gtfs' : '', station), 'station-line-link');
    lineButton.setAttribute('aria-label', `查看${line.line}详情（${lineType}）`);
    if (/^#[0-9a-f]{6}$/i.test(line.color || '')) {
      const swatch = el('span', null, lineButton, 'source-line-swatch');
      swatch.style.backgroundColor = line.color;
      swatch.title = '中国铁路地图站卡配色';
    }
    el('span', line.line, lineButton, 'station-line-name');
    el('span', lineType, lineButton, 'station-line-type');
    el('span', '›', lineButton, 'station-line-arrow').setAttribute('aria-hidden', 'true');
    if (line.directions) {
      const neighbors = el('div', null, card, 'line-neighbors');
      for (const [side,label] of [['previous','前一站'], ['next','后一站']]) {
        for (const direction of line.directions[side]) {
          if (!direction.name && !direction.destination) continue;
          const item = el('div', null, neighbors);
          el('small', label, item);
          if (direction.id) action(item, direction.name, () => showStation(direction.id));
          else if (direction.name) el('span', direction.name, item);
          if (direction.destination) el('small', direction.destination + '方向', item);
        }
      }
      for (const [side,label] of [['previous','线路起点'], ['next','线路终点']]) {
        const entries = line.directions[side];
        if (entries.length && entries.every(entry => entry.status === 'END' && !entry.name)) {
          el('small', line.source === 'line-sequence' ? label.replace('线路', '站序') : label, card, 'line-endpoint');
        }
      }
      continue;
    }
    if (!line.previous && !line.next) {
      el('small', '线路表未列出相邻站', card, 'station-line-source');
      continue;
    }
    const neighbors = el('div', null, card, 'line-neighbors');
    for (const [label, name, id] of [
      ['前一站', line.previous, line.previousId],
      ['后一站', line.next, line.nextId]
    ]) {
      if (!name) continue;
      const item = el('div', null, neighbors);
      const caption = el('div', null, item, 'line-neighbor-caption');
      el('span', label, caption);
      if (!line.previous) el('span', '线路起点', caption, 'line-endpoint');
      if (!line.next) el('span', '线路终点', caption, 'line-endpoint');
      if (id) action(item, name, () => showStation(id));
      else el('div', name, item);
    }
  }
}

const stationCallLabels = {origin: '始发', through: '经停', terminal: '终到'};

function stationTrainList(trains, body, initialKind = 'all') {
  function orderTime(train) {
    const time = train.arrival || train.departure;
    return time ? showTime(time).slice(0, 5) : '99:99';
  }
  const rows = [...trains].sort((a, b) => orderTime(a).localeCompare(orderTime(b)));
  const filters = el('div', null, body, 'station-filters');
  filters.setAttribute('role', 'group');
  filters.setAttribute('aria-label', '列车类型');
  const choices = [['all', '全部'], ...Object.entries(stationCallLabels)];
  const filterButtons = choices.map(([kind, label]) => {
    const count = kind === 'all' ? rows.length : rows.filter(train => train.kind === kind).length;
    const button = action(filters, `${label} ${count}`, () => render(kind));
    return {kind, button};
  });
  el('p', '按到站时间排列，始发车按离站时间。次日以列车始发日为准。', body, 'meta');
  const results = el('div', null, body);

  function render(kind) {
    for (const filter of filterButtons) filter.button.setAttribute('aria-pressed', String(filter.kind === kind));
    results.replaceChildren();
    const filtered = kind === 'all' ? rows : rows.filter(train => train.kind === kind);
    if (!filtered.length) {
      el('p', '快照中没有符合条件的列车。', results, 'meta');
      return;
    }
    const table = el('table', null, results, 'station-trains');
    const header = el('tr', null, table.createTHead());
    for (const title of ['车次', '始发 → 终到', '到站', '离站']) el('th', title, header);
    const tbody = table.createTBody();
    for (const train of filtered) {
      const row = el('tr', null, tbody);
      const number = el('td', null, row);
      action(number, train.number, () => showTrip(train.id));
      el('span', stationCallLabels[train.kind], number, `station-call station-call-${train.kind}`);
      const endpoints = el('td', null, row, 'station-endpoints');
      el('span', train.origin, endpoints);
      el('strong', `→ ${train.destination}`, endpoints);
      el('td', showTime(train.arrival), row, 'station-time');
      el('td', showTime(train.departure), row, 'station-time');
    }
  }
  render(initialKind);
}

function renderStation(station) {
  section(station.name);
  openStationBoard(station);
  if (station.sourceStale) el('p', '原站刷新失败，当前展示上一次成功取得的站卡。', panel, 'meta');
  if (station.pinyin) el('p', station.pinyin, panel, 'station-pinyin');
  if (!station.hasTimetable) {
    el('p', '本站尚未关联本地时刻表。', panel, 'meta');
    if (station.boardCandidate) {
      action(panel, '查看同名同电报码车站的实时状态', () => {
        showStation(station.boardCandidate);
      });
    }
    stationOverview(station, panel);
    return;
  }
  const tabs = el('div', null, panel, 'station-tabs');
  tabs.setAttribute('role', 'group');
  tabs.setAttribute('aria-label', '车站详情');
  const body = el('div', null, panel, 'station-tab-content');
  const statsState = {line: '', side: 'either'};
  function selectTab(index, kind, drill) {
    for (const [i, tab] of Array.from(tabs.children).entries()) {
      tab.setAttribute('aria-pressed', String(i === index));
    }
    body.replaceChildren();
    if (index === 2) {
      renderStationStats(station, body, statsState, (label, rows) => {
        const position = panel.scrollTop;
        selectTab(1, 'all', {label, rows, position});
        panel.scrollTop = 0;
      });
      return;
    }
    if (index === 1) {
      if (drill) {
        const context = el('div', null, body, 'stats-drilldown');
        el('strong', drill.label, context);
        if (statsState.line) {
          el('span', `${statsState.lineName} · ${stationLineSides[statsState.side]}`, context);
        }
        action(context, '返回统计', () => {
          selectTab(2);
          statsState.request?.then(() => {
            if (body.querySelector('.stats-charts')) panel.scrollTop = drill.position;
          }).catch(() => {});
        });
        action(context, '查看本站全部列车', () => selectTab(1));
      }
      stationTrainList(drill?.rows ?? station.trains, body, kind);
      return;
    }
    const shortcuts = el('div', null, body, 'station-filters');
    for (const [call, label] of Object.entries(stationCallLabels)) {
      const count = station.trains.filter(train => train.kind === call).length;
      action(shortcuts, `${label} ${count}`, () => selectTab(1, call));
    }
    stationOverview(station, body);
  }
  for (const [index,label] of ['概览','列车','统计'].entries()) {
    action(tabs, label, () => selectTab(index));
  }
  selectTab(0);
}

function showStation(id, focusView = followView.station) {
  clearMapSelection();
  return updatePanel(() => get('/api/station/' + encodeURIComponent(id)), station => {
    renderStation(station);
    const center=station.mapPosition || (hasPosition(station) && toMapCoordinates([station.lon, station.lat]));
    if (!center) return;
    selectedStation={type:'Feature',properties:{name:station.name,kid:station.kid || station.id},geometry:{type:'Point',coordinates:center}};
    stationSymbols.select();
    if (map && focusView) {
      map.easeTo({center, zoom: Math.max(map.getZoom(), 11.5),
        padding: typeof mapPadding() === 'number' ? {top: 0, bottom: 0, left: 0, right: 0} : mapPadding()});
    }
  });
}

function stationDistance(station, [lon, lat]) {
  if (!station.mapPosition && !hasPosition(station)) return Infinity;
  const [stationLon, stationLat] = station.mapPosition || toMapCoordinates([station.lon, station.lat]);
  return Math.hypot((stationLon - lon) * Math.cos(lat * Math.PI / 180), stationLat - lat);
}

function search(term, stationPosition) {
  term = term.trim();
  if (!term) return;
  query.value = term;
  const params = new URLSearchParams({q: term});
  if (stationPosition) params.set('scope', 'station');
  updatePanel(() => get('/api/search?' + params), results => {
    if (stationPosition) {
      const matches = results.stations.filter(station => station.name === term);
      if (matches.length) {
        const nearest = matches.reduce((best, station) =>
          stationDistance(station, stationPosition) < stationDistance(best, stationPosition) ? station : best);
        showStation(nearest.id);
        return;
      }
    }
    const exact = results.trains.filter(train => train.number.toUpperCase() === term.toUpperCase());
    if (!stationPosition && exact.length === 1) {
      showTrip(exact[0].id);
      return;
    }
    const exactLines = results.lines.filter(line =>
      [line.name, ...line.aliases].some(name => name.toLowerCase() === term.toLowerCase()));
    if (exactLines.length === 1 && !results.stations.some(station => station.name === term)) {
      showLine(exactLines[0].id);
      return;
    }
    section('搜索结果');
    if (!results.trains.length && !results.stations.length && !results.lines.length) {
      el('p', `找不到「${term}」。可尝试车次前缀、车站名或线路名。`, panel);
    }
    if (results.trains.length) {
      el('h3', '车次', panel);
      for (const train of results.trains) {
        const button = action(panel, train.number, () => showTrip(train.id), 'result');
        el('small', [train.origin, train.destination].filter(Boolean).join(' → '), button);
      }
    }
    if (results.stations.length) {
      el('h3', '车站', panel);
      const counts = new Map();
      for (const station of results.stations) counts.set(station.name, (counts.get(station.name) || 0) + 1);
      for (const station of results.stations) {
        const button = action(panel, station.name, () => showStation(station.id), 'result');
        if (counts.get(station.name) > 1) {
          const context = station.sourceOnly ? ['原站站卡', station.location] : ['时刻表', station.id];
          el('small', context.filter(Boolean).join(' · '), button);
        }
      }
    }
    if (results.lines.length) {
      el('h3', '线路', panel);
      for (const line of results.lines) {
        const button = action(panel, line.name, () => showLine(line.id), 'result');
        if (line.id.startsWith('map:')) el('small', '底图轨道', button);
        if (line.origin && line.destination) el('small', `${line.origin} → ${line.destination}`, button);
      }
    }
  });
}

form.onsubmit = event => { event.preventDefault(); search(query.value); };
function clearMapSelection() {
  if (!panel.hasChildNodes() && !selectedStation && !route.features.length) return;
  map?.stop();
  ++panelVersion;
  panel.replaceChildren();
  if (sidebar.classList.contains('collapsed')) setSidebarVisible(true);
  liveTimeline.detachTrip();
  clearStationHighlight();
  route = {type: 'FeatureCollection', features: []};
  refreshRoute();
  liveTimeline.refreshPausedMap();
}

function ensureSelectedTrainSource() {
  if (!map?.getLayer('trip-stop-name')) return null;
  const existing=map.getSource('selected-train');
  if (existing) return existing;
  map.addSource('selected-train',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
  // Station ink paints over the train only where their pixels actually meet.
  map.addLayer({id:'selected-train-heading',type:'symbol',source:'selected-train',
    filter:['==','parked',false],layout:{
      'icon-image':trainBodyIcon('#7643b5'),'icon-size':selectedTrainScale,'icon-rotate':['get','bearing'],
      'icon-rotation-alignment':'map','icon-allow-overlap':true,'icon-ignore-placement':true
    }},'trip-stop-name');
  map.addLayer({id:'selected-train-caption',type:'symbol',source:'selected-train',filter:['has','caption'],layout:{
    'text-field':['get','caption'],'text-font':['Noto Sans Regular'],'text-size':12,
    ...liveTrainLabelPlacement,'text-max-width':128
  },paint:{'text-color':'#492571','text-halo-color':'white','text-halo-width':1.6}},stationSymbols.nameLayers[0]);
  return map.getSource('selected-train');
}

function bindMiddleButtonCamera(map) {
  const canvas=map.getCanvas();
  let gesture;
  canvas.addEventListener('pointerdown', event=>{
    if (event.pointerType!=='mouse' || event.button!==1) return;
    event.preventDefault();
    canvas.setPointerCapture(event.pointerId);
    gesture={id:event.pointerId,x:event.clientX,y:event.clientY,started:false};
  });
  canvas.addEventListener('pointermove', event=>{
    if (!gesture || event.pointerId!==gesture.id || !(event.buttons & 4)) return;
    const dx=event.clientX-gesture.x, dy=event.clientY-gesture.y;
    if (!dx && !dy) return;
    if (!gesture.started) {
      liveTimeline.trainCamera?.detachCamera();
      map.stop();
      gesture.started=true;
    }
    gesture.x=event.clientX;
    gesture.y=event.clientY;
    map.jumpTo({bearing:map.getBearing()-dx*0.12,
      pitch:Math.max(map.getMinPitch(),Math.min(map.getMaxPitch(),map.getPitch()+dy*0.096))});
  });
  canvas.addEventListener('lostpointercapture',()=>{ gesture=null; });
  canvas.addEventListener('auxclick',event=>{ if (event.button===1) event.preventDefault(); });
}

async function start() {
  const info = await get('/api/info');
  const d = info.feed_version;
  document.querySelector('#snapshot').textContent =
    `${info.preview ? "阶段预览 · " : ""}${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6)} 快照 · ${info.numbers.toLocaleString()} 个车次号 · ${info.stops.toLocaleString()} 座车站 · 瓦片到 ${info.map.maxzoom} 级`;
  if (!mapboxgl.supported()) {
    el('p', '当前浏览器无法使用 WebGL，时刻表仍可搜索。', panel);
    return;
  }
  const [style,stationAtlas] = await Promise.all([get('./map/split-style.json'),stationSymbols.prepare()]);
  style.sources['rail-detail'] = {type: 'geojson', data: new URL('./map/rail-detail.geojson', document.baseURI).href};
  style.layers.push(
    {id: 'rail-detail-track', type: 'line', source: 'rail-detail', minzoom: 9,
      filter: ['==', '$type', 'LineString'], paint: {'line-color': '#ff3300', 'line-width': 1.75}},
    {id: 'rail-detail-name', type: 'symbol', source: 'rail-detail', minzoom: 11,
      filter: ['==', '$type', 'LineString'], layout: {'symbol-placement': 'line-center',
        'text-field': ['get', 'rail'], 'text-font': ['Noto Sans Regular'], 'text-size': 12, 'text-offset': [0, 0.9]},
      paint: {'text-color': '#ff3300', 'text-halo-color': '#fff', 'text-halo-width': 1}},
    {id: 'rail-detail-junction', type: 'circle', source: 'rail-detail', minzoom: 11,
      filter: ['==', '$type', 'Point'], paint: {'circle-color': '#aaa', 'circle-radius': 4,
        'circle-stroke-color': '#fff', 'circle-stroke-width': 1}},
    {id: 'rail-detail-label', type: 'symbol', source: 'rail-detail', minzoom: 11,
      filter: ['==', '$type', 'Point'], layout: {'text-field': ['get', 'name'], 'text-offset': [0.7, 0],
        'text-anchor': 'left', 'text-font': ['Noto Sans Regular'], 'text-size': 12},
      paint: {'text-color': '#696969', 'text-halo-color': '#fff', 'text-halo-width': 1}}
  );
  const railwayLayers = style.layers.filter(layer => layer['source-layer'] === 'rail' || layer.source === 'rail-detail');
  const railwayLabels = railwayLayers.filter(layer => layer.type === 'symbol').map(layer => layer.id);
  const railwayTracks = railwayLayers.filter(layer => layer.type === 'line').map(layer => layer.id);
  const siteRoot = new URL('.', document.baseURI).href;
  const localAsset = path => siteRoot + path.replace(/^\//, '');
  style.sprite = localAsset(style.sprite);
  style.glyphs = localAsset(style.glyphs);
  for (const source of Object.values(style.sources)) {
    if (source.tiles) source.tiles=source.tiles.map(localAsset);
  }
  const layers=style.layers, scenerySource=style.sources.scenery;
  if (baseMode==='blank') {
    style.layers=layers.filter(layer=>layer.source!=='scenery');
    delete style.sources.scenery;
  }
  map = new mapboxgl.Map({
    container: 'map', style, fadeDuration: 0, dragRotate: false,
    center: [105, 35], zoom: 3.8, minZoom: 2, maxZoom: 15,
    renderWorldCopies: false, attributionControl: false, transformRequest: alignSatelliteRequest,
    localIdeographFontFamily: 'Noto Sans CJK SC, sans-serif'
  });
  bindMiddleButtonCamera(map);
  new ResizeObserver(() => map.resize()).observe(map.getContainer());
  map.addControl(new mapboxgl.NavigationControl({showCompass: false}), 'top-right');
  map.addControl(new mapboxgl.ScaleControl({unit: 'metric'}));
  map.addControl(new mapboxgl.AttributionControl({
    customAttribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · 铁路底图 <a href="http://cnrail.geogv.org/zhcn/">中国铁路地图</a> · 时刻表 <a href="https://github.com/wensimehrp/chinese-railway-gtfs">中国铁路 GTFS / CC BY 4.0</a>'
  }));
  map.on('move',()=>{
    if (!liveTimeline.playing) {
      regionLive.tick(liveTimeline.time());
    }
  });
  map.on('zoomend',liveTimeline.refreshPausedMap);
  map.on('load', () => {
    document.body.dataset.mapReady = 'true';
    map.addSource('selected-route', {type: 'geojson', data: route});
    map.addLayer({id: 'trip-line', type: 'line', source: 'selected-route',
      filter: ['all', ['==', '$type', 'LineString'], ['==', 'estimated', false]],
      paint: {'line-color': selectionColor, 'line-width': ['case',['has','railway'],6,4]}});
    map.addLayer({id: 'trip-estimate', type: 'line', source: 'selected-route',
      filter: ['all', ['==', '$type', 'LineString'], ['==', 'estimated', true]],
      paint: {'line-color': selectionColor, 'line-width': 3, 'line-dasharray': [2, 2]}});
    map.addLayer({id: 'trip-stop', type: 'circle', source: 'selected-route',
      filter: ['==', '$type', 'Point'],
      paint: {'circle-color': selectionColor, 'circle-radius': 4, 'circle-stroke-color': '#fff', 'circle-stroke-width': 1}});
    addStopBadges();
    // The base station symbols own their positions even when a highlighted
    // route adds a stop number and a second label at the same location.
    for (const stationLayer of ['station-pax-245-LT','station-pax-01-LT']) {
      map.moveLayer(stationLayer);
    }
    map.addSource('selected-station',{type:'geojson',data:selectedStation || emptyStation});
    map.addLayer({id:'selected-station-ring',type:'symbol',source:'selected-station',layout:{}});
    stationSymbols.install(stationAtlas);
    map.addLayer({id:'selected-station-name',type:'symbol',source:'selected-station',layout:{
      'text-field':['get','name'],'text-font':['Open Sans Bold'],'text-size':16,
      'text-anchor':map.getLayoutProperty(stationSymbols.nameLayers[0],'text-anchor'),
      'text-offset':map.getLayoutProperty(stationSymbols.nameLayers[0],'text-offset'),
      'text-padding':5,'text-allow-overlap':true
    },paint:{'text-color':'#642f9d','text-halo-color':'#fff','text-halo-width':2}});
    setupBasemaps(layers,scenerySource);
    refreshRoute();
    if (!selectedStation && !route.features.length) fit(national);
    liveTimeline.refreshPausedMap();
  });
  let dragged = false;
  map.on('mousedown', () => { dragged = false; });
  map.on('touchstart', () => { dragged = false; });
  map.on('dragstart', () => { dragged = true; });
  map.on('click', event => {
    if (dragged || (event.originalEvent?.button ?? 0) !== 0) return;
    const layers = ['selected-station-ring', 'selected-station-name', ...stationSymbols.nameLayers, 'trip-stop-name', 'trip-stop-number', 'trip-stop', 'station-pax-01-LT', 'station-pax-245-LT'].filter(id => map.getLayer(id));
    if (!layers.length) { clearMapSelection(); return; }
    const feature = map.queryRenderedFeatures(event.point, {layers})[0];
    if (!feature) {
      if (map.getLayer('selected-train-heading') && map.queryRenderedFeatures(event.point,
          {layers:['selected-train-heading','selected-train-caption']}).length) {
        liveTimeline.trainCamera?.selectOnMap();
        return;
      }
      if (regionLive.selectPoint(event)) return;
      // Only a direct hit selects a railway. Empty pixels beside it dismiss the panel.
      const nameOf = feature => feature.properties.rail || feature.properties['name:zh'];
      const detail = map.queryRenderedFeatures(event.point, {layers: ['rail-detail-track', 'rail-detail-junction']})[0];
      if (detail?.geometry.type === 'Point' && detail.properties.kid) {
        showStation(detail.properties.kid);
        return;
      }
      const railway = detail || map.queryRenderedFeatures(event.point, {layers: railwayLabels}).find(nameOf)
        || map.queryRenderedFeatures(event.point, {layers: railwayTracks}).find(nameOf);
      if (railway && nameOf(railway)) showLine(nameOf(railway), 'map');
      else clearMapSelection();
      return;
    }
    if (feature.source === 'selected-station') return;
    if (feature.source === 'selected-route') {
      if (feature.properties.id) showStation(feature.properties.id);
      else {
        clearMapSelection();
        new mapboxgl.Popup().setLngLat(feature.geometry.coordinates)
        .setText(`${feature.properties.number} · ${feature.properties.name}`).addTo(map);
      }
      return;
    }
    const name = feature.properties['name:zh'] || feature.properties.name;
    if (name && feature.geometry.type === 'Point') {
      if (feature.properties.kid) showStation(feature.properties.kid);
      else {
        clearMapSelection();
        search(name.replace(/站$/, ''), feature.geometry.coordinates);
      }
    } else clearMapSelection();
  });
}

start().catch(error => el('p', `本地服务读取失败（${error.message}）`, panel));
