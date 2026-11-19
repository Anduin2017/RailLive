"""Exercise data-saving basemap switching and inspect blank-mode tile requests."""
import asyncio
import sys

from browser_driver import drive


async def switch(ev, wait):
    await ev("""document.querySelector('#blank-basemap').click();
        liveTimeline.jump(liveTimeline.midnight('2026-11-19')+13*3600000);
        window.requested=[];
        const manager=map._requestManager, transform=manager.transformRequest.bind(manager);
        manager.transformRequest=(url,type)=>{if(type==='Tile')requested.push(url);return transform(url,type)};
        void map.jumpTo({center:toMapCoordinates([120.62,31.32]),zoom:10});""")
    await wait("map.loaded() && map.queryRenderedFeatures({layers:['rail_hsr2_zoomto11','rail_r2','rail_rr2']}).length")
    await wait("map.getLayer('region-heading') && map.queryRenderedFeatures({layers:['region-heading']}).length")
    await ev("void map.jumpTo({center:toMapCoordinates([118.80,32.05]),zoom:9})")
    await wait("map.loaded() && requested.some(url=>url.includes('/map/railway/'))")
    assert await ev("requested.every(url=>url.includes('/map/railway/'))"), await ev("requested")
    assert await ev("!map.getLayer('water') && !map.getSource('eox-sentinel-satellite')")
    assert await ev("""(()=>{document.querySelector('#satellite-basemap').click();
        const installed=Boolean(map.getLayer('eox-sentinel-satellite'));
        document.querySelector('#blank-basemap').click();return installed;})()""")
    assert await ev("!map.getSource('eox-sentinel-satellite') && document.querySelector('#blank-basemap').getAttribute('aria-pressed')==='true'")


async def restore(ev, wait):
    assert await ev("document.querySelector('#blank-basemap').getAttribute('aria-pressed')==='true' && !map.getSource('scenery')")
    await ev("document.querySelector('#rail-basemap').click();void map.jumpTo({center:toMapCoordinates([120.62,31.32]),zoom:9})")
    await wait("map.loaded() && map.queryRenderedFeatures({layers:['water','waterway-river','highway-primary']}).length")
    print("Blank mode retained tracks and trains, requested only railway tiles, survived reload and restored map detail.")


async def main(port):
    await drive(port, switch)
    await drive(port, restore)


if __name__=='__main__':
    asyncio.run(main(sys.argv[1]))
