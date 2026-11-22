"""Pin and compare the coordinate input used by the selected GTFS release."""
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

DATA = Path(__file__).resolve().parent/'data'
REFERENCE = DATA/'gtfs-coordinates.csv'
MANIFEST = DATA/'gtfs-coordinates.reference.json'


def source_info():
    release = json.loads((DATA/'gtfs-release.json').read_text())
    return release['tag_name']


def acquire():
    tag = source_info()
    if MANIFEST.exists() and REFERENCE.exists():
        meta = json.loads(MANIFEST.read_text())
        if meta['tag']==tag and hashlib.sha256(REFERENCE.read_bytes()).hexdigest()==meta['sha256']:
            return
    base = 'https://api.github.com/repos/wensimehrp/chinese-railway-gtfs/contents/coordinates.csv'
    with urlopen(base+'?ref='+quote(tag,safe=''),timeout=20) as response:
        record = json.load(response)
    with urlopen(record['download_url'],timeout=20) as response:
        content = response.read()
    blob = hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
    if blob!=record['sha']:
        raise ValueError('Coordinate file does not match the published Git blob')
    meta = dict(tag=tag,url=record['download_url'],gitBlob=blob,sha256=hashlib.sha256(content).hexdigest())
    for path,value in [(REFERENCE,content),(MANIFEST,json.dumps(meta,indent=2).encode())]:
        tmp = path.with_suffix('.part')
        tmp.write_bytes(value)
        tmp.replace(path)


def classify(db):
    meta = json.loads(MANIFEST.read_text())
    if meta['tag']!=source_info() or hashlib.sha256(REFERENCE.read_bytes()).hexdigest()!=meta['sha256']:
        raise ValueError('Refresh the matching GTFS coordinate reference with gtfs_coordinates.py')
    by_name = defaultdict(set)
    with REFERENCE.open(encoding='utf-8-sig',newline='') as file:
        for point in csv.DictReader(file):
            by_name[point['station_name']].add((float(point['lon']),float(point['lat'])))
    quality = {}
    for code,name,lon,lat in db.execute('SELECT id,name,lon,lat FROM stops'):
        candidates = by_name.get(name)
        extra = meta.get('supplementalStations', {}).get(code)
        if extra and extra['name'] == name:
            candidates = {(extra['lon'], extra['lat'])}
        if not isinstance(lon,(int,float)) or not isinstance(lat,(int,float)):
            status = 'missing'
        elif not candidates:
            status = 'interpolated'
        elif len(candidates)>1:
            status = 'ambiguous'
        elif all(abs(a-b)<=0.000001 for a,b in zip((lon,lat),next(iter(candidates)))):
            status = 'reference'
        else:
            status = 'mismatch'
        quality[code] = status
    return quality,meta


if __name__=='__main__':
    acquire()
