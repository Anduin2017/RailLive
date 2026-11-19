"""Follow recovered departures on the map and seek into an unresolved leg."""
import asyncio
import sys
from browser_driver import drive


async def check(ev, wait):
    await ev("""window.inspectRun=async number=>{
      const list=await get('/api/search?q='+number);
      const hit=list.trains?.find(r=>r.number===number) || list.trips?.find(r=>r.number===number);
      const trip=await get('/api/trip/'+hit.id);
      const day=trip.dates.find(d=>d==='20261119') || trip.dates[0];
      const iso=day.slice(0,4)+'-'+day.slice(4,6)+'-'+day.slice(6);
      const midnight=liveTimeline.midnight(iso), departure=timetableSeconds(trip.stops[0].departure);
      window.displayLegs=await get('/api/region-live?trip='+hit.id);
      window.seek=s=>liveTimeline.jump(midnight+s*1000);
      window.details=trip;window.departure=departure;
      seek(departure-1);
      map.jumpTo({center:trip.stops[0].mapPosition || toMapCoordinates(displayLegs[0]?.path[0].slice(1) || trip.geometry[0]),zoom:10});
      window.boarded=()=>[...document.querySelectorAll('.dwell-marker b')].some(el=>el.textContent===number);
      window.regional=()=>map.getSource('region-trains')?._data.features.find(f=>f.properties.id===hit.id && !f.properties.car);
      window.selectRun=()=>showTrip(hit.id,iso,'region');
    }""")
    for number in ('K1456','G1065','T10','K598','K5131','K5181','G1294','C4486'):
        await ev(f"inspectRun('{number}')")
        await wait("boarded() && !regional()")
        await ev("seek(departure)")
        await wait("!boarded() && regional()")
        await wait("map.queryRenderedFeatures({layers:['region-heading']}).some(f=>f.properties.id===details.id)")
        await ev("window.startAt=regional().geometry.coordinates")
        await ev("seek(departure+30)")
        await wait("regional() && JSON.stringify(regional().geometry.coordinates)!==JSON.stringify(startAt)")
        await ev("window.before=regional().geometry.coordinates;selectRun()")
        await wait("map.getSource('selected-train')?._data.features[0] && !regional()")
        assert await ev("Math.hypot(...map.getSource('selected-train')._data.features[0].geometry.coordinates.map((v,i)=>v-before[i]))<0.0001")
        assert await ev("(!details.inferredLegs && !displayLegs.length) || panel.textContent.includes('轨迹可能和实际不符，仅供参考。')")
        assert await ev("route.features.some(f=>f.geometry.type==='LineString')")
        await ev("""window.gap=details.inferredLegs?.indexOf(null) ?? -1;
          if(gap>=0)seek((timetableSeconds(details.stops[gap].departure)+timetableSeconds(details.stops[gap+1].arrival))/2)""")
        if await ev("gap>=0"):
            await wait("map.getSource('selected-train')?._data.features[0] && /示意|推测/.test(panel.querySelector('.journey').textContent)")
        await ev("seek(departure+30)")
        await wait("map.getSource('selected-train')?._data.features[0]")
        await ev("clearMapSelection()")
        if number in ('G1065','K598','T10'):
            await ev("""window.departureTestLeg=displayLegs.find(l=>l.seq>0 && timetableSeconds(details.stops[l.seq].arrival)<l.start);
              window.atSection=()=>map.jumpTo({center:toMapCoordinates(departureTestLeg.path[0].slice(1)),zoom:11});
              atSection();seek(departureTestLeg.start-1)""")
            await wait("boarded() && !regional()")
            await ev("seek(departureTestLeg.start)")
            await wait("!boarded() && regional() && map.queryRenderedFeatures({layers:['region-heading']}).some(f=>f.properties.id===details.id)")
            await ev("seek(departureTestLeg.start-1)")
            await wait("boarded() && !regional()")
        print(number, 'passed',flush=True)


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],check))
