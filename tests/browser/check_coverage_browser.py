"""Exercise the shipping UI on a dedicated Chromium debug port (19851)."""
import asyncio
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'build/app'))
from browser_driver import drive
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'snapshot'))
from snapshot_case import case_day

async def check(ev,wait):
    await ev("""window.q=s=>document.querySelector(s);window.seek=n=>{q('#live-range').value=n;q('#live-range').dispatchEvent(new Event('input'))};
    window.searchFor=n=>{q('#query').value=n;q('#search').requestSubmit()};
    q('#live-date').value='%s';q('#live-date').dispatchEvent(new Event('change'));seek(43200);searchFor('哈佳线')""" % case_day())
    await wait("q('.line-facts') && q('.line-station-list')")
    await wait("!q('.line-live') && route.features.some(f=>f.properties.railway)")
    await ev("seek(19*3600+22*60);searchFor('Z164')")
    await wait("q('.live-runs')?.innerText.includes('西宁') && q('.live-runs').innerText.includes('苏州')")
    result=await ev("q('.live-runs').innerText")
    assert result.count('在途')==2 and '前两日' in result
    await ev("seek(42300);searchFor('G1211')")
    await wait("q('#panel h2')?.textContent==='G1211' && map.getSource('selected-train')?._data.features.length")
    assert await ev("!q('.relation-card')")
    assert await ev("!q('.coverage-link')")
    print('Browser passed Harbin line details, two dated Z164 at 19:22 and selected G1211.')

if __name__=='__main__': asyncio.run(drive(sys.argv[1],check))
