"""Check the passenger view against the published station times."""
import asyncio
import sys
from browser_driver import drive


async def check(ev, wait):
    await ev("""window.q=s=>document.querySelector(s);
      window.seek=s=>liveTimeline.jump(liveTimeline.midnight('2026-11-19')+1000*s);
      window.lookup=n=>{q('#query').value=n;q('#search').requestSubmit()};
      window.visibleStops=()=>[...document.querySelectorAll('.journey-stops li:not([hidden])')];
      seek(12*3600+10*60+39);lookup('G1211')""")
    await wait("q('.journey-speed b')?.textContent && map.getSource('selected-train')?._data.features[0]?.geometry")
    assert await ev("q('.live-runs').hidden && q('.journey-arrival').innerText.includes('南京南') && q('.journey-arrival').innerText.includes('05:21')")
    assert await ev("visibleStops().length===6 && visibleStops().slice(0,3).every(r=>r.innerText.includes('已离站')) && !q('.relation-marker') && q('.journey-track').children.length===6")
    # Keep the mouse down while checking that manual panning has taken control.
    await ev("""window.centered=c=>Math.hypot(map.getCenter().lng-c[0],map.getCenter().lat-c[1])<0.00002;window.atAnchor=()=>{const p=map.project(map.getSource('selected-train')._data.features[0].geometry.coordinates);
      return Math.abs(p.y-map.getCanvas().clientHeight*.65)<1 && Math.abs(p.x-map.getCanvas().clientWidth*.5)<1};window.follow=q('.journey-cameras button:nth-child(2)');follow.click();
      liveTimeline.replay(liveTimeline.time());""")
    await wait("follow.getAttribute('aria-pressed')==='true' && atAnchor()")
    await ev("""window.drag=(target,type,x,buttons)=>target.dispatchEvent(new MouseEvent(type,{
      clientX:x,clientY:240,button:0,buttons,bubbles:true,cancelable:true,view:window}));
      window.tilt=map.getPitch();window.angle=map.getBearing();drag(map.getCanvas(),'mousedown',790,1);drag(map.getCanvas(),'mousemove',850,1);""")
    await wait("follow.getAttribute('aria-pressed')==='false' && follow.textContent==='继续列车视角'")
    assert await ev("map.getPitch()===tilt && map.getBearing()===angle && q('#rail-basemap').getAttribute('aria-pressed')==='true'")
    await ev("drag(map.getCanvas(),'mouseup',850,0);map.stop();window.panCenter=map.getCenter().toArray();window.t1=liveTimeline.time();")
    await wait("liveTimeline.time()>t1+2000")
    assert await ev("centered(panCenter) && q('#panel h2').textContent==='G1211'")
    await ev("follow.click()")
    await wait("follow.getAttribute('aria-pressed')==='true' && atAnchor()")
    await ev("follow.click();seek(12*3600+10*60+39)")
    # Watch a non-stopping train through the station that exposed the collision bug.
    await ev("""window.xuzhou=toMapCoordinates([117.300205,34.269163]);
      map.stop();map.jumpTo({center:xuzhou,zoom:10});
      window.point=()=>map.getSource('selected-train')?._data.features[0];
      let left=11*3600,right=11*3600+30*60;
      while(left<right){const mid=(left+right)>>1;seek(mid);
        if(point().geometry.coordinates[1]>xuzhou[1])left=mid+1;else right=mid;}
      window.pass=left;seek(pass-5);""")
    assert await ev("""(()=>{for(let t=pass-600;t<pass+600;t+=15){
      seek(t);const parts=map.getSource('selected-train')._data.features;
      if(parts.length!==5 || new Set(parts.map(f=>String(f.geometry.coordinates))).size!==5)return false;
      const angles=parts.map(f=>f.properties.bearing);
      if(angles.some(a=>Math.abs((a-angles[0]+540)%360-180)>8)){
        const saved=JSON.stringify(parts);seek(t+60);seek(t);return JSON.stringify(map.getSource('selected-train')._data.features)===saved;
      }
    }return false})()""")
    for delta in [-5,0,5]:
        await ev(f"seek(pass+({delta}))")
        await wait(r"""map.queryRenderedFeatures({layers:['selected-train-heading']}).length &&
          map.queryRenderedFeatures({layers:['selected-train-caption']}).some(f=>
            f.properties.caption===point().properties.caption && /^G1211\n[0-9]+ km\/h\n-> 南京南 \([0-9:]+\)\n石家庄 --> 上海虹桥$/.test(f.properties.caption))""")
        await wait("""map.queryRenderedFeatures({layers:['station-pax-01-LT','station-pax-245-LT']})
          .some(f=>f.properties['name:zh']==='徐州东')""")
    # Probe the placed glyph box, not just the source string: a zero wrap
    # width in the bundled renderer can stack a correct caption vertically.
    assert await ev("""(()=>{const parts=map.getSource('selected-train')._data.features;
      if(!parts[0].properties.caption || parts.slice(1).some(f=>f.properties.caption))return false;
      const p=map.project(parts[0].geometry.coordinates);
      const hit=(x,y)=>map.queryRenderedFeatures([p.x+x,p.y+y],{layers:['selected-train-caption']}).length>0;
      return hit(0,-20) && hit(0,-54) && !hit(0,15);
    })()"""), 'The selected train caption must occupy four compact rows.'
    await ev("liveTimeline.replay(liveTimeline.midnight('2026-11-19')+(pass-8)*1000)")
    missing=await ev("""new Promise(resolve=>{const start=performance.now();let missing=0;
      function frame(now){for(const layer of ['selected-train-heading','selected-train-caption']){
        if(!map.queryRenderedFeatures({layers:[layer]}).length)missing++;
      }
      if(now-start<2200)requestAnimationFrame(frame);else resolve(missing);}
      requestAnimationFrame(frame);})""")
    assert missing==0, missing
    await ev("seek(12*3600+16*60)")
    assert await ev("q('.journey-stops .parked').innerText.includes('停靠中 · 06:00 后开') && q('.journey-speed b').textContent==='0' && q('.journey-arrival').innerText.includes('06:00') && q('.journey-arrival').innerText.includes('苏州北')")
    await ev("seek(12*3600+22*60)")
    assert await ev("visibleStops().length===6 && visibleStops()[3].innerText.includes('已离站') && visibleStops()[4].innerText.includes('下一站 · 45:00') && !q('.relation-marker')")
    # A timed intermediate stop resets the tail even though the path extends behind it.
    assert await ev("""(()=>{
      map.setZoom(12);
      const start=12*3600+22*60;
      const counts=[];
      for(let offset=0;offset<=120;offset+=3){
        seek(start+offset);
        const parts=map.getSource('selected-train')._data.features;
        if (!parts[0].properties.caption || parts.slice(1).some(f=>f.properties.caption)) return false;
        counts.push(parts.length);
      }
      if([...new Set(counts)].join()!=='1,2,3,4,5')return false;
      return counts.every((count,i)=>{
        const j=counts.length-1-i;
        seek(start+3*j);
        return map.getSource('selected-train')._data.features.length===counts[j];
      });
    })()"""), 'Carriages must emerge one by one and retract identically on reverse seek.'
    await ev("seek(12*3600+23*60)")
    assert await ev("q('.journey-speed b').textContent==='38'"), 'G1211 gentle start one minute out of Nanjing South'
    await ev("seek(13*3600+10*60)")
    assert await ev("point().properties.caption.endsWith(' km/h\\n-> 上海虹桥 (23:00)\\n石家庄 --> 上海虹桥')")
    await ev("seek(13*3600+33*60)")
    assert await ev("visibleStops().length===6 && visibleStops()[5].innerText.includes('已终到') && q('.journey-header').innerText.includes('已终到')")
    await ev("seek(7*3600);lookup('G1211')")
    await wait("visibleStops()[0]?.innerText.includes('待始发') && q('.journey-header')?.innerText.includes('等待始发') && q('.live-runs').hidden")
    await ev("seek(19*3600+22*60);lookup('Z164')")
    await wait("q('.live-runs') && !q('.live-runs').hidden && q('.live-runs').innerText.includes('西宁')")
    print('Passed G1211 arrival, dwell, departure, terminus, pre-departure and Z164 overlap.')


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],check))
