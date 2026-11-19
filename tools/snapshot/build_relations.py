"""Build the versioned, offline indexes for same-direction train comparisons."""
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import zlib

from infer_routes import infer_routes
from server import DB, ROOT, GRAPH, motion_lines, annotate_position


def pack(value):
    return zlib.compress(json.dumps(value,separators=(',',':')).encode(),1)


def fingerprint():
    h=hashlib.sha256()
    for path in [DB, ROOT/'data/cnrail-graph.json.gz', ROOT/'data/line-facts.json', ROOT/'server.py',
                 ROOT/'train-motion.js', ROOT/'relation-profile.cjs', ROOT/'infer_routes.py', Path(__file__)]:
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def main():
    src=sqlite3.connect(DB); src.row_factory=sqlite3.Row
    target=DB.with_name('relations.sqlite')
    tmp=target.with_suffix('.building')
    tmp.unlink(missing_ok=True)
    out=sqlite3.connect(tmp)
    out.executescript("""
      CREATE TABLE meta(signature TEXT);
      CREATE TABLE inferred(trip TEXT PRIMARY KEY, path BLOB, legs BLOB);
      CREATE TABLE blocks(shape TEXT, block INTEGER, start REAL, end REAL);
      CREATE TABLE leg_metrics(trip TEXT, seq INTEGER, metres REAL, peak_kmh REAL, PRIMARY KEY(trip,seq));
      CREATE TABLE profiles(trip TEXT PRIMARY KEY, knots BLOB, anchors BLOB);
    """)
    def geometries():
        for row in src.execute('SELECT id,path FROM shapes'):
            yield row[0],[tuple(p) for p in json.loads(gzip.decompress(row[1]))]
    # Split at graph junctions and shape endpoints. A block ID describes the
    # entire ordered vertex sequence, so opposite tracks/directions cannot match.
    vertices={}
    neighbors={}
    ends=set()
    def ids(points):
        return [vertices.setdefault(p,len(vertices)) for p in points]
    for _,points in geometries():
        path=ids(points)
        if not path: continue
        ends.update((path[0],path[-1]))
        for a,b in zip(path,path[1:]):
            neighbors.setdefault(a,set()).add(b)
            neighbors.setdefault(b,set()).add(a)
    ends.update(k for k,v in neighbors.items() if len(v)!=2)
    blocks={}
    for shape,points in geometries():
        path=ids(points); start=0; distance=last=0
        for i,((x,y),(u,v)) in enumerate(zip(points,points[1:]),1):
            distance+=math.pi*6371008.8/180*math.hypot((u-x)*math.cos((v+y)*math.pi/360),v-y)
            if path[i] in ends or (i+1<len(path) and path[i+1]==path[i-1]):
                block=tuple(path[start:i+1])
                key=blocks.setdefault(block,len(blocks))
                out.execute("INSERT INTO blocks VALUES(?,?,?,?)",(shape,key,last,distance))
                start=i; last=distance
    out.execute('CREATE INDEX by_shape ON blocks(shape,start)')
    out.execute('CREATE INDEX by_block ON blocks(block)')
    out.commit()
    print('Indexed directed track blocks',len(blocks),flush=True)
    worker=subprocess.Popen(['node',str(ROOT/'relation-profile.cjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        def profile(trip,number,geometry,legs=None):
            stops=[dict(r) for r in src.execute('SELECT s.*,arrival,departure FROM times JOIN stops s ON station=s.id WHERE trip=? ORDER BY seq',(trip,))]
            for stop in stops: annotate_position(stop,stop['id'])
            if len(geometry)<2 or len(stops)<2: return
            record=dict(number=number,geometry=geometry,inferredLegs=legs,stops=stops,motionLines=motion_lines(src,trip))
            worker.stdin.write(json.dumps(record,separators=(',',':'))+'\n'); worker.stdin.flush()
            knots,anchors,metrics=json.loads(worker.stdout.readline())
            out.executemany('INSERT INTO leg_metrics VALUES(?,?,?,?)', ((trip,*leg) for leg in metrics))
            out.execute('INSERT INTO profiles VALUES(?,?,?)',(trip,pack(knots),pack(anchors)))
        for idx,(trip,number,blob) in enumerate(src.execute('SELECT t.id,number,path FROM trips t JOIN shapes ON shapes.id=t.shape')):
            profile(trip,number,json.loads(gzip.decompress(blob)))
            if idx%2000==0: print('profiles',idx,flush=True)
        for trip,number,geometry,legs in infer_routes(src,out):
            out.execute('INSERT INTO inferred VALUES(?,?,?)',(trip,pack(geometry),pack(legs)))
            profile(trip,number,geometry,legs)
        worker.stdin.close()
        if worker.wait(): raise RuntimeError('trajectory build failed')
    finally:
        worker.kill()
        worker.wait()
    out.execute('INSERT INTO meta VALUES(?)',(fingerprint(),)); out.commit(); out.close()
    tmp.replace(target)


if __name__=='__main__': main()
