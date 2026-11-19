"""Compile rail motion into date-independent five-minute static tiles."""
import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
from contextlib import closing
from datetime import date
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from array import array
import sys
from tempfile import TemporaryDirectory
import time

from departure_paths import geographic
from line_live import departure_anchors, indexed_profile, label_stops
from region_live import INDEX, DAY, WINDOW, board_windows, slice_window, station_locations, timetable, shape_path, unpack

ROOT = Path(__file__).resolve().parent
DELTA = (118, 29, 123, 33)


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode()


def write_gzip(path, value):
    path.write_bytes(gzip.compress(encode(value), compresslevel=4, mtime=0))


class Compiler:
    def __init__(self, directory, db, step, bounds):
        self.directory, self.db, self.step, self.bounds = directory, db, step, bounds
        self.blocks = []
        self.block_ids = {}
        self.used = defaultdict(set)
        db.execute('CREATE TABLE rows(cell TEXT,slot INTEGER,row BLOB)')

    def cells(self, box):
        w,s,e,n = box
        if self.bounds:
            x,y,u,v = self.bounds
            w,s,e,n=max(w,x),max(s,y),min(e,u),min(n,v)
            if w>e or s>n: return
        for x in range(math.floor(w/self.step), math.floor(e/self.step)+1):
            for y in range(math.floor(s/self.step), math.floor(n/self.step)+1):
                yield f'{x}_{y}'

    def axis(self, distances, coords):
        # Consecutive chunks share their boundary vertex. Distances relative to
        # the first vertex permit reuse on shapes with different mileage origins.
        refs = []
        ends = []
        i=0
        for j in range(1,len(coords)):
            x,y=coords[j]
            # Use a geographic landmark to cut identical stretches consistently
            # even when different trips enter the stretch at different offsets.
            landmark=(round(x*1e5)*73856093 ^ round(y*1e5)*19349663) % 17 == 0
            if not landmark and j-i<64 and j<len(coords)-1:
                continue
            base = distances[i]
            block = tuple((round((d-base)*100),round(p[0]*1e7),round(p[1]*1e7))
                          for d,p in zip(distances[i:j+1],coords[i:j+1]))
            key = self.block_ids.get(block)
            if key is None:
                key=len(self.blocks)
                self.block_ids[block]=key
                self.blocks.append(block)
            refs.append((key,round(base,2)))
            ends.append(j)
            i=j
        return ends,refs

    def row(self, cells, t, record, refs=()):
        data=encode(record)
        for cell in cells:
            self.db.execute('INSERT INTO rows VALUES(?,?,?)',(cell,t%DAY//WINDOW,data))
            self.used[cell].update(key for key,_ in refs)

    def finish(self):
        self.db.execute('CREATE INDEX bycell ON rows(cell,slot)')
        occupancy={}
        homes=[-1]*len(self.blocks)
        groups=defaultdict(list)
        for key in sorted(set().union(*self.used.values())):
            block=self.blocks[key]
            _,x,y=block[len(block)//2]
            home=math.floor(x/(1e7*self.step))*1024+math.floor(y/(1e7*self.step))
            homes[key]=home
            groups[home].append(key)
        geometry=self.directory/'g'
        geometry.mkdir()
        for home,keys in groups.items():
            vertices={}
            blocks={}
            for key in keys:
                block=[]
                for distance,x,y in self.blocks[key]:
                    idx=vertices.setdefault((x,y),len(vertices))
                    block.extend((idx,distance))
                blocks[key]=block
            # Shared geographic packs can be mapped directly by the browser.
            packed=array('i',[len(vertices)])
            for xy in vertices: packed.extend(xy)
            for key,block in blocks.items():
                packed.extend((key,len(block)))
                packed.extend(block)
            if packed.itemsize!=4: raise RuntimeError('32-bit integer arrays required')
            if sys.byteorder!='little': packed.byteswap()
            (geometry/f'{home}.gz').write_bytes(gzip.compress(packed.tobytes(),compresslevel=4,mtime=0))
        for cell in sorted(self.used):
            folder=self.directory/cell
            folder.mkdir()
            slots=[]; records=[]
            for slot,data in self.db.execute('SELECT slot,row FROM rows WHERE cell=? ORDER BY slot',(cell,)):
                if not slots or slots[-1]!=slot:
                    if records: write_gzip(folder/f'{slots[-1]}.gz',records)
                    slots.append(slot)
                    records=[]
                records.append(json.loads(data))
            if records: write_gzip(folder/f'{slots[-1]}.gz',records)
            bits=sum(1<<slot for slot in slots)
            occupancy[cell]=bits.to_bytes(36,'little').hex()
        return occupancy, homes


def build(output,step,bounds):
    start=time.monotonic()
    output.mkdir(parents=True,exist_ok=True)
    runs,calendar,names=timetable()
    locations=station_locations()
    @lru_cache(maxsize=1)
    def version():
        h=hashlib.sha256()
        inputs = ('data/relations.sqlite','data/trains.sqlite','data/cnrail-graph.json.gz',
                  'space_tiles.py','region_live.py','line_live.py','departure_paths.py',
                  'railway_graph.py','train_relations.py')
        for f in (INDEX, *(ROOT/name for name in inputs)):
            with f.open('rb') as stream: h.update(hashlib.file_digest(stream,'sha256').digest())
        h.update(encode([step,bounds]))
        return h.hexdigest()[:20]
    with TemporaryDirectory(dir=output) as temp, closing(sqlite3.connect(INDEX)) as source, closing(sqlite3.connect('')) as scratch:
        folder=Path(temp)/version()
        folder.mkdir()
        compiler=Compiler(folder,scratch,step,bounds)
        # Read the spatial index in trip order, keeping the current geometry hot.
        sql='SELECT trip,offset,bucket,west,south,east,north FROM windows ORDER BY trip,offset,bucket'
        from itertools import groupby
        @lru_cache(maxsize=32)
        def indexed_axis(shape):
            ds,coords=shape_path(shape)
            return ds,coords,compiler.axis(ds,coords)
        for trip,rows in groupby(source.execute(sql),lambda row:row[0]):
            num,service,shape,stops=runs[trip]
            axis=None
            for _,offset,bucket,w,s,e,n in rows:
                cells=list(compiler.cells((w-.04,s-.025,e+.04,n+.025)))
                if not cells: continue
                if axis is None:
                    axis=indexed_axis(shape)
                    curve,times,anchors=indexed_profile(trip)
                    places={dep:locations[code] for code,_,dep in stops}
                ds,coords,blocks=axis
                t=bucket+offset*DAY
                samples,first,last=slice_window(curve,times,ds,coords,t)
                ends,refs_all=blocks
                first=max(0,bisect_right(ds,ds[first]-90000)-1)
                refs=refs_all[bisect_right(ends,first):bisect_left(ends,last-1)+1]
                deps=departure_anchors(stops,anchors,t,t+WINDOW)
                for dep in deps: dep.extend(places[dep[0]])
                labels=label_stops(stops,names,t,t+WINDOW)
                calls=[[names[c],a,b,c,*locations[c]] for c,a,b in stops[1:-1] if a<t+WINDOW and b>t and b>a]
                compiler.row(cells,t,[0,trip,offset,samples,refs,deps,labels['arrivals'],calls],refs)

        sql="""SELECT w.trip,w.seq,w.bucket,w.offset,w.west,w.south,w.east,w.north
                 FROM display_windows w ORDER BY w.trip,w.seq,w.offset,w.bucket"""
        for (trip,seq),rows in groupby(source.execute(sql),lambda r:r[:2]):
            blocks=None
            for _,_,bucket,offset,w,s,e,n in rows:
                cells=list(compiler.cells((w,s,e,n)))
                if not cells: continue
                if blocks is None:
                    dep,arr,kind,blob=source.execute('SELECT start,end,kind,path FROM display_legs WHERE trip=? AND seq=?',(trip,seq)).fetchone()
                    path=unpack(blob)
                    ds=[p[0] for p in path]
                    blocks=compiler.axis(ds,[p[1:] for p in path])
                t=bucket+offset*DAY
                length=ds[-1]
                ramp=min(60,(arr-dep)/4)
                first=max(0,bisect_right(ds,max(0,(t-dep-ramp)/(arr-dep))*length)-1)
                last=min(len(path),bisect_left(ds,min(1,(t+WINDOW-dep+ramp)/(arr-dep))*length)+2)
                ends,refs_all=blocks
                first=max(0,bisect_left(ds,ds[min(first,last-2)]-90000)-1)
                refs=refs_all[bisect_right(ends,first):bisect_left(ends,last-1)+1]
                compiler.row(cells,t,[1,trip,offset,seq,dep,arr,kind,length,refs,names[runs[trip][3][seq+1][0]]],refs)

        for trip,(_,_,_,stops) in runs.items():
            for seq,c,visible,end,kind in board_windows(stops):
                position,gtfs=locations[c]
                x,y=geographic(position) if position else gtfs
                cells=list(compiler.cells((x,y,x,y)))
                stop=[names[c],visible,end,c,position,gtfs]
                for t in range(visible//WINDOW*WINDOW,end,WINDOW):
                    row=[2,trip,t//DAY,seq,stop]
                    if kind=='terminal': row.append(kind)
                    compiler.row(cells,t,row)
        occupancy,homes=compiler.finish()
        meta={trip:[num,service,stops[0][2],stops[-1][1],names[stops[-1][0]]] for trip,(num,service,_,stops) in runs.items()}
        base=min(min(days) for days in calendar.values())
        active={service:[n-base for n in sorted(days)] for service,days in calendar.items()}
        write_gzip(folder/'catalog.gz',dict(runs=meta,base=date.fromordinal(base).isoformat(),services=active,cells=occupancy,homes=homes))
        manifest=dict(version=version(),step=step,window=WINDOW)
        target=output/version()
        if not target.exists(): folder.rename(target)
        latest=Path(temp)/'manifest.json'
        latest.write_bytes(encode(manifest))
        latest.replace(output/'manifest.json')
        print(dict(manifest=manifest,cells=len(occupancy),blocks=len(compiler.blocks),seconds=round(time.monotonic()-start,2)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'space-time')
    parser.add_argument('--step',type=float,default=1)
    parser.add_argument('--delta',action='store_true')
    args=parser.parse_args()
    build(args.output,args.step,DELTA if args.delta else None)
