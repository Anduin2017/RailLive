"""Collect the public Chinese station and railway graph by original IDs. Re-run to resume."""
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import argparse
import hashlib
import itertools
import sqlite3
import struct
from datetime import datetime, timezone
from urllib.parse import urlencode
import json
from pathlib import Path
import re
from threading import Lock
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from index_rail_map import fields, packed

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'data/cnrail-source'
BASE = 'http://cnrail.geogv.org'
ID = re.compile('[0-9a-f]{32}')
gate = Lock()
next_request = 0
refresh_since = 0


def tile_ids():
    for path in (ROOT / 'map/tiles').glob('*/*/*.pbf'):
        for tag, layer in fields(path.read_bytes()):
            if tag != 3:
                continue
            parts = defaultdict(list)
            for key, value in fields(layer):
                parts[key].append(value)
            if parts[1][0] not in (b'railpoi0',b'railpoi1') or b'kid' not in parts[3]:
                continue
            kid = parts[3].index(b'kid')
            values = [dict(fields(blob)).get(1, b'') for blob in parts[4]]
            for feature in parts[2]:
                attrs = iter(packed(dict(fields(feature)).get(2, b'')))
                for key, index in zip(attrs, attrs):
                    if key == kid:
                        yield values[index].decode()


def references(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in ('kid','eid') and isinstance(value,str) and ID.fullmatch(value):
                yield ('poi' if key == 'kid' else 'feature', value)
            else:
                yield from references(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from references(item)


def request(path):
    global next_request
    url = BASE + path
    for attempt in range(3):
        with gate:
            time.sleep(max(0, next_request-time.monotonic()))
            next_request = time.monotonic() + .27
        try:
            with urlopen(Request(url, headers={'User-Agent':'Rails-offline/1.0'}), timeout=20) as response:
                obj = json.load(response)
            break
        except HTTPError as exc:
            if exc.code not in (408, 500, 502, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt + 1))
    if isinstance(obj,str):
        # The source client indexes JavaScript UTF-16 code units. A rare Han
        # character can span two units and must not shift the remaining key.
        raw = obj.encode('utf-16-le', errors='surrogatepass')
        units = struct.unpack(f'<{len(raw)//2}H', raw)
        length = units[0]
        cipher = units[1:length+1]
        decoded = [(unit-cipher[i%length])%65536 for i,unit in enumerate(units[length+1:])]
        obj = json.loads(struct.pack(f'<{len(decoded)}H', *decoded).decode('utf-16-le'))
    return obj


def read_record(key):
    kind, identifier = key
    path = CACHE / kind / (identifier + '.json')
    if path.exists() and path.stat().st_mtime >= refresh_since:
        return json.loads(path.read_text())
    obj = request(f'/api/{kind}/{identifier}?locale=zhcn')
    if not isinstance(obj,dict) or ('view' if kind=='feature' else 'name') not in obj:
        raise ValueError('Unexpected source response')
    tmp = path.with_suffix('.part')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(',',':')))
    tmp.replace(path)
    return obj


def discover():
    # Search proposes IDs to archive; its display string never creates a crosswalk.
    known_stops = set()
    known_lines = set()
    for path in (CACHE/'poi').glob('*.json'):
        for view in json.loads(path.read_text()).get('exd',[]):
            if view.get('view')=='station':
                data = view['data']
                known_stops.add((data.get('tele_code'),data.get('name')))
                known_lines.update(e.get('linename') for e in data.get('connection') or [])
    for path in (CACHE/'feature').glob('*.json'):
        known_lines.add(json.loads(path.read_text()).get('data',{}).get('name'))
    with sqlite3.connect(ROOT/'data/trains.sqlite') as db:
        stops = {name for code,name in db.execute('SELECT id,name FROM stops') if (code,name) not in known_stops}
        lines = {r[0] for r in db.execute('SELECT DISTINCT line FROM line_stations')}
    lines.update(json.loads((ROOT/'map/rail-selection.json').read_text()))
    needed = stops | (lines-known_lines)
    result = set()
    def search_term(term):
        path = CACHE/'search'/(hashlib.sha256(term.encode()).hexdigest()+'.json')
        if not path.exists() or path.stat().st_mtime < refresh_since:
            hits = request('/api/search?'+urlencode({'keyword':term}))
            if not isinstance(hits,list):
                raise ValueError(f'Invalid search results for {term}')
            tmp = path.with_suffix('.part')
            tmp.write_text(json.dumps({'term':term,'hits':hits},ensure_ascii=False))
            tmp.replace(path)
    # Limit outstanding work so the discovery step stops promptly on a server rate limit.
    terms = iter(sorted(needed))
    with ThreadPoolExecutor(5) as workers:
        active = {workers.submit(search_term, term) for term in itertools.islice(terms, 5)}
        while active:
            ready, active = wait(active, return_when=FIRST_COMPLETED)
            for future in ready:
                future.result()
            active.update(workers.submit(search_term, term) for term in itertools.islice(terms, len(ready)))
    for path in (CACHE/'search').glob('*.json'):
        for hit in json.loads(path.read_text())['hits']:
            match = re.fullmatch(r'(geo|rail)/([0-9a-f]{32})',hit.get('query',''))
            if match:
                result.add(('poi' if match[1]=='geo' else 'feature',match[2]))
    path = CACHE/'extra-seeds.json'
    tmp = path.with_suffix('.part')
    tmp.write_text(json.dumps(sorted(result)))
    tmp.replace(path)
    return result


def main():
    global refresh_since
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discover',action='store_true',help='Search unlinked timetable stations and infrastructure names for more IDs')
    parser.add_argument('--refresh-since',metavar='YYYY-MM-DD',help='Re-fetch cached records older than this UTC date')
    args = parser.parse_args()
    if args.refresh_since:
        refresh_since = datetime.strptime(args.refresh_since,'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()
    for kind in ('poi','feature','search'):
        (CACHE / kind).mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(ROOT/'data/trains.sqlite') as db:
        feed = json.loads(db.execute('SELECT json FROM metadata').fetchone()[0])['feed_version']
    dataset = json.loads((ROOT/'map/coverage.json').read_text())['dataset']
    version = [dataset,feed]
    target_file = CACHE/'collection-target.json'
    previous = json.loads(target_file.read_text()) if target_file.exists() else {}
    threshold = previous.get('refreshSince',0)
    if previous and previous.get('version') != version:
        threshold = time.time()
    refresh_since = max(refresh_since,threshold)
    target = target_file.with_suffix('.part')
    target.write_text(json.dumps(dict(version=version,refreshSince=refresh_since)))
    target.replace(target_file)
    seeds = [('poi', k) for k in sorted(set(tile_ids())) if ID.fullmatch(k)]
    extra_map = ROOT/'map/rail-detail.geojson'
    if extra_map.exists():
        seeds.extend(references(json.loads(extra_map.read_text())))
    seeds += [('feature', r['id']) for r in json.loads((ROOT/'data/cnrail-lines.json').read_text()).values() if r.get('id')]
    if args.discover:
        from gtfs_coordinates import acquire
        acquire()
        seeds.extend(discover())
    saved_seeds = CACHE/'extra-seeds.json'
    if saved_seeds.exists():
        seeds.extend(tuple(row) for row in json.loads(saved_seeds.read_text()))
    pending = deque(dict.fromkeys(seeds))
    seen = set(pending)
    errors = {}
    with ThreadPoolExecutor(5) as pool:
        active = {}
        while pending or active:
            while pending and len(active)<5:
                key = pending.popleft()
                active[pool.submit(read_record,key)] = key
            ready,_ = wait(active,return_when=FIRST_COMPLETED)
            for future in ready:
                key = active.pop(future)
                try:
                    obj = future.result()
                except Exception as exc:
                    errors['/'.join(key)] = str(exc)
                    if isinstance(exc,HTTPError) and exc.code in (403,429,503):
                        raise
                    continue
                contents = obj.get('data', {}) if key[0]=='feature' else [
                    v['data'] for v in obj.get('exd',[]) if v.get('view')=='station']
                for ref in references(contents):
                    if ref not in seen:
                        seen.add(ref)
                        pending.append(ref)
    inventory = CACHE/'discovered-ids.json'
    tmp = inventory.with_suffix('.part')
    tmp.write_text(json.dumps(sorted(seen),separators=(',',':')))
    tmp.replace(inventory)
    report = dict(timetable=feed,refreshSince=refresh_since,finished=datetime.now(timezone.utc).isoformat(),seedCount=len(set(seeds)),
                  discovered=len(seen),status="completed",discovery=args.discover,
                  dataset=json.loads((ROOT/"map/coverage.json").read_text())["dataset"],errors=errors)
    output = CACHE/"collection-report.json"
    temporary = output.with_suffix(".part")
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    temporary.replace(output)


if __name__=='__main__':
    main()
