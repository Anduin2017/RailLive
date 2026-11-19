'use strict';

function peerDistance(samples, t) {
  if (!samples?.length || t<samples[0][0] || t>samples.at(-1)[0]) return null;
  let lo=0, hi=samples.length-1;
  while (lo<hi) {
    const mid=(lo+hi+1)>>>1;
    if (samples[mid][0]<=t) lo=mid;
    else hi=mid-1;
  }
  const [time,at]=samples[lo];
  if (t===time) return at;
  const next=samples[lo+1];
  if (at===null || !next || next[1]===null) return null;
  return at+(next[1]-at)*(t-time)/(next[0]-time);
}

// A common axis exists only while both positions are inside the same
// connected, same-direction run of matched shape blocks.
function sharedDistance(spans, own, other) {
  for (const span of spans) {
    const a=span[0], b=span.at(-1);
    if (own<a[0] || own>b[0] || other<a[1] || other>b[1]) continue;
    for (let i=1;i<span.length;i++) {
      const left=span[i-1],right=span[i];
      if (other<=right[1]) return left[0]+(other-left[1])*(right[0]-left[0])/(right[1]-left[1]);
    }
  }
  return null;
}

function trainRelationView(trip, motionHost) {
  const fold=el('details',null,null,'trip-extra');
  el('summary','同线前后车与越行',fold);
  const host=el('section',null,fold,'relation-card');
  motionHost.after(fold);
  fold.hidden=true;
  let mapFocus, edges;
  let version=0, data, current, nearest, lastDraw, lastRows;
  const clock = t => liveTimeline.iso(current.serviceMidnight+t*1000).slice(11,16);
  const dateOf = p => liveTimeline.iso(current.serviceMidnight+p.offset*86400000).slice(0,10);
  const nameOf = p => p.number+(p.offset ? '（'+dateOf(p).slice(5)+' 始发）' : '');
  function clear() {
    version++;
    mapFocus=null;
    regionLive.setFocus(null);
    current=data=null;
    lastRows=lastDraw=null;
    fold.hidden=true;
    host.replaceChildren();
  }
  function renderDetails(peerIndex, box) {
    box.replaceChildren();
    const peer=data.peers[peerIndex];
    if (!peer) return;
    action(box,'跟随 '+nameOf(peer),()=>showTrip(peer.id,dateOf(peer),trip.liveLine));
    if (peer.gaps.length) {
      const table=el('table',null,box);
      const header=el('tr',null,table.createTHead());
      for (const s of ['共同站','到达间隔','发车间隔']) el('th',s,header);
      function cell(row,time,delta) {
        const td=el('td',null,row);
        if (delta===null) { td.textContent='—'; return; }
        td.textContent=(delta>0?'晚 ':delta<0?'早 ':'')+Math.abs(delta)/60+' 分';
        td.title=trip.number+' '+clock(time)+' / '+peer.number+' '+clock(time+delta);
      }
      for (const [name,a,ad,d,dd] of peer.gaps) {
        const row=el('tr',null,table);
        el('td',name,row); cell(row,a,ad); cell(row,d,dd);
      }
    }
    for (const event of data.events.filter(e=>e.peer===peerIndex)) {
      const fast=event.fast?trip.number:peer.number, slow=event.fast?peer.number:trip.number;
      const info=el('div',null,box,'relation-event');
      el('strong',event.pass ? '候选越行 · '+event.pass[0] : '顺序翻转 · 地点待定',info);
      el('small',event.up+' 发车 '+clock(event.departure[0])+' / '+clock(event.departure[1])+
        '；'+event.down+' 到达 '+clock(event.arrival[0])+' / '+clock(event.arrival[1])+'（本车 / 对照车）',info);
      if (event.pass) {
        const [station,at,arrival,departure]=event.pass;
        el('p',slow+' 计划在'+station+'停靠 '+clock(arrival)+'–'+clock(departure)+
          '，'+fast+' 推算约 '+clock(Math.round(at/60)*60)+' 通过。',info);
        el('small','按两车在共同区段换序一次、快车在慢车停车期间通过推演。',info);
        action(info,'8× 回放',()=>replay(event));
      } else {
        el('p',event.sites?.length ? '可行停站窗口：'+event.sites.join('、')+'。具体通过地点或时刻待定。' :
          '两站车序已改变，共同径路或站内通过时间不足以定位越行。',info);
      }
    }
  }
  function replay(event) {
    const t=event.pass[1]-45;
    liveTimeline.replay(current.serviceMidnight+t*1000);
    const path=trip.matchedPath?.path;
    const distance=peerDistance(data.peers[event.peer].curve,t);
    // For the camera use the known station, even when the selected train is the waiting one.
    const stop=trip.stops.find(s=>s.name===event.pass[0]);
    const coord=stop?.mapPosition || (stop && hasPosition(stop) && toMapCoordinates([stop.lon,stop.lat]));
    if (map && coord) map.jumpTo({center:coord,zoom:Math.max(9,map.getZoom())});
    else if (map && path && distance!==null) {
      const peer=data.peers[event.peer];
      for (const span of peer.spans) {
        const at=sharedDistance([span],span[0][0],distance);
        if (at!==null) { map.jumpTo({center:toMapCoordinates(path.point(at)),zoom:Math.max(9,map.getZoom())}); break; }
      }
    }
  }
  function build() {
    host.replaceChildren();
    el('h3','同线前后车',host);
    nearest=el('div',null,host,'relation-nearest');
    el('small','前后车仅统计已匹配同向径路的班次，间距按沿线里程估算。',host);
    if (!data.peers.length) { nearest.textContent='没有可比较的班次。'; return; }
    const label=el('label','对照车次 · 相对本车的计划间隔',host);
    const select=el('select',null,label);
    const ranked=data.peers.map((p,i)=>{
      const deltas=p.gaps.flatMap(g=>[g[2],g[4]]).filter(d=>d!==null);
      const close=deltas.filter(d=>Math.abs(d)<=15*60).length;
      return {i,score:(p.spans.length ? 1 : 0)*10000+close*100+deltas.length};
    }).sort((a,b)=>b.score-a.score || data.peers[a.i].number.localeCompare(data.peers[b.i].number));
    for (const {i} of ranked) { const o=el('option',nameOf(data.peers[i]),select); o.value=i; }
    const detail=el('div',null,host,'relation-detail');
    function choose(i) {
      select.value=i; lastDraw=null;
      renderDetails(i,detail);
    }
    select.onchange=()=>choose(Number(select.value));
    choose(ranked[0].i);
    const passes=data.events.filter(e=>e.pass).sort((a,b)=>a.pass[1]-b.pass[1]);
    const unresolved=data.events.filter(e=>!e.pass);
    if (unresolved.length) {
      const group=el('details',null,host);
      el('summary','换序地点待定 · '+unresolved.length,group);
      for (const e of unresolved) {
        action(group,nameOf(data.peers[e.peer])+' · '+e.up+' → '+e.down,()=>choose(e.peer));
      }
    }
    if (passes.length) {
      const group=el('details',null,host);
      el('summary','沿途候选越行 · '+passes.length,group);
      for (const event of passes) {
        action(group,clock(Math.round(event.pass[1]/60)*60)+' '+event.pass[0]+' · '+nameOf(data.peers[event.peer]),()=>{
          choose(event.peer); replay(event);
        });
      }
    }
  }
  let requested=false;
  function load(run) {
    current=run;
    fold.hidden=false;
    requested=false;
    if (fold.open) read(run);
  }
  async function read(run) {
    requested=true;
    const token=++version;
    host.textContent='正在匹配同向径路与班次…';
    try {
      const day=liveTimeline.iso(run.serviceMidnight).slice(0,10);
      const result=await get('/api/relations/'+encodeURIComponent(trip.id)+'?day='+day);
      if (version!==token || !host.isConnected) return;
      data=result;
      for (const e of data.events) {
        const p=data.peers[e.peer];
        (p.events??=[]).push(e);
      }
      build();
      liveTimeline.refreshPausedMap();
    } catch {
      if (version!==token || !host.isConnected) return;
      host.textContent='车序资料读取失败。';
      action(host,'重试',()=>read(run));
    }
  }
  fold.ontoggle=()=>{
    lastDraw=null;
    if (fold.open && current && !requested) read(current);
  };
  function draw(run,time,sample) {
    if (current!==run) return;
    if (sample) edges ??= regionLive.routeEdges(trip.geometry,trip.inferredLegs);
    const day=liveTimeline.iso(run.serviceMidnight).slice(0,10);
    const selected=sample ? regionLive.runKey(trip.id,day) : null;
    if (mapFocus?.selected!==selected) {
      mapFocus={selected,id:trip.id,day,edges:sample ? edges : null};
      regionLive.setFocus(mapFocus);
    }
    if (sample?.display) sample=null;
    if (!data?.peers.length || !fold.open) return;
    const tick=liveTimeline.playing ? Math.floor(time/200) : time;
    if (tick===lastDraw) return;
    lastDraw=tick;
    const t=(time-run.serviceMidnight)/1000;
    let ahead,behind,tied;
    if (sample) {
      data.peers.forEach((peer,i)=>{
        const pos=peerDistance(peer.curve,t);
        if (pos===null) return;
        const at=sharedDistance(peer.spans,sample.at,pos);
        if (at===null) return;
        if (peer.events?.some(e=>!e.pass && Math.min(...e.departure)<=t && t<=Math.max(...e.arrival))) return;
        const gap=at-sample.at;
        // Do not display an interpolated inversion that contradicts the two
        // surrounding timetable anchors (or the chosen station crossing).
        for (let k=1;k<peer.gaps.length;k++) {
          const [, , , dep, d0]=peer.gaps[k-1], [,arr,d1]=peer.gaps[k];
          if (dep===null || arr===null || t<Math.max(dep,dep+d0) || t>Math.min(arr,arr+d1)) continue;
          if (d0*d1>0 && gap*d0>0 && Math.abs(gap)>=100) return;
        }
        if (peer.events?.some(e=>e.pass && Math.max(...e.departure)<t && t<Math.min(...e.arrival)
          && (e.fast ? 1 : -1)*(t-e.pass[1])*gap>0 && Math.abs(gap)>=100)) return;
        const row={i,at,gap};
        if (Math.abs(gap)<100) tied=row;
        else if (gap>0 && (!ahead || gap<ahead.gap)) ahead=row;
        else if (gap<0 && (!behind || gap>behind.gap)) behind=row;
      });
    }
    const labels=[[ahead,'前车'],[behind,'后车']];
    if (tied) labels.push([tied,'近同里程']);
    const rows=labels.map(([v,label])=>!v ? label+' · 暂无可判定班次' :
      label+' · '+nameOf(data.peers[v.i])+'　'+(Math.abs(v.gap)/1000).toFixed(1)+' km');
    for (const e of data.events) {
      if (!e.pass || t<e.pass[2] || t>=e.pass[3]) continue;
      const [,cross]=e.pass, who=nameOf(data.peers[e.peer]);
      const verb=e.fast ? '越过 '+who : '被 '+who+' 越过';
      rows.push(e.pass[0]+' · '+(t<cross?'预计 '+Math.ceil((cross-t)/60)+' 分钟后':'推演中已')+verb);
    }
    if (lastRows!==rows.join('|')) {
      nearest.replaceChildren();
      rows.forEach((row,index)=>{
        const neighbor=labels[index]?.[0];
        if (!neighbor) { el('div',row,nearest); return; }
        const peer=data.peers[neighbor.i];
        action(nearest,row,()=>showTrip(peer.id,dateOf(peer),trip.liveLine));
      });
      lastRows=rows.join('|');
    }
  }
  return {load,draw,clear};
}
