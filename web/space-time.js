'use strict';

// Immutable, snapshot-scoped files. Only the small manifest is mutable.
const spaceTime = (() => {
  const cache = new Map(), pending = new Map(), failed = new Map();
  const stats = {received:0, speculative:0, usedPrefetch:0, evictedUnused:0, peakBytes:0, bytes:0, loads:0};
  const budget = 48 * 1024 * 1024;
  const previous=new Map(), attempted=new Set();
  let manifest, catalog, boot, base, changed=0, notify, raf=0, plan=[], visible=[];
  let desired=new Set(), nextUpdate=0, latency=250, previousCenter, pan=[0,0], assembled, lastPlan='';
  const refresh = () => {
    if (!raf) raf=requestAnimationFrame(()=>{raf=0;notify?.();});
  };

  async function read(url,signal) {
    const res=await fetch(url,{signal});
    if (!res.ok) throw Error(res.status);
    const blob=await res.blob();
    const decoded=new Response(blob.stream().pipeThrough(new DecompressionStream('gzip')));
    if(url.includes('/g/')) {
      const words=new Int32Array(await decoded.arrayBuffer());
      const blocks=new Map();
      for(let i=1+2*words[0];i<words.length;) {
        blocks.set(words[i],i+2);
        i+=2+words[i+1];
      }
      return {data:{words,blocks},wire:blob.size,size:words.byteLength+blocks.size*48};
    }
    const text=await decoded.text();
    return {data:JSON.parse(text),wire:blob.size,size:text.length*3};
  }
  function init() {
    if (!boot) boot=(async()=>{
      const r=await fetch('/space-time/manifest.json',{cache:'no-cache'});
      if (!r.ok) throw Error(r.status);
      manifest=await r.json();
      base='/space-time/'+manifest.version+'/';
      catalog=(await read(base+'catalog.gz')).data;
      catalog.base=Date.parse(catalog.base+'T00:00:00+08:00');
      catalog.days=new Set();
      catalog.geo=new Set(catalog.homes);
      for (const [service,days] of Object.entries(catalog.services)) {
        catalog.services[service]=new Set(days);
        for(const d of days) catalog.days.add(d);
      }
      refresh();
    })().catch(()=>{setTimeout(()=>{boot=null;init();},2000)});
    return boot;
  }
  function touch(key,used) {
    const v=cache.get(key);
    if (v) {
      cache.delete(key);cache.set(key,v);
      if (used && v.prefetch) {v.prefetch=false;stats.usedPrefetch+=v.wire;}
    }
    return v;
  }
  function trim() {
    if (stats.bytes<=budget) return;
    for (const [key,v] of cache) {
      if (desired.has(key)) continue;
      if (v.prefetch) stats.evictedUnused+=v.wire;
      stats.bytes-=v.size;cache.delete(key);
      if (stats.bytes<=budget) break;
    }
  }
  function pump() {
    const now=performance.now();
    for (const job of plan) {
      if(pending.size>=(job.priority===0?48:6)) break;
      const {key,priority}=job;
      if(priority>0 && attempted.has(key))continue;
      if(cache.has(key)||pending.has(key)||(failed.get(key)||0)>now) continue;
      const controller=new AbortController();
      pending.set(key,controller);
      read(base+key+'.gz',controller.signal).then(v=>{
        if(controller.signal.aborted) return;
        const elapsed=performance.now()-now;
        latency=.85*latency+.15*elapsed;
        v.prefetch=priority>0;
        if(v.prefetch)attempted.add(key);
        stats.received+=v.wire;stats.loads++;
        if(v.prefetch) stats.speculative+=v.wire;
        if(!key.startsWith('g/')) {
          const deps=new Set();
          for(const row of v.data)if(row[0]!==2)
            for(const [id] of row[row[0]===0?4:8])deps.add(catalog.homes[id]);
          v.deps=[...deps];
          nextUpdate=0;
          refresh();
        }
        cache.set(key,v);stats.bytes+=v.size;trim();
        stats.peakBytes=Math.max(stats.peakBytes,stats.bytes);
        if(desired.has(key)) {changed++;refresh();}
        failed.delete(key);
      }).catch(()=>{
        if (!controller.signal.aborted) {
          failed.set(key,performance.now()+2000);setTimeout(refresh,2050);
        }
      }).finally(()=>{
        if(pending.get(key)===controller)pending.delete(key);
        pump();
      });
    }
  }
  function slotKey(cell,bucket) {
    const slot=((bucket%288)+288)%288, bits=catalog.cells[cell];
    if(!bits || !(parseInt(bits.slice(2*(slot>>3),2*(slot>>3)+2),16)&(1<<(slot%8))))return null;
    return cell+'/'+slot;
  }
  function schedule(time,b,rate) {
    // Bucket zero is midnight Beijing time. Distances outside the visible
    // rectangle are measured in grid cells; time is measured in five-minute slices.
    const span=manifest.step, W=300000, local=time+28800000;
    const bucket=Math.floor(local/W), phase=local/W-bucket;
    const box=b.map(x=>Math.floor(x/span));
    const cx=(b[0]+b[2])/(2*span), cy=(b[1]+b[3])/(2*span);
    if(previousCenter) {
      const dx=cx-previousCenter[0],dy=cy-previousCenter[1];
      if(Math.abs(dx)+Math.abs(dy)>.01)pan=[Math.sign(dx),Math.sign(dy)];
    }
    previousCenter=[cx,cy];
    const forward=rate>=0, remaining=(forward?1-phase:phase)*W;
    const lead=Math.abs(rate)*Math.max(1500,latency*4);
    const horizon=W+Math.abs(rate)*Math.max(15000,latency*4);
    const depth=Math.min(4,Math.floor(horizon/W));
    const urgent=rate!==0 && remaining<=lead;
    const stamp=[...box,bucket,Math.sign(rate),depth,urgent,...pan].join('/');
    if(stamp===lastPlan && performance.now()<nextUpdate) return;
    if(stamp!==lastPlan)attempted.clear();
    lastPlan=stamp;nextUpdate=performance.now()+800;
    const jobs=new Map(), current=[];
    desired=new Set();
    function add(key,priority) {
      if(priority===0)desired.add(key);
      if(!jobs.has(key)||jobs.get(key)>priority)jobs.set(key,priority);
    }
    for(let x=box[0]-2;x<=box[2]+2;x++) for(let y=box[1]-2;y<=box[3]+2;y++){
      const cell=x+'_'+y;
      if(!catalog.cells[cell])continue;
      const dx=x-Math.max(box[0],Math.min(box[2],x)),dy=y-Math.max(box[1],Math.min(box[3],y));
      const spatial=Math.hypot(dx/(dx*pan[0]>0?2:1.3),dy/(dy*pan[1]>0?2:1.3));
      for(let dt=-depth;dt<=depth;dt++){
        const ahead=forward?dt:-dt;
        const temporal=ahead>=0?ahead/depth:-ahead;
        if(spatial*spatial+temporal*temporal>1.001)continue;
        const key=slotKey(cell,bucket+dt);
        if(!key)continue;
        const priority=!spatial&&!dt?0:!spatial&&ahead===1&&urgent?.01:.1+spatial+temporal;
        add(key,priority);
        const deps=cache.get(key)?.deps;
        if(deps)for(const home of deps)add('g/'+home,priority);
        else if(catalog.geo.has(x*1024+y))add('g/'+(x*1024+y),priority);
        if(priority===0)current.push([cell,key]);
      }
    }
    visible=current;
    plan=Array.from(jobs,([key,priority])=>{
      const [x,y]=key.startsWith('g/')
        ? [Math.floor(Number(key.slice(2))/1024),Number(key.slice(2))%1024]
        : key.split('/')[0].split('_').map(Number);
      return {key,priority,distance:Math.hypot(x+.5-cx,y+.5-cy)};
    }).sort((a,b)=>a.priority-b.priority || a.distance-b.distance);
    for(const [key,c] of pending)if(!jobs.has(key))c.abort();
    trim();pump();
  }
  function pathOf(refs,packs) {
    const path=[];
    for(const [key,offset] of refs) {
      const {words,blocks}=packs.get(catalog.homes[key]);
      const start=blocks.get(key), end=start+words[start-1];
      for(let i=start+(path.length?2:0);i<end;i+=2) {
        const vertex=1+2*words[i];
        path.push([offset+words[i+1]/100,words[vertex]/1e7,words[vertex+1]/1e7]);
      }
    }
    return path;
  }
  function windowAt(time,b,rate,callback) {
    notify=callback;
    if(!catalog){init();return null;}
    schedule(time,b,rate);
    const day=Math.floor((time-catalog.base)/86400000);
    const tag=[lastPlan,changed,day].join('|');
    if(assembled?.key===tag && assembled.complete)return assembled;
    const seen=new Set(), dates=new Map(), runs=[],display=[],stationCalls=[];
    const bucket=Math.floor(time/300000), onScreen=new Set();
    const current=[], waiting=[];
    for(const [cell,key] of visible) {
      const tile=touch(key,true);
      if(tile && tile.deps.every(home=>cache.has('g/'+home)))current.push([cell,tile,false]);
      else waiting.push([cell,tile,true]);
    }
    const ready=current.length;
    for(const [cell,tile,fallback] of current.concat(waiting)) {
      onScreen.add(cell);
      let record=previous.get(cell);
      if(fallback) {
        if(!record || Math.abs(record.bucket-bucket)>1)continue;
      } else {
        if(record?.bucket!==bucket || record.tile!==tile) {
          const packs=new Map(tile.deps.map(home=>[home,touch('g/'+home,true).data]));
          record={tile,packs,bucket,day,paths:new Map()};
          previous.set(cell,record);
        }
      }
      for(const row of record.tile.data) {
        const [type,id,offset]=row, [number,service,start,end,destination]=catalog.runs[id];
        const serviceDay=record.day-offset;
        if(!catalog.services[service].has(serviceDay))continue;
        const instance=[type,id,serviceDay,type?row[3]:''].join('/');
        if(seen.has(instance))continue;
        const midnight=catalog.base+serviceDay*86400000;
        if(!dates.has(serviceDay))dates.set(serviceDay,liveTimeline.iso(midnight).slice(0,10));
        const run={id,number,midnight,day:dates.get(serviceDay),start,end,destination};
        const sec=(time-midnight)/1000;
        let path=record.paths.get(row);
        if(type!==2 && !path) {
          path=pathOf(row[type===0?4:8],record.packs);
          record.paths.set(row,path);
        }
        if(type===0) {
          if(fallback) {
            const at=peerDistance(row[3],sec);
            if(at==null || at<path[0][0] || at>path.at(-1)[0])continue;
          }
          runs.push({...run,curve:row[3],path,departures:row[5],arrivals:row[6],stops:row[7]});
        } else if(type===1) {
          const leg={...run,seq:row[3],start:row[4],end:row[5],kind:row[6],length:row[7],path,next:row[9]};
          if(fallback) {
            const pose=displayMotion(leg,sec);
            if(!pose || pose.at<leg.path[0][0] || pose.at>leg.path.at(-1)[0])continue;
          }
          display.push(leg);
        } else stationCalls.push({...run,origin:row[3]===0,terminal:row[5]==='terminal',stop:row[4]});
        seen.add(instance);
      }
    }
    for(const cell of previous.keys())if(!onScreen.has(cell))previous.delete(cell);
    stats.complete=ready===visible.length;
    stats.visible=visible.length;
    stats.peakPreparedPoints=Math.max(stats.peakPreparedPoints||0,[...runs,...display].reduce((n,r)=>n+r.path.length,0));
    if(!ready && visible.length && !runs.length && !display.length && !stationCalls.length)return null;
    const modeled=new Set(runs.map(r=>r.id+'/'+r.day));
    assembled={key:tag,runs,display,stationCalls:stationCalls.filter(r=>r.origin||r.terminal||!modeled.has(r.id+'/'+r.day)),
      covered:catalog.days.has(day)||runs.length||display.length||stationCalls.length,
      complete:ready===visible.length};
    return assembled;
  }
  function reset() {
    for(const c of pending.values())c.abort();
    desired.clear();attempted.clear();previous.clear();visible=[];plan=[];assembled=null;lastPlan='';previousCenter=null;pan=[0,0];trim();
  }
  // Fetch version and calendar while the base map initializes.
  init();
  return {windowAt,reset,stats};
})();
