#!/usr/bin/env python3
"""Build a searchable SQLite snapshot from the national railway GTFS feed."""
from collections import defaultdict
import csv
import gzip
import io
import itertools
import json
from pathlib import Path
import sqlite3
import zipfile

DATA = Path(__file__).resolve().parent / "data"


def rows(archive, path):
    with archive.open(path) as file:
        yield from csv.DictReader(io.TextIOWrapper(file, encoding="utf-8-sig"))


def insert(db, archive, name, fields, table=None):
    records = (tuple(row[field] for field in fields) for row in rows(archive, name + ".txt"))
    placeholders = ",".join("?" * len(fields))
    db.executemany(f"INSERT INTO {table or name} VALUES ({placeholders})", records)


def geometry(archive):
    for shape, points in itertools.groupby(rows(archive, "shapes.txt"), lambda row: row["shape_id"]):
        coords = [(int(p["shape_pt_sequence"]), round(float(p["shape_pt_lon"]), 5),
                   round(float(p["shape_pt_lat"]), 5)) for p in points]
        path = [(lon, lat) for _, lon, lat in sorted(coords)]
        path = [point for point, _ in itertools.groupby(path)]
        yield shape, gzip.compress(json.dumps(path, separators=(",", ":")).encode(), compresslevel=3)


def line_stations(lines):
    for line, stations in lines.items():
        for position, entry in enumerate(stations):
            # Some entries omit the telegraph code and shift the remaining fields.
            coded = entry[3].startswith("-")
            bureau, region, mileage = entry[5:8] if coded else entry[4:7]
            yield line, position, entry[1], entry[3][1:] if coded else None, bureau, region, mileage


def repair_overnight_arrivals(db):
    """Remove a duplicate day offset only when it restores local stop order."""
    day = 24 * 60 * 60

    def seconds(value):
        if not value:
            return None
        hours, minutes, seconds = map(int, value.split(':'))
        return hours * 3600 + minutes * 60 + seconds

    updates = []
    for trip, seq, arrival, departure, previous, following in db.execute("""
        SELECT trip,seq,arrival,departure,lag(departure) OVER calls,lead(arrival) OVER calls
        FROM times WINDOW calls AS (PARTITION BY trip ORDER BY seq)
    """):
        arrived, departed, previous, following = map(seconds, (arrival, departure, previous, following))
        if None in (arrived, departed, previous, following) or arrived <= departed:
            continue
        corrected = arrived - day
        if corrected - day <= previous < corrected <= departed < following:
            hours, remainder = divmod(corrected, 3600)
            minutes, second = divmod(remainder, 60)
            updates.append((f'{hours:02d}:{minutes:02d}:{second:02d}', trip, seq))
    db.executemany('UPDATE times SET arrival=? WHERE trip=? AND seq=?', updates)
    return len(updates)


def merge_station_aliases(db, source_aliases):
    # The publisher assigns coordinates by name. Several codes sharing those
    # coordinates only count as one physical station with independent evidence.
    from railway_graph import station_views
    identities = defaultdict(set)
    for path in (DATA/'cnrail-source/poi').glob('*.json'):
        for view in station_views(json.loads(path.read_text())):
            if view.get('name'):
                identities[view['name']].add((path.stem,view.get('tele_code')))
    db.execute("CREATE TEMP TABLE unique_source_station(name TEXT PRIMARY KEY,code TEXT)")
    db.executemany("INSERT INTO unique_source_station VALUES (?,?)",
                   ((name,next(iter(records))[1]) for name,records in identities.items() if len(records)==1))
    db.execute("CREATE TABLE station_aliases(alias TEXT PRIMARY KEY, station TEXT NOT NULL)")
    db.execute("""
        WITH identified AS (
            SELECT s.*, EXISTS(SELECT 1 FROM line_stations l
                WHERE l.name=s.name AND l.code=s.id) AS has_code FROM stops s
        ), groups_with_code AS (
            SELECT name, lat, lon, max(CASE WHEN has_code THEN id END) AS station
            FROM identified WHERE lat IS NOT NULL AND lon IS NOT NULL
            GROUP BY name, lat, lon HAVING count(*)>1 AND sum(has_code)=1
        )
        INSERT INTO station_aliases SELECT s.id,g.station FROM stops s
        JOIN groups_with_code g USING(name,lat,lon)
        JOIN unique_source_station u ON u.name=g.name AND u.code=g.station WHERE s.id<>g.station
    """)
    for alias, station in source_aliases:
        pair = db.execute("""
            SELECT a.id FROM stops a JOIN stops s ON a.name=s.name AND a.id<>s.id
            WHERE a.id=? AND s.id=? AND abs(a.lon-s.lon)<0.000001 AND abs(a.lat-s.lat)<0.000001
        """, (alias, station)).fetchone()
        current = db.execute("SELECT station FROM station_aliases WHERE alias=?", (alias,)).fetchone()
        if not pair or (current and current[0] != station):
            raise ValueError(f'Conflicting timetable station alias: {alias} to {station}')
        if not current:
            db.execute("INSERT INTO station_aliases VALUES (?,?)", (alias, station))
    if db.execute("SELECT 1 FROM station_aliases a JOIN station_aliases b ON a.station=b.alias").fetchone():
        raise ValueError('Timetable station alias chains need a single canonical station')
    db.execute("CREATE TABLE grouped_visits(trip TEXT,seq INTEGER,source_code TEXT,PRIMARY KEY(trip,seq))")
    db.execute("""INSERT INTO grouped_visits SELECT trip,seq,station FROM times
                  WHERE station IN (SELECT alias FROM station_aliases)""")
    db.execute("""UPDATE times SET station=(SELECT a.station FROM station_aliases a
                  WHERE a.alias=times.station) WHERE station IN (SELECT alias FROM station_aliases)""")
    db.execute("DELETE FROM stops WHERE id IN (SELECT alias FROM station_aliases)")


def main():
    target = DATA / "trains.sqlite"
    temp = target.with_suffix(".building")
    temp.unlink(missing_ok=True)
    with zipfile.ZipFile(DATA / "output_gtfs.zip") as archive, sqlite3.connect(temp) as db:
        db.executescript("""
            CREATE TABLE stops(id TEXT PRIMARY KEY, name TEXT, lat REAL, lon REAL);
            CREATE TABLE trips(id TEXT PRIMARY KEY, number TEXT COLLATE NOCASE, service TEXT, shape TEXT);
            CREATE TABLE inherited_shapes(trip TEXT PRIMARY KEY, donor TEXT NOT NULL);
            CREATE TABLE times(trip TEXT, station TEXT, seq INTEGER, arrival TEXT, departure TEXT,
                               PRIMARY KEY(trip, seq));
            CREATE TABLE dates(service TEXT, date TEXT, kind INTEGER, PRIMARY KEY(service, date));
            CREATE TABLE shapes(id TEXT PRIMARY KEY, path BLOB);
            CREATE TABLE stop_pinyin(id TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE line_stations(line TEXT, position INTEGER, name TEXT, code TEXT,
                                       bureau TEXT, region TEXT, mileage TEXT, PRIMARY KEY(line, position));
            CREATE TABLE metadata(json TEXT);
        """)
        insert(db, archive, "stops", ["stop_id", "stop_name", "stop_lat", "stop_lon"])
        insert(db, archive, "trips", ["trip_id", "trip_short_name", "service_id", "shape_id"])
        db.executemany("INSERT INTO inherited_shapes VALUES (?,?)", (
            (r["trip_id"], r["raillive_shape_donor"]) for r in rows(archive, "trips.txt")
            if r.get("raillive_shape_donor")
        ))
        insert(db, archive, "stop_times", ["trip_id", "stop_id", "stop_sequence",
                                         "arrival_time", "departure_time"], "times")
        corrected_arrivals = repair_overnight_arrivals(db)
        print("Corrected overnight arrivals:", corrected_arrivals)
        insert(db, archive, "calendar_dates", ["service_id", "date", "exception_type"], "dates")
        db.executemany("INSERT INTO shapes VALUES (?,?)", geometry(archive))
        db.executemany("INSERT INTO stop_pinyin VALUES (?,?)", (
            (row["record_id"], row["translation"]) for row in rows(archive, "translations.txt")
            if (row["table_name"], row["field_name"], row["language"]) == ("stops", "stop_name", "zh-Latn-pinyin")
        ))
        lines = DATA / "lines.json"
        if lines.is_file():
            db.executemany("INSERT INTO line_stations VALUES (?,?,?,?,?,?,?)",
                           line_stations(json.loads(lines.read_text())))
        db.executescript("""
            CREATE INDEX trips_number ON trips(number);
            CREATE INDEX stops_name ON stops(name);
            CREATE INDEX times_station ON times(station, trip, seq);
            CREATE INDEX line_station_code ON line_stations(code);
            ANALYZE;
        """)
        merge_station_aliases(db, ((r["stop_id"], r["raillive_same_station"])
                                   for r in rows(archive, "stops.txt") if r.get("raillive_same_station")))
        from shape_excursions import repair_shapes
        print("Corrected within-leg excursions:", repair_shapes(db))
        meta = next(rows(archive, "feed_info.txt"))
        meta["overnightArrivalsCorrected"] = corrected_arrivals
        for table in ("trips", "stops", "times", "shapes"):
            meta[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        meta["numbers"] = db.execute("SELECT count(DISTINCT number) FROM trips").fetchone()[0]
        meta["mappedTrips"] = db.execute("SELECT count(*) FROM trips JOIN shapes ON shape=shapes.id").fetchone()[0]
        db.execute("INSERT INTO metadata VALUES (?)", (json.dumps(meta, ensure_ascii=False),))
        print(json.dumps(meta, ensure_ascii=False))
    db.close()
    temp.replace(target)


if __name__ == "__main__":
    main()
