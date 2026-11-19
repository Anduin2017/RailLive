"""Measure Suzhou cold load, ten playback boundaries and selected-train updates via CDP."""
import asyncio
import sys
from browser_driver import drive


async def benchmark(ev, wait):
    await ev("""window.q=s=>document.querySelector(s);
      window.seek=s=>liveTimeline.jump(liveTimeline.midnight('2026-11-19')+s*1000);
      window.src=()=>map.getSource('region-trains');
      window.rate=r=>{let s=q('#live-speed');s.value=Math.abs(r);s.dispatchEvent(new Event('change'));
          q('[data-rate="'+Math.sign(r)+'"]').click()};
      window.resources=()=>performance.getEntriesByType('resource').filter(r=>r.name.includes('/space-time/') || r.name.includes('/api/region-live?day'));
      window.startup=resources().reduce((n,r)=>n+r.encodedBodySize,0);
      performance.setResourceTimingBufferSize(30000);performance.clearResourceTimings();
      window.t0=performance.now();seek(46800);void map.jumpTo({center:toMapCoordinates([120.64,31.42]),zoom:8})""")
    await wait("src()?._data.features.length>100 && (typeof spaceTime==='undefined' || spaceTime.stats.complete) && map.queryRenderedFeatures({layers:['region-heading']}).length>0")
    print('cold',await ev("""[Math.round(performance.now()-t0),resources().length,resources().reduce((n,r)=>n+r.encodedBodySize,0),innerWidth,innerHeight]"""),flush=True)
    await ev("""window.sample=async(ms,target)=>{
      let start=performance.now(),last=start,empty=0,updates=0,old,gaps=[],intervals=0,wasEmpty=false;
      let heap=0,measureAfter=start;
      await new Promise(done=>{function frame(t){let data=target()?._data;
        const missing=!data?.features.length;
        if(missing){empty+=t-last;if(!wasEmpty)intervals++}wasEmpty=missing;
        if(t>=measureAfter){heap=Math.max(heap,performance.memory?.usedJSHeapSize||0);measureAfter=t+1000}
        if(data!==old)updates++;old=data;gaps.push(t-last);last=t;
        if(t-start<ms)requestAnimationFrame(frame);else done()}requestAnimationFrame(frame)});
      gaps.sort((a,b)=>a-b);return {emptyMs:empty,emptyIntervals:intervals,peakSampledHeap:heap,hz:updates*1000/(last-start),p95:gaps[Math.floor(gaps.length*.95)],worst:gaps.at(-1)}}""")
    if '--cold' in sys.argv: return
    for direction in (1,-1):
        await ev(f"rate({32*direction})")
        print('boundaries',direction,await ev('sample(95000,src)'),flush=True)
    await ev("""seek(46920);q('#query').value='G1211';q('#search').requestSubmit()""")
    await wait("q('.journey-header button') && map.getSource('selected-train')?._data.features.length")
    await ev("""q('.journey-header button').click();rate(1)""")
    print('follow',await ev("sample(5000,()=>map.getSource('selected-train'))"),flush=True)
    print('total',await ev("resources().reduce((n,r)=>n+r.encodedBodySize,0)"),flush=True)
    print('startup region bytes',await ev("startup"),flush=True)
    print('font requests',await ev("performance.getEntriesByType('resource').filter(r=>r.name.endsWith('/0-255.pbf')).length"),flush=True)
    stats=await ev("typeof spaceTime!=='undefined' && spaceTime.stats")
    if stats:
        stats['unusedPrefetchAtEnd']=stats['speculative']-stats['usedPrefetch']
    print('cache',stats,flush=True)


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],benchmark,(2467,1257)))
