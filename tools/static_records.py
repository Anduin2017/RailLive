"""Compile the application's read-only detail views into portable CDN records."""
from contextlib import closing
from datetime import date
from functools import lru_cache
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from server import (DB, GRAPH, Handler, RAIL_SELECTION, LINE_FACTS, CNRAIL_LINES,
                    TRIP_SUMMARY, annotate_position, query_catalog)
from train_relations import TRACKS, overview, timetable
from region_live import trip_display
from station_statistics import station_line_attribution


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


@lru_cache(maxsize=1)
def connection():
    db = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode(value))


def blob(folder, value):
    raw = encode(value)
    key = hashlib.sha256(raw).hexdigest()
    path = folder / 'objects' / key[:2] / (key[2:] + '.gz')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.' + str(os.getpid()) + '.tmp')
        temp.write_bytes(gzip.compress(raw, compresslevel=3, mtime=0))
        temp.replace(path)
    return key


def query(path, params=None):
    return Handler.query(connection(), '/api/' + path, params or {})


def catalogs():
    result = {}
    for kind in ('lines', 'stations', 'trains'):
        first = query_catalog(kind, {})
        rows = first['rows'][:]
        for i in range(2, first['pages'] + 1):
            rows.extend(query_catalog(kind, {'page': [str(i)]})['rows'])
        result[kind] = sorted(rows, key=lambda row: (row['name'], row['id']))
    return result


def inventory(folder):
    db = connection()
    catalog = catalogs()
    records = {}
    for kind, rows in catalog.items():
        records['catalog/' + kind] = blob(folder, rows)
    stops = []
    for row in db.execute('SELECT * FROM stops'):
        stop = dict(row)
        annotate_position(stop, stop['id'])
        stops.append(stop)
    records['search-trains'] = blob(folder, [dict(row) for row in db.execute(TRIP_SUMMARY + ' ORDER BY t.id')])
    records['search-stations'] = blob(folder, stops + GRAPH.searchable)
    records['info'] = blob(folder, query('info'))
    runs, calendar, _ = timetable()
    records['services'] = blob(folder, {
        'trips': {t: r[1] for t, r in runs.items()},
        'days': {s: sorted(date.fromordinal(d).isoformat() for d in days) for s, days in calendar.items()},
    })
    for kind, source in [('feature', GRAPH.feature_records), ('poi', GRAPH.raw_pois)]:
        for key, value in source.items():
            write_json(folder / 'source' / kind / (key + '.json'), value)
    write_json(folder / 'source/coverage.json', query('source/coverage'))
    keys = {r['id'] for r in catalog['stations']} | set(GRAPH.pois)
    keys.update(r[0] for r in db.execute('SELECT alias FROM station_aliases'))
    jobs = [('station', key) for key in sorted(keys)]
    names = set(GRAPH.features) | set(GRAPH.eid_for) | set(GRAPH.line_names) | set(LINE_FACTS) | set(CNRAIL_LINES)
    names.update(r['id'] for r in catalog['lines'])
    gtfs = {r[0] for r in db.execute('SELECT DISTINCT line FROM line_stations')}
    jobs.extend(('line', key) for key in sorted(names | gtfs | set(RAIL_SELECTION)))
    jobs.extend(('map-line', key) for key in sorted(RAIL_SELECTION))
    jobs.extend(('gtfs-line', key) for key in sorted(gtfs))
    jobs.extend(('trip', key) for key in sorted(runs, key=lambda t: (runs[t][2], t)))
    return records, jobs


def compile_one(task):
    folder, kind, key = task
    folder = Path(folder)
    def record(kind, value):
        return kind, key, blob(folder, value)
    if kind == 'trip':
        with closing(sqlite3.connect(TRACKS.as_uri() + '?mode=ro', uri=True)) as db:
            peers = overview(db, key, None)
        return [record('trip', query('trip/' + key)), record('display', trip_display(key)),
                record('relations', peers)]
    if kind == 'station':
        station = query('station/' + key)
        if not station:
            return []
        result = [record(kind, station)]
        if station['id'] == key:
            result.append(record('station-lines', station_line_attribution(connection(), station, GRAPH)))
        return result
    origin = {'line': '', 'map-line': 'map', 'gtfs-line': 'gtfs'}[kind]
    value = query('line/' + key, {'from': [origin]})
    return [record(kind, value)] if value else []
