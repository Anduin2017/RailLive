"""Offline display paths for timetable legs that lack a usable motion profile."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from contextlib import closing
from functools import lru_cache
from itertools import chain
import gzip
import heapq
import json
import math
import re

from index_rail_map import ROOT, coordinate, _named_features
import sqlite3
import zlib

from shape_excursions import cumulative, early_station_pass, project_segment, simplify_leg
from train_relations import TIMETABLE, TRACKS, metres, point, shape_path, unpack

STATION_ACCESS_METRES = 1800

# Confirmed parallel passenger corridors. The same station codes serve both
# routes, so calling at both stations cannot identify a donor's alignment.
CONVENTIONAL_LINES = {
    frozenset(('JBH', 'YWH')): '沪昆线',
    frozenset(('WHH', 'ECH')): '皖赣线',
}


def conventional_prefixes(feed_version):
    # Ordinary services began migrating from T/K and numeric passenger numbers
    # to N/V on 2026-11-25. Interpret a frozen timetable using its own edition.
    if re.fullmatch(r'[0-9]{8}',feed_version) and feed_version<'20261125':
        return frozenset('KTZ0123456789')
    return frozenset('ZNV')


def snapshot_conventional_prefixes(db):
    metadata=json.loads(db.execute('SELECT * FROM metadata').fetchone()[0])
    return conventional_prefixes(str(metadata.get('feed_version','')))


def geographic(position):
    x,y=position
    x+=5.116568470
    y-=1.113596968
    a,b,c,d=1.015972842,-0.000070497,0.000009795,1.012049241
    return [(d*x-b*y)/(a*d-b*c),(a*y-c*x)/(a*d-b*c)]


def same_corridor(first, second):
    a, b = cumulative(first), cumulative(second)
    length = min(a[-1], b[-1])
    if abs(a[-1]-b[-1]) > max(1500, .05*length):
        return False
    steps = max(2, math.ceil(length/2000))
    return all(metres(point((a, first), a[-1]*i/steps),
                      point((b, second), b[-1]*i/steps)) <= 1500
               for i in range(steps+1))


class TrackNetwork:
    def __init__(self, paths):
        self.edges=defaultdict(dict)
        self.grid=defaultdict(list)
        for path in paths:
            for a,b in zip(path,path[1:]):
                if a!=b and b not in self.edges[a]:
                    self.edges[a][b]=self.edges[b][a]=metres(a,b)
        for p in self.edges:
            self.grid[self.cell(p)].append(p)

    @staticmethod
    def cell(p):
        return math.floor(p[0]*40), math.floor(p[1]*40)

    @lru_cache(maxsize=None)
    def nearest(self,p):
        x,y=self.cell(p)
        options=(q for i in range(x-1,x+2) for j in range(y-1,y+2) for q in self.grid[i,j])
        q=min(options,key=lambda q:metres(p,q),default=None)
        return q if q and metres(p,q)<STATION_ACCESS_METRES else None

    @lru_cache(maxsize=12000)
    def route(self,start,end):
        a,b=self.nearest(start),self.nearest(end)
        if a and b and a!=b:
            queue=[(metres(a,b),0,a)]
            cost={a:0}
            parent={}
            limit=metres(start,end)*2.4+8000
            while queue:
                score,d,u=heapq.heappop(queue)
                if score>limit: break
                if d!=cost[u]: continue
                if u==b:
                    path=[b]
                    while path[-1]!=a: path.append(parent[path[-1]])
                    return '路网推测',[start,*reversed(path),end]
                for v,w in self.edges[u].items():
                    nd=d+w
                    if nd<cost.get(v,math.inf):
                        cost[v]=nd
                        parent[v]=u
                        heapq.heappush(queue,(nd+metres(v,b),nd,v))
        return '站间示意',[start,end]


class SegmentNetwork(TrackNetwork):
    """Index the interiors of simplified map edges as station access points."""
    def __init__(self,paths):
        super().__init__(paths)
        self.segments=defaultdict(list)
        for a,neighbors in self.edges.items():
            for b in neighbors:
                if a>=b: continue
                x,y=self.cell(a)
                u,v=self.cell(b)
                steps=max(abs(x-u),abs(y-v),1)
                cells={self.cell((a[0]+(b[0]-a[0])*i/steps,a[1]+(b[1]-a[1])*i/steps))
                       for i in range(steps+1)}
                for cell in cells:
                    self.segments[cell].append((a,b))

    @lru_cache(maxsize=8192)
    def access(self,p):
        x,y=self.cell(p)
        best=None
        for a,b in {edge for i in range(x-1,x+2) for j in range(y-1,y+2)
                    for edge in self.segments.get((i,j),())}:
            _,q=project_segment(p,a,b)
            candidate=(metres(p,q),a,b,q)
            if candidate[0]<=750 and (best is None or candidate<best):
                best=candidate
        return best

    @lru_cache(maxsize=8192)
    def projected_route(self,start,end):
        first,last=self.access(start),self.access(end)
        if not first or not last: return None
        _,a,b,p=first
        _,c,d,q=last
        if (a,b)==(c,d): return [start,p,q,end]
        # Virtual endpoints connect only to the two ends of their own edge.
        # Keep the underlying track topology intact at crossings.
        best=None
        for u in (a,b):
            for v in (c,d):
                if u==v: middle=[u]
                else:
                    kind,middle=self.route(u,v)
                    if kind=='站间示意': continue
                path=[start,p,*middle,q,end]
                length=sum(metres(x,y) for x,y in zip(path,path[1:]))
                if length<=metres(start,end)*2.4+8000 and (best is None or length<best[0]):
                    best=length,path
        return best[1] if best else None


def original_shapes():
    with closing(sqlite3.connect(TIMETABLE)) as db:
        for blob, in db.execute('SELECT path FROM shapes'):
            yield [tuple(p) for p in json.loads(gzip.decompress(blob))]


class TileRoutes:
    def __init__(self, locations):
        self.locations=locations
        self.lines=defaultdict(list)
        self.visits=defaultdict(dict)
        self.paths=defaultdict(list)
        conventional=defaultdict(list)
        unnamed=[]
        unnamed_conventional=[]
        @lru_cache(maxsize=65536)
        def vertex(p):
            return tuple(geographic(coordinate(*p)))
        for tile in sorted((ROOT/'map/tiles/8').glob('*/*.pbf')):
            for layer,name,paths,kind in _named_features(tile,with_type=True,include_unnamed_rail=True):
                if layer!=b'rail' or '地铁' in name or '轨道交通' in name or kind not in {'R','R2','RR','RR2','HSR','HSR2'}:
                    continue
                for points in paths:
                    section=[vertex(p) for p in points]
                    if not name:
                        unnamed.append(section)
                        if not kind.startswith('HSR'):
                            unnamed_conventional.append(section)
                        continue
                    self.paths[name].append(section)
                    if not kind.startswith('HSR'):
                        conventional[name].append(section)
        with closing(sqlite3.connect(TIMETABLE)) as db:
            for name,code,mileage in db.execute('SELECT line,code,mileage FROM line_stations ORDER BY line,position'):
                match=re.fullmatch(r'([0-9,.]+)\s*km',(mileage or '').strip())
                km=float(match[1].replace(',','')) if match else None
                self.visits[code][name]=len(self.lines[name])
                self.lines[name].append((code,km))
        self.conventional=conventional
        self.slow_network=TrackNetwork(path for paths in conventional.values() for path in paths)
        self.combined=TrackNetwork(path for paths in self.paths.values() for path in paths)
        self.tile_networks={
            True: SegmentNetwork(chain(unnamed_conventional, *conventional.values())),
            False: SegmentNetwork(chain(unnamed, *self.paths.values())),
        }

    @lru_cache(maxsize=64)
    def network(self,name,slow):
        return TrackNetwork((self.conventional if slow else self.paths)[name])

    @lru_cache(maxsize=8192)
    def route(self,s,n,start,end,slow):
        # Prefer an identified line. Check its mileage and the order in which
        # intermediate stations lie along the candidate before accepting it.
        known=self.visits[s].keys() & self.visits[n].keys()
        accepted=[]
        for name in sorted(known):
            if name not in self.paths: continue
            kind,path=self.network(name,slow).route(start,end)
            if kind!='站间示意' and self.fits_line(name,s,n,path):
                accepted.append((sum(metres(a,b) for a,b in zip(path,path[1:])),name,path))
        if accepted:
            return '底图推测',min(accepted)[2]
        if not any(name in self.paths for name in known):
            kind,path=(self.slow_network if slow else self.combined).route(start,end)
            if kind!='站间示意':
                return '底图推测',path
        # The map also paints track features without a line name. Their railway
        # type is usable for routing, but a shared published axis still constrains it.
        kind,path=self.tile_networks[slow].route(start,end)
        if kind!='站间示意' and (not known or any(self.fits_line(name,s,n,path) for name in known)):
            return '底图推测',path
        path=self.tile_networks[slow].projected_route(start,end)
        if path and (not known or any(self.fits_line(name,s,n,path) for name in known)):
            return '底图推测',path
        # If tiles are incomplete, use independently located intermediate
        # stations only on an unambiguous line with a consistent mileage axis.
        if len(known)==1:
            name=next(iter(known))
            i,j=self.visits[s][name],self.visits[n][name]
            calls=self.lines[name]
            first,last=calls[i][1],calls[j][1]
            if first is not None and last is not None and first!=last:
                stops=[(start,first)]
                for idx in range(i+(1 if j>i else -1),j,1 if j>i else -1):
                    code,km=calls[idx]
                    mapped,gtfs=self.locations.get(code,(None,None))
                    if km is not None and (mapped or gtfs):
                        stops.append((tuple(geographic(mapped)) if mapped else tuple(gtfs),km))
                stops.append((end,last))
                if len(stops)>2 and all((kb-ka)*(last-first)>0 and
                        metres(a,b)<=1000*abs(kb-ka)*1.12+1200
                        for (a,ka),(b,kb) in zip(stops,stops[1:])):
                    return '站序示意',[p for p,_ in stops]
        return '站间示意',[start,end]

    @lru_cache(maxsize=256)
    def conventional_station_path(self,corridors,s,n,start,end,line):
        if line not in self.visits[s] or line not in self.visits[n]:
            return None
        kind,path=self.route(s,n,start,end,True)
        if kind!='底图推测' or not self.fits_line(line,s,n,path):
            return None
        # One search is restricted to conventional track, the other weights
        # named edges on the published line. Check the station exits as well.
        _,reference=corridors.station_route(s,n,start,end,False,(line,))
        if not reference or not same_corridor(path,reference):
            return None
        origin,destination,_=corridors.station_line_evidence(path,(line,),(line,))
        if origin and destination and not corridors.follows_parallel_high_speed(path,line):
            return path
        return None

    def conventional_parallel_route(self,corridors,s,n,path,duration):
        # In the Shanghai–Nanjing corridor both published routes list many
        # of the same stations. A matching mileage cannot distinguish them.
        conventional='京沪线'
        common=self.visits[s].keys() & self.visits[n].keys()
        if (not {conventional,'沪宁高速线'} <= common
                or not corridors.follows_parallel_high_speed(path,conventional)):
            return None
        lines=(conventional,)
        source,candidate=corridors.station_route(s,n,path[0],path[-1],False,lines)
        if not candidate or not self.fits_line(conventional,s,n,candidate):
            return '径路不确定',path
        distances=cumulative(candidate)
        if (distances[-1]*3.6>200*(duration-min(60,duration/4))
                or early_station_pass(candidate,distances,path[-1]) is not None
                or not corridors.station_line_support(candidate,lines,lines)
                or corridors.follows_parallel_high_speed(candidate,conventional)):
            return '径路不确定',path
        return source,candidate

    def compare_fallback(self,corridors,donors,s,n,start,end,path,duration,group):
        # The shape graph can miss a direct connection even when its detour
        # fits the timetable. Compare that case with the independent track map.
        cruise_seconds=duration-min(60,duration/4)
        length=cumulative(path)[-1]
        ceiling=350 if group in {'G','D','C'} else 200
        too_fast=length*3.6>ceiling*cruise_seconds
        origin_lines=self.visits[s].keys()
        destination_lines=self.visits[n].keys()
        common=tuple(sorted(origin_lines & destination_lines))
        fast=group in {'G','D','C','S'}
        saving=max(3000,.12*length) if too_fast else max(8000,.25*length)
        reference=None
        if not too_fast:
            if length-metres(start,end)<saving:
                return None
            if any(abs(length-m)<=max(5000,.18*m) for m in self.station_distances(s,n)):
                return None
            kind,reference=self.route(s,n,start,end,not fast)
            if kind!='底图推测' or length-cumulative(reference)[-1]<saving:
                return None
            donor=donors.route(s,n,group if group.isalpha() else '#',start,end)
            if donor and same_corridor(path,donor):
                return None

        # A conventional passenger service can run along a shared passenger
        # line. Test the named alignment without the general HSR penalty.
        modes=(True,False) if common and not fast else (fast,)
        for mode in modes:
            source,alternative=corridors.station_route(s,n,start,end,mode,common)
            if not alternative: continue
            ds=cumulative(alternative)
            if (length-ds[-1]<saving or ds[-1]*3.6>ceiling*cruise_seconds
                    or early_station_pass(alternative,ds,end) is not None):
                continue
            if reference and not same_corridor(reference,alternative):
                continue
            if common:
                direct=any(corridors.station_line_support(alternative,(line,),(line,))
                           and self.fits_line(line,s,n,alternative,check_mileage=False)
                           for line in common)
                if not direct and not any(self.fits_line(line,s,n,alternative) for line in common):
                    continue
            elif not corridors.station_line_support(alternative,origin_lines,destination_lines):
                if not reference:
                    continue
                origin,destination,_=corridors.station_line_evidence(alternative,origin_lines,destination_lines)
                rs=cumulative(reference)
                if (not (origin and destination)
                        or rs[-1]*3.6>ceiling*cruise_seconds
                        or early_station_pass(reference,rs,end) is not None
                        or not corridors.connected_station_route(reference,origin_lines,destination_lines)):
                    continue
                # The two routes agree. Publish the one with explicit mapped
                # connections, without using the inferred joins of the other.
                return source,reference
            return source,alternative
        if reference:
            return '径路不确定',path
        return None

    def station_distances(self,s,n):
        distances=[]
        for line in self.visits[s].keys() & self.visits[n].keys():
            a,b=(self.lines[line][self.visits[code][line]][1] for code in (s,n))
            if None not in (a,b) and a!=b: distances.append(abs(b-a)*1000)
        return distances

    def fits_line(self,name,s,n,path,*,check_mileage=True):
        i,j=self.visits[s][name],self.visits[n][name]
        calls=self.lines[name]
        length=sum(metres(a,b) for a,b in zip(path,path[1:]))
        a,b=calls[i][1],calls[j][1]
        if check_mileage and a is not None and b is not None and abs(length-1000*abs(a-b))>max(3500,.12*length):
            return False
        cursor=checked=missed=0
        for idx in range(i+(1 if j>i else -1),j,1 if j>i else -1):
            mapped,gtfs=self.locations.get(calls[idx][0],(None,None))
            pos=geographic(mapped) if mapped else gtfs
            if not pos: continue
            checked+=1
            distance,nearest=min((metres(pos,q),k+frac)
                                 for k,(a,b) in enumerate(zip(path,path[1:]))
                                 for frac,q in (project_segment(pos,a,b),))
            if distance>2500:
                missed+=1
                continue
            if nearest<cursor: break
            cursor=nearest
        else:
            # A named line may include an older station on a local branch.
            # Allow one missed checkpoint only with three corroborating stops.
            if not check_mileage and checked==0: return False
            return missed==0 or (missed==1 and checked-missed>=3)
        return False


class DonorRoutes:
    """Use original timetable shapes serving both stations, including return services."""
    def __init__(self,runs):
        self.visits=defaultdict(set)
        with closing(sqlite3.connect(TRACKS)) as db:
            for trip,blob in db.execute('SELECT trip,anchors FROM profiles'):
                number,_,shape,calls=runs[trip]
                if not shape or shape.startswith('inferred:'): continue
                visits=list(zip(calls,unpack(blob)))
                for i,((a,_,_),first) in enumerate(visits):
                    if first is None: continue
                    for (b,_,_),last in visits[i+1:]:
                        if a==b or last is None or last<=first: continue
                        group=number[0] if number[0].isalpha() else '#'
                        self.visits[a,b,group].add((shape,first,last))
                        self.visits[b,a,group].add((shape,last,first))

    @lru_cache(maxsize=4096)
    def route(self,s,n,number_group,start,end):
        routes=sorted(self.visits.get((s,n,number_group),()))
        if not routes: return None
        shape,a,b=routes[0]
        axis=shape_path(shape)
        length=abs(b-a)
        if metres(point(axis,a),start)>750 or metres(point(axis,b),end)>750:
            return None
        steps=max(2,math.ceil(length/1500))
        for alternative,c,d in routes[1:]:
            if abs(abs(d-c)-length)>max(300,.015*length): return None
            other=shape_path(alternative)
            if any(metres(point(axis,a+(b-a)*i/steps),point(other,c+(d-c)*i/steps))>650
                   for i in range(steps+1)): return None
        lo,hi=sorted((a,b))
        interior=axis[1][bisect_right(axis[0],lo):bisect_left(axis[0],hi)]
        if len(interior)<2: return None
        return [start,point(axis,a),*(interior if a<b else reversed(interior)),point(axis,b),end]


def save_fallbacks(out,runs,locations):
    from line_live import WINDOW,indexed_profile
    from train_relations import DAY,shape_path,inferred_route
    from corridor_paths import CorridorNetwork
    corridors=CorridorNetwork()
    network=TrackNetwork(original_shapes())
    tiles=TileRoutes(locations)
    donors=DonorRoutes(runs)
    out.executescript("""
      CREATE TABLE display_legs(trip TEXT, seq INTEGER, start INTEGER, end INTEGER, kind TEXT, path BLOB,
        PRIMARY KEY(trip,seq));
      CREATE TABLE display_windows(bucket INTEGER, offset INTEGER,trip TEXT,seq INTEGER,
        west REAL,south REAL,east REAL,north REAL);
    """)
    with closing(sqlite3.connect(TIMETABLE)) as db:
        ordinary=snapshot_conventional_prefixes(db)
    for trip,(number,_,shape,calls) in runs.items():
        curve,times,anchors=indexed_profile(trip) if shape else ([],[],[])
        legs=inferred_route(trip)[1] if shape.startswith('inferred:') else None
        for seq,((s,_,dep),(n,arr,next_dep)) in enumerate(zip(calls,calls[1:])):
            if seq+2<len(calls): arr=min(arr,next_dep)
            i,j=bisect_right(times,dep),bisect_left(times,arr)
            if (anchors and anchors[seq] is not None and anchors[seq+1] is not None
                    and anchors[seq+1]>anchors[seq] and i<j and all(x is not None for _,x in curve[i:j])):
                continue
            if arr<=dep:
                raise ValueError(f'{trip}: nonpositive running time {s} to {n}')
            def endpoint(code):
                mapped,gtfs=locations[code]
                if not (mapped or gtfs):
                    raise ValueError(f'{trip}: missing station position {code}')
                return tuple(geographic(mapped) if mapped else gtfs)
            start,end=endpoint(s),endpoint(n)
            if (legs is None or legs[seq]) and anchors and None not in anchors[seq:seq+2] and anchors[seq+1]>anchors[seq]:
                axis=shape_path(shape)
                a,b=anchors[seq:seq+2]
                path=[point(axis,a),*axis[1][bisect_right(axis[0],a):bisect_left(axis[0],b)],point(axis,b)]
                path[0],path[-1]=start,end
                kind='时刻推算'
            else:
                kind,path=network.route(start,end)
                if kind=='路网推测':
                    # A disconnected platform vertex can force the graph search
                    # past the destination to a distant crossover and back.
                    lines=tiles.visits[s].keys() & tiles.visits[n].keys()
                    mileages=tiles.station_distances(s,n)
                    distances=cumulative(path)
                    length=distances[-1]
                    first_visit=early_station_pass(path,distances,end)
                    group=runs[trip][0][:1]
                    limit=380 if group in 'GDC' else 220
                    if (first_visit is not None or length*3.6>(arr-dep)*limit
                            or (mileages and all(length-m>max(5000,.18*m) for m in mileages))):
                        tile_kind,tile_path=tiles.route(s,n,start,end,group not in 'GDCS')
                        options=[]
                        if tile_kind=='底图推测':
                            options.append((tile_kind,tile_path))
                        donor=donors.route(s,n,group if group.isalpha() else '#',start,end)
                        if donor:
                            options.append(('同类车径路推测',donor))
                        for alt_kind,alt in options:
                            ds=cumulative(alt)
                            if ds[-1]*3.6>(arr-dep)*limit or early_station_pass(alt,ds,end) is not None:
                                continue
                            if lines and not any(tiles.fits_line(line,s,n,alt) for line in lines):
                                continue
                            if not lines and alt_kind=='底图推测':
                                # Anonymous tracks alone do not identify a service's route.
                                # Require independent agreement with the first approach.
                                if first_visit is None or abs(ds[-1]-first_visit)>max(1200,.02*first_visit):
                                    continue
                            saving=length-ds[-1]
                            if saving>max(3000,.12*length) or (first_visit is not None and saving>8000):
                                kind,path=alt_kind,alt
                                break
                if kind=='站间示意':
                    kind,path=tiles.route(s,n,start,end,runs[trip][0][:1] not in {'G','D','C','S'})
                    if kind in {'底图推测','站序示意'}:
                        length=sum(metres(a,b) for a,b in zip(path,path[1:]))
                        speed=length*3.6/(arr-dep)
                        if speed>(380 if runs[trip][0].startswith(('G','D','C')) else 220):
                            kind,path='站间示意',[start,end]
                if kind in {'站间示意','站序示意'}:
                    prefix=runs[trip][0][0]
                    donor=donors.route(s,n,prefix if prefix.isalpha() else '#',start,end)
                    if donor:
                        length=sum(metres(a,b) for a,b in zip(donor,donor[1:]))
                        known=tiles.visits[s].keys() & tiles.visits[n].keys()
                        if (length*3.6/(arr-dep)<=(380 if prefix in 'GDC' else 220)
                                and (not known or any(tiles.fits_line(name,s,n,donor) for name in known))):
                            kind,path='同类车径路推测',donor
                if kind in {'站间示意','站序示意'}:
                    fast=prefix in 'GDCS'
                    lines=tuple(sorted(tiles.visits[s].keys() & tiles.visits[n].keys()))
                    kind,path=corridors.station_route(s,n,start,end,fast,lines)
                    if not path:
                        raise ValueError(f'{trip} {s} → {n}: unresolved railway corridor')
                    speed=sum(metres(a,b) for a,b in zip(path,path[1:]))*3.6/(arr-dep)
                    if speed>(380 if fast else 220):
                        raise ValueError(f'{trip} {s} → {n}: implausible corridor speed {speed:.1f}')
            line=CONVENTIONAL_LINES.get(frozenset((s,n))) if number[:1] in ordinary else None
            if line and corridors.follows_parallel_high_speed(path,line):
                replacement=tiles.conventional_station_path(corridors,s,n,start,end,line)
                distances=cumulative(replacement) if replacement else None
                if (distances and distances[-1]*3.6<=200*(arr-dep-min(60,(arr-dep)/4))
                        and early_station_pass(replacement,distances,end) is None):
                    kind,path='底图推测',replacement
                else:
                    kind='径路不确定'
            if kind in {'时刻推算','路网推测'}:
                mileages=tiles.station_distances(s,n)
                cleaned=simplify_leg([tuple(p) for p in path], arr-dep,
                                     380 if runs[trip][0][:1] in 'GDC' else 220, mileages)
                if cleaned:
                    kind,path='轨迹去绕行推测',cleaned
            if kind=='路网推测':
                group=runs[trip][0][:1]
                candidate=(tiles.conventional_parallel_route(corridors,s,n,path,arr-dep)
                           if group in ordinary else None)
                if not candidate:
                    candidate=tiles.compare_fallback(corridors,donors,s,n,start,end,path,arr-dep,group)
                if candidate:
                    kind,path=candidate
            indexed=[]
            distance=0.
            for p in path:
                if indexed:
                    delta=metres(indexed[-1][1:],p)
                    if delta<0.01: continue
                    distance+=delta
                indexed.append([distance,*p])
            if len(indexed)<2: raise ValueError(f'{trip}: coincident stations {s} and {n}')
            blob=zlib.compress(json.dumps(indexed,separators=(',',':')).encode(),1)
            out.execute('INSERT INTO display_legs VALUES(?,?,?,?,?,?)',(trip,seq,dep,arr,kind,blob))
            ds=[v[0] for v in indexed]
            for t in range(dep//WINDOW*WINDOW,arr,WINDOW):
                # A one-minute ramp at each end. Bound the nonlinear progress by
                # its maximum displacement from constant speed (one ramp duration).
                ramp=min(60,(arr-dep)/4)
                lo=max(0,(t-dep-ramp)/(arr-dep))*distance
                hi=min(1,(t+WINDOW-dep+ramp)/(arr-dep))*distance
                part=indexed[max(0,bisect_right(ds,lo)-1):bisect_left(ds,hi)+1]
                xs=[p[1] for p in part]; ys=[p[2] for p in part]
                out.execute('INSERT INTO display_windows VALUES(?,?,?,?,?,?,?,?)',
                    (t%DAY,t//DAY,trip,seq,min(xs),min(ys),max(xs),max(ys)))
    out.execute('CREATE INDEX display_bucket ON display_windows(bucket,west)')
