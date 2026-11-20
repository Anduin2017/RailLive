"""Exercise slow five-minute tiles in both directions and a retired static snapshot."""
import asyncio
import sys

from browser_driver import drive


# Install before navigation so speculative requests use the same gates as visible ones.
GATES = """
const gates=new Map();
window.releaseSlot=slot=>gates.get(slot).release();
for (const slot of [182,183,185]) {
  let release;
  const promise=new Promise(resolve=>{release=resolve});
  gates.set(slot,{promise,release});
}
const rawFetch=window.fetch.bind(window);
window.fetch=async (url,...args)=>{
  const path=new URL(url,location.href).pathname;
  const match=path.match(/\\/space-time\\/[^/]+\\/(?:120|121)_31\\/(\\d+)\\.gz$/);
  const slot=match && Number(match[1]);
  if (gates.has(slot)) {
    await gates.get(slot).promise;
    if (slot===182) return new Response(null,{status:404});
  }
  const response=await rawFetch(url,...args);
  if (window.testRetired && path.endsWith('/space-time/manifest.json')) {
    const manifest=await response.json();
    return Response.json({...manifest,version:'replacement'});
  }
  return response;
};
"""


async def check(ev, wait):
    await ev("""window.base=liveTimeline.midnight('2026-11-20')+15*3600000;
      window.seek=seconds=>liveTimeline.jump(base+seconds*1000);
      window.heads=()=>map.getSource('region-trains')?._data.features.filter(f=>!f.properties.car)||[];
      window.statusText=()=>document.querySelector('#region-status').textContent;
      window.direction=rate=>document.querySelector('[data-rate="'+rate+'"]').click();
      const speed=document.querySelector('#live-speed');
      speed.value='4';speed.onchange();
      seek(1497);void map.jumpTo({center:toMapCoordinates([120.889,31.371]),zoom:12.25})""")
    for start, rate, slot in [(1497,1,185),(1203,-1,183)]:
        await ev(f'seek({start})')
        await wait("spaceTime.stats.complete && heads().some(f=>f.properties.number==='G7015' || f.properties.number==='G23')")
        await ev(f'direction({rate})')
        await wait("!spaceTime.stats.complete && statusText().includes('暂显')")
        held = await ev('heads()')
        assert held, (rate, 'blank while waiting')
        await asyncio.sleep(.65)
        assert await ev('heads()') == held, (rate, 'partial frame replaced the last complete one')
        await ev(f'releaseSlot({slot})')
        await wait("spaceTime.stats.complete && !statusText().includes('暂显') && heads().length")
        assert await ev('heads()') != held, (rate, 'playback did not resume')
        await ev('direction(0)')
        print('4x handoff',rate,'held',len(held),'heads and resumed',flush=True)

    # Retire the next tile while this tab still uses its original catalog.
    await ev('seek(902)')
    await wait("spaceTime.stats.complete && heads().length")
    await ev('window.testRetired=true;releaseSlot(182);seek(899)')
    await wait("statusText().includes('地图数据已更新') && heads().length")
    await ev("liveTimeline.jump(liveTimeline.midnight('2030-01-01'))")
    await wait("!heads().length")
    print('retired snapshot reported; distant seek cleared old trains',flush=True)


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],check,viewport=(1926,1053),init_script=GATES))
