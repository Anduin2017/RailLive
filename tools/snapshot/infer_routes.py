"""Recover missing timetable legs from same-direction, original GTFS station visits."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from functools import lru_cache
import gzip
import json
import math

from corridor_paths import CorridorNetwork
from departure_paths import CONVENTIONAL_LINES, conventional_trips
from train_relations import metres, point, seconds, unpack


def infer_routes(src, out):
    calls = defaultdict(list)
    for trip, station, arrival, departure in src.execute('SELECT trip,station,arrival,departure FROM times ORDER BY trip,seq'):
        calls[trip].append((station, seconds(arrival), seconds(departure)))
    trips = list(src.execute('SELECT id,number,shape FROM trips ORDER BY id'))
    missing = [(trip, number) for trip, number, shape in trips if not shape]
    wanted = {(a[0], b[0]) for trip, _ in missing for a,b in zip(calls[trip], calls[trip][1:])}
    candidates = defaultdict(dict)
    for trip, number, shape in trips:
        if not shape:
            continue
        row = out.execute('SELECT anchors FROM profiles WHERE trip=?',(trip,)).fetchone()
        if not row:
            continue
        visits = list(zip(calls[trip], unpack(row[0])))
        for i, ((station, _, _), start) in enumerate(visits):
            if start is None:
                continue
            for (dest, _, _), end in visits[i+1:]:
                if (station,dest) in wanted and end is not None and end>start:
                    candidates[station,dest].setdefault((shape,start,end),trip)

    @lru_cache(maxsize=256)
    def path(shape):
        coords = json.loads(gzip.decompress(src.execute('SELECT path FROM shapes WHERE id=?',(shape,)).fetchone()[0]))
        dist = [0.]
        for a,b in zip(coords, coords[1:]):
            dist.append(dist[-1]+metres(a,b))
        return dist, coords

    @lru_cache(maxsize=None)
    def corridors():
        return CorridorNetwork()

    @lru_cache(maxsize=None)
    def segment(station,dest,line):
        routes = sorted(candidates[station,dest])
        if line:
            eligible=[]
            for route in routes:
                shape,start,end=route
                dist,coords=path(shape)
                section=[point((dist,coords),start),
                         *coords[bisect_right(dist,start):bisect_left(dist,end)],
                         point((dist,coords),end)]
                if not corridors().follows_parallel_high_speed(section,line):
                    eligible.append(route)
            routes=eligible
        if not routes:
            return None
        shape,start,end = routes[0]
        length = end-start
        # Compare whole corridors, not just their shared endpoints. Disagreement
        # (including genuine parallel alternatives) leaves this leg unresolved.
        steps = max(2, math.ceil(length/2000))
        for other,a,b in routes[1:]:
            if abs(b-a-length)>max(300,0.015*length):
                return None
            ref, alt = path(shape), path(other)
            if any(metres(point(ref,start+length*i/steps),point(alt,a+(b-a)*i/steps))>650
                   for i in range(steps+1)):
                return None
        dist, coords = path(shape)
        part = [point((dist,coords),start), *coords[bisect_right(dist,start):bisect_left(dist,end)], point((dist,coords),end)]
        return part, length, candidates[station,dest][routes[0]]

    ordinary = conventional_trips(src)
    for trip, number in missing:
        coords, legs = [], []
        for (station,_,dep),(dest,arr,_) in zip(calls[trip],calls[trip][1:]):
            line = CONVENTIONAL_LINES.get(frozenset((station,dest))) if trip in ordinary else None
            result = segment(station,dest,line)
            if not result or arr<=dep or result[1]*3.6/(arr-dep)>(350 if number[:1] in 'GDC' else 200):
                legs.append(None)
                continue
            part, _, donor = result
            if legs and legs[-1]:
                if metres(coords[-1],part[0])>650:
                    legs.append(None)
                    continue
                start=len(coords)-1
                coords.extend(part[1:])
            else:
                start=len(coords)
                coords.extend(part)
            legs.append([start,len(coords)-1,donor])
        if coords:
            yield trip, number, coords, legs
