"""Verify line selection, alternate stop order and train colours on the rendered map."""
import asyncio
import sys
from browser_driver import drive


async def check(ev, wait):
    for select,name in [("showLine('京哈线','map')",'京哈线'),("search('京沪高速线')",'京沪高速线')]:
        await ev('void '+select)
        await wait("panel.querySelector('h2')?.textContent==='"+name+"' && !map.isMoving()")
        await wait("map.queryRenderedFeatures({layers:['trip-line']}).some(f=>f.properties.railway)")
        await wait("map.queryRenderedFeatures({layers:['trip-stop-number']}).some(f=>f.properties.number>1)")
        assert await ev("route.features.filter(f=>f.geometry.type==='Point').every(f=>panel.querySelectorAll('.line-station-list li')[f.properties.number-1]?.textContent.includes(f.properties.name))")
        if name=='京哈线':
            await ev("panel.querySelector('[aria-label=站序来源]').lastChild.click()")
            assert await ev("Math.max(...route.features.map(f=>f.properties.number||0))<=panel.querySelectorAll('.line-station-list li').length")
        print(name, await ev("[route.features.filter(f=>f.properties.number).length,route.features.filter(f=>f.geometry.type==='LineString').length]"))
    await ev("""document.querySelector('[data-catalog=lines]').click();
      const input=document.querySelector('.catalog-search');input.value='京哈线';input.oninput()""")
    await wait("Array.from(document.querySelectorAll('.catalog-row-link')).some(b=>b.textContent==='京哈线')")
    await ev("Array.from(document.querySelectorAll('.catalog-row-link')).find(b=>b.textContent==='京哈线').click()")
    await wait("panel.querySelector('h2')?.textContent==='京哈线' && route.features.some(f=>f.properties.railway)")
    await ev("void search('G1211')")
    await wait("route.features.some(f=>f.geometry.type==='LineString') && route.features.every(f=>!f.properties.railway)")
    await ev("clearMapSelection()")
    await wait("!route.features.length && !map.queryRenderedFeatures({layers:['trip-stop-number','trip-line']}).length")


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],check))
