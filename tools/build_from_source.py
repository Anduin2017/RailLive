"""Generate a complete static release from a pinned set of original feeds."""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from prepare import ROOT, prepare
from snapshot_layout import runtime_files

SOURCE_FILES = (
    'data/output_gtfs.zip', 'data/lines.json', 'data/GTFS-LICENSE.txt',
    'data/cnrail-source', 'data/cnrail-lines.json', 'data/gtfs-release.json',
    'data/gtfs-coordinates.csv', 'data/gtfs-coordinates.reference.json',
    'map/offline-style.json', 'map/coverage.json', 'map/tiles', 'map/fonts',
    'map/sprite.png', 'map/sprite.json', 'map/sprite@2x.png', 'map/sprite@2x.json',
    'map/rail-detail.geojson', 'map/rail-extra-points.json',
)
STAGES = (
    'import_gtfs', 'index_rail_map', 'build_cnrail', 'audit_cnrail',
    'build_basemap_tiles', 'build_relations', 'region_live', 'station_symbols',
    'space_tiles', 'audit_live',
)


def apply_stop_time_corrections(feed, changes):
    if not changes:
        return
    patches = {}
    for patch in changes:
        key = patch['trip_id'], str(patch['stop_sequence'])
        before, after = patch['before'], patch['after']
        if (key in patches or not patch['source'].strip()
                or len(before) != 2 or len(after) != 2
                or not all(re.fullmatch(r'\d{2,}:[0-5]\d:[0-5]\d', t) for t in [*before, *after])):
            raise ValueError(f'Invalid or duplicate stop-time correction for {key}')
        patches[key] = patch

    target = feed.with_name('corrected-gtfs.zip')
    columns = ['arrival_time', 'departure_time']
    affected = {key[0] for key in patches}
    ordered = {trip: [] for trip in affected}

    def seconds(t):
        h, m, s = map(int, t.split(':'))
        return h * 3600 + m * 60 + s

    with zipfile.ZipFile(feed) as original, zipfile.ZipFile(target, 'w') as corrected:
        for entry in original.infolist():
            if entry.filename != 'stop_times.txt':
                with original.open(entry) as src, corrected.open(entry, 'w') as dst:
                    shutil.copyfileobj(src, dst)
                continue
            with original.open(entry) as raw, corrected.open(entry, 'w') as output:
                rows = csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig', newline=''))
                with io.TextIOWrapper(output, encoding='utf-8', newline='') as text:
                    writer = csv.DictWriter(text, rows.fieldnames)
                    writer.writeheader()
                    for row in rows:
                        key = row['trip_id'], row['stop_sequence']
                        if key in patches:
                            patch = patches.pop(key)
                            if row['stop_id'] != patch['stop_id'] or [row[f] for f in columns] != patch['before']:
                                raise ValueError(f'Stale stop-time correction for {key}')
                            row.update(zip(columns, patch['after']))
                        if key[0] in affected:
                            ordered[key[0]].append((int(key[1]), row['stop_id'], *(seconds(row[f]) for f in columns)))
                        writer.writerow(row)
    if patches:
        raise ValueError(f'Unmatched stop-time corrections: {list(patches)}')
    for trip, calls in ordered.items():
        previous_stop, previous_departure = None, -1
        for _, stop, arrival, departure in sorted(calls):
            if departure < arrival or arrival < previous_departure or (arrival == previous_departure and stop != previous_stop):
                raise ValueError(f'Invalid chronological order after correcting {trip}')
            previous_stop, previous_departure = stop, departure
    target.replace(feed)


def build(source, snapshot, site):
    if snapshot.exists() or site.exists():
        raise ValueError('Choose fresh paths for --snapshot and --output.')
    if source == snapshot or source in snapshot.parents or source == site or source in site.parents:
        raise ValueError('Keep generated directories outside the input directory.')
    missing = [name for name in SOURCE_FILES if not (source / name).exists()]
    if missing:
        raise ValueError('Missing original sources: ' + ', '.join(missing))
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.raillive-source-', dir=snapshot.parent) as scratch:
        temp = Path(scratch)
        staging = temp / 'snapshot'
        app = temp / 'app'
        for name in SOURCE_FILES:
            src, dst = source / name, staging / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        shutil.copy2(ROOT / 'inputs/line-facts.json', staging / 'data/line-facts.json')
        apply_stop_time_corrections(staging / 'data/output_gtfs.zip',
                                    json.loads((ROOT / 'inputs/stop-times.json').read_text()))
        (staging / 'space-time').mkdir()
        prepare(app, staging)
        env = dict(os.environ, PYTHONHASHSEED='0', PYTHONUNBUFFERED='1')
        for stage in STAGES:
            print(f'Rebuilding {stage}', flush=True)
            subprocess.run([sys.executable, str(app / (stage + '.py'))], check=True, env=env)
        runtime_files(staging)
        staging.rename(snapshot)
    subprocess.run([sys.executable, str(ROOT / 'tools/build_static.py'),
                    '--snapshot', str(snapshot), '--output', str(site)], check=True, env=env)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'inputs/raw')
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'build/snapshot')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/site')
    args = parser.parse_args()
    build(args.source.resolve(), args.snapshot.resolve(), args.output.resolve())
