"""Compare catalog statistics with the actual train playback, then sort the table."""
import asyncio
import sys

from browser_driver import drive


async def check(ev, wait):
    await ev("document.querySelector('[data-catalog=trains]').click()")
    await wait("document.querySelectorAll('#catalog tbody tr').length===50")
    for field in ('distanceKm', 'averageKmh', 'peakKmh'):
        await ev(f"""document.querySelector('#catalog th[data-field="{field}"] button').click()""")
        await wait("document.querySelector('.catalog-scroll').getAttribute('aria-busy')==='false'")
        assert await ev(f"""(async()=>{{
          const data=await get('/api/catalog/trains?sort={field}&order=desc');
          return document.querySelector('#catalog tbody tr button').textContent===data.rows[0].name &&
            data.rows.every((row,i)=>!i || data.rows[i-1].{field}>=row.{field});
        }})()""")
    for number in ('G1211', 'K1456', 'Z164', 'S5127', 'G345', 'D2638', 'Z272', 'S5557'):
        metrics = await ev(f"""(async()=>{{
          const row=(await get('/api/catalog/trains?q={number}')).rows.find(r=>r.name==='{number}');
          const [trip,display]=await Promise.all([get('/api/trip/'+row.id),get('/api/region-live?trip='+row.id)]);
          trip.matchedPath=matchTrainPath(trip);
          const model=makeTrainMotion(trip);
          let km=0, peak=0;
          for(let i=0;i<trip.stops.length-1;i++){{
            const fallback=display.find(l=>l.seq===i);
            if(fallback){{
              km+=fallback.path.at(-1)[0]/1000;
              peak=Math.max(peak,displayMotion(fallback,(fallback.start+fallback.end)/2).speed);
            }}else{{
              const start=timetableSeconds(trip.stops[i].departure), end=timetableSeconds(trip.stops[i+1].arrival);
              const leg=model.state((start+end)/2).leg;
              km+=leg.distance/1000;
              for(let t=0;t<end-start;t++) peak=Math.max(peak,leg.sample(t).speed);
            }}
          }}
          const elapsed=timetableSeconds(trip.stops.at(-1).arrival)-timetableSeconds(trip.stops[0].departure);
          return [Math.abs(km-row.distanceKm),Math.abs(km*3600/elapsed-row.averageKmh),row.peakKmh-peak];
        }})()""")
        assert max(map(abs, metrics[:2])) < .001 and -.001 <= metrics[2] < 1, (number, metrics)
        print(number, 'catalog matches playback', flush=True)


if __name__ == '__main__':
    asyncio.run(drive(sys.argv[1], check))
