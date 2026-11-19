"""Offline ID-based view of the Chinese Railway Map and its timetable crosswalk."""
from collections import defaultdict
import hashlib
import gzip
import json
import math
from pathlib import Path

from index_rail_map import station_key, tile_coordinate, ZOOM
from station_statistics import map_point

ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / 'data/cnrail-graph.json.gz'


def station_views(poi):
    return [item['data'] for item in poi.get('exd', ())
            if item.get('view') == 'station' and isinstance(item.get('data'), dict)]


def sequence_key(names):
    names = tuple(station_key(name) for name in names)
    return min(names, names[::-1])


class RailwayGraph:
    def __init__(self, document, db, facts):
        self.raw_pois = document['poi']
        self.metro_stations = {kid for kid, poi in self.raw_pois.items() if poi.get('featureType') == '地铁站'}
        self.metro_lines = set()
        self.metro_names = set()
        for eid, record in document['feature'].items():
            if record.get('view') != 'line':
                continue
            data = record['data']
            name = data['name']
            points = data.get('stations') or []
            if ('地铁' in name or '轨道交通' in name or
                    points and all(point.get('kid') in self.metro_stations for point in points)):
                self.metro_lines.add(eid)
                self.metro_names.add(name)
        self.map_links = {name: link for name, link in document.get('mapLinks', {}).items()
                          if link['eid'] not in self.metro_lines}
        self.map_candidates = document.get('mapCandidates',{})
        self.maps_for = defaultdict(list)
        for name,evidence in self.map_links.items():
            self.maps_for[evidence['eid']].append(name)
        self.coordinate_quality = document.get('coordinateQuality',{})
        self.pois = {kid: poi for kid, poi in self.raw_pois.items() if kid not in self.metro_stations}
        self.feature_records = document['feature']
        self.collection = document.get('collection',{})
        self.stale = set(self.collection.get('stale',[]))
        self.features = {eid: record['data'] for eid, record in document['feature'].items()
                         if eid not in self.metro_lines and record.get('view') == 'line' and isinstance(record.get('data'), dict)}
        self.views = {kid: station_views(poi) for kid, poi in self.pois.items()}
        stops = dict(db.execute('SELECT id,name FROM stops'))
        legacy = dict(db.execute('SELECT alias,station FROM station_aliases'))
        codes = defaultdict(set)
        declared_codes = set()
        kid_codes = defaultdict(set)
        for kid, views in self.views.items():
            for view in views:
                declared = view.get('tele_code')
                code = legacy.get(declared,declared)
                if code:
                    declared_codes.add(code)
                if code in stops and stops[code] == view.get('name'):
                    codes[code].add(kid)
                    kid_codes[kid].add(code)
        # Require a unique exact match in both directions. A different station
        # claiming the same code alone is not a competing code-and-name match.
        self.kid_for = {}
        for code, kids in codes.items():
            if len(kids) == 1:
                kid, = kids
                if len(kid_codes[kid]) == 1:
                    self.kid_for[code] = kid
        self.stop_for = {kid: code for code, kid in self.kid_for.items()}
        self.station_match = {kid:'telegraph' for kid in self.stop_for}

        # Some source junctions have no telegraph code. Require a unique name
        # on both sides plus independent agreement after map-grid calibration.
        source_names = defaultdict(set)
        for kid, views in self.views.items():
            for view in views:
                source_names[view.get('name')].add(kid)
        timetable_names = defaultdict(list)
        for code, name, lon, lat in db.execute('SELECT id,name,lon,lat FROM stops'):
            timetable_names[name].append((code,lon,lat))
        for name, kids in source_names.items():
            matches = timetable_names.get(name,[])
            if len(kids) != 1 or len(matches) != 1:
                continue
            kid = next(iter(kids))
            code, lon, lat = matches[0]
            position = self.position(kid)
            if (self.coordinate_quality.get(code) != "reference" or not position or lon is None or lat is None or kid in self.stop_for
                    or code in declared_codes or any(v.get('tele_code') for v in self.views[kid])):
                continue
            km_per_tile = 40075 * math.cos(math.radians(position[1])) / 2**ZOOM
            if math.dist(tile_coordinate(*position), map_point(lon,lat)) * km_per_tile <= 0.2:
                self.stop_for[kid] = code
                self.kid_for[code] = kid
                self.station_match[kid] = 'unique-name-and-position'
        self.position_differences = {}
        for code,lon,lat in db.execute('SELECT id,lon,lat FROM stops'):
            kid = self.kid_for.get(code)
            pos = self.position(kid)
            if pos and lon is not None and lat is not None:
                km = math.dist(tile_coordinate(*pos), map_point(lon,lat))*40075*math.cos(math.radians(pos[1]))/2**ZOOM
                if km>3:
                    self.position_differences[kid] = round(km,1)
        self.searchable = []
        for kid, views in self.views.items():
            if kid in self.stop_for or not views:
                continue
            name = next((v['name'] for v in views if v.get('name')), self.pois[kid].get('name'))
            if name:
                self.searchable.append(dict(id=kid,name=name,mapPosition=self.position(kid),
                    location=self.pois[kid].get("location"),sourceOnly=True))
        self.line_names = defaultdict(set)
        self.gtfs_names = defaultdict(set)
        self.title = {}
        self.eid_for = {}
        gtfs_points = defaultdict(list)
        for name, point in db.execute('SELECT line,name FROM line_stations ORDER BY line,position'):
            gtfs_points[name].append(point)
        self.positions_for = defaultdict(list)
        sequences = defaultdict(set)
        official = defaultdict(set)
        for eid, data in self.features.items():
            name = data['name']
            self.line_names[eid].add(name)
            official[name].add(eid)
            for index, point in enumerate(data.get('stations') or []):
                if point.get('kid'):
                    self.positions_for[point['kid']].append((eid,index))
            points = [p['name'] for p in data.get('stations',[]) if p.get('name')]
            if len(set(points)) >= 3:
                sequences[sequence_key(points)].add(eid)
        self.connected_kids = defaultdict(set)
        for kid,views in self.views.items():
            for view in views:
                for edge in view.get('connection') or []:
                    if edge.get('eid') in self.metro_lines:
                        continue
                    if edge.get('eid'):
                        self.connected_kids[edge['eid']].add(kid)
                    if edge.get('eid') and edge.get('linename'):
                        self.line_names[edge['eid']].add(edge['linename'])
        reviewed = {title:info['sourceId'] for title,info in facts.items() if info.get('sourceId')}
        reviewed_aliases = {alias:eid for title,eid in reviewed.items()
                            for alias in [title,*facts[title].get('aliases',[])]}
        gtfs_sequences = defaultdict(set)
        for name,pts in gtfs_points.items():
            gtfs_sequences[sequence_key(pts)].add(name)
        self.sequence_candidates = {}
        for name, pts in gtfs_points.items():
            matches = sequences.get(sequence_key(pts), set()) if len(set(pts))>=3 else set()
            if matches:
                self.sequence_candidates[name] = sorted(matches)
            if (len(matches)==1 and len(gtfs_sequences[sequence_key(pts)])==1
                    and (name not in official or matches==official[name])):
                candidate = next(iter(matches))
                terms = {n.removesuffix('线') for n in self.line_names[candidate]}
                if name.removesuffix('线') not in terms and reviewed_aliases.get(name)!=candidate:
                    continue
                eid = next(iter(matches))
                self.gtfs_names[eid].add(name)
                self.line_names[eid].add(name)
        # Reviewed cross-source aliases retain the map's label and link to the original ID.
        for title,eid in reviewed.items():
            if eid in self.features:
                self.title[eid] = title
                self.line_names[eid].update([title,*facts.get(title,{}).get('aliases',[])])
        for eid,data in self.features.items():
            self.title.setdefault(eid,data['name'])
        lookup = defaultdict(set)
        for eid,names in self.line_names.items():
            for name in names:
                lookup[name].add(eid)
        self.candidates = lookup
        self.map_terms = defaultdict(set)
        for name,ids in lookup.items():
            self.map_terms[name.removesuffix('线')].update(ids)
        self.eid_for = {name:next(iter(ids)) for name,ids in lookup.items()
                        if len(ids)==1 and ids.issubset(self.features)}
        for name,ids in official.items():
            if len(ids)==1:
                self.eid_for[name]=next(iter(ids))
        for name, evidence in self.map_links.items():
            eid = evidence['eid']
            if name not in self.eid_for and eid in self.features:
                self.eid_for[name] = eid
                self.line_names[eid].add(name)

    def position(self,kid):
        geom = self.pois.get(kid,{}).get('geom') or {}
        return geom.get('coordinates') if geom.get('type')=='Point' else None

    def station_link(self,kid):
        return self.stop_for.get(kid,kid) if kid in self.pois else None

    def sequence_edges(self,kid):
        edges = []
        for eid,index in self.positions_for.get(kid,()):
            points = self.features[eid]['stations']
            edge = dict(line=self.title[eid],eid=eid,source='line-sequence',directions={})
            for side,offset in [('previous',-1),('next',1)]:
                at = index + offset
                point = points[at] if 0<=at<len(points) else {}
                edge[side] = point.get('name')
                edge[side+'Id'] = self.station_link(point.get('kid'))
                edge['directions'][side] = [dict(name=point.get('name'),kid=point.get('kid'),
                    id=edge[side+'Id'],destination=None,status=None if point else 'END')]
            edges.append(edge)
        return edges

    def edges(self,kid):
        edges = []
        for view in self.views.get(kid,()):
            for connection in view.get('connection') or []:
                eid = connection.get('eid')
                if eid in self.metro_lines:
                    continue
                row = dict(line=self.title.get(eid) or connection.get('linename'), eid=eid,
                           shortName=connection.get('linename'), source='station-card',
                           color=connection.get('linecolor'), directions={})
                for side,key in [('previous','prev'),('next','next')]:
                    entries = connection.get(key) or []
                    directions = []
                    for item in entries:
                        point, dest = item.get('adj') or {}, item.get('dest') or {}
                        directions.append(dict(name=point.get('name'), id=self.station_link(point.get('kid')),
                                               kid=point.get('kid'), destinationKid=dest.get('kid'),
                                               destination=dest.get('name'), status=dest.get('status')))
                    row['directions'][side] = directions
                    first = next((d for d in directions if d['name']), {})
                    row[side],row[side+'Id'] = first.get('name'),first.get('id')
                edges.append(row)
        return edges


def load_graph(db, facts):
    with gzip.open(SNAPSHOT,'rt',encoding='utf8') as source:
        return RailwayGraph(json.load(source),db,facts)


def snapshot_hash(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def audited_files():
    return [SNAPSHOT,ROOT/'data/trains.sqlite',ROOT/'data/line-facts.json',
            ROOT/'data/cnrail-lines.json',ROOT/'map/rail-selection.json',
            *(ROOT/name for name in ('railway_graph.py','server.py','catalog.py','station_statistics.py','index_rail_map.py'))]


def verify_packaged_snapshot(preview=False):
    report = json.loads((ROOT/'data/cnrail-audit.json').read_text())
    collection = report.get('collection', {})
    if not preview and (not collection.get('discovery') or collection.get('status') != 'completed'):
        raise ValueError('Complete source discovery and audit before packaging the offline snapshot')
    if not preview and report['totals'].get('discoveredIds') != collection.get('discovered'):
        raise ValueError('Snapshot has no matching source inventory audit')
    for path in audited_files():
        if report['sha256'].get(path.name) != snapshot_hash(path):
            raise ValueError(f'Railway audit is out of date for {path.name}')
