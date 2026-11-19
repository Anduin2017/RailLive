"""Paginated, read-only catalogs derived from the local snapshot."""
from collections import defaultdict
from contextlib import closing
from functools import lru_cache
from itertools import groupby
import re
import sqlite3

PAGE_SIZE = 50
RAIL_TYPES = {
    "高速铁路": "high", "快速铁路": "fast", "普速铁路": "regular",
    "高速/普速铁路": "mixed", "LINK": "link", "铁路渡轮": "ferry",
}
FIELDS = {
    "lines": ("name", "type", "origin", "destination", "stations", "length", "speed", "tracks", "electric"),
    "trains": ("name", "origin", "destination", "departure", "arrival", "duration", "stops", "distanceKm", "averageKmh", "peakKmh"),
    "stations": ("name", "total", "origin", "through", "terminal", "lines", "code"),
}
DEFAULT_SORT = {"lines": "name", "trains": "name", "stations": "total"}
TIME = re.compile(r"(\d+):([0-5]\d):([0-5]\d)")
MILEAGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:km)?", re.I)


def named_line_role(name):
    return "LINK" if name and "联络线" in name else None


def seconds(value):
    match = TIME.fullmatch(value or "")
    if not match:
        return None
    hours, minutes, secs = map(int, match.groups())
    return hours * 3600 + minutes * 60 + secs


def train_rows(db, metrics):
    records = db.execute("""
        SELECT trips.id, trips.number, stops.name, times.arrival, times.departure
        FROM times JOIN trips ON trips.id=times.trip JOIN stops ON stops.id=times.station
        ORDER BY times.trip, times.seq
    """)
    for key, calls in groupby(records, key=lambda row: row["id"]):
        calls = list(calls)
        first, last = calls[0], calls[-1]
        number = first["number"]
        departure, arrival = first["departure"], last["arrival"]
        start, end = seconds(departure), seconds(arrival)
        duration = end - start if len(calls) > 1 and None not in (start, end) and end >= start else None
        distance, peak, fastest = metrics.get(key, (None, None, None))
        prefix = re.match(r"[A-Z]+", number.upper())
        yield dict(id=key, name=number, prefix=prefix[0] if prefix else "numeric",
                   origin=first["name"], destination=last["name"], arrival=arrival, departure=departure,
                   duration=duration, stops=len(calls), distanceKm=distance, peakKmh=peak,
                   peakSection=[calls[fastest]["name"], calls[fastest+1]["name"]] if fastest is not None else None,
                   averageKmh=distance * 3600 / duration if distance is not None and duration else None)


def line_rows(db, original, facts, mapped_names, rail_type, graph):
    gtfs = defaultdict(list)
    for row in db.execute("SELECT line,name,mileage FROM line_stations ORDER BY line,position"):
        gtfs[row["line"]].append(row)
    ambiguous_maps = {name for name,ids in graph.map_candidates.items()
                      if len(ids)>1 and name not in graph.map_links and not facts.get(name,{}).get("sections")}
    all_names = set(gtfs) | set(original) | set(facts) | (mapped_names-ambiguous_maps) | set(graph.title.values())
    groups = defaultdict(set)
    for name in all_names - graph.metro_names:
        canonical = graph.title.get(graph.eid_for.get(name), name)
        if facts.get(name,{}).get("sections"):
            canonical = name
        groups[canonical].add(name)

    records = [(name,names,None if facts.get(name,{}).get("sections") else graph.eid_for.get(name))
               for name,names in groups.items()]
    resolved = {eid for _,_,eid in records}
    for eid,title in graph.title.items():
        if eid not in resolved:
            records.append((title,{title},eid))
    for name, names, eid in records:
        if not eid and name in graph.title.values() and not facts.get(name,{}).get("sections"):
            continue
        aliases = sorted((names | graph.line_names.get(eid,set())) - {name})
        infrastructure = graph.features.get(eid) or original.get(name) or next((original[n] for n in aliases if n in original), {})
        verified = facts.get(name, {})
        points = infrastructure.get("stations") or gtfs.get(name) or next((gtfs[n] for n in aliases if n in gtfs), [])
        if not points:
            points = []
            for section in verified.get("sections", {}).values():
                record = graph.features.get(graph.eid_for.get(section),{})
                points.extend(record.get("stations") or gtfs.get(section, ()))
        positions = []
        for point in points:
            value = dict(point).get("mileage")
            match = MILEAGE.fullmatch(str(value).strip())
            positions.append(float(match[1]) if match else None)
        length = None
        # Blank intermediate mileposts are common. A reset or missing endpoint leaves the span unknown.
        known = [value for value in positions if value is not None]
        if (len(positions) > 1 and positions[0] is not None and positions[-1] is not None
                and known == sorted(known) and positions[-1] > positions[0]):
            length = round(positions[-1] - positions[0], 3)
        entry_id = eid or name
        if name in mapped_names and not (eid or points or infrastructure or verified):
            entry_id = "map:"+name
        yield dict(id=entry_id, name=name, aliases=sorted(set(aliases) | set(verified.get("aliases", ()))), type=RAIL_TYPES.get(infrastructure.get("railtype") or rail_type(name, any(n in gtfs for n in names)), "unknown"),
                   origin=points[0]["name"] if points else None, destination=points[-1]["name"] if points else None,
                   stations=len(points) or None, length=verified.get("lengthKm", length),
                   speed=verified.get("designSpeed") or infrastructure.get("design_speed"),
                   tracks=infrastructure.get("lines"), electric=infrastructure.get("electrification"))

    # A label spanning several IDs is its own map entry, alongside the distinct
    # source sections. Opening it must not select whichever section sorts first.
    for name in ambiguous_maps - graph.metro_names:
        yield dict(id="map:"+name,name=name,aliases=[],type=RAIL_TYPES.get(named_line_role(name),"unknown"),origin=None,destination=None,
                   stations=None,length=None,speed=None,tracks=None,electric=None)


def station_rows(db, original, graph):
    links = defaultdict(set)
    for name, code in db.execute("""
        SELECT DISTINCT line_stations.line, stops.id FROM line_stations
        JOIN stops ON stops.id=line_stations.code AND stops.name=line_stations.name
    """):
        links[code].add(name)
    for row in db.execute("""
        WITH endpoints AS (SELECT trip, min(seq) AS first, max(seq) AS last FROM times GROUP BY trip),
        counts AS (
            SELECT station, count(*) AS total, sum(seq=first) AS origin,
                   sum(seq<>first AND seq=last) AS terminal,
                   sum(seq<>first AND seq<>last) AS through
            FROM times JOIN endpoints USING(trip) GROUP BY station
        )
        SELECT id,name, coalesce(total,0) AS total, coalesce(origin,0) AS origin,
               coalesce(through,0) AS through, coalesce(terminal,0) AS terminal
        FROM stops LEFT JOIN counts ON stops.id=counts.station
    """):
        code = row["id"]
        kid = graph.kid_for.get(code)
        if any(isinstance(v.get("connection"),list) for v in graph.views.get(kid,())):
            links[code] = {edge["eid"] for edge in graph.edges(kid)}
        elif graph.positions_for.get(kid):
            links[code] = {eid for eid,_ in graph.positions_for[kid]}
        yield dict(row, code=code, lines=len(links[code]))
    for item in graph.searchable:
        kid = item["id"]
        views = graph.views[kid]
        edges = graph.edges(kid)
        yield dict(id=kid,name=item["name"],code=next((v["tele_code"] for v in views if v.get("tele_code")),None),
                   total=None,origin=None,through=None,terminal=None,
                   lines=len({e["eid"] for e in edges}) if any(isinstance(v.get("connection"),list) for v in views)
                   else len({eid for eid,_ in graph.positions_for[kid]}) or None)


def make_catalog(database_path, original, facts, mapped_names, rail_type, graph):
    index = database_path.with_name("region.sqlite")

    @lru_cache(maxsize=3)
    def summaries(kind, snapshot):
        with closing(sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            if kind == "trains":
                metrics = {}
                if index.is_file():
                    with closing(sqlite3.connect(f"{index.as_uri()}?mode=ro", uri=True)) as regional:
                        if regional.execute("SELECT 1 FROM sqlite_master WHERE name='trip_metrics'").fetchone():
                            metrics = {trip: (km, peak, seq) for trip, km, peak, seq in regional.execute(
                                "SELECT trip,distance_km,peak_kmh,peak_seq FROM trip_metrics")}
                rows = train_rows(db, metrics)
            elif kind == "lines":
                rows = line_rows(db, original, facts, mapped_names, rail_type, graph)
            else:
                rows = station_rows(db, original, graph)
            return sorted(rows, key=lambda row: (row["name"], row["id"]))

    def query(kind, params, *, relevance=False):
        if kind not in FIELDS:
            return None

        def param(name, default=""):
            return params.get(name, [default])[0]

        field = param("sort", DEFAULT_SORT[kind])
        if field not in FIELDS[kind]:
            field = DEFAULT_SORT[kind]
        descending = param("order", "desc" if kind == "stations" and field == "total" else "asc") == "desc"
        page = param("page", "1")
        page = max(1, int(page)) if re.fullmatch(r"[0-9]{1,8}", page) else 1
        term = param("q").strip()[:80].casefold()
        tags = set(params.get("tag", ()))
        multi = param("multi") == "1"
        snapshot = database_path.stat()
        stats_snapshot = index.stat() if kind == "trains" and index.is_file() else None
        rows = summaries(kind, (snapshot.st_ino, snapshot.st_mtime_ns, snapshot.st_size,
                                (stats_snapshot.st_ino, stats_snapshot.st_mtime_ns) if stats_snapshot else None))

        def names(row):
            return tuple(name.casefold() for name in (row["name"], *row.get("aliases", ())))

        def matches(row):
            if term and not any(term in name for name in names(row)):
                return False
            if kind == "stations":
                return (not multi or (row["lines"] or 0) >= 2) and (
                    not tags or any(row[tag] for tag in tags & {"origin", "through", "terminal"}))
            if kind == "lines" and "link" in tags and named_line_role(row["name"]):
                return True
            return not tags or row["prefix" if kind == "trains" else "type"] in tags

        def sort_value(row):
            if relevance:
                candidates = [name for name in names(row) if term in name]
                return min((name != term, not name.startswith(term), len(name), name) for name in candidates)
            value = row[field]
            if kind == "trains" and field in {"arrival", "departure"}:
                return seconds(value)
            if kind == "trains" and field == "name":
                match = re.fullmatch(r"([A-Z]*)(\d+)", value)
                return (match[1], int(match[2])) if match else (value, 0)
            if kind == "lines" and field in {"speed", "tracks"}:
                pattern = r"(?<![0-9])([1-9][0-9]{1,2})(?![0-9])" if field == "speed" else r"\b([1-9]\d?)\b"
                numbers = re.findall(pattern, str(value))
                return max(map(int, numbers), default=None)
            return value

        selected = [row for row in rows if matches(row)]
        known, missing = [], []
        for row in selected:
            value = sort_value(row)
            if value is None:
                missing.append(row)
            else:
                known.append((value, row))
        known.sort(key=lambda pair: pair[0], reverse=descending)
        total = len(selected)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        offset = (page - 1) * PAGE_SIZE
        ordered = [row for _, row in known] + missing
        return dict(rows=ordered[offset:offset + PAGE_SIZE], total=total, page=page, pages=pages, pageSize=PAGE_SIZE)

    return query
