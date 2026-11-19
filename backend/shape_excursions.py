"""Repair demonstrable excursions inside one scheduled leg of a GTFS shape."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
import gzip
import json
import math
import re

from train_relations import metres, seconds


def project_segment(p, a, b):
    scale = math.cos(math.radians(p[1]))
    x, y = (b[0]-a[0])*scale, b[1]-a[1]
    f = max(0, min(1, (((p[0]-a[0])*scale)*x+(p[1]-a[1])*y)/(x*x+y*y or 1)))
    return f, (a[0]+f*(b[0]-a[0]), a[1]+f*(b[1]-a[1]))


def station_hits(p, path, distances, radius):
    for i, (a, b) in enumerate(zip(path, path[1:])):
        if not min(a[1],b[1])-.012 <= p[1] <= max(a[1],b[1])+.012:
            continue
        f, q = project_segment(p,a,b)
        gap = metres(p,q)
        if gap <= radius:
            yield gap, distances[i]+f*(distances[i+1]-distances[i]), i, q


def cumulative(path):
    result = [0.]
    for a,b in zip(path,path[1:]):
        result.append(result[-1]+metres(a,b))
    return result


def early_station_pass(path, distances, station):
    """Mileage of an approach within 250 m followed by a substantial departure."""
    for _, along, segment, _ in station_hits(station,path,distances,250):
        if distances[-1]-along < 10000:
            break
        if any(metres(p,station)>2000 for p in path[segment+1:]):
            return along
    return None


def simplify_leg(path, scheduled_seconds, limit, mileages):
    distances = cumulative(path)
    length = distances[-1]
    impossible = scheduled_seconds > 0 and length*3.6 > limit*scheduled_seconds
    def fits(length):
        return any(abs(length-m) <= max(1200, .08*m) for m in mileages)
    if not impossible and (not mileages or fits(length)):
        return
    # A station can occur on two parallel approaches to its platforms. Prefer
    # the final exit and first arrival only if the retained track fits the evidence.
    start, end = path[0], path[-1]
    exits = [h for h in station_hits(start,path,distances,250) if h[1]>3000]
    arrivals = [h for h in station_hits(end,path,distances,250) if length-h[1]>3000]
    lo = min(exits,key=lambda h:(round(h[0]/25),-h[1])) if exits else (0,0,0,start)
    hi = min(arrivals,key=lambda h:(round(h[0]/25),h[1])) if arrivals else (0,length,len(path)-2,end)
    if lo[1]>=hi[1]:
        # A → B → A → B: retain the first traversal when both ends repeat.
        lo = (0,0,0,start)
    retained = [start,lo[3],*path[lo[2]+1:hi[2]+1],hi[3],end]
    # Erase a closed excursion at the exact same railway vertex. Every retained
    # interior edge already exists in the source. Calls bound each leg separately.
    out, seen = [], {}
    for p in retained:
        if p in seen:
            cut = seen[p]+1
            for old in out[cut:]:
                del seen[old]
            del out[cut:]
        else:
            seen[p] = len(out)
            out.append(p)
    new = cumulative(out)[-1]
    if len(out)<2 or length-new < max(3000,.12*length):
        return
    if mileages and not fits(new):
        return
    if not mileages and (not impossible or new*3.6>scheduled_seconds*limit):
        return
    return out


def repair_shapes(db):
    axes = defaultdict(dict)
    for line,code,km in db.execute('SELECT line,code,mileage FROM line_stations WHERE code IS NOT NULL'):
        m = re.fullmatch(r'([\d,.]+)\s*km',km or '')
        if m:
            axes[code][line] = float(m[1].replace(',',''))*1000
    visits = defaultdict(list)
    for shape,trip,number,code,x,y,arr,dep in db.execute("""
        SELECT shape,trips.id,number,station,lon,lat,arrival,departure FROM trips
        JOIN times ON trip=trips.id JOIN stops ON station=stops.id
        WHERE shape<>'' ORDER BY trip,seq"""):
        visits[shape,trip].append((code,(x,y),seconds(arr),seconds(dep)))
    by_shape = defaultdict(list)
    for shape,trip,number in db.execute("SELECT shape,id,number FROM trips WHERE shape<>''"):
        by_shape[shape].append((trip,number))
    repaired = 0
    for shape,blob in db.execute('SELECT id,path FROM shapes').fetchall():
        path = list(map(tuple,json.loads(gzip.decompress(blob))))
        if len(set(path))==len(path):
            continue
        distances = cumulative(path)
        for trip,number in by_shape[shape]:
            calls = visits[shape,trip]
            anchors = []
            after = -1
            for _,xy,_,_ in calls:
                hit = min((h for h in station_hits(xy,path,distances,1200) if h[1]>after),default=None) if None not in xy else None
                anchors.append(hit)
                if hit: after = hit[1]
            pieces = []
            cursor = 0
            for i,(a,b) in enumerate(zip(anchors,anchors[1:])):
                if not a or not b: continue
                code,_,_,dep = calls[i]
                dest,_,arr,_ = calls[i+1]
                km = [abs(axes[code][k]-axes[dest][k]) for k in axes[code].keys() & axes[dest].keys()]
                leg = [a[3],*path[bisect_right(distances,a[1]):bisect_left(distances,b[1])],b[3]]
                fixed = simplify_leg(leg,arr-dep,380 if number.startswith(('G','D','C')) else 220,km)
                if fixed:
                    pieces.extend(path[cursor:bisect_left(distances,a[1])])
                    pieces.extend(fixed)
                    cursor = bisect_right(distances,b[1])
                    repaired += 1
            if not pieces: continue
            pieces.extend(path[cursor:])
            coords = [p for j,p in enumerate(pieces) if not j or p!=pieces[j-1]]
            packed = gzip.compress(json.dumps(coords,separators=(',',':')).encode(),compresslevel=3,mtime=0)
            if len(by_shape[shape])==1:
                db.execute('UPDATE shapes SET path=? WHERE id=?',(packed,shape))
            else:
                key = f'{shape}:corrected:{trip}'
                db.execute('INSERT INTO shapes VALUES(?,?)',(key,packed))
                db.execute('UPDATE trips SET shape=? WHERE id=?',(key,trip))
    return repaired
