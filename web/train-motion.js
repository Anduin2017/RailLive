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

// Geometry distances use the unshifted GTFS coordinates; apply the basemap
// transform only when drawing. All internal motion quantities use metres and seconds.
function motionPath(points) {
  const metres = Math.PI * 6371008.8 / 180;
  const segments = [];
  let length = 0;
  for (let i = 1; i < points.length; i++) {
    const [x,y] = points[i-1], [u,v] = points[i];
    const scale = metres * Math.cos((y+v)*Math.PI/360);
    const dx = (u-x)*scale, dy = (v-y)*metres;
    const size = Math.hypot(dx,dy);
    if (!size) continue;
    segments.push({x,y,u,v,scale,dx,dy,size,start:length,end:length+size});
    length += size;
  }
  function project(lon, lat, after = -1) {
    let best = null, gap = 1200;
    for (const s of segments) {
      if (s.end <= after || lat < Math.min(s.y,s.v)-0.012 || lat > Math.max(s.y,s.v)+0.012 ||
          lon < Math.min(s.x,s.u)-0.025 || lon > Math.max(s.x,s.u)+0.025) continue;
      const x = (lon-s.x)*s.scale, y = (lat-s.y)*metres;
      const f = Math.max(0, Math.min(1,(x*s.dx+y*s.dy)/(s.size*s.size)));
      const at = s.start + f*s.size, error = Math.hypot(x-f*s.dx,y-f*s.dy);
      if (at > after && error < gap) { best = at; gap = error; }
    }
    return best;
  }
  function point(at) {
    let lo=0, hi=segments.length-1;
    while (lo<hi) {
      const mid=(lo+hi)>>>1;
      if (segments[mid].end < at) lo=mid+1;
      else hi=mid;
    }
    const s=segments[lo], f=Math.max(0,Math.min(1,(at-s.start)/s.size));
    return [s.x+(s.u-s.x)*f,s.y+(s.v-s.y)*f];
  }
  function between(start,end) {
    return [point(start),...segments.filter(s=>s.end>start && s.end<end).map(s=>[s.u,s.v]),point(end)];
  }
  return {project, point, between, length};
}

function motionSections(path, lines) {
  const sections = [];
  const cache = new Map();
  for (const line of lines) {
    let previous = null;
    for (const [lon,lat,km,order] of line.points) {
      const key = lon + ',' + lat;
      if (!cache.has(key)) cache.set(key,path.project(lon,lat));
      const at = cache.get(key);
      if (at !== null && previous) {
        const distance = Math.abs(km-previous.km)*1000;
        const span = Math.abs(at-previous.at);
        // A detour, mileage reset, or a different parallel route invalidates this axis.
        if (distance > 0 && span > 0 && Math.abs(distance-span) < Math.min(distance*0.09,2400)) {
          const ranges=line.ranges.filter(([a,b])=>a<=previous.order && order<=b);
          const speed=ranges.length===1 ? ranges[0][2] : line.speed;
          sections.push({start:Math.min(at,previous.at), end:Math.max(at,previous.at),
            ratio:distance/span, line:{name:line.name,speed}});
        }
      }
      previous = at === null ? null : {at,km,order};
    }
  }
  return sections;
}

function motionLeg(start, end, duration, sections, fast) {
  if (!(duration > 0 && end > start)) return null;
  const acceleration=fast ? 0.175 : 0.11;
  const b=fast ? 0.5 : 0.38;
  const parts = sections.filter(s => s.start < end && s.end > start);
  // Station and line projections can differ by a few floating-point units.
  // Keep the exact stop anchors and merge sub-millimetre interior boundaries.
  const cuts = [start];
  for (const at of parts.flatMap(s=>[s.start,s.end]).sort((x,y)=>x-y)) {
    if (at-cuts.at(-1)>=0.001 && end-at>=0.001) cuts.push(at);
  }
  cuts.push(end);
  const nodes = [{at:start, distance:0, cap:Infinity}];
  let distance = 0, surveyed = 0;
  for (let i=1; i<cuts.length; i++) {
    const from=cuts[i-1], to=cuts[i], mid=(from+to)/2;
    const matches=parts.filter(s=>s.start<=mid && mid<s.end);
    const section=matches.length===1 ? matches[0] : null;
    const line=section?.line;
    // Broad class ceilings are model assumptions where vehicle details are absent.
    const cap=Math.min(line?.speed ?? Infinity, fast ? 350 : 200)/3.6;
    const span=(to-from)*(section?.ratio ?? 1);
    if (section) surveyed+=span;
    const steps=Math.max(2,Math.ceil(span/750));
    nodes.at(-1).cap=Math.min(nodes.at(-1).cap,cap);
    for (let j=1;j<=steps;j++) {
      nodes.push({at:from+(to-from)*j/steps,distance:distance+span*j/steps,cap,line});
    }
    distance+=span;
  }
  // Forward traction and backward braking envelopes, including changes of ceiling.
  nodes[0].cap=0;
  for (let i=1;i<nodes.length;i++) {
    nodes[i].limit=nodes[i].cap;
  }
  function envelope(a) {
    let peak=0;
    for (let i=1;i<nodes.length;i++) {
      const prev=nodes[i-1], n=nodes[i];
      n.cap=Math.min(n.limit,Math.sqrt(prev.cap**2+2*a*(n.distance-prev.distance)));
    }
    nodes.at(-1).cap=0;
    for (let i=nodes.length-2;i>=0;i--) {
      const n=nodes[i], next=nodes[i+1];
      n.cap=Math.min(n.cap,Math.sqrt(next.cap**2+2*b*(next.distance-n.distance)));
      peak=Math.max(peak,n.cap);
    }
    return peak;
  }
  function travel(cruise, save=false) {
    let seconds=0, v0=0;
    for (let i=1;i<nodes.length;i++) {
      const n=nodes[i], v=Math.min(cruise,n.cap);
      seconds+=2*(n.distance-nodes[i-1].distance)/(v0+v);
      if (save) { n.v=v; n.time=seconds; }
      v0=v;
    }
    return seconds;
  }
  let lo=0, hi=envelope(acceleration);
  if (!(travel(hi)<=duration+1e-5)) {
    // For a tight schedule find the gentlest feasible start up to the old cap.
    let slow=acceleration, feasible=2*acceleration;
    hi=envelope(feasible);
    if (!(travel(hi)<=duration+1e-5)) return null;
    for (let i=0;i<12;i++) {
      const mid=(slow+feasible)/2;
      if (travel(envelope(mid))>duration+1e-5) slow=mid;
      else feasible=mid;
    }
    hi=envelope(feasible);
  }
  for (let i=0;i<45;i++) {
    const mid=(lo+hi)/2;
    if (travel(mid)>duration) lo=mid;
    else hi=mid;
  }
  nodes[0].v=nodes[0].time=0;
  travel(hi,true);
  function sample(seconds) {
    let lo=1, hi=nodes.length-1;
    while (lo<hi) {
      const mid=(lo+hi)>>>1;
      if (nodes[mid].time<seconds) lo=mid+1;
      else hi=mid;
    }
    const n=nodes[lo],prev=nodes[lo-1];
    const dt=Math.min(n.time-prev.time,Math.max(0,seconds-prev.time));
    const speed=prev.v+(n.v-prev.v)*dt/(n.time-prev.time);
    const f=(prev.v+speed)*dt/2/(n.distance-prev.distance);
    return {at:prev.at+(n.at-prev.at)*f,speed:speed*3.6,line:n.line};
  }
  return {sample,distance,duration,surveyed,peakKmh:hi*3.6};
}

function matchTrainPath(trip) {
  if (!trip.geometry?.length) return null;
  const path=motionPath(trip.geometry);
  if (!path.length) return null;
  const distances=[0];
  if (trip.inferredLegs) {
    for (let i=1;i<trip.geometry.length;i++) {
      const [x,y]=trip.geometry[i-1], [u,v]=trip.geometry[i];
      distances.push(distances[i-1]+Math.PI*6371008.8/180*Math.hypot((u-x)*Math.cos((y+v)*Math.PI/360),v-y));
    }
  }
  let previous=-1;
  const stops=trip.stops.map((stop,i)=>{
    const coord=hasPosition(stop) ? [stop.lon,stop.lat] : stop.mapPosition && fromMapCoordinates(...stop.mapPosition);
    let at=trip.inferredLegs ? distances[trip.inferredLegs[i]?.[0] ?? trip.inferredLegs[i-1]?.[1]] ?? null : coord ? path.project(...coord,previous) : null;
    if (at===null && !trip.inferredLegs && hasPosition(stop) && stop.mapPosition)
      at=path.project(...fromMapCoordinates(...stop.mapPosition),previous);
    if (at!==null) previous=at;
    return {name:stop.name,at,arrival:timetableSeconds(stop.arrival),departure:timetableSeconds(stop.departure)};
  });
  return {path,stops};
}

function makeTrainMotion(trip) {
  const matched=trip.matchedPath;
  if (!matched) return null;
  const {path,stops}=matched;
  if (stops.some((s,i)=>s.arrival===null || s.departure===null || s.departure<s.arrival ||
      (i>0 && (s.arrival<stops[i-1].departure ||
       (s.arrival===stops[i-1].departure && trip.stops[i].id!==trip.stops[i-1].id))))) {
    return {path,state:()=>null,reason:'原始到发时刻存在矛盾，本班仅展示计划时刻。'};
  }
  const sections=motionSections(path,trip.motionLines || []);
  const legs=stops.slice(1).map((stop,i)=>{
    if (trip.inferredLegs && !trip.inferredLegs[i]) return null;
    const from=stops[i];
    if ([stop.at,from.at,stop.arrival,from.departure].includes(null)) return null;
    return motionLeg(from.at,stop.at,stop.arrival-from.departure,sections,/^[GDC]/i.test(trip.number));
  });
  function state(seconds) {
    for (let i=0;i<stops.length;i++) {
      const stop=stops[i];
      if (stop.arrival!==null && seconds>=stop.arrival && seconds<stop.departure && stop.at!==null)
        return {at:stop.at,speed:0,label:'停靠 · '+stop.name};
      const next=stops[i+1],leg=legs[i];
      if (leg && seconds>=stop.departure && seconds<next.arrival)
        return {...leg.sample(seconds-stop.departure),leg,label:stop.name+' → '+next.name};
    }
    return null;
  }
  return {path,state};
}
