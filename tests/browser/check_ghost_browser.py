"""A delayed regional tile must never draw a second copy of a focused run."""
import asyncio
import sys
from browser_driver import drive


async def check(ev, wait):
    await ev("""window.day='2026-11-19'; window.idG8='5l000000G802';
      window.t=liveTimeline.midnight(day)+8.4*3600000;
      liveTimeline.jump(t);void showTrip(idG8,day,'region');""")
    await wait("map.getSource('selected-train')?._data.features[0]")
    await ev("""window.here=map.getSource('selected-train')._data.features[0].geometry.coordinates;
      clearMapSelection();void map.jumpTo({center:here,zoom:10});
      window.reg=()=>map.getSource('region-trains');
      window.glyphs=()=>map.queryRenderedFeatures({layers:['region-heading','region-label']}).filter(f=>f.properties.id===idG8);
      window.single=()=>map.getLayer('selected-train-caption') && map.queryRenderedFeatures({layers:['selected-train-caption']}).some(f=>f.properties.caption.startsWith('G8\\n'));""")
    await wait("reg() && glyphs().some(f=>!f.properties.car)")
    await ev("""window.oldTile=structuredClone(reg()._data);
      const head=glyphs().find(f=>!f.properties.car);
      void map.fire('click',{point:map.project(head.geometry.coordinates),originalEvent:{target:map.getCanvas()}});""")
    await wait("single() && !glyphs().length")
    # Replay a pre-click snapshot after the handoff, including after zooming
    # away and back. A distinct service day with the same trip ID stays visible.
    for zoom in (10,9,10):
        await ev(f"void map.jumpTo({{zoom:{zoom}}});liveTimeline.jump(t+20000)")
        await wait("reg() && map.isStyleLoaded()")
        await ev("""var other=structuredClone(oldTile.features.find(f=>!f.properties.car && f.properties.id===idG8));
          other.properties.day='2026-11-18';
          // Keep the delayed tile in place while the worker paints it.
          var regionSource=reg(), originalUpdate=regionSource.setData;
          regionSource.setData=()=>regionSource;
          originalUpdate.call(regionSource,{type:'FeatureCollection',features:[...oldTile.features,other]});void 0""")
        await wait("glyphs().some(f=>f.properties.day==='2026-11-18')")
        assert await ev("single() && glyphs().every(f=>f.properties.day!==day)")
        await ev("regionSource.setData=originalUpdate;void 0")
    await ev("clearMapSelection()")
    await wait("!single() && glyphs().some(f=>f.properties.day===day)")
    print('Passed G8 handoff, delayed data, zoom round trip, service-day isolation and deselection.')


asyncio.run(drive(sys.argv[1],check))
