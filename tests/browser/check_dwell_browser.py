"""Replay a single-train stop and a two-train overlap at Suzhou."""
import asyncio
import sys
from browser_driver import drive


async def check(ev, wait):
    await ev("""window.seek=t=>liveTimeline.jump(liveTimeline.midnight('2026-11-18')+t*1000);
        window.badge=()=>document.querySelector('.dwell-marker[data-station="SZH"]');
        window.train=n=>map.getSource('region-trains')?._data.features.find(f=>f.properties.number===n);
        window.station=()=>map.queryRenderedFeatures({layers:['station-pax-01-LT','station-pax-245-LT']})
            .some(f=>f.properties['name:zh']==='苏州');
        map.jumpTo({center:toMapCoordinates([120.606676,31.331883]),zoom:11});seek(32519)""")
    await wait("train('G8988') && station()")
    await ev("seek(32520)")
    await wait("badge()?.querySelector('b').textContent==='G8988' && !train('G8988') && station()")
    assert await ev("badge().querySelector('time').textContent==='02:00' && badge().querySelector('button').title.includes('09:02 到 · 09:04 开')")
    await ev("seek(32639)")
    assert await ev("badge().innerText.includes('00:01')")
    await ev("seek(32640)")
    await wait("!badge() && train('G8988') && station()")
    await ev("seek(32639)")
    await wait("badge() && !train('G8988')")
    await ev("seek(35880)")
    await wait("badge()?.querySelectorAll('button').length===3")
    await ev("badge().querySelector('button').focus()")
    assert await ev("badge().innerText.includes('G3379') && badge().innerText.includes('D956') && badge().innerText.includes('K784')")
    await ev("seek(35940)")
    await wait("badge()?.querySelectorAll('button').length===2 && !badge().innerText.includes('G3379') && station()")
    await ev("seek(32639)")
    await wait("badge()?.querySelector('b').textContent==='G8988'")
    await ev("badge().querySelector('.dwell-badge').click()")
    await wait("map.getSource('selected-train')?._data.features[0]?.properties.parked")
    assert await ev("map.queryRenderedFeatures({layers:['selected-train-heading']}).length===0 && !!badge()")
    await wait("station() && map.isStyleLoaded()")
    assert await ev("""(()=>{const ids=map.getStyle().layers.map(l=>l.id);
      const layers=['region-heading','selected-train-heading','station-pax-245-LT','station-pax-01-LT',
        'selected-station-ring','region-label','selected-train-caption',...stationSymbols.nameLayers,'selected-station-name'];
      return layers.every((id,i)=>ids.includes(id) && (!i || ids.indexOf(layers[i-1])<ids.indexOf(id)))})()""")
    await ev("seek(32519)")
    await wait("station() && !badge()")
    await ev("seek(32520)")
    await wait("station() && badge() && map.getSource('selected-train')?._data.features[0]?.properties.parked")
    await ev("seek(32740)")
    await wait("!badge() && !map.getSource('selected-train')?._data.features[0]?.properties.parked && map.queryRenderedFeatures({layers:['selected-train-heading']}).length>0")
    assert await ev("station()")
    await ev("""clearMapSelection(); window.nanjing=()=>document.querySelector('.dwell-marker[data-station="NKH"]');
      const centerStation=station=>{map.jumpTo({center:station.mapPosition,zoom:11})};
      liveTimeline.jump(liveTimeline.midnight('2026-11-16')+(14*3600+21*60)*1000);
      get('/api/station/NKH').then(centerStation)""")
    await wait("nanjing()?.querySelectorAll('button:not(.terminal)').length===7 && nanjing().querySelector('.terminal b')?.textContent==='G7134'")
    assert await ev("""(()=>{const rows=[...nanjing().querySelectorAll('button:not(.terminal)')];
      return rows.every((r,i)=>{const n=r.querySelector('b').textContent,t=r.querySelector('time').textContent;
        return r.offsetHeight>0 && r.style.getPropertyValue('--badge-ink')===(trainClasses[n[0]]?.[1] || ordinaryTrainColor)
          && /^\\d+:\\d{2}$/.test(t) && (!i || rows[i-1].querySelector('time').textContent<=t)})})()""")
    await ev("""get('/api/station/VNP').then(s=>{map.jumpTo({center:s.mapPosition,zoom:11})});
      window.originRow=()=>[...document.querySelectorAll('.dwell-marker[data-station="VNP"] button')]
        .filter(b=>b.querySelector('b').textContent==='G7');
      seek(9*3600-120)""")
    await wait("originRow().length===1 && originRow()[0].querySelector('time').textContent==='02:00'")
    assert await ev("originRow()[0].title.includes('本站始发 · 09:00 开') && !train('G7')")
    await ev("seek(9*3600-121)")
    await wait("!originRow().length && !train('G7')")
    await ev("seek(9*3600-1)")
    await wait("originRow().length===1 && originRow()[0].innerText.includes('00:01')")
    await ev("seek(9*3600)")
    await wait("!originRow().length && train('G7')")
    await ev("seek(9*3600-1)")
    await wait("originRow().length===1 && !train('G7')")
    await ev("""window.terminal=n=>[...document.querySelectorAll('.dwell-marker[data-station="AOH"] .terminal')]
        .find(row=>row.querySelector('b').textContent===n);
        window.arrived=13*3600+33*60;
        seek(arrived-1);get('/api/station/AOH').then(s=>{map.jumpTo({center:s.mapPosition,zoom:11})})""")
    await wait("spaceTime.stats.complete && train('G1211') && !terminal('G1211')")
    for seconds,clock,opacity in [(0,'00:00',1),(102,'01:42',1),(179,'02:59',1),(180,'03:00',1),(210,'03:30',.5),(239,'03:59',1/60)]:
        await ev(f"seek(arrived+{seconds})")
        await wait(f"terminal('G1211')?.querySelector('time').textContent==='{clock}'")
        assert await ev(f"Math.abs(parseFloat(getComputedStyle(terminal('G1211')).opacity)-{opacity})<.00001")
    assert await ev("""getComputedStyle(terminal('G1211'),'::before').content.includes('▼') &&
        terminal('G1211').title.includes('13:33 终到') && !train('G1211')""")
    await ev("seek(arrived+240)")
    await wait("!terminal('G1211') && terminal('G7')?.querySelector('time').textContent==='00:00'")
    await ev("seek(arrived+210)")
    await wait("terminal('G1211') && getComputedStyle(terminal('G1211')).opacity==='0.5'")
    await ev("terminal('G1211').click()")
    await wait("document.querySelector('.journey-header')?.innerText.includes('已终到')")
    await ev("seek(86400+30)")
    await wait("terminal('G1942')?.querySelector('time').textContent==='03:30'")
    assert await ev("getComputedStyle(terminal('G1942')).opacity==='0.5'")
    await ev("seek(86400+60)")
    await wait("!terminal('G1942')")
    print('Passed Hongqiao terminal arrival, simulated stopwatch, linear fade, removal, reverse seek and midnight.')
    print('Passed Beijing South G7 origin countdown, departure across time buckets and reverse seek.')
    print('Passed Suzhou station label, G8988 arrival/departure, live tooltip, reverse seek and multi-train badge including a previous-day service.')


if __name__ == '__main__':
    asyncio.run(drive(sys.argv[1],check))
