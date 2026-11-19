"""Check the two station queues and the shared simulation clock in Chromium."""
import asyncio
import sys
from browser_driver import drive


async def check(ev, wait):
    await ev("""window.q=s=>document.querySelector(s);
    window.seek=t=>{q('#live-range').value=t;q('#live-range').dispatchEvent(new Event('input'))};
    window.jump=(day,t)=>liveTimeline.jump(liveTimeline.midnight(day)+t*1000);
    window.rows=kind=>[...document.querySelectorAll('.board-'+kind+' button')];
    window.timers=kind=>rows(kind).map(b=>b.querySelector('time').textContent);
    jump('2026-11-19',11*3600+33*60+29);
    window.poi='07716fd88114427883640c2d226dbc8d';
    window.stationFeature=()=>map.queryRenderedFeatures({layers:['station-pax-01-LT','station-pax-245-LT']})
      .find(f=>f.properties.kid===poi);
    void map.jumpTo({center:[117.449807,32.916864],zoom:11})""")
    await wait("stationFeature()")
    await ev("""const canvas=map.getCanvas(), rect=canvas.getBoundingClientRect();
      const p=map.project(stationFeature().geometry.coordinates);
      for(const type of ['mousedown','mouseup','click'])canvas.dispatchEvent(new MouseEvent(type,{
        bubbles:true,button:0,clientX:rect.left+p.x,clientY:rect.top+p.y,view:window}));""")
    await wait("rows('departures').length===10 && rows('arrivals').length===10")
    assert await ev("q('.station-tabs [aria-pressed=true]').textContent==='概览' && q('.station-tabs').children.length===3 && q('#station-board').contains(rows('arrivals')[0]) && rows('departures')[0].querySelector('.board-scheduled').textContent==='11:33 到 · 11:35 开'")
    assert await ev("rows('departures')[0].innerText.includes('G1252') && timers('departures')[0]==='01:31'")
    assert await ev("!rows('arrivals').some(b=>b.innerText.includes('G1252'))")
    assert await ev("timers('arrivals').at(-1)>'10:00' || timers('arrivals').at(-1).length>5")
    await ev("window.first=rows('departures')[0];q('[data-rate=\"1\"]').click()")
    await wait("timers('departures')[0]==='01:30'")
    await ev("q('[data-rate=\"0\"]').click()")
    assert await ev("first===rows('departures')[0]")
    await ev("q('[data-rate=\"-1\"]').click()")
    await wait("timers('departures')[0]==='01:31'")
    await ev("q('[data-rate=\"0\"]').click();rows('departures')[0].click()")
    assert await ev("timers('departures')[0]==='00:00' && !liveTimeline.playing")
    await ev("seek(11*3600+35*60+1)")
    assert await ev("!rows('departures')[0].textContent.includes('G1252')")
    assert await ev("Array.from(q('.station-live').querySelectorAll('h3'), n=>n.textContent).join('/')==='即将到达/即将出发'")
    await ev("seek(47339)")
    assert await ev("rows('departures').some(b=>b.innerText.includes('G1211') && b.querySelector('time').textContent==='00:01')")
    await ev("seek(86399)")
    assert await ev("rows('arrivals').length===10 && rows('departures').length===10")
    await ev("window.n=rows('arrivals')[0].dataset.event;jump('2026-11-20',0)")
    assert await ev("rows('arrivals')[0].dataset.event===n")
    await ev("jump('2026-11-19',43190);void showStation('SJP')")
    await wait("rows('departures')[0]?.innerText.includes('K157') && timers('departures')[0]==='00:10'")
    assert await ev("!rows('arrivals').some(b=>b.innerText.includes('K157'))")
    await ev("jump('2026-11-19',7*3600+39*60+15);void showStation('AOH')")
    await wait("rows('departures').some(b=>b.querySelector('strong').textContent==='D3131')")
    assert await ev("""['D2212','D3131'].map(n=>rows('departures').find(b=>b.querySelector('strong').textContent===n)
      .querySelector('.board-call-kind').textContent).join('/')==='经停/本站始发'""")
    assert await ev("""(()=>{const r=q('#station-board').getBoundingClientRect();
      return Math.abs(r.height/r.width-.618)<.01 && rows('arrivals')[0].getBoundingClientRect().right<
        rows('departures')[0].getBoundingClientRect().left})()""")
    await ev("q('.station-tabs').children[1].click()")
    assert await ev("rows('departures').length===10 && !!q('.station-trains')")
    await ev("clearMapSelection()")
    assert await ev("!q('#station-board')")
    # A synthetic weekly service checks that take(10) can reach beyond tomorrow.
    await ev("""window.fake=document.createElement('section');document.body.append(fake);
    jump('2026-11-19',0);
    showStationBoard(fake,{trains:[{id:'r',service:'s',number:'始发',sequence:1,arrival:null,departure:'01:00:00'},
      {id:'a',service:'s',number:'终到',sequence:2,arrival:'27:00:00',departure:null}]},
      {start:'20261101',end:'20261130',calendars:{s:'20261118,20261125'}});
    """)
    assert await ev("fake.querySelector('.board-arrivals time').textContent==='03:00:00' && fake.querySelectorAll('.board-departures button').length===1 && fake.querySelector('.board-departures time').textContent==='145:00:00'")
    await ev("""jump('2026-11-19',0);showStationBoard(fake,{trains:[{
      id:'z',service:'night',number:'夜车',sequence:2,arrival:'23:58:00',departure:'24:03:00'}]},
      {start:'20261118',end:'20261119',calendars:{night:'20261118'}})""")
    assert await ev("fake.querySelector('.board-departures time').textContent==='03:00'")
    await ev("seek(181)")
    assert await ev("!fake.querySelector('.board-departures button')")
    print('Passed Suzhoubei queues, live countdown, replay, midnight and sparse services.')


if __name__=='__main__':
    asyncio.run(drive(sys.argv[1],check))
