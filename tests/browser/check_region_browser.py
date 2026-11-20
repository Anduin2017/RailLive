"""Run against a dedicated headless Chromium tab with remote debugging on port 19851.
Usage: python3 tests/browser/check_region_browser.py 8080
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'build/app'))
from browser_driver import drive
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'snapshot'))
from snapshot_case import case_day

async def check(ev,wait):
    await ev("""window.q=s=>document.querySelector(s);window.seek=n=>{q('#live-range').value=n;q('#live-range').dispatchEvent(new Event('input'))};
    window.place=(lon,lat,z)=>map.jumpTo({center:toMapCoordinates([lon,lat]),zoom:z});
    window.feats=()=>map.getSource('region-trains')?._data?.features.filter(x=>!x.properties.car)||[];window.f=n=>feats().find(f=>f.properties.number===n);
    if(q('#live-clock').hidden)q('#live-toggle').click();
    q('#live-date').value='%s';q('#live-date').dispatchEvent(new Event('change'));seek(47110);void place(120.64,31.42,9)""" % case_day())
    await wait("f('G1211')")
    await ev("""window.setLabel=(key,on)=>{const input=q('[data-train-label="'+key+'"]');
      if(input.checked!==on)input.click()};setLabel('next',true);setLabel('destination',false)""")
    assert await ev(r"f('G1211').properties.label==='G1211\n-> 苏州北'")
    await ev("setLabel('destination',true)")
    assert await ev(r"f('G1211').properties.label==='G1211\n-> 苏州北\n--> 上海虹桥'")
    await ev("seek(47380)")
    await wait(r"f('G1211')?.properties.label==='G1211\n-> 上海虹桥'")
    await ev("seek(47110);setLabel('next',false)")
    await wait(r"f('G1211')?.properties.label==='G1211\n--> 上海虹桥'")
    await ev("setLabel('next',true);setLabel('destination',false)")
    await ev("window.original=f('G1211').geometry.coordinates;")
    # Cross the edge and return while preserving the identity and coordinates.
    for lon in [121.04,121.65,121.04,120.64]:
        await ev("void place("+str(lon)+",31.42,9)")
        await asyncio.sleep(.3)
        assert await ev("feats().length===new Set(feats().map(x=>x.properties.id+x.properties.day)).size")
    await wait("JSON.stringify(f('G1211')?.geometry.coordinates)===JSON.stringify(original)")
    # Put the eastern screen edge on the moving G7. Positions should
    # continue along the route while the renderer clips it at the edge.
    await ev("""seek(47280);window.edgeOrigin=f('G7').geometry.coordinates;void map.jumpTo({center:edgeOrigin,zoom:10});
    void map.panBy([-map.getCanvas().clientWidth/2+2,0],{duration:0})""")
    await wait("f('G7')")
    points=await ev("""(async()=>{let out=[];for(let t=47260;t<=47300;t+=4){
      seek(t);let feature=f('G7');if(!feature)throw Error('lost moving train');
      let p=map.project(feature.geometry.coordinates);out.push([p.x,map.getCanvas().clientWidth]);}
      return out})()""")
    assert points[0][0]<points[0][1] and points[-1][0]>points[-1][1],points
    assert all(0<b[0]-a[0]<20 for a,b in zip(points,points[1:])),points
    await ev("seek(47110);void place(120.64,31.42,6.8)")
    await wait("feats().some(x=>x.properties.count>1)")
    await ev("void place(120.64,31.42,8.6)")
    await wait("feats().every(x=>!x.properties.count) && f('G1211')")
    # Verify playback across the 13:10 bucket boundary in both directions.
    for rate,t in [(1,47365),(-1,47445)]:
        await ev(f"""seek({t});q('#live-speed').value='32';q('#live-speed').dispatchEvent(new Event('change'));q('[data-rate="{rate}"]').click();""")
        await wait("f('G1211') && f('G7')")
        result=await ev("""new Promise(resolve=>{let last=performance.now(),frames=[],positions=[],missing=0;
          function frame(ts){frames.push(ts-last);last=ts;let v=f('G1211');if(v)positions.push(v.geometry.coordinates);else missing++;
          if(ts-start<2800)requestAnimationFrame(frame);else resolve({frames:frames.length,maxGap:Math.round(Math.max(...frames)),missing,distinct:new Set(positions.map(JSON.stringify)).size})}
          const start=performance.now();requestAnimationFrame(frame)})""")
        await ev("q('[data-rate=\"0\"]').click()")
        print('32x',rate,result,flush=True)
        assert result['missing']==0 and result['distinct']>2
    await ev("seek(47110);void place(120.64,31.42,10)")
    await wait("f('G1211')")
    await wait("map.queryRenderedFeatures(map.project(original),{layers:['region-heading']}).some(f=>f.properties.number==='G1211')")
    await ev("""window.tail=map.getSource('region-trains')._data.features.find(x=>
      x.properties.car===4 && x.properties.id===f('G1211').properties.id).geometry.coordinates""")
    await wait("map.queryRenderedFeatures(map.project(tail),{layers:['region-heading']}).some(x=>x.properties.car===4)")
    await ev("void map.fire('click',{originalEvent:{target:map.getCanvas()},point:map.project(tail),lngLat:map.unproject(map.project(tail))})")
    await wait("q('#panel h2')?.textContent.includes('G1211') && map.getSource('selected-train')?._data.features[0]?.geometry && !f('G1211')")
    await wait("feats().some(x=>x.properties.priority) && feats().some(x=>!x.properties.priority)")
    # Opposing directions along an identical physical segment share priority.
    assert await ev("regionLive.routeEdges([[1,2],[3,4]]).has([...regionLive.routeEdges([[3,4],[1,2]])][0])")
    assert await ev("map.getZoom()===10 && !q('#region-status').hidden")
    await wait("""(window.other=map.queryRenderedFeatures({layers:['region-heading']})
      .find(x=>!x.properties.car && !x.properties.priority && x.properties.number!=='G1211'))""")
    await ev("void map.fire('click',{originalEvent:{target:map.getCanvas()},point:map.project(other.geometry.coordinates),lngLat:map.unproject(map.project(other.geometry.coordinates))})")
    await wait("q('#panel h2')?.textContent.includes(other.properties.number) && feats().length>0")
    await ev("clearMapSelection()")
    await wait("f('G1211') && !map.getSource('selected-train')?._data.features[0]?.geometry")
    await ev("showLine('京沪高速线').then(()=>{map.stop();place(120.64,31.42,9)})")
    await wait("f('G1211') && q('.line-facts')")
    assert await ev("!q('.line-live') && route.features.some(f=>f.properties.railway && f.geometry.type==='LineString') && route.features.some(f=>f.properties.railway && f.properties.number===1)")
    assert await ev("!q('#live-clock').hidden")
    print('Passed pan/zoom cluster, direction, playback, run identity, drill-down and cleanup')

if __name__=='__main__': asyncio.run(drive(sys.argv[1],check))
