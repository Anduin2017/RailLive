"""Scan the final moving leg for an early terminal pass, departure and return."""
import argparse
from bisect import bisect_left, bisect_right
from functools import lru_cache
import gzip
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
from train_relations import metres, point, unpack
from shape_excursions import cumulative, early_station_pass
from departure_paths import geographic
from region_live import station_locations


def scan(trains_path, relations_path, region_path, all_legs=False):
    def connect(path):
        return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)

    locations = station_locations()
    suspects = []
    scanned = 0
    with connect(trains_path) as trains, connect(relations_path) as relations, connect(region_path) as region:
        @lru_cache(maxsize=32)
        def axis(shape):
            row = trains.execute('SELECT path FROM shapes WHERE id=?', (shape,)).fetchone()
            coords = json.loads(gzip.decompress(row[0]))
            return cumulative(coords), coords

        for trip, number, shape in trains.execute('SELECT id,number,shape FROM trips ORDER BY shape'):
            # Array indices, unlike stop_sequence, always start at zero.
            stops = [r[0] for r in trains.execute('SELECT station FROM times WHERE trip=? ORDER BY seq',(trip,))]
            spans = range(len(stops)-1) if all_legs else [len(stops)-2]
            overrides = dict(region.execute('SELECT seq,path FROM display_legs WHERE trip=?',(trip,)))
            profile = relations.execute('SELECT anchors FROM profiles WHERE trip=?',(trip,)).fetchone()
            anchors = unpack(profile[0]) if profile else []
            geometry = None
            for seq in spans:
                if seq < 0:
                    continue
                code = stops[seq+1]
                mapped, ref = locations[code]
                station = geographic(mapped) if mapped else ref
                if not station:
                    continue
                if seq in overrides:
                    vertices = [p[1:] for p in unpack(overrides[seq])]
                else:
                    if not anchors:
                        continue
                    a,b = anchors[seq:seq+2]
                    if a is None or b is None or a >= b:
                        continue
                    if geometry is None:
                        inferred = relations.execute('SELECT path FROM inferred WHERE trip=?',(trip,)).fetchone()
                        if inferred:
                            coords = unpack(inferred[0])
                            geometry = cumulative(coords), coords
                        else:
                            geometry = axis(shape)
                    ds,coords = geometry
                    vertices = [point(geometry,a), *coords[bisect_right(ds,a):bisect_left(ds,b)], point(geometry,b)]
                scanned += 1
                if metres(vertices[-1],station) > 500:
                    continue
                distances = cumulative(vertices)
                visit = early_station_pass(vertices, distances, station)
                if visit is not None:
                    suspects.append((number,code,round((distances[-1]-visit)/1000,1)))
    return dict(scanned=scanned, candidates=len(suspects), trips=suspects)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('trains','relations','region'):
        parser.add_argument('--'+key, default=ROOT/'data'/(key+'.sqlite'))
    parser.add_argument('--all-legs', action='store_true', help='also inspect arrivals at intermediate calls')
    args = parser.parse_args()
    print(json.dumps(scan(args.trains,args.relations,args.region,args.all_legs),ensure_ascii=False))
