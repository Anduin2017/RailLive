"""Compare static motion to the dynamic reference and verify camera/cache boundaries."""
import asyncio
import sys
from browser_driver import drive


async def check(ev,wait):
    await ev("""window.clock=liveTimeline.midnight('2026-11-19');
      window.seek=(s)=>liveTimeline.jump(clock+s*1000);
      window.place=(x,y,z)=>map.jumpTo({center:toMapCoordinates([x,y]),zoom:z});
      window.reg=()=>map.getSource('region-trains')?._data.features||[];
      window.head=(number)=>reg().find(f=>!f.properties.car&&f.properties.number===number);
      window.files=()=>performance.getEntriesByType('resource').filter(r=>r.name.includes('/space-time/'));
      performance.setResourceTimingBufferSize(30000);
      seek(47110);void place(120.60,31.33,11)""")
    await wait("spaceTime.stats.complete && head('G1211')")
    await ev("void place(120.64,31.42,11)")
    await wait("spaceTime.stats.complete && head('G1211')")
    # Shift across a loader grid boundary as well as between the two stations.
    await ev("void place(119.92,31.38,11)")
    await wait("spaceTime.stats.complete && reg().length")
    await asyncio.sleep(.6)
    await ev("window.already=new Set(files().map(r=>r.name));window.cut=performance.now();void place(120.60,31.33,11)")
    await wait("spaceTime.stats.complete && head('G1211')")
    await asyncio.sleep(.4)
    repeated=await ev("files().filter(r=>r.startTime>=cut && already.has(r.name)).map(r=>r.name)")
    assert not repeated,repeated
    await ev("""window.reference=async (day,t,lon,lat,number)=>{
      clock=liveTimeline.midnight(day);seek(t);place(lon,lat,10);
      let start=Math.floor(t/300)*300;
      let data=await get('/api/region-live?'+new URLSearchParams({day,start,box:[lon-.5,lat-.4,lon+.5,lat+.4]}));
      let ref=(data.display||[]).find(r=>r.number===number && r.start<=t+(clock-liveTimeline.midnight(r.day))/1000 && r.end>t+(clock-liveTimeline.midnight(r.day))/1000);
      let expected;
      if(ref) {
        const pose=displayMotion(ref,t+(clock-liveTimeline.midnight(ref.day))/1000);
        expected=pose.coord(pose.at);
      } else {
        ref=data.runs.find(r=>r.number===number);
        const sec=t+(clock-liveTimeline.midnight(ref.day))/1000;
        const at=peerDistance(ref.curve,sec),dep=ref.departures.findLast(r=>r[0]<=sec);
        const project=s=>indexedTrackPoint(ref.path,s).coord;
        expected=dep?platformExit(project,dep[1],dep[2]||(dep[3]&&toMapCoordinates(dep[3])),at)(at):project(at);
      }
      window.refId=ref.id;window.refDay=ref.day;window.refPos=expected;
      map.jumpTo({center:expected,zoom:10});
    }""")
    for args in [("2026-11-19",47110,120.64,31.42,"G1211"),("2026-11-19",74680,110.14,20.14,"Z502")]:
        import json
        await ev('reference('+','.join(map(json.dumps,args))+')')
        await wait("spaceTime.stats.complete && reg().some(f=>f.properties.id===refId)")
        delta=await ev("""(()=>{const cars=reg().filter(f=>f.properties.id===refId && f.properties.day===refDay);
          let head=cars.find(f=>!f.properties.car);
          return [cars.length,Math.hypot(...head.geometry.coordinates.map((x,i)=>x-refPos[i]))]})()""")
        assert delta[0]==5 and delta[1]<.000002,(args,delta)
        print(args[-1],'five cars / head deviation in degrees',delta,flush=True)
    # G1211 crosses 120° E on this snapshot at 12:50:48. Check each carriage
    # until the tail has crossed, including the interval straddling both cells.
    await ev("seek(46240);void place(120,31.84,11)")
    await wait("spaceTime.stats.complete && head('G1211')")
    crossed=await ev("""(()=>{
      let prior, straddled=false, tailCrossed=false;
      for(let t=46240;t<=46290;t++) {
        seek(t);
        const engine=head('G1211');
        if(!engine)return false;
        const cars=reg().filter(f=>f.properties.id===engine.properties.id)
          .sort((a,b)=>(a.properties.car||0)-(b.properties.car||0));
        if(cars.length!==5 || new Set(cars.map(f=>f.properties.id+'/'+f.properties.day)).size!==1)return false;
        const xs=cars.map(f=>fromMapCoordinates(...f.geometry.coordinates)[0]);
        if(prior && xs.some((x,i)=>x<=prior[i] || x-prior[i]>.002))return false;
        if(t===46240 && xs.some(x=>x>=120))return false;
        straddled ||= xs[0]>=120 && xs[4]<120;
        tailCrossed ||= xs[4]>=120;
        prior=xs;
      }
      return straddled && tailCrossed;
    })()""")
    assert crossed,'G1211 carriage continuity at 120° E'
    print('G1211 head and all four trailing carriages crossed 120° E continuously.',flush=True)
    await ev("clock=liveTimeline.midnight('2026-11-18');seek(69720);void place(120.61,31.33,11)")
    await wait("Array.from(document.querySelectorAll('.dwell-marker')).some(m=>m.innerText.includes('Z164'))")
    await ev("void place(101.81,36.62,11)")
    await wait("Array.from(document.querySelectorAll('.dwell-marker')).some(m=>m.innerText.includes('Z164'))")
    print('Suzhou → Suzhou North → adjacent grid → Suzhou: zero cached refetches; Z164 present in Suzhou and Xining.',flush=True)


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],check))
