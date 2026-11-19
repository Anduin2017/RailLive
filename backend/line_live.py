"""Five-minute windows of dated movements on the Beijing–Shanghai high-speed line."""
from bisect import bisect_left, bisect_right
from contextlib import closing
from datetime import date
from functools import lru_cache
import sqlite3
import math

from train_relations import DAY, TRACKS, TIMETABLE, profile, shape_path, shared_routes, timetable, thin

WINDOW = 300
CORRIDORS = {
    'jinghu': ('京沪高速线', '北京南', '上海虹桥', 'G7', 'G8'),
    'xicheng': ('西成客专线', '西安北', '成都东', 'G3349', 'G3350'),
    'lanxin': ('兰新客专线', '兰州西', '乌鲁木齐', 'D575', 'D2706'),
    'hajia': ('哈佳线', '哈尔滨', '佳木斯', 'D7833', 'D7814'),
    'huningyanjiang': ('沪宁沿江高速线', '南京南', '太仓', 'G8973', 'G8978'),
    'hangshen': ('杭深线', '杭州东', '深圳北', 'D3307', 'D3308'),
    'jingha': ('京哈线', '北京', '哈尔滨', 'Z203', 'Z16'),
    'ningan': ('宁安城际线', '南京南', '安庆', 'D5601', 'D5604'),
    'jingjin': ('京津城际线', '北京南', '滨海', 'C2559', 'C2302'),
    'guangshengang': ('广深港高速线', '广州南', '香港西九龙', 'G6505', 'G6504'),
}


def live_descriptor(name):
    # Published infrastructure title for the GTFS line-table alias.
    if name == '兰新客运专线':
        name = '兰新客专线'
    for key, (line, origin, destination, *_) in CORRIDORS.items():
        if line == name:
            return dict(key=key, origin=origin, destination=destination)
    return None


def validate_reference(shape, stations, line):
    """Require each known passenger station in order, near its published chainage."""
    distances, vertices = shape_path(shape)
    previous = -1
    first_km = stations[0][2]
    for lon, lat, km in stations:
        scale = math.cos(math.radians(lat))
        best = (math.inf, 0)
        for i, ((x,y),(u,v)) in enumerate(zip(vertices, vertices[1:])):
            dx, dy = (u-x)*scale, v-y
            ax, ay = (lon-x)*scale, lat-y
            fraction = max(0, min(1, (dx*ax+dy*ay)/(dx*dx+dy*dy or 1)))
            gap = 111195 * math.hypot(ax-fraction*dx, ay-fraction*dy)
            along = distances[i] + fraction*(distances[i+1]-distances[i])
            best = min(best, (gap, along))
        error, at = best
        if error > 1200 or at <= previous or abs(at - abs(km-first_km)*1000) > 12000:
            raise ValueError(f'{line}参考径路与站序或里程不一致')
        previous = at


@lru_cache(maxsize=len(CORRIDORS))
def corridor(key):
    line, first, last, forward, reverse = CORRIDORS[key]
    runs, _, names = timetable()
    with closing(sqlite3.connect(TIMETABLE)) as db:
        stations = {s for s, in db.execute('SELECT name FROM line_stations WHERE line=?', (line,))}
        checkpoints = db.execute("""
            SELECT s.lon,s.lat,CAST(REPLACE(l.mileage,',','') AS INTEGER)
            FROM line_stations l JOIN stops s ON s.name=l.name
            WHERE l.line=? AND s.lon IS NOT NULL AND s.lat IS NOT NULL
            ORDER BY l.position""", (line,)).fetchall()
    if len(checkpoints) < 2:
        raise ValueError(f'{line}缺少校验站点')
    directions = []
    for number, origin, destination in [(forward,first,last),(reverse,last,first)]:
        candidates = sorted({(label != number, shape) for label, _, shape, calls in runs.values()
                             if shape and not shape.startswith('inferred:') and calls and names[calls[0][0]] == origin
                             and names[calls[-1][0]] == destination})
        tried = set()
        for _, shape in candidates:
            if shape in tried:
                continue
            tried.add(shape)
            try:
                validate_reference(shape, checkpoints if not directions else checkpoints[::-1],line)
            except ValueError:
                continue
            directions.append(shared_routes(shape))
            break
        else:
            raise ValueError(f'需核验{line}参考径路 {origin} → {destination}')
    return stations, directions


@lru_cache(maxsize=256)
def indexed_profile(trip):
    with closing(sqlite3.connect(TRACKS.as_uri()+'?mode=ro', uri=True)) as db:
        curve, anchors = profile(db, trip)
    return curve, [k[0] for k in curve], anchors


def label_stops(calls, names, start, end):
    # Include the first passenger call beyond the window, even on a long nonstop leg.
    arrivals = []
    for code, arrival, departure in calls[1:]:
        if start < arrival <= departure:
            arrivals.append([arrival, names[code]])
            if arrival >= end:
                break
    return dict(arrivals=arrivals, destination=names[calls[-1][0]])


def departure_anchors(calls, anchors, start, end):
    """Last departure before the window, plus each departure within it."""
    result = []
    for (_, _, departure), at in zip(calls, anchors):
        if departure >= end:
            break
        if at is None:
            continue
        if departure <= start:
            result.clear()
        result.append([departure, at])
    return result


def carriage_path(distances, coords, first, end, lookback):
    # Beyond the motion window only trailing carriages use this geometry.
    # Keep original vertices, thinning dense stretches increasingly far behind:
    # a skipped stretch spans under one screen pixel at the scale that needs it.
    start = max(0, bisect_right(distances, distances[first]-lookback)-1)
    previous = []
    i = first
    while i > start:
        gap = (distances[first]-distances[i])/128
        i = max(start, min(i-1, bisect_left(distances, distances[i]-gap, start, i)))
        previous.append(i)
    return [[distances[k], *coords[k]] for k in (*reversed(previous), *range(first, end))]


def movement(trip, shape, calls, paths, stations, names, lo, hi):
    curve, times, anchors = indexed_profile(trip)
    start = max(0, bisect_right(times, lo)-1)
    end = bisect_left(times, hi)+1
    samples = curve[start:end]
    positions = [s for _, s in samples if s is not None]
    if not positions:
        return None
    first, last = min(positions), max(positions)
    stops = [(at, names[s], a, d) for at, (s, a, d) in zip(anchors, calls)
             if at is not None and names[s] in stations]
    sections = []
    for direction, spans in enumerate(paths):
        for span in spans:
            a, b = span[0][1], span[-1][1]
            if last < a-1200 or first > b+1200:
                continue
            # A merging train's platform anchor may sit just outside a matched
            # junction. Extend only to its recorded call at a station of this line.
            a = min([a]+[s for s, *_ in stops if a-1200 <= s < a])
            b = max([b]+[s for s, *_ in stops if b < s <= b+1200])
            if first <= b and last >= a:
                sections.append([a, b, direction])
    if not sections:
        return None
    distances, coordinates = shape_path(shape)
    i = max(0, bisect_right(distances, first)-1)
    j = min(len(distances), bisect_left(distances, last)+2)
    return dict(curve=thin(samples), path=carriage_path(distances, coordinates, i, j, 1_000_000),
                departures=departure_anchors(calls, anchors, lo, hi), spans=sections, **label_stops(calls, names, lo, hi), stops=[[name, a, d] for _, name, a, d in stops if d>lo and a<hi and d>a])


@lru_cache(maxsize=36)
def traffic_window(day_text, start_text, key="jinghu"):
    if key not in CORRIDORS:
        return None
    try:
        day = date.fromisoformat(day_text)
        start = int(start_text)
    except (ValueError, TypeError):
        return None
    if start % WINDOW or not 0 <= start < DAY:
        return None
    runs, calendar, names = timetable()
    stations, directions = corridor(key)
    result = []
    ordinal = day.toordinal()
    for trip, (number, service, shape, calls) in runs.items():
        paths = [direction.get(shape, ()) for direction in directions]
        if not any(paths) or not calls:
            continue
        for offset in range((start-calls[-1][1])//DAY+1, (start+WINDOW-1-calls[0][2])//DAY+1):
            if ordinal+offset not in calendar[service]:
                continue
            lo = start-offset*DAY
            part = movement(trip, shape, calls, paths, stations, names, lo, lo+WINDOW)
            if part:
                result.append(dict(id=trip, number=number, day=date.fromordinal(ordinal+offset).isoformat(),
                                   start=calls[0][2], end=calls[-1][1], **part))
    return dict(runs=result, covered=any(ordinal in days for days in calendar.values()) or bool(result))
