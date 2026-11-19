#!/usr/bin/env python3
"""Serve the railway map and timetable over local HTTP, with no outbound access."""
from contextlib import closing
import gzip
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import parse_qs, unquote, urlsplit

from train_relations import relations
from railway_graph import load_graph
from catalog import named_line_role, make_catalog
from station_statistics import station_line_attribution

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/trains.sqlite"
LINE_FACTS = json.loads((ROOT / "data/line-facts.json").read_text())
CNRAIL_LINES = json.loads((ROOT / "data/cnrail-lines.json").read_text())
RAIL_SELECTION = json.loads((ROOT / "map/rail-selection.json").read_text())
RAIL_TRACKS = json.loads(gzip.decompress((ROOT / "map/rail-tracks.json.gz").read_bytes()))
with closing(sqlite3.connect(DB)) as graph_db:
    GRAPH = load_graph(graph_db, LINE_FACTS)
for eid, feature in GRAPH.features.items():
    title = GRAPH.title[eid]
    if GRAPH.eid_for.get(title) == eid:
        CNRAIL_LINES[title] = {**feature, "id": eid, "stations": feature.get("stations", [])}
TRIP_SUMMARY = """
    SELECT t.id, t.number, t.service,
        (SELECT name FROM times JOIN stops ON stops.id=station
         WHERE trip=t.id ORDER BY seq LIMIT 1) AS origin,
        (SELECT name FROM times JOIN stops ON stops.id=station
         WHERE trip=t.id ORDER BY seq DESC LIMIT 1) AS destination
    FROM trips t
"""


def rail_type(name, has_gtfs_stations):
    canonical = GRAPH.title.get(GRAPH.eid_for.get(name),name)
    for key in dict.fromkeys((canonical, name)):
        verified = LINE_FACTS.get(key, {}).get("railType")
        if verified:
            return verified
    sections = LINE_FACTS.get(name, {}).get("sections", {})
    if sections:
        types = {GRAPH.features.get(GRAPH.eid_for.get(section), {}).get("railtype")
                 for section in sections.values()}
        if len(types) == 1 and None not in types:
            return types.pop()
    eid = GRAPH.eid_for.get(name)
    if eid and (not has_gtfs_stations or name in GRAPH.gtfs_names[eid]):
        return GRAPH.features[eid].get("railtype") or named_line_role(name)
    # A shared display label alone cannot transfer track classification to a
    # timetable corridor, especially when the two sources cover different extents.
    return named_line_role(name)


query_catalog = make_catalog(DB, CNRAIL_LINES, LINE_FACTS, RAIL_SELECTION.keys(), rail_type, GRAPH)


def station_details(db, station):
    kid = station if station in GRAPH.pois else None
    station = GRAPH.stop_for.get(station, station)
    alias = db.execute("SELECT station FROM station_aliases WHERE alias=?", (station,)).fetchone()
    if alias:
        station = alias["station"]
    row = db.execute("""
        SELECT stops.*, stop_pinyin.name AS pinyin
        FROM stops LEFT JOIN stop_pinyin ON stops.id=stop_pinyin.id WHERE stops.id=?
    """, (station,)).fetchone()
    kid = kid or GRAPH.kid_for.get(station)
    if row is None and kid is None:
        return None
    poi = GRAPH.pois.get(kid, {})
    views = GRAPH.views.get(kid, [])
    result = dict(row) if row else dict(id=kid, name=next((v["name"] for v in views if v.get("name")),poi.get("name")), pinyin=poi.get("secname"))
    result.update(kid=kid,mapPosition=GRAPH.position(kid),location=poi.get("location"),
                  sourceStale=("poi/"+kid in GRAPH.stale) if kid else False, matchMethod=GRAPH.station_match.get(kid),positionDifferenceKm=GRAPH.position_differences.get(kid),
                  teleCode=next((v["tele_code"] for v in views if v.get("tele_code")),None),
                  operators=list(dict.fromkeys(op["name"] for v in views for op in v.get("operators",[]) if op.get("name"))),
                  sourceCard=any(isinstance(v.get("connection"),list) for v in views),
                  hasTimetable=row is not None,coordinateQuality=GRAPH.coordinate_quality.get(station))

    if row is None and result["teleCode"]:
        candidate = db.execute("SELECT id FROM stops WHERE id=? AND name=?",
                               (result["teleCode"], result["name"])).fetchone()
        if candidate:
            result["boardCandidate"] = candidate[0]
    result["lines"] = [dict(line) for line in db.execute("""
        SELECT here.line, here.bureau, here.region,
               before.name AS previous, before_stop.id AS previousId,
               after.name AS next, after_stop.id AS nextId
        FROM line_stations here
        LEFT JOIN line_stations before ON before.line=here.line AND before.position=here.position-1
        LEFT JOIN line_stations after ON after.line=here.line AND after.position=here.position+1
        LEFT JOIN stops before_stop ON before_stop.id=before.code AND before_stop.name=before.name
        LEFT JOIN stops after_stop ON after_stop.id=after.code AND after_stop.name=after.name
        WHERE here.code=? AND here.name=? ORDER BY here.line, here.position
    """, (station, result["name"]))]
    from_sequence = GRAPH.sequence_edges(kid)
    if result["sourceCard"] or from_sequence:
        gtfs = result["lines"]
        result["lines"] = GRAPH.edges(kid) if result["sourceCard"] else from_sequence
        result["relationSource"] = "station-card" if result["sourceCard"] else "line-sequence"
        primary_ids = {e["eid"] for e in result["lines"]}
        result["otherSourceLines"] = [e for e in from_sequence if e["eid"] not in primary_ids]
        result["gtfsDifferences"] = []
        for edge in gtfs:
            eid = GRAPH.eid_for.get(edge["line"])
            matching = [r for r in result["lines"] if eid and r["eid"] == eid]
            def same_neighbors(record):
                def neighbors(side):
                    return {d["name"] for d in record["directions"][side] if d["name"]} or {None}
                previous, following = neighbors("previous"), neighbors("next")
                return ((edge["previous"] in previous and edge["next"] in following) or
                        (edge["next"] in previous and edge["previous"] in following))
            if not any(same_neighbors(record) for record in matching):
                result["gtfsDifferences"].append(edge)
    else:
        for edge in result["lines"]:
            edge["source"] = "gtfs"
    for line in result["lines"]:
        line["railType"] = GRAPH.features.get(line.get("eid"), {}).get("railtype") or rail_type(line["line"], True)
    result["alternateCodes"] = [r[0] for r in db.execute(
        "SELECT alias FROM station_aliases WHERE station=? ORDER BY alias",(station,))]
    result["trains"] = [dict(train) for train in db.execute("""
        SELECT t.id, t.number, t.service, visit.seq AS sequence, COALESCE(g.source_code,visit.station) AS timetableStationCode, origin.name AS origin, destination.name AS destination,
               CASE WHEN visit.seq=first.seq THEN 'origin'
                    WHEN visit.seq=last.seq THEN 'terminal' ELSE 'through' END AS kind,
               CASE WHEN visit.seq<>first.seq THEN visit.arrival END AS arrival,
               CASE WHEN visit.seq<>last.seq THEN visit.departure END AS departure
        FROM times visit
        JOIN trips t ON visit.trip=t.id
        LEFT JOIN grouped_visits g ON g.trip=visit.trip AND g.seq=visit.seq
        JOIN times first ON first.trip=t.id AND first.seq=(SELECT min(seq) FROM times WHERE trip=t.id)
        JOIN times last ON last.trip=t.id AND last.seq=(SELECT max(seq) FROM times WHERE trip=t.id)
        JOIN stops origin ON origin.id=first.station
        JOIN stops destination ON destination.id=last.station
        WHERE visit.station=? ORDER BY t.number, visit.seq
    """, (station,))]
    return result


def annotate_position(place,code):
    place["mapPosition"] = GRAPH.position(GRAPH.kid_for.get(code))
    place["coordinateQuality"] = GRAPH.coordinate_quality.get(code)


def gtfs_line_points(db, line):
    points = [dict(point) for point in db.execute("""
        SELECT item.name, item.mileage, item.region, stops.id AS stationId, stops.lon, stops.lat
        FROM line_stations item
        LEFT JOIN stops ON stops.id=item.code AND stops.name=item.name
        WHERE item.line=? ORDER BY item.position
    """, (line,))]
    for point in points:
        annotate_position(point,point["stationId"])
    return points


def candidate_summary(eid):
    points = GRAPH.features[eid].get("stations") or []
    return dict(id=eid,name=GRAPH.title[eid],origin=points[0]["name"] if points else None,
                destination=points[-1]["name"] if points else None)


def line_details(db, line, map_entry=False, gtfs_entry=False):
    if line.startswith("map:"):
        line = line.removeprefix("map:")
        map_entry = True
    if line in GRAPH.metro_lines or line in GRAPH.metro_names:
        return None
    if gtfs_entry:
        points = gtfs_line_points(db,line)
        return dict(name=line,points=points,gtfsPoints=[],railType=named_line_role(line),infrastructure=None,
                    facts=None,mapName=None,mapBounds=None,sections=[],sequenceSource="gtfs")
    if re.fullmatch(r"[0-9a-f]{32}",line) and line not in GRAPH.features:
        names = sorted(GRAPH.line_names.get(line,()))
        return dict(name=names[0] if names else "线路详情", points=[], gtfsPoints=[], railType=named_line_role(names[0]) if names else None,
                    infrastructure=None, mapName=None, mapBounds=None, sections=[], facts=None, missingEid=line)
    eid = line if line in GRAPH.features else GRAPH.eid_for.get(line)
    if map_entry and line in RAIL_SELECTION:
        eid = GRAPH.map_links.get(line,{}).get("eid")
        if not eid and not LINE_FACTS.get(line,{}).get("sections"):
            selection = RAIL_SELECTION[line]
            return dict(name=line, points=[], gtfsPoints=[], railType=named_line_role(line), infrastructure=None,
                        mapName=line, mapBounds=selection["bounds"], mapDetail=selection.get("detail",False),
                        mapUnresolved=True, sections=[], facts=None,
                        candidates=[candidate_summary(key) for key in GRAPH.map_candidates.get(line,[])
                                    if key in GRAPH.features], gtfsAvailable=bool(gtfs_line_points(db,line)))
    if LINE_FACTS.get(line,{}).get("sections"):
        eid = None
    requested = line
    if eid:
        line = GRAPH.title[eid]
    facts = LINE_FACTS.get(line)
    points = gtfs_line_points(db, line)
    uses_section_points = not points and bool((facts or {}).get("sections"))
    if uses_section_points:
        for label, name in facts.get("sections", {}).items():
            section_id = GRAPH.eid_for.get(name)
            if section_id in GRAPH.features:
                section = line_details(db, section_id)
                points.extend({**p,"section":label} for p in section["points"])
            else:
                points.extend({**point, "section": label} for point in gtfs_line_points(db, name))
    if not points and eid and len(GRAPH.gtfs_names[eid]) == 1:
        points = gtfs_line_points(db,next(iter(GRAPH.gtfs_names[eid])))
    source = ({**GRAPH.features[eid], "id": eid} if eid in GRAPH.features else CNRAIL_LINES.get(line))
    original_points = []
    if source:
        local_by_name = {point["name"]: point for point in points}
        for station in source.get("stations", []):
            name = station["name"]
            local = local_by_name.get(name)
            kid = station.get("kid")
            station_id = GRAPH.station_link(kid) if kid else None
            mileage = station.get("mileage")
            original_points.append({
                "name": name, "mileage": f"{mileage:g} km" if isinstance(mileage, (int, float)) else mileage,
                "region": local["region"] if local else None, "kid":kid, "stationId": station_id, "mapPosition":GRAPH.position(kid)
            })
    map_name = line if line in RAIL_SELECTION and (facts or {}).get("sections") else None
    if eid:
        choices = GRAPH.maps_for[eid]
        map_name = next((name for name in (requested,line) if name in choices), choices[0] if len(choices)==1 else None)
    selection = RAIL_SELECTION.get(map_name, {})
    pins = selection.get("stations", {})
    for point in original_points + points:
        point["coordinateQuality"] = GRAPH.coordinate_quality.get(point["stationId"])
        point["mapPosition"] = point.get("mapPosition") or GRAPH.position(GRAPH.kid_for.get(point["stationId"])) or (pins.get(point["name"]) if not point.get("kid") else None)
        if point.get("lon") is None and point["stationId"] and not point["mapPosition"]:
            position = db.execute("SELECT lon,lat FROM stops WHERE id=?", (point["stationId"],)).fetchone()
            if position:
                point.update(dict(position))
    infrastructure = {key:source.get(key) for key in ("id","lines","design_speed","electrification","railtype")} if source else None
    ordered_kids = {point.get("kid") for point in original_points}
    additional = [{"kid":kid, "name":next((v.get("name") for v in GRAPH.views[kid] if v.get("name")),
                                         GRAPH.pois[kid].get("name"))}
                  for kid in sorted(GRAPH.connected_kids.get(eid,set()) - ordered_kids)]
    points_differ = [point["name"] for point in original_points] != [point["name"] for point in points]
    return {
        "name": line, "mapName":map_name, "railType": (source or {}).get("railtype") or rail_type(line, bool(points)), "points": original_points or points,
        "gtfsPoints": points if original_points and points_differ else [],
        "sequenceSource":"cnrail" if original_points else "sections" if uses_section_points else "gtfs",
        "sections": [{"label":label, "id":GRAPH.eid_for.get(name,name)}
                     for label,name in (facts or {}).get("sections",{}).items()],
        "facts": facts, "infrastructure": infrastructure, "sourceArchived":eid in GRAPH.features,"sourceStale":("feature/"+eid in GRAPH.stale) if eid else False,
        "additionalStations":sorted(additional,key=lambda p:p["name"]),
        "mapBounds": selection.get("bounds"), "mapDetail": selection.get("detail", False),
        "supplement": selection.get("supplement")
    }


def motion_lines(db, trip):
    """Candidate mileage axes. The client checks station order and path length before use."""
    rows = db.execute("""
        SELECT l.line,l.position,l.name,l.code,l.mileage,s.lon,s.lat FROM line_stations l
        LEFT JOIN stops s ON s.id=l.code AND s.name=l.name
        WHERE l.line IN (SELECT DISTINCT n.line FROM times v
                         JOIN line_stations n ON n.code=v.station WHERE v.trip=?)
        ORDER BY l.line,l.position
    """, (trip,))
    groups = {}
    for row in rows:
        groups.setdefault(row["line"], []).append(row)
    lines = []
    for name, stations in groups.items():
        feature = GRAPH.features.get(GRAPH.eid_for.get(name), {})
        speed = LINE_FACTS.get(name, {}).get("designSpeed") or feature.get("design_speed") or ""
        unit = r"(\d+)\s*(?:km/h|公里/小时)"
        uniform = re.fullmatch(unit, speed)
        positions = {}
        for station in stations:
            positions.setdefault(station["name"], []).append(station["position"])
        ranges = []
        for first, last, value in re.findall(r"（([^（）]+?)至([^（）]+?)）\s*" + unit, speed):
            a, b = positions.get(first, []), positions.get(last, [])
            if len(a) == len(b) == 1:
                ranges.append([min(a[0], b[0]), max(a[0], b[0]), int(value)])
        points = []
        for station in stations:
            if GRAPH.coordinate_quality.get(station["code"]) != "reference" or station["lon"] is None or station["lat"] is None:
                continue
            km = re.fullmatch(r"([0-9]+(?:[.][0-9]+)?)\s*km", (station["mileage"] or "").replace(",", ""))
            if km:
                points.append([station["lon"], station["lat"], float(km[1]), station["position"]])
        if len(points) >= 2:
            lines.append(dict(name=name, speed=int(uniform[1]) if uniform else None, ranges=ranges, points=points))
    return lines


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".pbf": "application/x-protobuf"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path.startswith("/api/"):
            try:
                with closing(sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    data = self.query(db, url.path, parse_qs(url.query))
                self.send_json(data, 200 if data is not None else 404)
            except sqlite3.Error:
                self.log_error("Timetable database query failed")
                self.send_json({"error": "本地时刻表不可用"}, 503)
        elif re.fullmatch(r"/map/tiles/\d+/\d+/\d+\.pbf", url.path):
            if not (ROOT / url.path.lstrip("/")).is_file():
                self.send_response(204)
                self.end_headers()
                return
            super().do_GET()
        elif url.path.startswith("/map/fonts/") and not Path(self.translate_path(url.path)).is_file():
            # A valid protobuf with no glyphs. CJK glyphs are rendered from local fonts.
            self.send_response(200)
            self.send_header("Content-Type", self.extensions_map[".pbf"])
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            super().do_GET()

    def send_json(self, data, status):
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if len(body) > 4096 and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, compresslevel=3)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def list_directory(self, path):
        self.send_error(404)
        return None

    @staticmethod
    def query(db, path, params):
        if path.startswith("/api/catalog/"):
            return query_catalog(path.removeprefix("/api/catalog/"), params)
        if path == "/api/source/coverage":
            return json.loads((ROOT/"data/cnrail-audit.json").read_text())
        if path.startswith("/api/source/"):
            kind, _, identifier = path.removeprefix("/api/source/").partition("/")
            if kind == "poi":
                return GRAPH.raw_pois.get(identifier)
            if kind == "feature":
                return GRAPH.feature_records.get(identifier)
            return None
        if path == "/api/info":
            meta = json.loads(db.execute("SELECT json FROM metadata").fetchone()[0])
            meta["infrastructure"] = GRAPH.collection
            meta["preview"] = os.getenv("RAILS_PREVIEW") == "1"
            meta["map"] = json.loads((ROOT / "map/coverage.json").read_text())
            return meta
        if path == "/api/search":
            term = params.get("q", [""])[0].strip()[:60]
            if not term:
                return {"trains": [], "stations": [], "lines": []}
            trains = []
            if re.fullmatch(r"[A-Za-z0-9]+", term):
                trains = [dict(row) for row in db.execute(
                    TRIP_SUMMARY + "WHERE number LIKE ? ORDER BY number != ?, length(number), number, id LIMIT 80",
                    (term + "%", term))]
            stations = [dict(row) for row in db.execute(
                "SELECT * FROM stops WHERE instr(name, ?) ORDER BY name != ?, length(name), name LIMIT 30",
                (term, term))]
            for stop in stations:
                annotate_position(stop,stop["id"])
            stations.extend(row for row in GRAPH.searchable if term in row["name"])
            stations.sort(key=lambda row:(row["name"]!=term,len(row["name"]),row["name"],bool(row.get("sourceOnly")),row["id"]))
            stations = stations[:30]
            lines = []
            if params.get("scope") != ["station"]:
                matches = query_catalog("lines", {"q": [term]}, relevance=True)
                lines = [{key: row[key] for key in ("id", "name", "aliases", "origin", "destination")}
                         for row in matches["rows"][:20]]
            return {"trains": trains, "stations": stations, "lines": lines}
        if path == "/api/region-live":
            from region_live import region_window
            return region_window(params)
        if path.startswith("/api/line-live/"):
            from line_live import traffic_window
            return traffic_window(params.get("day", [""])[0], params.get("start", [""])[0], path.rsplit("/", 1)[1])
        if path.startswith("/api/relations/"):
            return relations(unquote(path.removeprefix("/api/relations/")), params.get("day", [""])[0])
        if path.startswith("/api/trip/"):
            trip = unquote(path.removeprefix("/api/trip/"))
            row = db.execute(TRIP_SUMMARY + "WHERE t.id=?", (trip,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["stops"] = [dict(stop) for stop in db.execute(
                """SELECT stops.*, seq, arrival, departure FROM times
                   JOIN stops ON stops.id=station WHERE trip=? ORDER BY seq""", (trip,))]
            for stop in result["stops"]:
                annotate_position(stop,stop["id"])
            result["snapshot"] = json.loads(db.execute("SELECT json FROM metadata").fetchone()[0])["feed_version"]
            result["dates"] = [record[0] for record in db.execute(
                "SELECT date FROM dates WHERE service=? AND kind=1 ORDER BY date", (row["service"],))]
            shape = db.execute("SELECT path FROM shapes JOIN trips ON trips.shape=shapes.id WHERE trips.id=?",
                               (trip,)).fetchone()
            if not shape:
                from train_relations import inferred_route
                recovered = inferred_route(trip)
                if recovered:
                    result["geometry"], result["inferredLegs"] = recovered
                    result["motionLines"] = motion_lines(db, trip)
                    return result
            result["motionLines"] = motion_lines(db, trip) if shape else []
            result["geometry"] = json.loads(gzip.decompress(shape[0])) if shape else None
            return result
        if path.startswith("/api/station-calendar/"):
            # Station details already resolve map IDs and aliases to the timetable stop ID.
            station = unquote(path.removeprefix("/api/station-calendar/"))
            rows = db.execute("""
                SELECT service, group_concat(date) FROM dates WHERE kind=1 AND service IN (
                    SELECT DISTINCT t.service FROM times v JOIN trips t ON t.id=v.trip
                    WHERE v.station=?
                ) GROUP BY service
            """, (station,))
            meta = json.loads(db.execute("SELECT json FROM metadata").fetchone()[0])
            return {"calendars": dict(rows), "start": meta["feed_start_date"], "end": meta["feed_end_date"]}
        if path.startswith("/api/station-lines/"):
            station = station_details(db, unquote(path.removeprefix("/api/station-lines/")))
            return station_line_attribution(db, station, GRAPH) if station else None
        if path.startswith("/api/station/"):
            station = unquote(path.removeprefix("/api/station/"))
            return station_details(db, station)
        if path.startswith("/api/line/"):
            line = unquote(path.removeprefix("/api/line/"))
            result = line_details(db, line, params.get("from")==["map"],params.get("from")==["gtfs"])
            if result:
                from line_live import live_descriptor
                result["tracks"] = RAIL_TRACKS.get(result.get("mapName"), [])
                result["live"] = live_descriptor(result["name"])
            return result
        return None


if __name__ == "__main__":
    bind = os.getenv("RAILS_BIND", "127.0.0.1")
    port = int(os.getenv("RAILS_PORT", "8080"))
    if not DB.is_file():
        sys.exit("运行 python3 import_gtfs.py 以生成本地时刻表索引")
    with ThreadingHTTPServer((bind, port), Handler) as server:
        print(f"Rails: http://{bind}:{port}", flush=True)
        server.serve_forever()
