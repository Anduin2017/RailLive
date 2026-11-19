"""Build the offline railway graph and verify candidate map labels against station geometry."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import gzip
import json
import math
import sqlite3

from gtfs_coordinates import classify
from collect_cnrail import CACHE, references, tile_ids
from railway_graph import ROOT, SNAPSHOT, RailwayGraph, station_views
from index_rail_map import ZOOM, named_features, tile_coordinate, distance_to_tiles


def map_crosswalk(graph):
    paths = defaultdict(lambda: defaultdict(list))
    for tile in (ROOT/'map/tiles'/str(ZOOM)).glob('*/*.pbf'):
        cell = int(tile.parent.name), int(tile.stem)
        for kind,name,parts in named_features(tile):
            if kind == b'rail':
                paths[name][cell].extend(parts)
    detail = json.loads((ROOT/'map/rail-detail.geojson').read_text())
    for f in detail['features']:
        if f['geometry']['type'] == 'LineString':
            part = [tile_coordinate(*p) for p in f['geometry']['coordinates']]
            for cell in {(math.floor(x),math.floor(y)) for x,y in part}:
                paths[f['properties']['rail']][cell].append(part)
    bounds_index = json.loads((ROOT/'map/rail-selection.json').read_text())
    associations = {}
    candidates = {}
    for name,tiles in paths.items():
        ids = graph.map_terms.get(name.removesuffix('线'),set())
        if ids:
            candidates[name] = sorted(ids)
        if len(ids) != 1:
            continue
        eid = next(iter(ids))
        if eid not in graph.features:
            continue
        kids = list(dict.fromkeys(p.get('kid') for p in graph.features[eid].get('stations',[])))
        hits = []
        coords = []
        for kid in kids:
            point = graph.position(kid)
            if point:
                coords.append(point)
                km_per_tile = 40075*math.cos(math.radians(point[1]))/(2**ZOOM)
                if distance_to_tiles(tile_coordinate(*point),tiles)*km_per_tile**2 <= 0.8**2:
                    hits.append(kid)
        if len(hits)<2 or len(hits)*2<len(coords):
            continue
        # Also check the reverse direction: a short section can have all its
        # stations on a national corridor whose remaining track is unrelated.
        bounds = bounds_index.get(name,{}).get('bounds')
        if not bounds:
            continue
        covered = True
        for axis in (0,1):
            low, high = min(p[axis] for p in coords), max(p[axis] for p in coords)
            margin = max(.06,(high-low)*.08)
            if bounds[0][axis]<low-margin or bounds[1][axis]>high+margin:
                covered = False
        if covered:
            associations[name] = dict(eid=eid, matched=hits, located=len(coords), total=len(kids))
    return associations,candidates


def main():
    document = {}
    inventory = CACHE/'discovered-ids.json'
    expected = {tuple(k) for k in json.loads(inventory.read_text())} if inventory.exists() else None
    report_file = CACHE/'collection-report.json'
    report = json.loads(report_file.read_text()) if report_file.exists() else {'state':'collecting'}
    errors = report.get('errors',{})
    provenance = {}
    for kind in ('poi','feature'):
        document[kind] = {}
        for path in sorted((CACHE/kind).glob('*.json')):
            error = errors.get(kind+'/'+path.stem,'')
            if (expected is not None and (kind,path.stem) not in expected
                    or error.startswith(('HTTP Error 404:', 'HTTP Error 410:'))):
                continue
            content = path.read_bytes()
            document[kind][path.stem] = json.loads(content)
            provenance[kind+'/'+path.stem] = [int(path.stat().st_mtime), hashlib.sha256(content).hexdigest()]
    keep = set(tile_ids())
    for record in document['feature'].values():
        keep.update(k for kind,k in references(record.get('data',{})) if kind=='poi')
    for kid,poi in document['poi'].items():
        views = station_views(poi)
        if views:
            keep.add(kid)
            keep.update(k for kind,k in references(views) if kind=='poi')
    for kid in document['poi'].keys() - keep:
        del document['poi'][kid]
        del provenance['poi/'+kid]
    document['provenance'] = provenance
    with sqlite3.connect(ROOT/'data/trains.sqlite') as db:
        document["coordinateQuality"],document["coordinateReference"] = classify(db)
        graph = RailwayGraph(document,db,json.loads((ROOT/'data/line-facts.json').read_text()))
    document['mapLinks'],document['mapCandidates'] = map_crosswalk(graph)
    document['collection'] = report
    document['collection']['stale'] = [key for key in errors if key in provenance]
    if provenance:
        dates = [r[0] for r in provenance.values()]
        document['collection'].update(api= 'http://cnrail.geogv.org/api/', locale='zhcn',
            firstRetrieved=datetime.fromtimestamp(min(dates),timezone.utc).isoformat(),
            lastRetrieved=datetime.fromtimestamp(max(dates),timezone.utc).isoformat(),
            dataset=json.loads((ROOT/'map/coverage.json').read_text())['dataset'])
    temporary = SNAPSHOT.with_suffix('.building')
    with gzip.open(temporary,'wt',encoding='utf8') as out:
        json.dump(document,out,ensure_ascii=False,separators=(',',':'))
    temporary.replace(SNAPSHOT)
    print({k:len(document[k]) for k in ('poi','feature','mapLinks')})


if __name__=='__main__':
    main()
