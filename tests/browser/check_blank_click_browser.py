"""Click empty canvas pixels after selecting a station, a line and a moving train."""
import asyncio
import sys
from browser_driver import drive


async def check(ev, wait):
    await ev("""liveTimeline.jump(liveTimeline.midnight('2026-11-19')+45000000);
      window.clickBlank=()=>{
        const canvas=map.getCanvas(), rect=canvas.getBoundingClientRect();
        const layers=map.getStyle().layers.filter(l=>l['source-layer']==='rail'||l.source==='rail-detail'||
          l.source==='selected-station'||l.source==='selected-route'||l.source==='region-trains'||
          l.source==='selected-train'||l.id.startsWith('station-pax')).map(l=>l.id);
        for(let x=rect.width*.55;x<rect.width*.9;x+=8)for(let y=90;y<rect.height*.7;y+=8){
          if(map.queryRenderedFeatures([x,y],{layers}).length)continue;
          const clientX=rect.left+x,clientY=rect.top+y;
          if(document.elementFromPoint(clientX,clientY)!==canvas)continue;
          for(const type of ['mousedown','mouseup','click'])canvas.dispatchEvent(new MouseEvent(type,{
            clientX,clientY,button:0,buttons:type==='mousedown'?1:0,bubbles:true,view:window}));
          return true;
        }
        return false;
      }""")
    for select, ready in [
        ("showStation('OHH')", "selectedStation?.properties.name==='苏州北'"),
        ("showLine('京沪高速线')", "panel.querySelector('.line-facts')"),
        ("search('G1211')", "panel.querySelector('.journey-header button') && map.getSource('selected-train')._data.features.length")
    ]:
        await ev('void '+select)
        await wait(ready)
        if select.startswith('search'):
            await ev("panel.querySelector('.journey-header button').click()")
            await wait("panel.querySelector('.journey-header button').getAttribute('aria-pressed')==='true'")
        await wait("map.loaded() && !map.isMoving()")
        assert await ev("clickBlank()")
        await wait("!panel.hasChildNodes() && !selectedStation && !route.features.length && !map.getSource('selected-train')?._data.features.length && sidebar.hidden && query.offsetWidth===0")
    # Select the overnight G897 directly on the map, then hold the empty
    # GeoJSON update so the old tile is still drawable during deselection.
    await ev("""window.when=liveTimeline.midnight('2026-11-17')+119000;
      window.tripId='240000G8970A'; liveTimeline.jump(when);
      void showTrip(tripId,'2026-11-16','region')""")
    await wait("map.getSource('selected-train')?._data.features[0]")
    await ev("""window.centerG=map.getSource('selected-train')._data.features[0].geometry.coordinates;
      clearMapSelection();void map.jumpTo({center:centerG,zoom:10});
      window.heads=()=>map.getLayer('region-heading') ? map.queryRenderedFeatures({layers:['region-heading']})
        .filter(f=>f.properties.id===tripId && !f.properties.car) : []""")
    await wait("heads().length")
    await ev("""window.clickHead=()=>{const pt=map.project(heads()[0].geometry.coordinates),canvas=map.getCanvas(),r=canvas.getBoundingClientRect();
      for(const type of ['mousedown','mouseup','click'])canvas.dispatchEvent(new MouseEvent(type,{
        clientX:r.left+pt.x,clientY:r.top+pt.y,button:0,buttons:type==='mousedown'?1:0,bubbles:true,view:window}));}; window.clickHead()""")
    await wait("map.getLayer('selected-train-caption') && map.queryRenderedFeatures({layers:['selected-train-caption']}).some(f=>f.properties.caption.startsWith('G897'))")
    await ev("""const old=map.getSource('selected-train'),send=old.setData.bind(old);
      old.setData=data=>data.features.length ? send(data) : old;""")
    assert await ev("clickBlank()")
    await wait("!panel.hasChildNodes()")
    for zoom in (10,9,10):
        await ev(f"void map.jumpTo({{zoom:{zoom}}});liveTimeline.jump(when+30000)")
        await wait("heads().length && !map.isMoving() && map.loaded()")
        for layer in ('selected-train-caption','selected-train-heading'):
            assert await ev(f"!map.getLayer('{layer}') || !map.queryRenderedFeatures({{layers:['{layer}']}}).length"), layer
    await ev("window.resumed=JSON.stringify(heads()[0].geometry.coordinates);liveTimeline.jump(when+90000)")
    await wait("heads().length && heads().every(f=>JSON.stringify(f.geometry.coordinates)!==resumed)")
    await ev("clickHead()")
    await wait("map.getLayer('selected-train-heading') && map.queryRenderedFeatures({layers:['selected-train-heading']}).length && !heads().length")
    assert await ev("clickBlank()")
    await wait("heads().length && !map.getLayer('selected-train-caption') && !map.getSource('selected-train')")
    print("Passed G897 train click, blank click, delayed cleanup and zoom round trip.")
    print("Passed blank clicks for station, line and followed train.")


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],check))
