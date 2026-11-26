"""Offline spatial and service-time index for the regional live map."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from contextlib import closing
from datetime import date
from functools import lru_cache
import sqlite3
from departure_paths import save_fallbacks
from train_relations import unpack

from index_rail_map import tile_coordinate
from station_statistics import map_point
from line_live import WINDOW, carriage_path, departure_anchors, indexed_profile, label_stops
from train_relations import DAY, TRACKS, TIMETABLE, shape_path, timetable, unpack

INDEX = TRACKS.with_name('region.sqlite')
ORIGIN_BOARD_SECONDS = 120
TERMINAL_BOARD_SECONDS = 240


def slice_window(curve, times, distances, coordinates, start):
    # Include the knots and geometry bracketing both boundaries so adjacent
    # requests evaluate exactly the same trajectory, even during long dwells.
    i = max(0, bisect_right(times, start)-1)
    j = bisect_left(times, start+WINDOW)+1
    samples = curve[i:j]
    valid = [at for _, at in samples if at is not None]
    if not valid:
        return None
    first = max(0, bisect_right(distances, min(valid))-1)
    last = min(len(distances), bisect_left(distances, max(valid))+2)
    return samples, first, last


def verify():
    with closing(sqlite3.connect(TRACKS)) as db, closing(sqlite3.connect(INDEX.as_uri()+'?mode=ro', uri=True)) as index:
        assert db.execute('SELECT signature FROM meta').fetchone() == index.execute('SELECT signature FROM meta').fetchone()
        unresolved=index.execute("SELECT trip,seq,kind FROM display_legs WHERE kind IN ('站间示意','站序示意') LIMIT 1").fetchone()
        if unresolved:
            raise ValueError(f'区域索引仍含停车站连线，请重建区间轨迹：{unresolved}')



def build():
    tmp = INDEX.with_suffix('.building')
    tmp.unlink(missing_ok=True)
    runs, calendar, _ = timetable()
    with closing(sqlite3.connect(TRACKS)) as db, closing(sqlite3.connect(tmp)) as out:
        out.execute('CREATE TABLE windows(bucket INTEGER, trip TEXT, offset INTEGER, west REAL, south REAL, east REAL, north REAL)')
        for trip, blob in db.execute('SELECT trip,knots FROM profiles'):
            _, _, shape, stops = runs[trip]
            distances, coords = shape_path(shape)
            curve = unpack(blob)
            times = [k[0] for k in curve]
            rows = []
            for t in range(stops[0][2]//WINDOW*WINDOW, stops[-1][1], WINDOW):
                part = slice_window(curve,times,distances,coords,t)
                if part:
                    _, a, b = part
                    xs, ys = zip(*coords[a:b])
                    rows.append((t%DAY,trip,t//DAY,min(xs),min(ys),max(xs),max(ys)))
            out.executemany('INSERT INTO windows VALUES(?,?,?,?,?,?,?)', rows)
        out.execute('CREATE INDEX by_window ON windows(bucket,west)')
        save_fallbacks(out,runs,station_locations(),calendar)
        save_trip_metrics(out,db,runs)
        out.execute('CREATE TABLE meta(signature TEXT)')
        out.execute('INSERT INTO meta VALUES(?)',db.execute('SELECT signature FROM meta').fetchone())
        out.commit()
    tmp.replace(INDEX)


def save_trip_metrics(out, tracks, runs):
    """Summarize the same legs used by the selected train, including display overrides."""
    metrics = {(trip, seq): (distance, peak) for trip, seq, distance, peak in
               tracks.execute('SELECT trip,seq,metres,peak_kmh FROM leg_metrics')}
    for trip, seq, start, end, blob in out.execute('SELECT trip,seq,start,end,path FROM display_legs'):
        metres = unpack(blob)[-1][0]
        duration = end - start
        # displayMotion accelerates and brakes over two equal linear ramps.
        ramp = min(60, duration / 4)
        metrics[trip, seq] = (metres, 3.6 * metres / (duration - ramp))
    out.execute('CREATE TABLE trip_metrics(trip TEXT PRIMARY KEY, distance_km REAL, peak_kmh REAL, peak_seq INTEGER)')
    for trip, (_, _, _, stops) in runs.items():
        legs = [metrics.get((trip, seq)) for seq in range(len(stops)-1)]
        if legs and all(legs):
            fastest = max(range(len(legs)), key=lambda seq: legs[seq][1])
            out.execute('INSERT INTO trip_metrics VALUES(?,?,?,?)',
                        (trip, sum(distance for distance, _ in legs) / 1000, legs[fastest][1], fastest))


@lru_cache(maxsize=1)
def station_locations():
    from server import GRAPH
    with closing(sqlite3.connect(TIMETABLE)) as db:
        locations = {code: (GRAPH.position(GRAPH.kid_for.get(code)),
                       [lon,lat] if lon is not None and lat is not None
                       and GRAPH.coordinate_quality.get(code) in (None,'reference') else None)
                for code,lon,lat in db.execute('SELECT id,lon,lat FROM stops')}
        # Resolve an unmapped timetable code only when its station name has one
        # mapped railway station and the feed coordinate corroborates the area.
        from train_relations import metres
        from departure_paths import geographic
        for code,name,lon,lat in db.execute('SELECT id,name,lon,lat FROM stops'):
            if any(locations[code]): continue
            candidates=[p['mapPosition'] for p in GRAPH.searchable if p['name']==name and p.get('mapPosition')]
            if len(candidates)==1 and (lon is None or metres(geographic(candidates[0]),[lon,lat])<30000):
                locations[code]=(candidates[0],None)
        return locations


def board_windows(stops):
    """Visibility intervals for scheduled station badges, in service-day seconds."""
    for seq, (code, arrival, departure) in enumerate(stops):
        if seq == len(stops)-1:
            yield seq, code, arrival, arrival + TERMINAL_BOARD_SECONDS, 'terminal'
        elif seq == 0:
            yield seq, code, departure - ORIGIN_BOARD_SECONDS, departure, 'origin'
        elif arrival < departure:
            yield seq, code, arrival, departure, 'stop'


@lru_cache(maxsize=1)
def dwell_index():
    runs, _, _ = timetable()
    buckets = defaultdict(list)
    locations = station_locations()
    for trip, (_, _, _, stops) in runs.items():
        for _, code, visible_from, visible_until, kind in board_windows(stops):
            position, gtfs = locations[code]
            if not (position or gtfs):
                continue
            x,y = tile_coordinate(*position) if position else map_point(*gtfs)
            for bucket in range(visible_from//WINDOW*WINDOW, visible_until, WINDOW):
                buckets[bucket%DAY].append((trip,code,visible_from,visible_until,bucket//DAY,x,y,kind))
    return buckets


@lru_cache(maxsize=12)
def query_window(day, start, west, south, east, north):
    locations=station_locations()
    runs, calendar, names = timetable()
    result = []
    with closing(sqlite3.connect(INDEX.as_uri()+'?mode=ro',uri=True)) as db:
        rows = db.execute("""SELECT trip, offset FROM windows WHERE bucket=?
            AND west<=? AND east>=? AND south<=? AND north>=? ORDER BY trip,offset""",
            (start,east+.04,west-.04,north+.025,south-.025))
        for trip, offset in rows:
            number, service, shape, stops = runs[trip]
            service_day = day-offset
            if service_day not in calendar[service]:
                continue
            t = start+offset*DAY
            curve, times, anchors = indexed_profile(trip)
            distances, coords = shape_path(shape)
            samples, a, b = slice_window(curve,times,distances,coords,t)
            anchors_in_window=departure_anchors(stops, anchors, t, t+WINDOW)
            places={dep:locations[s] for s,_,dep in stops}
            for anchor in anchors_in_window:
                anchor.extend(places[anchor[0]])
            result.append(dict(id=trip, number=number, day=date.fromordinal(service_day).isoformat(),
                start=stops[0][2], end=stops[-1][1], curve=samples, **label_stops(stops,names,t,t+WINDOW),
                departures=anchors_in_window,
                path=carriage_path(distances, coords, a, b, 90_000),
                stops=[[names[s],arr,dep,s,*locations[s]] for s,arr,dep in stops[1:-1] if arr<t+WINDOW and dep>t and dep>arr]))
        display=[]
        for trip,seq,offset,dep,arr,kind,blob in db.execute("""
            SELECT w.trip,w.seq,w.offset,l.start,l.end,l.kind,l.path FROM display_windows w
            JOIN display_legs l USING(trip,seq) WHERE bucket=? AND west<=? AND east>=? AND south<=? AND north>=?
            """,(start,east,west,north,south)):
            number,service,_,stops=runs[trip]
            if day-offset in calendar[service]:
                path=unpack(blob)
                distances=[p[0] for p in path]
                length=distances[-1]
                t=start+offset*DAY
                ramp=min(60,(arr-dep)/4)
                lo=max(0,(t-dep-ramp)/(arr-dep))*length
                hi=min(1,(t+WINDOW-dep+ramp)/(arr-dep))*length
                first=max(0,bisect_right(distances,lo)-1)
                last=min(len(path),bisect_left(distances,hi)+2)
                first=min(first,last-2)
                display.append(dict(id=trip,number=number,day=date.fromordinal(day-offset).isoformat(),
                    start=dep,end=arr,kind=kind,length=length,path=path[max(0,bisect_left(distances,distances[first]-90000)-1):last],next=names[stops[seq+1][0]],destination=names[stops[-1][0]]))
    # Timetabled station calls also exist for trips without a usable motion profile.
    modeled = {(r['id'],r['day']) for r in result}
    calls = []
    corners = [map_point(x,y) for x in (west,east) for y in (south,north)]
    xs,ys = zip(*corners)
    left,right,top,bottom = min(xs),max(xs),min(ys),max(ys)
    for trip,code,visible_from,visible_until,offset,x,y,kind in dwell_index()[start]:
        if not (left<=x<=right and top<=y<=bottom):
            continue
        number,service,_,_ = runs[trip]
        if day-offset not in calendar[service]:
            continue
        service_date = date.fromordinal(day-offset).isoformat()
        if kind != 'stop' or (trip,service_date) not in modeled:
            calls.append(dict(id=trip,number=number,day=service_date,
                              origin=kind=='origin', terminal=kind=='terminal',
                              stop=[names[code],visible_from,visible_until,code,*locations[code]]))
    response = dict(runs=result,covered=bool(result or calls) or any(day in dates for dates in calendar.values()))
    if display: response['display']=display
    if calls:
        response['stationCalls'] = calls
    return response


@lru_cache(maxsize=64)
def trip_display(trip):
    with closing(sqlite3.connect(INDEX.as_uri()+'?mode=ro',uri=True)) as db:
        return [dict(seq=i,start=a,end=b,kind=k,path=unpack(p)) for i,a,b,k,p in db.execute(
            'SELECT seq,start,end,kind,path FROM display_legs WHERE trip=? ORDER BY seq',(trip,))]


def region_window(params):
    if 'trip' in params:
        return trip_display(params['trip'][0])
    try:
        day = date.fromisoformat(params['day'][0]).toordinal()
        start = int(params['start'][0])
        west,south,east,north = map(float,params['box'][0].split(','))
    except (KeyError,ValueError,TypeError,IndexError):
        return None
    if not (0<=start<DAY and start%WINDOW==0 and -180<=west<east<=180
            and -90<=south<north<=90 and east-west<=45 and north-south<=32):
        return None
    return query_window(day,start,west,south,east,north)


if __name__ == '__main__':
    build()
