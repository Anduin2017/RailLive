"""Exercise wall-clock following and transitions to timetable replay."""
import asyncio
import sys
from browser_driver import drive


async def check(ev, wait):
    await ev("""window.q=s=>document.querySelector(s);
    window.live=()=>q('#live-now').getAttribute('aria-pressed')==='true';
    window.clickRate=r=>q('[data-rate="'+r+'"]').click();
    window.speed=n=>{q('#live-speed').value=n;q('#live-speed').dispatchEvent(new Event('change'))};
    window.resume=()=>q('#live-now').click();
    window.following=()=>live() && liveTimeline.playbackRate===1 && q('#live-speed').value==='1'
      && Math.abs(Date.now()-liveTimeline.time())<100 && !q('#live-clock').hidden""")
    assert await ev("q('#live-clock').hidden && q('#live-toggle').getAttribute('aria-expanded')==='false' && live() && liveTimeline.playbackRate===1")
    await ev("q('#live-toggle').click()")
    assert await ev("following() && q('#live-toggle').getAttribute('aria-expanded')==='true'")
    await ev("speed(64);clickRate(-1);q('#live-minimize').click();window.minimizedAt=liveTimeline.time()")
    await asyncio.sleep(.15)
    assert await ev("q('#live-clock').hidden && liveTimeline.playbackRate===-64 && liveTimeline.time()<minimizedAt && document.activeElement===q('#live-toggle')")
    await wait("getComputedStyle(document.body).getPropertyValue('--live-height')==='0px'")
    await ev("q('#live-toggle').click()")
    assert await ev("!q('#live-clock').hidden && q('#live-toggle').getAttribute('aria-expanded')==='true' && liveTimeline.playbackRate===-64")
    await ev("q('#live-toggle').click()")
    assert await ev("q('#live-clock').hidden && q('#live-toggle').getAttribute('aria-expanded')==='false'")
    await ev("q('#live-toggle').click();resume()")
    for rate in (32,64,128):
        await ev(f"resume();speed({rate})")
        assert await ev(f"!live() && liveTimeline.playbackRate==={rate}")
        await ev("clickRate(-1)")
        assert await ev(f"liveTimeline.playbackRate===-{rate}")
        if rate>32:
            assert await ev(f"q('#live-speed').selectedOptions[0].textContent==='{rate}×（不推荐）'")

    await ev("clickRate(-1);resume()")
    assert await ev("following()")
    await ev("clickRate(-1)")
    assert await ev("!live() && liveTimeline.playbackRate===-1")
    await ev("resume();clickRate(0);window.frozen=liveTimeline.time()")
    await asyncio.sleep(.3)
    assert await ev("!live() && frozen===liveTimeline.time()")
    await ev("""window.scrub=n=>{const slider=q('#live-range');
      slider.dispatchEvent(new PointerEvent('pointerdown'));
      slider.value=n; slider.dispatchEvent(new Event('input'));
      slider.dispatchEvent(new PointerEvent('pointerup',{bubbles:true}))};resume();scrub(3601)""")
    assert await ev("!live() && liveTimeline.playbackRate===1 && q('#live-time').value==='01:00:01'")
    for direction in (1,-1,0):
        await ev(f"speed(32);clickRate({direction});scrub(43200);window.afterSeek=liveTimeline.time()")
        assert await ev(f"liveTimeline.playbackRate==={32*direction} && Math.abs(afterSeek-liveTimeline.midnight(q('#live-date').value)-43200000)<1000")
        await asyncio.sleep(.15)
        assert await ev(f"Math.sign(liveTimeline.time()-afterSeek)==={direction}")
    await ev("resume();liveTimeline.replay(Date.now()-60000)")
    assert await ev("!live() && liveTimeline.playbackRate===8")
    await ev("resume()")
    assert await ev("following()")
    print('Passed persistent timeline, LIVE, speed, reverse, pause, seek and event replay.')


if __name__ == '__main__':
    asyncio.run(drive(sys.argv[1], check))
