"""Offline railway routing across simplified vector-tile boundaries.

All distances and access costs are in metres. A route follows mapped segments
except for station access, short inferred joins, and the explicit ferry link.
The live server reads indexed polylines and never builds this graph.
"""
import heapq
import math
from collections import defaultdict
from functools import lru_cache

from departure_paths import SegmentNetwork, geographic, project_segment
from index_rail_map import ROOT, _named_features, coordinate
from train_relations import metres


class CorridorNetwork:
    def __init__(self):
        segments={}
        ends=set()
        for tile in sorted((ROOT/'map/tiles/8').glob('*/*.pbf')):
            for layer,name,paths,kind in _named_features(tile,True,True):
                if layer!=b'rail' or kind not in {'R','R2','RR','RR2','HSR','HSR2','F','F2'}:
                    continue
                if '地铁' in name or '轨道交通' in name:
                    continue
                for path in paths:
                    if len(path)<2: continue
                    ends.update((path[0],path[-1]))
                    for a,b in zip(path,path[1:]):
                        if a!=b:
                            segments.setdefault(tuple(sorted((a,b))),set()).add((name,kind))
        @lru_cache(maxsize=None)
        def vertex(p):
            return tuple(geographic(coordinate(*p)))
        self.tags={tuple(sorted((vertex(a),vertex(b)))):v for (a,b),v in segments.items()}
        self.net=SegmentNetwork(self.tags)
        self.joins=defaultdict(list)
        # Buffered/clipped tile copies can terminate inside an adjacent copy
        # of the same physical rail. Their ends need not have degree one,
        # since both tile copies can share a neighbor before they diverge.
        # Intersections of segment interiors do not create a connection.
        for p in map(vertex,sorted(ends)):
            neighbors=self.net.edges[p]
            tags=set().union(*(self.tags[tuple(sorted((prev,p)))] for prev in neighbors))
            for a,b in self.nearby(p):
                if p in (a,b) or neighbors.keys() & {a,b}: continue
                other=self.tags[a,b]
                same=any(n and n==m for n,_ in tags for m,_ in other)
                _,q=project_segment(p,a,b)
                gap=metres(p,q)
                if gap>(230 if same else 100): continue
                # End-to-interior is an inferred junction, never a free turn at a crossing.
                for v in (a,b):
                    cost=metres(q,v)+gap+200
                    self.joins[p].append((v,q,cost,other))
                    self.joins[v].append((p,q,cost,other))

    def nearby(self,p):
        x,y=self.net.cell(p)
        return {edge for i in range(x-1,x+2) for j in range(y-1,y+2)
                for edge in self.net.segments.get((i,j),())}

    @staticmethod
    def weight(tags,fast,lines):
        def cost(name, kind):
            factor = 1.8 if kind.startswith('F') else 1.0
            if not fast and (kind.startswith('HSR') or '客专' in name or '城际' in name):
                factor *= 8
            if lines and not any(name == line or name.startswith(line + ' ') for line in lines):
                factor *= 1.4
            return factor

        return min(cost(name, kind) for name, kind in tags)

    def station_line_support(self,path,origin_lines,destination_lines):
        """Evidence on mapped edges, excluding inferred joins and unnamed track."""
        length=sum(metres(a,b) for a,b in zip(path,path[1:]))
        if not length: return False
        along=supported=0.
        origin=destination=False
        for a,b in zip(path,path[1:]):
            span=metres(a,b)
            tags=self.tags.get(tuple(sorted((a,b))),())
            names=[name for name,_ in tags if name]
            # Tile names can omit the section qualifier after the line's name.
            first=any(line==name or (name.endswith('线') and line.startswith(name))
                      for name in names for line in origin_lines)
            last=any(line==name or (name.endswith('线') and line.startswith(name))
                     for name in names for line in destination_lines)
            if first or last: supported+=span
            if first and along<15000: origin=True
            if last and length-along-span<15000: destination=True
            along+=span
        return origin and destination and supported>=.85*length

    def station_route(self,s,n,start,end,fast,lines):
        if {s,n} in ({'VUQ','XJQ'}, {'VUQ','ZWQ'}):
            # Rail ends at the two Yuehai railway ferry terminals in the tile
            # source. Join only the marine portion with a port-to-port estimate.
            south=(110.1498050043318,20.03940522173724)
            north=(110.1241357484516,20.227533060201026)
            island,mainland=(start,end) if s=='VUQ' else (end,start)
            first=self.route(island,south,False,())
            last=self.route(north,mainland,False,('湛海线',))
            if first and last:
                result=first+last
                return '含铁路轮渡推测',result if s=='VUQ' else result[::-1]
            return '含铁路轮渡推测',None
        return '底图接轨推测',self.route(start,end,fast,lines)

    @lru_cache(maxsize=4096)
    def route(self,start,end,fast,lines=()):
        # A station can touch several tracks. Seed the search with nearby
        # projections, charging extra for distance off the drawn railway.
        def access(p):
            candidates=[]
            for a,b in self.nearby(p):
                _,q=project_segment(p,a,b)
                gap=metres(p,q)
                if gap>800: continue
                tags=self.tags[a,b]
                w=self.weight(tags,fast,lines)
                for u in (a,b):
                    candidates.append((u,q,4*gap+metres(u,q)*w))
            return candidates
        starts,ends=access(start),access(end)
        if not starts or not ends: return None
        target=defaultdict(list)
        for u,q,cost in ends: target[u].append((q,cost))
        costs={};parent={};queue=[]
        def relax(u,d,prev,q):
            if d>=costs.get(u,math.inf): return
            costs[u]=d;parent[u]=(prev,q)
            heapq.heappush(queue,(d+metres(u,end),d,u))
        for u,q,d in starts: relax(u,d,None,q)
        best=math.inf;last=None
        while queue:
            score,d,u=heapq.heappop(queue)
            if score>=best: break
            if d!=costs[u]: continue
            for q,c in target.get(u,()):
                if d+c<best: best,last=d+c,(u,q)
            for v,c in self.net.edges[u].items():
                tags=self.tags[tuple(sorted((u,v)))]
                relax(v,d+c*self.weight(tags,fast,lines),u,None)
            for v,q,c,tags in self.joins.get(u,()):
                relax(v,d+c*self.weight(tags,fast,lines),u,q)
        if last is None:
            return None
        # Store the projected join itself as well as its target vertex so
        # the resulting polyline follows the mapped segment after the gap.
        u,q=last
        path=[end,q]
        while u is not None:
            path.append(u)
            u,q=parent[u]
            if q: path.append(q)
        path.append(start)
        return list(reversed(path))
