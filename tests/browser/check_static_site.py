"""Check a compiled site on a plain HTTP server, optionally below a path prefix.

Example (from the repo root after tools/prepare.py):
    python3 tests/browser/check_static_site.py 8090/site/
"""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
from contextlib import closing
from urllib.parse import parse_qs, urlsplit

from browser_driver import drive

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'build/app'))
from server import DB, Handler


async def check(ev, wait):
    assert await ev("!!window.staticRailData")
    await ev("performance.setResourceTimingBufferSize(20000)")
    routes = ['/api/info', '/api/search?q=G12', '/api/search?q=苏州',
              '/api/search?q=京沪', '/api/search?q=南京&scope=station',
              '/api/catalog/trains?tag=G&tag=D&sort=peakKmh&order=desc&page=2',
              '/api/catalog/stations?multi=1&tag=through&sort=name',
              '/api/catalog/lines?tag=link&sort=speed&order=desc',
              '/api/station/OHH', '/api/station/07716fd88114427883640c2d226dbc8d',
              '/api/station-calendar/OHH', '/api/station-lines/OHH',
              '/api/line/京沪高速线?from=map', '/api/line/京沪高速线?from=gtfs']
    with closing(sqlite3.connect(DB)) as db:
        db.row_factory = sqlite3.Row
        for number in ('G1211', 'G6605', 'Z164', 'Z502'):
            trip, = db.execute('SELECT id FROM trips WHERE number=? LIMIT 1', (number,)).fetchone()
            routes.extend(['/api/trip/' + trip, '/api/region-live?trip=' + trip])
            for day in ('2026-11-02', '2026-11-19', '2027-01-31'):
                routes.append(f'/api/relations/{trip}?day={day}')
        for route in routes:
            url = urlsplit(route)
            expected = Handler.query(db, url.path, parse_qs(url.query))
            actual = await ev(f'get({json.dumps(route)})')
            if url.path.startswith('/api/station-calendar/'):
                # SQL group_concat does not promise chronological order.
                for data in (actual, expected):
                    data['calendars'] = {k: sorted(v.split(',')) for k,v in data['calendars'].items()}
            assert actual == expected, route

    await ev("""liveTimeline.jump(liveTimeline.midnight('2026-11-19')+47110000);
      void map.jumpTo({center:toMapCoordinates([120.64,31.42]),zoom:10})""")
    await wait("spaceTime.stats.complete && map.getSource('region-trains')?._data.features.some(f=>f.properties.number==='G1211')")
    await ev("void search('G1211')")
    await wait("panel.querySelector('h2')?.textContent==='G1211' && map.getSource('selected-train')?._data.features.length")
    await ev("document.querySelector('.relation-card').parentNode.open=true")
    await wait("document.querySelector('.relation-card select')?.options.length")
    await ev("void showStation('OHH')")
    await wait("document.querySelector('#station-board .board-departures button')")
    # Includes startup assets, JSON, data, glyphs and map tile requests.
    unexpected = await ev("""performance.getEntriesByType('resource').map(r=>new URL(r.name))
      .filter(u=>u.origin===location.origin && (!u.href.startsWith(new URL('.',location.href).href) ||
        u.pathname.split('/').includes('api'))).map(u=>u.href)""")
    assert not unexpected, unexpected
    print(f'{len(routes)} static queries matched the snapshot; map, station board and relations ran inside the deployment prefix.')


if __name__ == '__main__':
    asyncio.run(drive(sys.argv[1],check))
