'use strict';

const regionLive = (() => {
  const id='region-trains', layers=['region-count','region-label','region-heading','region-dot'];
  const status=document.querySelector('#region-status');

  let last='', focus, lastFocus, completeFrame;
  const runKey=(id,day)=>id+'/'+day;
  const empty=()=>({type:'FeatureCollection',features:[]});

  const boardSwitch=document.querySelector('#map-departure-board');
  const badges=new Map();

  function syncBadges(groups,time) {
    for (const [station,badge] of badges) {
      if (!groups.has(station)) { badge.marker.remove(); badges.delete(station); }
    }
    for (const [station,calls] of groups) {
      calls.sort((a,b)=>Number(a.terminal)-Number(b.terminal) ||
        (a.terminal ? b.arrival-a.arrival : a.until-b.until) || a.key.localeCompare(b.key));
      let badge=badges.get(station);
      if (!badge) {
        const {name,coord}=calls[0];
        const root=el('div',null,null,'dwell-marker');
        root.dataset.station=station;
        root.setAttribute('role','group');
        root.setAttribute('aria-label',name+' · 待发与刚终到列车');
        root.onclick=e=>e.stopPropagation();
        const marker=new mapboxgl.Marker({element:root,anchor:'bottom-left',offset:[7,-30]}).setLngLat(coord).addTo(map);
        badge={root,marker,rows:new Map()};
        badges.set(station,badge);
      }
      const signature=calls.map(c=>c.key).join('|');
      if (signature!==badge.signature) {
        badge.signature=signature;
        const rows=new Map();
        calls.forEach((call,index)=>{
          const key=call.key;
          let row=badge.rows.get(key);
          if (!row) {
            const button=action(badge.root,null,()=>showTrip(call.run.id,call.run.day,'region'),'dwell-badge');
            button.style.setProperty('--badge-ink',trainClasses[call.run.number[0]]?.[1] || ordinaryTrainColor);
            button.classList.toggle('terminal',call.terminal);
            button.title=call.terminal ? call.name+' · '+liveTimeline.iso(call.arrival).slice(11,16)+' 终到 · 已到站时长' :
              call.name+' · '+(call.run.origin ? '本站始发 · ' : liveTimeline.iso(call.arrival).slice(11,16)+' 到 · ')+
                liveTimeline.iso(call.until).slice(11,16)+' 开';
            el('b',call.run.number,button);
            el('span','–',button).setAttribute('aria-hidden','true');
            row={button,timer:el('time',null,button),call};
          }
          const at=badge.root.children[index];
          if (at!==row.button) badge.root.insertBefore(row.button,at || null);
          rows.set(key,row);
        });
        for (const [key,row] of badge.rows) if (!rows.has(key)) row.button.remove();
        badge.rows=rows;
        badge.second=null;
      }
      const second=Math.floor(time/1000);
      if (badge.second!==second) {
        badge.second=second;
        for (const row of badge.rows.values()) {
          const {terminal,arrival,until}=row.call;
          const seconds=terminal ? second-arrival/1000 : until/1000-second;
          const minutes=String(Math.floor(seconds/60)).padStart(2,'0');
          row.timer.textContent=minutes+':'+String(seconds%60).padStart(2,'0');
        }
      }
      for (const row of badge.rows.values()) {
        if (!row.call.terminal) continue;
        const remaining=row.call.until-time;
        const opacity=remaining<60000 ? String(remaining/60000) : '';
        if (row.button.style.opacity!==opacity) row.button.style.opacity=opacity;
      }
    }
  }

  function reset(message='') {
    spaceTime.reset();

    if (map?.getSource(id)) {
      for (const layer of layers) map.removeLayer(layer);
      map.removeSource(id);
    }
    if (badges.size) syncBadges(new Map(),0);
    last='';
    completeFrame=null;
    status.hidden=!message;
    status.textContent=message;
  }
  const trainFilters={
    'region-heading':['==','parked',false],
    'region-label':['has','label'],
    'region-dot':['any',['has','count'],['==','parked',true]]
  };
  function visibleFilter(layer) {
    const base=trainFilters[layer];
    if (!focus?.selected) return base;
    // These filters also apply to cached tiles while GeoJSON updates are in flight.
    return ['all',base,['any',['!=','id',focus.id],['!=','day',focus.day]]];
  }
  function setFocus(value) {
    const changed=focus?.selected!==value?.selected;
    focus=value;
    if (changed && map?.getSource(id)) {
      for (const layer of Object.keys(trainFilters)) map.setFilter(layer,visibleFilter(layer));
    }
  }
  function source() {
    if (map.getSource(id)) return map.getSource(id);
    map.addSource(id,{type:'geojson',data:empty()});
    const before='trip-stop';
    map.addLayer({id:layers[3],type:'circle',source:id,filter:visibleFilter(layers[3]),paint:{
      'circle-radius':['case',['has','count'],13,5],
      'circle-color':['case',['has','count'],'#17574e',trainClassStyle()],
      'circle-stroke-width':1.5,'circle-stroke-color':'#ffffff'}},before);
    const text={'text-font':['Noto Sans Regular'],'text-size':12};
    map.addLayer({id:layers[0],type:'symbol',source:id,filter:['has','count'],layout:{
      ...text,'text-field':['to-string',['get','count']],'text-allow-overlap':true},paint:{'text-color':'#ffffff'}},before);
    map.addLayer({id:layers[2],type:'symbol',source:id,filter:visibleFilter(layers[2]),layout:{
      'icon-image':trainClassStyle(true),'icon-allow-overlap':true,'icon-ignore-placement':true,
      'icon-rotate':['get','bearing'],'icon-rotation-alignment':'map'}},before);
    map.addLayer({id:layers[1],type:'symbol',source:id,minzoom:8,filter:visibleFilter(layers[1]),layout:{
      ...text,'symbol-sort-key':['case',['get','priority'],0,1],'text-field':['get','label'],...liveTrainLabelPlacement,'text-max-width':24,'text-padding':2
    },paint:{'text-color':'#17343a','text-halo-color':'white','text-halo-width':1.6}},map.getLayer('selected-train-caption') ? 'selected-train-caption' : stationSymbols.nameLayers[0]);
    return map.getSource(id);
  }

  function bounds() {
    const b=map.getBounds();
    const points=[b.getWest(),b.getEast()].flatMap(x=>[b.getSouth(),b.getNorth()].map(y=>fromMapCoordinates(x,y)));
    return [Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),
            Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];
  }
  function edgeKey(x,y,u,v) {
    // Static geometry stores geographic coordinates in ten-millionths of a degree.
    const a=Math.round(x*1e7), b=Math.round(y*1e7), c=Math.round(u*1e7), d=Math.round(v*1e7);
    return a<c || (a===c && b<=d) ? [a,b,c,d].join(',') : [c,d,a,b].join(',');
  }
  function routeEdges(geometry,legs) {
    const edges=new Set();
    for (const leg of legs || [[0,(geometry?.length || 0)-1]]) {
      if (!leg) continue;
      for (let i=leg[0]+1;i<=leg[1];i++) edges.add(edgeKey(...geometry[i-1],...geometry[i]));
    }
    return edges;
  }
  function locate(run,t) {
    if (t<run.start || t>=run.end) return null;
    const at=peerDistance(run.curve,t);
    if (at===null) return null;
    const path=run.path;
    const {coord:raw,i}=indexedTrackPoint(path,at);
    const departure=run.departures.findLast(([sec])=>sec<=t);
    const project=s=>indexedTrackPoint(path,s).coord;
    const coord=departure ? platformExit(project,departure[1],departure[2] || (departure[3] && toMapCoordinates(departure[3])),at) : project;
    const coordinate=coord===project ? raw : coord(at);
    const [,x,y]=path[i],[,u,v]=path[i+1];
    const bearing=Math.atan2((u-x)*Math.cos(coordinate[1]*Math.PI/180),v-y)*180/Math.PI;
    const parked=run.stops.some(([,a,b])=>a<=t && t<b);
    return {type:'Feature',geometry:{type:'Point',coordinates:coordinate},
      properties:{id:run.id,number:run.number,prefix:run.number[0],day:run.day,parked,bearing,label:runLabel(run,t),
        priority:Boolean(focus?.edges?.has(edgeKey(x,y,u,v)))}};
  }
  function cluster(features, zoom) {
    if (zoom>=8) return features;
    const cells=new Map(), priority=[];
    const width=360*44/(512*2**Math.floor(zoom));
    for (const f of features) {
      if (f.properties.priority) { priority.push(f); continue; }
      const [x,y]=f.geometry.coordinates;
      const merc=Math.log(Math.tan(Math.PI/4+y*Math.PI/360))*180/Math.PI;
      const key=Math.floor(x/width)+'/'+Math.floor(merc/width);
      const cell=cells.get(key);
      if (cell) {cell.x+=x;cell.y+=y;cell.n++;}
      else cells.set(key,{x,y,n:1,f});
    }
    return [...cells.values()].map(c=>c.n===1?c.f:{
      type:'Feature',properties:{count:c.n},geometry:{type:'Point',coordinates:[c.x/c.n,c.y/c.n]}}).concat(priority);
  }

  function tick(time,force=false) {
    if (!map || !map.getLayer('trip-stop')) {reset();return;}
    const zoom=map.getZoom(), b=bounds();
    if (zoom<5.5 || b[2]-b[0]>18 || b[3]-b[1]>13) {reset('放大地图可看区域车流');return;}
    status.hidden=false;
    const rate=liveTimeline.playbackRate;
    const canvas=map.getCanvas();
    const dx=96*(b[2]-b[0])/Math.max(1,canvas.clientWidth);
    const dy=96*(b[3]-b[1])/Math.max(1,canvas.clientHeight);
    const entry=spaceTime.windowAt(time,[b[0]-dx,b[1]-dy,b[2]+dx,b[3]+dy],rate,()=>tick(liveTimeline.time()));
    const src=source();
    const view=[zoom,...b,focus?.selected,boardSwitch.checked].join('/');
    let loading='';
    if (!entry?.complete) {
      loading=spaceTime.outdated ? '地图数据已更新，请刷新页面继续播放' : '正在读取本时段区域车流…';
      // During a handoff the old curves end at the five-minute boundary.
      // Publishing a partial frame here makes still-running trains disappear.
      if (completeFrame && completeFrame.view===view &&
          Math.abs(time-completeFrame.time)<300000) {
        const holding=loading+'（暂显 '+liveTimeline.iso(completeFrame.time).slice(11,19)+' 的位置）';
        if (status.textContent!==holding) status.textContent=holding;
        last='waiting';
        return;
      }
      completeFrame=null;
      if (!entry) {
        if (last!=='empty') src.setData(empty());
        if (badges.size) syncBadges(new Map(),time);
        last='empty';
        if (status.textContent!==loading) status.textContent=loading;
        return;
      }
    }
    const tag=[entry.key,time,view,loading].join('/');
    if (!force && tag===last && focus===lastFocus) return;
    lastFocus=focus;
    last=tag;
    const features=[], groups=new Map(), paths=new Map();
    const inView=trainViewport();
    function addCall(run,stop) {
      const [name,arrival,until,station,position,gtfs]=stop;
      const t=(time-run.midnight)/1000;
      if (t<arrival || t>=until) return false;
      if (!boardSwitch.checked) return true;
      const coord=position || (gtfs && toMapCoordinates(gtfs)) || (run.path && locate(run,t)?.geometry.coordinates);
      if (!coord) return false;
      if (!groups.has(station)) groups.set(station,[]);
      const terminal=Boolean(run.terminal);
      groups.get(station).push({name,coord,run,terminal,key:[runKey(run.id,run.day),terminal,arrival,until].join('/'),
        arrival:run.midnight+arrival*1000,until:run.midnight+until*1000});
      return true;
    }
    if (zoom>=8 && boardSwitch.checked) for (const call of entry.stationCalls || []) addCall(call,call.stop);
    const displaying=new Set();
    for (const run of entry.display || []) {
      const pose=displayMotion(run,(time-run.midnight)/1000);
      if (!pose) continue;
      const key=runKey(run.id,run.day);
      displaying.add(key);
      if (focus?.selected===key) continue;
      const coord=pose.coord(pose.at);
      if (zoom>=8 && !inView(coord)) continue;
      const a=pose.coord(pose.at-60), b=pose.coord(pose.at+60);
      const bearing=Math.atan2((b[0]-a[0])*Math.cos(coord[1]*Math.PI/180),b[1]-a[1])*180/Math.PI;
      const head={type:'Feature',geometry:{type:'Point',coordinates:coord},properties:{
        id:run.id,day:run.day,number:run.number,prefix:run.number[0],parked:false,priority:false,bearing,
        label:trainLabel(run.number,run.next,run.destination)}};
      features.push(head);
      paths.set(head,{pose});
    }
    for (const run of entry.runs) {
      const key=runKey(run.id,run.day);
      if (zoom>=8 && run.stops.some(stop=>addCall(run,stop))) continue;
      if (displaying.has(key) || focus?.selected===key) continue;
      const f=locate(run,(time-run.midnight)/1000);
      if (f) {
        if (zoom>=8 && !inView(f.geometry.coordinates)) continue;
        features.push(f);
        paths.set(f,run);
      }
    }
    syncBadges(groups,time);
    const visible=cluster(features,zoom).flatMap(head=>{
      const run=paths.get(head);
      if (!run || head.properties.parked) return [head];
      if (run.pose) return trainCarriages(head,run.pose.at,run.pose.coord,0);
      const t=(time-run.midnight)/1000;
      const at=peerDistance(run.curve,t);
      const departure=run.departures.findLast(([sec])=>sec<=t);
      const project=s=>indexedTrackPoint(run.path,s).coord;
      const coord=departure ? platformExit(project,departure[1],departure[2] || (departure[3] && toMapCoordinates(departure[3])),at) : project;
      return trainCarriages(head,at,coord,visibleTailStart(run,t));
    });
    src.setData({type:'FeatureCollection',features:visible});
    if (entry.complete) completeFrame={view,time};
    const message=loading || (!entry.covered?'该日没有时刻表覆盖':zoom<8?'区域车流 · 点击聚合数字放大':'区域车流 · 车身尖纹指向行进方向'+(boardSwitch.checked?'，▲ 待发倒计时，▼ 终到后计时':''));
    if (status.textContent!==message) status.textContent=message;
  }
  function selectPoint(event) {
    if (!map.getLayer(layers[3])) return false;
    const hits=map.queryRenderedFeatures(event.point,{layers});
    const hit=hits.find(feature=>feature.layer.id==='region-heading') || hits[0];
    if (!hit) return false;
    if (hit.properties.count) map.easeTo({center:hit.geometry.coordinates,zoom:Math.min(8,map.getZoom()+1.5)});
    else {
      showTrip(hit.properties.id,hit.properties.day,'region');
    }
    return true;
  }
  return {tick,selectPoint,runKey,routeEdges,setFocus};
})();
