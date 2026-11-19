"""Check the painted station axis and the dated, precomputed hub symbol."""
import asyncio
import sys
from browser_driver import drive


async def check(ev, wait):
    await ev("liveTimeline.jump(liveTimeline.midnight('2026-11-19')+36000000)")
    for center,stations in [
        ([114.602,34.083],[['定远',155],['定远东',42]]),
        ([113.1128,41.457076],[['北京南',53]]),
        ([113.034548,41.443592],[['北京丰台',66]]),
    ]:
        await ev(f"window.checkStations={stations!r};void map.jumpTo({{center:{center!r},zoom:12}})")
        await wait("""map.isStyleLoaded() && checkStations.every(([name])=>
          map.queryRenderedFeatures({layers:['station-pax-245-LT','station-pax-01-LT']})
            .some(f=>f.properties['name:zh']===name))""")
        result=await ev("""new Promise(resolve=>{
          map.once('render',()=>{
            const canvas=map.getCanvas(), gl=canvas.getContext('webgl')||canvas.getContext('experimental-webgl');
            const k=canvas.width/canvas.clientWidth;
            const features=map.queryRenderedFeatures({layers:['station-pax-245-LT','station-pax-01-LT']});
            function inkNear(x,y) {
              const side=Math.ceil(7*k), pixels=new Uint8Array(side*side*4);
              gl.readPixels(Math.round(x*k-side/2),Math.round(canvas.height-y*k-side/2),
                side,side,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
              let n=0;
              for(let i=0;i<pixels.length;i+=4)
                if(Math.abs(pixels[i]-36)<12 && Math.abs(pixels[i+1]-89)<12 && Math.abs(pixels[i+2]-90)<12)n++;
              return n;
            }
            resolve(checkStations.every(([name,degrees])=>{
              const f=features.find(f=>f.properties['name:zh']===name);
              const p=map.project(f.geometry.coordinates),a=degrees*Math.PI/180;
              return [-20,20].every(d=>inkNear(p.x+d*Math.sin(a),p.y-d*Math.cos(a))>0);
            }));
          });map.triggerRepaint()
        })""")
        assert result, 'Each station must extend along its own railway.'
    await ev("void showStation('e648d816e810494b9e7e08aa11b2ad7a')")
    await wait("""map.queryRenderedFeatures({layers:['selected-station-ring']})
      .some(f=>f.properties.kid==='e648d816e810494b9e7e08aa11b2ad7a')""")
    assert await ev("""(async()=>{
      const m=await (await fetch('/map/station-symbols/index.json')).json();
      const tiers=await (await fetch('/map/station-symbols/'+m.days['2026-11-19']+'.json')).json();
      const [l,w]=m.sizes[tiers['07716fd88114427883640c2d226dbc8d']];
      const [L,W]=m.sizes[tiers['e648d816e810494b9e7e08aa11b2ad7a']];
      return L/l<=1.2 && W/w>2 && map.hasImage('platform-focus-'+tiers['e648d816e810494b9e7e08aa11b2ad7a'])
    })()""")
    await ev("""clearMapSelection();
      window.suzhou=()=>get('/api/station/SZH').then(st=>map.jumpTo({center:st.mapPosition,zoom:12}));
      void suzhou();window.baseSuzhou=()=>map.queryRenderedFeatures({layers:['station-pax-245-LT','station-pax-01-LT']})
        .find(f=>f.properties['name:zh']==='苏州');
      window.focusSuzhou=()=>map.queryRenderedFeatures({layers:['selected-station-ring']})
        .find(f=>f.properties.name==='苏州')""")
    for zoom in (12,10,11.5):
        await ev(f"void map.jumpTo({{zoom:{zoom}}})")
        await wait("map.loaded() && baseSuzhou()")
        await ev("window.before=baseSuzhou().geometry.coordinates; void showStation('SZH',false)")
        await wait("map.loaded() && focusSuzhou() && !baseSuzhou()")
        assert await ev("map.project(before).dist(map.project(focusSuzhou().geometry.coordinates))<0.5")
        await ev("clearMapSelection()")
        await wait("map.loaded() && baseSuzhou() && !focusSuzhou()")
    print('Passed Beijing and Dingyuan rendered station axes, selected station and daily hub dimensions.')


asyncio.run(drive(sys.argv[1],check))
