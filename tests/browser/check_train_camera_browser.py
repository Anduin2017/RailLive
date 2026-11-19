"""Exercise heading-up train navigation on a phone-sized map and the real timeline."""
import asyncio
import sys

from browser_driver import drive


async def check(ev, wait):
    await ev("""window.q=s=>document.querySelector(s);
        window.cam=()=>q('.journey-cameras button:nth-child(2)');
        window.flat=()=>q('.journey-cameras button');
        window.seek=s=>liveTimeline.jump(liveTimeline.midnight('2026-11-19')+1000*s);
        window.pressed=b=>b.getAttribute('aria-pressed')==='true';
        window.head=()=>map.getSource('selected-train')?._data.features[0];
        window.tracking=()=>head() && pressed(cam()) && map.getPitch()>45 &&
          Math.abs(map.project(head().geometry.coordinates).x-map.getCanvas().clientWidth*.5)<1 &&
          Math.abs(map.project(head().geometry.coordinates).y-map.getCanvas().clientHeight*.65)<1 &&
          Math.abs((map.getBearing()-head().properties.bearing+540)%360-180)<0.5;
        window.select1211=()=>{q('#query').value='G1211';q('#search').requestSubmit()};
        seek(10*3600+12*60);""")
    assert await ev("!cam() && q('.basemap-switcher').children.length===3")
    await ev("followView.autoFollow=true;select1211()")
    await wait("flat() && pressed(flat()) && !cam().hidden")
    assert await ev("map.getPitch()===0 && !pressed(cam()) && Math.abs(map.project(head().geometry.coordinates).y-map.getCanvas().clientHeight/3)<1")
    await ev("cam().click()")
    await wait("tracking() && sidebar.hidden && aboutPanel.hidden")
    assert await ev("pressed(q('#rail-basemap')) && !pressed(flat()) && map.getZoom()===13")
    # Across the Jinan stop this service turns from the eastbound line onto
    # the southbound main line. Check both arms and direct seeks in either order.
    angles=[]
    for sec in (9*3600+45*60,10*3600+14*60,10*3600+30*60,9*3600+45*60):
        await ev(f'seek({sec})')
        await wait('tracking()')
        angles.append(await ev('map.getBearing()'))
    assert abs((angles[2]-angles[0]+180)%360-180)>35, angles
    assert abs(angles[0]-angles[3])<0.1, angles
    await ev("seek(10*3600+17*60)")
    parked=await ev('[map.getBearing(),...map.getCenter().toArray()]')
    await ev("seek(10*3600+17*60+20)")
    assert await ev('[map.getBearing(),...map.getCenter().toArray()]')==parked

    await ev("""seek(10*3600+30*60);q('#live-speed').value='32';
        q('#live-speed').dispatchEvent(new Event('change'));q('[data-rate="-1"]').click()""")
    await wait("liveTimeline.playbackRate===-32 && tracking()")
    # Both the zoom control and manual zoom-out must work during playback.
    for target in (14,15):
        await ev("q('.mapboxgl-ctrl-zoom-in').click()")
        await wait(f"Math.abs(map.getZoom()-{target})<0.001 && !map.isZooming() && tracking()")
    assert await ev("map.getMaxZoom()===15")
    await ev("q('.mapboxgl-ctrl-zoom-out').click()")
    await wait("Math.abs(map.getZoom()-14)<0.001 && !map.isZooming() && tracking()")
    await ev("q('#live-minimize').click()")
    start=await ev('map.getCenter().toArray()')
    await asyncio.sleep(.4)
    await wait('tracking()')
    assert await ev('map.getCenter().toArray()')!=start
    assert await ev("q('#live-clock').hidden && liveTimeline.playbackRate===-32")
    await ev("q('#live-toggle').click()")
    await ev("q('[data-rate=\"0\"]').click();q('#rail-basemap').click()")
    assert await ev("pressed(q('#rail-basemap')) && tracking()")
    await ev("q('.mapboxgl-ctrl-zoom-in').click()")
    await wait("map.getZoom()===15 && !map.isZooming() && tracking()")
    await ev("sidebarToggle.click();flat().click()")
    assert await ev("!sidebar.hidden")
    assert await ev("pressed(flat()) && !pressed(cam()) && map.getPitch()===0")
    await ev("cam().click()")
    await wait("map.getZoom()===15 && tracking()")
    await ev("seek(13*3600+33*60)")
    await wait("cam().hidden && !pressed(cam()) && map.getPitch()===0")
    await ev("seek(10*3600+30*60);cam().click();clearMapSelection()")
    assert await ev("!cam() && map.getPitch()===0 && pressed(q('#rail-basemap'))")
    # This run has no primary shape. Its dwell between two reconstructed
    # legs must keep a camera target until the train leaves the platform.
    await ev("""followView.autoFollow=false;seek(14*3600+29*60);q('#query').value='C240';q('#search').requestSubmit()""")
    await wait("cam() && !cam().hidden && !pressed(flat()) && !pressed(cam()) && q('#panel h2').textContent==='C240'")
    await ev('cam().click();seek(14*3600+31*60)')
    await wait("tracking() && head().properties.parked")
    await ev('seek(14*3600+32*60+15)')
    await wait('tracking() && !head().properties.parked')
    await ev("q('#satellite-basemap').click()")
    assert await ev("tracking()")
    await ev("q('#rail-basemap').click()")
    print('Phone follow, turn at Jinan, dwell, reverse and exit passed. Bearings:',angles)


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],check,(390,844)))
