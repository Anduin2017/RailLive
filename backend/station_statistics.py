"""Attribute station calls to physical lines using local railway and timetable data."""
from collections import defaultdict
from functools import lru_cache
import gzip
import json
import math
from statistics import median

from index_rail_map import ROOT, ZOOM, distance_squared, named_features, tile_coordinate

SAMPLE_KM = (2, 4, 7, 10)


def map_point(lon, lat):
    return tile_coordinate(
        1.015972842 * lon - 0.000070497 * lat - 5.116568470,
        0.000009795 * lon + 1.012049241 * lat + 1.113596968,
    )


@lru_cache(maxsize=64)
def tile_lines(x, y):
    lines = defaultdict(list)
    tile = ROOT / f"map/tiles/{ZOOM}/{x}/{y}.pbf"
    if tile.is_file():
        for kind, name, paths in named_features(tile):
            if kind == b"rail":
                lines[name].extend(paths)
    return lines


def local_geometry(point, lines):
    x, y = map(math.floor, point)
    return {
        line: [path for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               for path in tile_lines(x + dx, y + dy).get(line, ())]
        for line in lines
    }


def shape_sides(blob, point, tile_km, geometry):
    if not blob or not geometry or not all(geometry.values()):
        return None, None

    route = [map_point(*position) for position in json.loads(gzip.decompress(blob))]
    # Match the timetable station to a segment, even if the shape omits a vertex there.
    candidates = []
    traveled = 0
    for index, ((ax, ay), (bx, by)) in enumerate(zip(route, route[1:])):
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        if length:
            part = max(0, min(1, ((point[0] - ax) * dx + (point[1] - ay) * dy) / length))
            closest = ax + part * dx, ay + part * dy
            segment_length = math.sqrt(length) * tile_km
            candidates.append((math.dist(point, closest), index, closest, traveled + part * segment_length))
            traveled += segment_length
    if not candidates:
        return None, None
    gap, index, closest, progress = min(candidates)
    # The shape may revisit the same station; a direction match is then ambiguous.
    if gap * tile_km > 0.65 or any(
        abs(other_progress - progress) > 1.5 and distance * tile_km < 0.3
        and math.dist(other, closest) * tile_km < 0.12
        for distance, _, other, other_progress in candidates
    ):
        return None, None

    def match(path):
        samples = []
        start, traveled = closest, 0
        for end in path:
            length = math.dist(start, end) * tile_km
            while length and len(samples) < len(SAMPLE_KM) and SAMPLE_KM[len(samples)] <= traveled + length:
                fraction = (SAMPLE_KM[len(samples)] - traveled) / length
                samples.append(tuple(a + (b - a) * fraction for a, b in zip(start, end)))
            if len(samples) == len(SAMPLE_KM):
                break
            start, traveled = end, traveled + length
        if len(samples) < 2:
            return None
        scores = sorted((median(
            min(distance_squared(sample, segment) for segment in paths) ** 0.5 * tile_km
            for sample in samples
        ), line) for line, paths in geometry.items())
        error, line = scores[0]
        if error <= 0.38 and (len(scores) == 1 or scores[1][0] - error >= 0.25):
            return line
        return None

    return match(reversed(route[:index + 1])), match(route[index + 1:])


def line_memberships(db, station, graph):
    labels = {}
    memberships = defaultdict(set)
    adjacent = defaultdict(set)
    map_names = {}
    complete = True
    for edge in station["lines"]:
        name = edge["line"]
        eid = edge.get("eid")
        key = eid or name
        labels[key] = name
        if eid:
            feature = graph.features.get(eid) or {}
            stops = feature.get("stations") or []
            complete = complete and bool(stops)
            for stop in stops:
                code = graph.stop_for.get(stop.get("kid"))
                if code:
                    memberships[code].add(key)
            for side in edge["directions"].values():
                for neighbor in side:
                    code = graph.stop_for.get(neighbor["kid"])
                    if code:
                        adjacent[code].add(key)
            map_names[key] = graph.maps_for.get(eid,[])
        else:
            for row in db.execute("SELECT code FROM line_stations WHERE line=?", (name,)):
                memberships[row[0]].add(key)
            for field in ("previousId","nextId"):
                if edge.get(field):
                    adjacent[edge[field]].add(key)
            map_names[key] = []

    def by_neighbor(code):
        if complete and len(adjacent[code]) == 1 and memberships[code] == adjacent[code]:
            return next(iter(adjacent[code]))
        return None

    return labels, map_names, by_neighbor


def station_line_attribution(db, station, graph):
    labels, map_names, by_neighbor = line_memberships(db,station,graph)
    options = [{"id":key,"name":name} for key,name in sorted(labels.items(),key=lambda pair:pair[1])]
    result = {"lines":[item["name"] for item in options],"options":options,"visits":[]}
    if not labels or not station["trains"]:
        return result

    shapes = {}
    point = None
    if station.get("mapPosition"):
        point = tile_coordinate(*station["mapPosition"])
    elif station.get("coordinateQuality") == "reference":
        point = map_point(station["lon"],station["lat"])
    geometry = {}
    if point and all(map_names.values()):
        tile_paths = local_geometry(point, {name for names in map_names.values() for name in names})
        geometry = {key:[path for name in names for path in tile_paths[name]]
                    for key,names in map_names.items()}
    tile_km = 40075 * math.cos(math.radians(station["mapPosition"][1] if station.get("mapPosition") else station["lat"])) / (2 ** ZOOM) if point else 0
    visits = []
    for row in db.execute("""
        SELECT visit.trip, visit.seq, t.shape, before.id AS previous, after.id AS following
        FROM times visit
        JOIN trips t ON visit.trip=t.id
        LEFT JOIN stops before ON before.id=(
            SELECT station FROM times WHERE trip=t.id AND seq<visit.seq ORDER BY seq DESC LIMIT 1)
        LEFT JOIN stops after ON after.id=(
            SELECT station FROM times WHERE trip=t.id AND seq>visit.seq ORDER BY seq LIMIT 1)
        WHERE visit.station=?
    """, (station["id"],)):
        shape = row["shape"]
        if shape not in shapes:
            blob = db.execute("SELECT path FROM shapes WHERE id=?", (shape,)).fetchone() if shape and geometry else None
            shapes[shape] = shape_sides(blob[0], point, tile_km, geometry) if blob else (None, None)

        def evidence(neighbor, matched):
            adjoining = by_neighbor(neighbor)
            if neighbor and (not adjoining or not matched or adjoining == matched):
                return adjoining or matched
            return None

        incoming, outgoing = shapes[shape]
        in_id = evidence(row["previous"],incoming)
        out_id = evidence(row["following"],outgoing)
        visits.append({
            "id": row["trip"], "sequence": row["seq"],
            "incomingId":in_id, "outgoingId":out_id,
            "incoming":labels.get(in_id), "outgoing":labels.get(out_id),
        })
    result["visits"] = visits
    return result
