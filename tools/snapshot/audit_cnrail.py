"""Reconcile every archived station-card edge, direction and line stop against served data."""
from collections import Counter, defaultdict
import csv
import io
import zipfile
import hashlib
import gzip
import json
import sqlite3

from collect_cnrail import CACHE, ID, references, tile_ids
from gtfs_coordinates import classify
from railway_graph import ROOT, SNAPSHOT, snapshot_hash, audited_files
from server import GRAPH, DB, station_details, line_details, Handler, RAIL_SELECTION, query_catalog


def main():
    issues = defaultdict(list)
    totals = Counter()
    with gzip.open(SNAPSHOT,'rt') as f:
        source = json.load(f)
    for key,record in source.get("provenance",{}).items():
        cached = ROOT/"data/cnrail-source"/(key+".json")
        assert record[1]==hashlib.sha256(cached.read_bytes()).hexdigest(),key
    if source['collection'].get('discovery'):
        inventory = {tuple(pair) for pair in json.loads((CACHE/'discovered-ids.json').read_text())}
        failures = source['collection']['errors']
        assert len(inventory) == source['collection']['discovered']
        seeds = {('poi', kid) for kid in tile_ids() if ID.fullmatch(kid)}
        assert seeds <= inventory, sorted(seeds - inventory)
        reachable = set(seeds)
        for record in source['feature'].values():
            reachable.update(references(record.get('data', {})))
        for poi in source['poi'].values():
            for part in poi.get('exd', []):
                if part.get('view') == 'station':
                    reachable.update(references(part['data']))
        unexplained = [(kind, key) for kind, key in reachable
                       if key not in source[kind] and f'{kind}/{key}' not in failures]
        assert not unexplained, unexplained[:20]
        for kind, key in inventory:
            assert (CACHE/kind/(key+'.json')).exists() or f'{kind}/{key}' in failures, (kind,key)
        totals['tileStationSeeds'] = len(seeds)
        totals['reachableReferencedIds'] = len(reachable)
        totals['discoveredIds'] = len(inventory)
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        coordinate_quality, reference = classify(db)
        assert source['coordinateReference']==reference and GRAPH.coordinate_quality==coordinate_quality
        issues['unverifiedTimetableCoordinates'] = [[code,status] for code,status in coordinate_quality.items()
                                                   if status!='reference']
        source_states = source.get('collection',{})
        totals['excludedUrbanTransitLines'] = len(GRAPH.metro_lines)
        totals['excludedMetroStations'] = len(GRAPH.metro_stations)
        for kid,poi in source['poi'].items():
            if poi.get('featureType') == '地铁站':
                assert station_details(db,kid) is None, kid
                continue
            cards = [part['data'] for part in poi.get('exd',[]) if part.get('view')=='station']
            if not cards:
                continue
            totals['stationCards'] += 1
            actual = station_details(db,kid)
            assert actual and actual['kid']==kid,(kid,'wrong card')
            if kid not in GRAPH.stop_for:
                issues['stationCardsWithoutTimetable'].append([kid,actual['name']])
            if not any(isinstance(c.get('connection'),list) for c in cards):
                issues['cardsWithoutConnectionField'].append(kid)
                continue
            expected = [e for c in cards for e in (c.get('connection') or []) if e.get('eid') not in GRAPH.metro_lines]
            assert len(actual['lines'])==len(expected),(kid,'edge count')
            for edge,record in zip(expected,actual['lines']):
                totals['connectionRows'] += 1
                eid = edge.get('eid')
                assert record['eid']==eid and record['color']==edge.get('linecolor') and record['shortName']==edge.get('linename')
                for field,key in [('prev','previous'),('next','next')]:
                    rows = edge.get(field) or []
                    dirs = record['directions'][key]
                    assert len(rows)==len(dirs)
                    for row,d in zip(rows,dirs):
                        adj,dest = row.get('adj') or {},row.get('dest') or {}
                        assert (adj.get('kid'),adj.get('name'),dest.get('kid'),dest.get('name'),dest.get('status')) == (
                            d['kid'],d['name'],d['destinationKid'],d['destination'],d['status']),(kid,eid,key)
                if eid not in GRAPH.features:
                    issues['edgeWithoutLineDetail'].append([kid,eid])
                elif kid not in {p.get('kid') for p in GRAPH.features[eid].get('stations',[])}:
                    issues['sourceCardAbsentFromSourceLineSequence'].append([kid,eid])
        for eid,feature in GRAPH.features.items():
            totals['lineDetails'] += 1
            actual = line_details(db,eid)
            assert actual['infrastructure']['id']==eid,(eid,'wrong detail')
            expected = feature.get('stations') or []
            if expected:
                assert [(p['kid'],p['name']) for p in actual['points']]==[(p.get('kid'),p['name']) for p in expected],eid
            remaining = GRAPH.connected_kids.get(eid,set()) - {point.get("kid") for point in expected}
            assert {p["kid"] for p in actual["additionalStations"]}==remaining,(eid,"reverse membership")
            for p in expected:
                kid = p.get("kid")
                if kid and kid in GRAPH.pois and any(isinstance(v.get("connection"),list) for v in GRAPH.views[kid]):
                    if kid not in GRAPH.connected_kids.get(eid,set()):
                        issues["sourceLineStopAbsentFromSourceCard"].append([eid,kid])
                totals['orderedLineStops'] += 1
                if p.get('kid') and p['kid'] not in source['poi']:
                    issues['lineStopWithoutPoi'].append([eid,p['kid']])
            for field in ('railtype','lines','electrification','design_speed'):
                assert actual['infrastructure'][field]==feature.get(field),(eid,field)
        for station,name,visits in db.execute(
                'SELECT stops.id,stops.name,count(times.trip) FROM stops LEFT JOIN times ON times.station=stops.id GROUP BY stops.id'):
            data = station_details(db,station)
            assert len(data['trains'])==visits,(station,visits)
            totals['timetableVisits']+=visits
            if station not in GRAPH.kid_for:
                issues['timetableWithoutSourceStation'].append([station,name])
        aliases = dict(db.execute("SELECT alias,station FROM station_aliases"))
        source_codes = {(trip,seq):code for trip,seq,code in db.execute("SELECT * FROM grouped_visits")}
        for alias,target in aliases.items():
            assert target in GRAPH.kid_for,(alias,target)
            kid = GRAPH.kid_for[target]
            assert any(v.get("tele_code")==target for v in GRAPH.views[kid]),(alias,target,kid)
        times = {(r["trip"],r["seq"]):(r["station"],r["arrival"],r["departure"])
                 for r in db.execute("SELECT * FROM times")}
        with zipfile.ZipFile(ROOT/'data/output_gtfs.zip') as archive, archive.open('stop_times.txt') as raw:
            for visit in csv.DictReader(io.TextIOWrapper(raw,encoding='utf-8-sig')):
                key = visit["trip_id"],int(visit["stop_sequence"])
                stop = visit["stop_id"]
                if stop in aliases:
                    assert source_codes.pop(key)==stop,key
                assert times.pop(key)==(aliases.get(stop,stop),visit["arrival_time"],visit["departure_time"]),key
        assert not times and not source_codes
        for key, name in (("stations","车站"),("lines","线路")):
            page = query_catalog(key,{})
            rows = list(page["rows"])
            for index in range(2,page["pages"]+1):
                rows.extend(query_catalog(key,{"page":[str(index)]})["rows"])
            assert len(rows)==len({r["id"] for r in rows})==page["total"],name
            totals[key+"InCatalog"]=len(rows)
            if key=="stations":
                for r in rows:
                    station = station_details(db,r["id"])
                    distinct = {edge.get("eid") or edge["line"] for edge in station["lines"]}
                    if station["sourceCard"]:
                        assert r["lines"]==len(distinct),(r["id"],r["lines"],distinct)
            else:
                listed = {row["id"] for row in rows}
                assert listed.isdisjoint(GRAPH.metro_lines)
                assert not any(r["name"] in GRAPH.metro_names for r in rows)
                assert listed.issuperset(GRAPH.features),set(GRAPH.features)-listed
                for r in rows:
                    detail = line_details(db,r["id"])
                    if '联络线' in r['name']:
                        assert r['type'] != 'unknown' and detail['railType'], r['id']
                    points = detail["points"]
                    assert r["stations"]==(len(points) or None),(r["id"],r["stations"],len(points))
                    if points:
                        assert (r["origin"],r["destination"])==(points[0]["name"],points[-1]["name"]),r["id"]
        for station in ("NJH","SZQ","SZH","HBB","XYU","RQI"):
            actual = station_details(db,station)
            if not actual or not actual["sourceCard"]:
                continue
            call_data = Handler.query(db,"/api/station-lines/"+station,{})
            expected_keys = {edge["eid"] for edge in actual["lines"]}
            assert {r["id"] for r in call_data["options"]}==expected_keys
            if expected_keys:
                assert {(v["id"],v["sequence"]) for v in call_data["visits"]}=={
                    (v["id"],v["sequence"]) for v in actual["trains"]}
            assert all(v[k] in expected_keys or v[k] is None
                       for v in call_data["visits"] for k in ("incomingId","outgoingId"))
        for name,eids in GRAPH.sequence_candidates.items():
            if not any(name in GRAPH.gtfs_names[eid] for eid in eids):
                issues['stationSequenceCandidatesWithoutConfirmedAlias'].append([name,eids])
        issues["sourceAndTimetablePositionDifferenceKm"] = [
            [kid,GRAPH.stop_for[kid],gap] for kid,gap in sorted(GRAPH.position_differences.items())]
        totals.update({f"stationMatch_{k}":v for k,v in Counter(GRAPH.station_match.values()).items()})
        for name in RAIL_SELECTION:
            if name not in GRAPH.map_links:
                issues['tracksWithoutVerifiedSingleLine'].append(name)
        totals['verifiedMapLabels'] = len(GRAPH.map_links)
    report = dict(totals=totals,collection=source_states, counts={k:len(v) for k,v in issues.items()},items=issues)
    report["sha256"] = {path.name:snapshot_hash(path) for path in audited_files()}
    output = ROOT/'data/cnrail-audit.json'
    tmp = output.with_suffix('.building')
    tmp.write_text(json.dumps(report,ensure_ascii=False,separators=(',',':')))
    tmp.replace(output)
    print(json.dumps({'totals':totals,'counts':report['counts']},ensure_ascii=False))


if __name__=='__main__':
    main()
