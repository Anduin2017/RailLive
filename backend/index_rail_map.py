#!/usr/bin/env python3
"""Index line extents and station positions in the offline vector tiles."""
import collections
import gzip
import json
import math
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
ZOOM = 8
SCALE = 1 << ZOOM
ROUTE_TAILS = {'京沪高速线': 'G25'}


def varint(data, offset):
    value = 0
    shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
        shift += 7


def fields(data):
    offset = 0
    while offset < len(data):
        key, offset = varint(data, offset)
        wire = key & 7
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire == 2:
            size, offset = varint(data, offset)
            value = data[offset:offset + size]
            offset += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value = data[offset:offset + size]
            offset += size
        else:
            raise ValueError(f"Unsupported vector tile wire type {wire}")
        yield key >> 3, value


def packed(data):
    offset = 0
    while offset < len(data):
        value, offset = varint(data, offset)
        yield value


def coordinate(x, y):
    return (x / SCALE * 360 - 180,
            math.degrees(math.atan(math.sinh((1 - 2 * y / SCALE) * math.pi))))


def tile_coordinate(lon, lat):
    return ((lon + 180) / 360 * SCALE,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * SCALE)


def tile_paths(data, tile_x, tile_y, extent):
    commands = iter(packed(data))
    x = y = 0
    path = []
    for command in commands:
        kind = command & 7
        if kind not in (1, 2):
            raise ValueError(f"Unexpected command for point or line {kind}")
        for _ in range(command >> 3):
            dx, dy = next(commands), next(commands)
            x += (dx >> 1) ^ -(dx & 1)
            y += (dy >> 1) ^ -(dy & 1)
            if kind == 1 and path:
                yield path
                path = []
            path.append((tile_x + x / extent, tile_y + y / extent))
    if path:
        yield path


def _named_features(path, with_type=False, include_unnamed_rail=False):
    tile_x, tile_y = int(path.parent.name), int(path.stem)
    for tag, data in fields(path.read_bytes()):
        if tag != 3:
            continue
        layer = collections.defaultdict(list)
        for key, value in fields(data):
            layer[key].append(value)
        kind = layer[1][0]
        if kind not in (b'rail', b'railpoi0', b'railpoi1'):
            continue
        keys = layer[3]
        if b'name:zh' not in keys and not (include_unnamed_rail and kind == b'rail'):
            continue
        name_key = keys.index(b'name:zh') if b'name:zh' in keys else None
        type_key = keys.index(b'type') if with_type and kind == b'rail' and b'type' in keys else None
        values = [next((value for key, value in fields(blob) if key == 1), b'')
                  for blob in layer[4]]
        extent = layer[5][0]
        for feature in layer[2]:
            attrs = dict(fields(feature))
            tags = iter(packed(attrs.get(2, b'')))
            name, feature_type = b'', b''
            for key, index in zip(tags, tags):
                if key == name_key:
                    name = values[index]
                elif key == type_key:
                    feature_type = values[index]
            if name or (include_unnamed_rail and kind == b'rail'):
                yield kind, name.decode(), list(tile_paths(attrs[4], tile_x, tile_y, extent)), feature_type.decode()


def named_features(path):
    for kind, name, paths, _ in _named_features(path):
        yield kind, name, paths


def station_key(name):
    return name.removesuffix('线路所') + '所' if name.endswith('线路所') else name


def distance_squared(point, path):
    x, y = point
    best = math.inf
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        fraction = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / length)) if length else 0
        best = min(best, (x - ax - fraction * dx) ** 2 + (y - ay - fraction * dy) ** 2)
    return best


def distance_to_tiles(point, tiles):
    x, y = point
    segments = (path for a in range(math.floor(x) - 1, math.floor(x) + 2)
                for b in range(math.floor(y) - 1, math.floor(y) + 2)
                for path in tiles.get((a, b), ()))
    return min((distance_squared(point, path) for path in segments), default=math.inf)


def route_tail(train, tiles):
    with sqlite3.connect(ROOT / 'data/trains.sqlite') as db:
        row = db.execute("SELECT path FROM shapes JOIN trips ON shape=shapes.id WHERE number=?", (train,)).fetchone()
    if not row:
        return []
    coords = [
        [1.015972842 * lon - 0.000070497 * lat - 5.116568470,
         0.000009795 * lon + 1.012049241 * lat + 1.113596968]
        for lon, lat in json.loads(gzip.decompress(row[0]))
    ]
    for index in range(len(coords) - 1, -1, -1):
        if distance_to_tiles(tile_coordinate(*coords[index]), tiles) < 0.004 ** 2:
            return coords[index:] if index < len(coords) - 1 else []
    return []


def main():
    local = json.loads((ROOT / 'data/lines.json').read_text())
    original = json.loads((ROOT / 'data/cnrail-lines.json').read_text())
    extra_file = ROOT / 'map/rail-extra-points.json'
    extra = json.loads(extra_file.read_text()) if extra_file.is_file() else {}
    wanted = collections.defaultdict(set)
    for line, points in local.items():
        wanted[line].update(point[1] for point in points)
    for line, source in original.items():
        wanted[line].update(point['name'] for point in source['stations'])
    facts = json.loads((ROOT / 'data/line-facts.json').read_text())
    for line, info in facts.items():
        for name in info.get('sections', {}).values():
            wanted[line].update(point[1] for point in local.get(name, ()))
    stations = collections.defaultdict(set)
    paths = collections.defaultdict(lambda: collections.defaultdict(list))
    rail_types = collections.defaultdict(set)
    for tile in sorted((ROOT / 'map/tiles' / str(ZOOM)).glob('*/*.pbf')):
        tile_key = (int(tile.parent.name), int(tile.stem))
        for kind, name, feature_paths, feature_type in _named_features(tile, with_type=True):
            if kind == b'rail':
                paths[name][tile_key].extend(feature_paths)
                rail_types[name].add(feature_type)
            else:
                stations[station_key(name)].update(path[0] for path in feature_paths)

    index = {}
    for name, tiles in paths.items():
        points = [point for paths_in_tile in tiles.values() for path in paths_in_tile for point in path]
        west, north = coordinate(min(x for x, y in points), min(y for x, y in points))
        east, south = coordinate(max(x for x, y in points), max(y for x, y in points))
        entry = {'bounds': [[west, south], [east, north]], 'stations': {},
                 'railTypes': sorted(rail_types[name])}
        extra_stations = extra.get(name, {})
        for station in sorted(wanted[name]):
            if station in extra_stations:
                entry['stations'][station] = extra_stations[station]
                continue
            distances = []
            for x, y in sorted(stations[station_key(station)]):
                best = distance_to_tiles((x, y), tiles)
                # Only use a map pin if the named point is near this line.
                if best < 0.012 ** 2:
                    distances.append((best, x, y))
            if distances:
                _, x, y = min(distances)
                entry['stations'][station] = coordinate(x, y)
        if name in ROUTE_TAILS:
            train = ROUTE_TAILS[name]
            tail = route_tail(train, tiles)
            if tail:
                entry['supplement'] = {'train': train, 'coordinates': tail}
        index[name] = entry
    tracks = {name: [[[round(v, 6) for v in coordinate(*p)] for p in path]
                     for group in tiles.values() for path in group if len(path) >= 2]
              for name, tiles in paths.items()}
    details = ROOT / 'map/rail-detail.geojson'
    if details.is_file():
        coordinates = collections.defaultdict(list)
        detail_stations = collections.defaultdict(dict)
        for feature in json.loads(details.read_text())['features']:
            name = feature['properties']['rail']
            shape = feature['geometry']
            if shape['type'] == 'LineString':
                coordinates[name].extend(shape['coordinates'])
                tracks.setdefault(name, []).append(shape['coordinates'])
            elif shape['type'] == 'Point':
                detail_stations[name][feature['properties']['name']] = shape['coordinates']
        for name, points in coordinates.items():
            entry = index.setdefault(name, {'stations': {}})
            corners = points + [p for p in entry.get('bounds', ())]
            longitudes, latitudes = zip(*corners)
            entry['bounds'] = [[min(longitudes), min(latitudes)], [max(longitudes), max(latitudes)]]
            entry['stations'].update(detail_stations[name])
            entry['detail'] = True
    (ROOT/'map/rail-tracks.json.gz').write_bytes(gzip.compress(
        json.dumps(tracks, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
    out = ROOT / 'map/rail-selection.json'
    out.write_text(json.dumps(index, ensure_ascii=False, separators=(',', ':')) + '\n')
    print(f"Indexed {len(index)} lines, {sum(len(x['stations']) for x in index.values())} station positions")


if __name__ == '__main__':
    main()
