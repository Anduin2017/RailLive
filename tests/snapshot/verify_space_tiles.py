"""Exhaustively reconcile serialized spatial/time membership against the regional index."""
from bisect import bisect_right
from collections import Counter, defaultdict
from contextlib import closing
from itertools import product
from array import array
import gzip
import json
import math
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'build/app'))
from departure_paths import geographic
from region_live import INDEX, WINDOW, DAY, station_locations, timetable
from train_relations import unpack


def load(p):
    with gzip.open(p) as f: return json.load(f)


def verify(root):
    manifest=json.loads((root/'manifest.json').read_bytes())
    folder=root/manifest['version']
    catalog=load(folder/'catalog.gz')
    step=manifest['step']
    expected=defaultdict(set)
    runs, calendar, names=timetable()
    for id,(number,service,_,stops) in runs.items():
        assert catalog['runs'][id]==[number,service,stops[0][2],stops[-1][1],names[stops[-1][0]]]
    from datetime import date
    base=date.fromisoformat(catalog['base']).toordinal()
    assert {k:{v+base for v in a} for k,a in catalog['services'].items()}==calendar
    def add(t,w,s,e,n,key):
        for x,y in product(range(math.floor(w/step),math.floor(e/step)+1),range(math.floor(s/step),math.floor(n/step)+1)):
            expected[f'{x}_{y}',t//WINDOW].add(key)
    with closing(sqlite3.connect(INDEX)) as db:
        for t,trip,off,w,s,e,n in db.execute('SELECT * FROM windows'):
            add(t,w-.04,s-.025,e+.04,n+.025,(0,trip,off,-1))
        for t,off,trip,seq,w,s,e,n in db.execute('SELECT * FROM display_windows'):
            add(t,w,s,e,n,(1,trip,off,seq))
    for trip,(_,_,_,stops) in runs.items():
        for seq,(code,a,d) in enumerate(stops):
            if seq==len(stops)-1: d=a+240
            mapped,gtfs=station_locations()[code]
            x,y=geographic(mapped) if mapped else gtfs
            for t in range((a if seq else d-120)//WINDOW*WINDOW,d,WINDOW):
                if seq and d<=a: continue
                add(t%DAY,x,y,x,y,(2,trip,t//DAY,seq))
    counts=Counter()
    blocks={}
    endpoints={}
    for file in (folder/'g').glob('*.gz'):
        packed=array('i')
        packed.frombytes(gzip.decompress(file.read_bytes()))
        if sys.byteorder!='little': packed.byteswap()
        vertices=range(packed[0])
        cursor=1+2*packed[0]
        while cursor<len(packed):
            key,size=packed[cursor:cursor+2]
            assert str(key) not in blocks and int(file.stem)==catalog['homes'][key]
            block=packed[cursor+2:cursor+2+size]
            assert len(block)>=4 and len(block)%2==0
            assert all(0<=i<len(vertices) for i in block[::2])
            assert all(a<=b for a,b in zip(block[1::2],block[3::2]))
            endpoints[key]=tuple(tuple(packed[1+2*i:3+2*i]) for i in (block[0],block[-2]))
            blocks[str(key)]=block
            cursor+=2+size
        assert cursor==len(packed)
    for cell,bits in catalog['cells'].items():
        listed=int.from_bytes(bytes.fromhex(bits),'little')
        for slot in range(DAY//WINDOW):
            if not listed>>slot&1: continue
            rows=load(folder/cell/f'{slot}.gz')
            keys={(r[0],r[1],r[2],r[3] if r[0] else -1) for r in rows}
            assert len(keys)==len(rows),(cell,slot,'duplicate')
            assert keys==expected.pop((cell,slot)),(cell,slot,'membership')
            for row in rows:
                if row[0]==2:
                    calls=runs[row[1]][3]
                    terminal=row[3]==len(calls)-1
                    assert (row[5:]==['terminal'])==terminal
                    if terminal:
                        code,arrival,_=calls[-1]
                        assert row[4][1:4]==[arrival,arrival+240,code]
                    continue
                refs=row[4 if row[0]==0 else 8]
                assert refs and all(str(i) in blocks for i,_ in refs)
                assert all(endpoints[i][1]==endpoints[j][0] for (i,_),(j,_) in zip(refs,refs[1:])),(cell,slot,'disconnected track')
                assert all(abs(a+blocks[str(i)][-1]/100-b)<.021 for (i,a),(_,b) in zip(refs,refs[1:])),(cell,slot,row[:3],'geometry discontinuity')
            counts['records']+=len(rows)
            counts['tiles']+=1
    assert not expected,('missing cells or time slices',len(expected))
    print(dict(counts),flush=True)


if __name__=='__main__':
    verify(Path(sys.argv[1]) if len(sys.argv)>1 else INDEX.parents[1]/'space-time')
