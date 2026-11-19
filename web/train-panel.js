'use strict';

function scheduledStopTimes(stop, terminal) {
  const arrival = timetableSeconds(stop.arrival), departure = timetableSeconds(stop.departure);
  const invalid = !terminal && arrival !== null && departure !== null && arrival > departure;
  return {arrival: invalid ? null : arrival, departure};
}

function trainMotionView(trip, host, runPicker) {
  let markerSource, model, position, lastCaption, lastParked, lastZoom, run, lastAt, lastSecond;
  let lastLeg, departureAt, captionSecond, captionSpeed;
  let cameraMode='static', heading, previousView, lastTrack;
  host.className='journey';
  const calls=trip.stops.map((stop,index)=>{
    const {arrival, departure} = scheduledStopTimes(stop, index === trip.stops.length - 1);
    return {...stop, arrive: arrival, depart: departure};
  });
  const routeLabel=[trip.origin || calls[0]?.name, trip.destination || calls.at(-1)?.name]
    .filter(Boolean).join(' --> ');
  const hero=el('section',null,host,'journey-hero');
  host.insertBefore(hero,runPicker);
  const details=el('div',null,host);
  hero.hidden=details.hidden=true;
  const header=el('div',null,hero,'journey-header');
  const stateLabel=el('strong',null,header);
  const cameras=el('div',null,header,'journey-cameras');
  const follow=action(cameras,'跟随',()=>changeCamera(cameraMode==='map' ? 'static' : 'map'));
  const navigation=action(cameras,'列车视角',()=>changeCamera(cameraMode==='navigation' ? 'static' : 'navigation'));
  const jump=action(cameras,'跳到始发',()=>liveTimeline.jump(run.start));
  function placeCamera(options={}) {
    if (!position) return;
    if (cameraMode==='navigation') {
      map.easeTo({...options,center:position,bearing:heading,
        offset:[0,map.getCanvas().clientHeight*.15],duration:0});
    } else if (cameraMode==='map') {
      const shift = mobileFocusShift();
      if (shift) map.easeTo({...options,center:position,offset:[0,-shift],duration:0});
      else map.jumpTo({...options,center:position});
    }
  }

  function detachCamera() {
    if (cameraMode==='navigation') changeCamera('detached');
    else if (cameraMode==='map') changeCamera('static');
  }

  function changeCamera(mode) {
    if (mode===cameraMode || ((mode==='map' || mode==='navigation') && !position)) return;
    const wasNavigation=cameraMode==='navigation' || cameraMode==='detached';
    const enteringNavigation=mode==='navigation' && !wasNavigation;
    if (enteringNavigation) previousView={pitch:map.getPitch(),bearing:map.getBearing()};
    map.off('dragstart',detachCamera);
    map.off('resize',onResize);
    cameraMode=mode;
    follow.setAttribute('aria-pressed',mode==='map');
    navigation.setAttribute('aria-pressed',mode==='navigation');
    navigation.textContent=mode==='detached' ? '继续列车视角' : '列车视角';
    if (mode==='map' || mode==='navigation') {
      map.on('dragstart',detachCamera);
      map.on('resize',onResize);
    }
    if (mode==='navigation') {
      if (matchMedia('(max-width:680px)').matches) {
        setPanelWindow('closed');
        sidebarToggle.focus({preventScroll:true});
      }
      placeCamera({pitch:60,zoom:enteringNavigation ? Math.max(13,map.getZoom()) : map.getZoom()});
    } else if (mode==='map') {
      placeCamera({...previousView,pitch:0});
    } else if (mode==='static' && wasNavigation) {
      map.jumpTo(previousView);
    }
    if (mode==='map' || mode==='static') previousView=null;
  }
  function onResize() { placeCamera(); }
  follow.setAttribute('aria-pressed','false');
  navigation.setAttribute('aria-pressed','false');
  const speedBox=el('div',null,hero,'journey-speed');
  const speed=el('b',null,speedBox);
  el('span','km/h · 推算速度',speedBox);
  const info=el('div',null,hero,'journey-arrival');
  const target=el('div',null,info), eta=el('div',null,info);
  const targetLabel=el('small',null,target), targetName=el('strong',null,target);
  const etaLabel=el('small',null,eta), etaValue=el('strong',null,eta);
  const note=el('small',null,hero,'journey-note');
  const plan=el('section',null,details,'journey-plan');
  const planHeading=el('div',null,plan,'journey-header');
  el('h3','全程停站',planHeading);
  const remaining=el('span',null,planHeading);
  const track=el('div',null,plan,'journey-track');
  track.setAttribute('role','progressbar');
  track.setAttribute('aria-label','计划行程时间进度');
  const ends=el('div',null,plan,'journey-ends');
  el('span',trip.origin,ends);
  el('span',trip.destination,ends);
  const list=el('ol',null,plan,'journey-stops');
  const first=calls[0]?.depart, end=calls.at(-1)?.arrive;
  const fraction=t=>Math.max(0,Math.min(1,(t-first)/(end-first || 1)));
  const rows=calls.map((stop,i)=>{
    const at=i===0 ? stop.depart : stop.arrive;
    if (at!==null && first!==null && end!==null) {
      const dot=el('i',null,track);
      dot.style.left=fraction(at)*100+'%';
      dot.title=stop.name+' · '+showTime(i===0 ? stop.departure : stop.arrival);
    }
    const row=el('li',null,list);
    const scheduled=el('time',null,row);
    action(row,stop.name,()=>showStation(stop.id));
    const countdown=el('span',null,row);
    return {row,scheduled,countdown};
  });

  function duration(seconds) {
    if (!Number.isFinite(seconds)) return '—';
    seconds=Math.max(0,Math.floor(seconds));
    const h=Math.floor(seconds/3600);
    const pad=n=>String(n).padStart(2,'0');
    return (h?h+':':'')+pad(Math.floor(seconds/60)%60)+':'+pad(seconds%60);
  }

  function updateJourney(t,sample) {
    const sec=Math.floor((t-run.serviceMidnight)/1000);
    if (sec===lastSecond) return;
    lastSecond=sec;
    const waiting=t<run.start, finished=t>=run.end;
    const parked=calls.find(s=>s.arrive!==null && s.depart!==null && s.arrive<=sec && sec<s.depart);
    const next=calls.find((s,i)=>i>0 && s.arrive!==null && s.arrive>sec);
    const origin=calls[0], destination=calls.at(-1);
    stateLabel.textContent=waiting ? '等待始发' : finished ? '已终到' : parked ? '停靠 · '+parked.name : '运行中';
    speed.textContent=waiting || finished ? '—' : parked ? '0' : sample ? Math.round(sample.speed) : '—';
    targetLabel.textContent=waiting ? '始发站' : finished ? '终到站' : '下一站';
    targetName.textContent=(waiting ? origin : finished ? destination : next)?.name || '—';
    etaLabel.textContent=waiting || parked ? '距发车' : finished ? '终到时间' : '距到站';
    const event=waiting ? origin.depart : parked ? parked.depart : next?.arrive;
    etaValue.textContent=finished ? showTime(destination.arrival) : event==null ? '—' : duration(event-sec);
    note.textContent=parked ? parked.name+' '+showTime(parked.departure)+' 开' :
      sample?.display?.kind || sample?.line?.name || (!sample && !waiting && !finished ? '本区间仅展示计划时刻' : '');
    note.hidden=!note.textContent;
    const percent=fraction(sec)*100;
    track.style.setProperty('--done',percent+'%');
    track.setAttribute('aria-valuenow',Math.round(percent));
    const today=liveTimeline.iso(t).slice(0,10);
    calls.forEach((stop,i)=>{
      const {row,scheduled,countdown}=rows[i];
      const departed=stop!==destination && stop.depart!==null && sec>=stop.depart;
      const atTerminus=finished && stop===destination;
      const departure=i===0 || stop===parked || departed;
      const event=departure ? stop.depart : stop.arrive;
      const upcoming=waiting ? i===0 : stop===next;
      const state=stop===parked ? 'parked' : departed || atTerminus ? 'past' : upcoming ? 'upcoming' : 'future';
      if (row.className!==state) row.className=state;
      const clock=event===null ? null : liveTimeline.iso(run.serviceMidnight+event*1000);
      const time=clock ? (clock.slice(0,10)===today?'':clock.slice(5,10)+' ')+clock.slice(11,16) : '—';
      if (scheduled.textContent!==time) scheduled.textContent=time;
      let status;
      if (atTerminus) status='已终到';
      else if (departed) status='已离站';
      else if (event===null) status='时刻待定';
      else {
        const prefix=stop===parked ? '停靠中 · ' : upcoming ? (waiting ? '待始发 · ' : '下一站 · ') : '';
        status=prefix+duration(event-sec)+(departure?' 后开':' 后到');
      }
      if (countdown.textContent!==status) countdown.textContent=status;
    });
    remaining.textContent=finished ? '行程结束' : '剩余 '+calls.filter((s,i)=>i>0 && s.arrive!==null && s.arrive>sec).length+' 站';
  }
  let edges, focusedKey;
  function removeMarker() {
    if (markerSource && map.getSource('selected-train')===markerSource) {
      // Detach cached symbol tiles before the regional renderer resumes.
      map.removeLayer('selected-train-caption');
      map.removeLayer('selected-train-heading');
      map.removeSource('selected-train');
    }
    markerSource=null;
    position=null;
    lastCaption=null;
    lastAt=null;
  }
  function clear() {
    run=null;
    lastSecond=null;
    removeMarker();
    focusedKey=null;
    regionLive.setFocus(null);
    hero.hidden=details.hidden=true;
    changeCamera('static');
  }
  function draw(updatePanel=true) {
    if (!run) return;
    if (!host.isConnected) { clear(); return; }
    const t=liveTimeline.time();
    const active=t>=run.start && t<run.end;
    const sec=(t-run.serviceMidnight)/1000;
    const display=active && trip.displayLegs?.find(l=>l.start<=sec && sec<l.end);
    let sample=active && (display ? displayMotion(display,sec) : model?.state(sec));
    // Runs reconstructed entirely from display legs have no primary dwell pose.
    if (active && !sample) {
      const i=calls.findIndex(c=>c.arrive<=sec && sec<c.depart);
      if (i>=0) {
        const incoming=trip.displayLegs?.find(l=>l.seq===i-1);
        const track=incoming || trip.displayLegs?.find(l=>l.seq===i);
        if (track) sample={at:incoming ? track.path.at(-1)[0] : 0,speed:0,dwellTrack:track,
          coord:s=>indexedTrackPoint(track.path,s).coord};
      }
    }
    if (updatePanel) {
      const day=liveTimeline.iso(run.serviceMidnight).slice(0,10);
      const key=sample ? regionLive.runKey(trip.id,day) : null;
      if (focusedKey!==key) {
        if (sample) edges ??= regionLive.routeEdges(trip.geometry,trip.inferredLegs);
        regionLive.setFocus(sample ? {selected:key,id:trip.id,day,edges} : null);
        focusedKey=key;
      }
      updateJourney(t,sample);
      follow.hidden=navigation.hidden=!active || !sample || !map;
      jump.hidden=active;
    }
    const source=sample && ensureSelectedTrainSource();
    if (!sample || !source) {
      if (position) {
        removeMarker();
        changeCamera('static');
      }
      return;
    }
    markerSource=source;
    const {at}=sample;
    let coord=sample.coord || (s=>toMapCoordinates(model.path.point(s)));
    if (sample.leg) {
      const stop=calls.findLast(c=>c.depart<=sec);
      if (stop) coord=platformExit(coord,sample.leg.sample(0).at,stop.mapPosition || (hasPosition(stop) && toMapCoordinates([stop.lon,stop.lat])),at);
    }
    const seconds=Math.floor((t-run.serviceMidnight)/1000);
    const speedKmh=Math.round(sample.speed);
    let caption=lastCaption;
    if (!caption || seconds!==captionSecond || speedKmh!==captionSpeed) {
      const next=calls.find((s,i)=>i>0 && s.arrive!==null && s.arrive>seconds);
      caption=trip.number+'\n'+speedKmh+' km/h';
      if (next) caption+='\n-> '+next.name+' ('+duration(next.arrive-seconds)+')';
      if (routeLabel) caption+='\n'+routeLabel;
      captionSecond=seconds;
      captionSpeed=speedKmh;
    }
    // Display legs restart their distance at zero. A seek to the same distance
    // on a different leg must still move both the train and its camera.
    const track=sample.display || sample.leg || sample.dwellTrack || model?.path;
    const moved=at!==lastAt || track!==lastTrack;
    const parked=!sample.leg && !sample.display;
    const zoomChanged=lastZoom!==map.getZoom();
    if (!moved && caption===lastCaption && parked===lastParked && !zoomChanged) return;
    lastZoom=map.getZoom();
    lastAt=at;
    lastTrack=track;
    lastCaption=caption;
    lastParked=parked;
    const firstPose=!position;
    position=coord(at);
    const ahead=coord(at+60), behind=coord(at-60);
    const bearing=Math.atan2((ahead[0]-behind[0])*Math.cos(position[1]*Math.PI/180),ahead[1]-behind[1])*180/Math.PI;
    // A chord on the route smooths heading through polyline corners. It always
    // points down the track, including during reverse playback or a time seek.
    if (ahead[0]!==behind[0] || ahead[1]!==behind[1]) heading=bearing;
    heading ??= map.getBearing();
    const head={type:'Feature',id:1,properties:{caption,bearing,parked,prefix:trip.number[0]},geometry:{type:'Point',coordinates:position}};
    if (sample.leg && sample.leg!==lastLeg) {
      lastLeg=sample.leg;
      departureAt=sample.leg.sample(0).at;
    }
    const features=parked ? [head] : trainCarriages(head,at,coord,sample.display ? 0 : departureAt,selectedTrainScale);
    source.setData({type:'FeatureCollection',features});
    // Let wheel, pinch and zoom-button animations finish before re-anchoring.
    if ((moved || firstPose || zoomChanged) && !map.isZooming()) placeCamera();
  }
  function update(selected) {
    if (!selected) { if (run) clear(); return; }
    if (run!==selected) {
      clear();
      run=selected;
      heading=null;
      model ??= makeTrainMotion(trip);
      hero.hidden=details.hidden=false;
      draw();
      if (followView.autoFollow) changeCamera('map');
      else if (trip.liveLine==='catalog' && position && map) {
        map.easeTo({center:position,offset:[0,-mobileFocusShift()],zoom:Math.max(9,map.getZoom()),duration:350});
      }
      return;
    }
    draw();
  }
  return {update,clear,draw,detachCamera,selectOnMap() {
    changeCamera(followView.autoFollow ? 'map' : 'static');
  }};
}
