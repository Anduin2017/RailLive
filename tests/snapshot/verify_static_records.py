"""Verify CDN lookup coverage and the bytes of every compiled detail object."""
from collections import defaultdict
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'build/app'))
from server import DB, GRAPH


def verify(site):
    root = site / 'records'
    manifest = json.loads((root / 'index.json').read_bytes())
    keys = defaultdict(set)
    all_objects = set(manifest.values())

    def path(digest):
        return root / 'objects' / digest[:2] / (digest[2:] + '.gz')

    for name, digest in manifest.items():
        if not name.startswith('index/'):
            continue
        _, kind, bucket = name.split('/')
        values = json.loads(gzip.decompress(path(digest).read_bytes()))
        assert all(sum(k.encode()) % 64 == int(bucket) for k in values), name
        keys[kind].update(values)
        all_objects.update(values.values())
    for digest in all_objects:
        assert hashlib.sha256(gzip.decompress(path(digest).read_bytes())).hexdigest() == digest, digest

    with closing(sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True)) as db:
        trips = {t for t, in db.execute('SELECT id FROM trips')}
        stations = {s for s, in db.execute('SELECT id FROM stops')}
        aliases = {s for s, in db.execute('SELECT alias FROM station_aliases')}
    assert trips == keys['trip'] == keys['relations'] == keys['display']
    assert stations | aliases | GRAPH.pois.keys() <= keys['station']
    assert stations <= keys['station-lines']
    line_rows = json.loads(gzip.decompress(path(manifest['catalog/lines']).read_bytes()))
    assert {r['id'] for r in line_rows} <= keys['line']
    for directory, ids in [('poi', GRAPH.raw_pois), ('feature', GRAPH.feature_records)]:
        assert set(ids) == {p.stem for p in (root / 'source' / directory).glob('*.json')}
    assert not any(p.is_symlink() or p.suffix in ('.sqlite','.py') for p in site.rglob('*'))
    print(f'{len(trips):,} trips, {len(keys["station"]):,} station IDs and aliases, '
          f'{len(line_rows):,} line entries; {len(all_objects):,} hashed objects verified.')


if __name__ == '__main__':
    verify(Path(sys.argv[1]))
