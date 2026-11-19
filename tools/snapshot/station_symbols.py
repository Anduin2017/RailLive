"""Measure station axes against the packaged railway map in projected tile space."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import sqlite3
import json
import math

from index_rail_map import ROOT, _named_features, tile_coordinate


def platform_axis(seed, nearby, x, y):
    """Fit a straight axis to the connected track inside the station footprint."""
    # At max zoom, one z8 tile spans 8192 screen pixels. Use ~36 px
    # either side of the platform centre, half the largest station symbol.
    radius = 36 / 8192
    name,ox,oy,dx,dy,length2 = seed
    f = max(0,min(1,((x-ox)*dx+(y-oy)*dy)/length2))
    cx,cy = ox+f*dx,oy+f*dy
    neighbors = defaultdict(set)
    for other,ax,ay,u,v,_ in nearby:
        if other == name:
            a,b = (ax,ay),(ax+u,ay+v)
            neighbors[a].add(b)
            neighbors[b].add(a)
    def end_of_platform(prev, node):
        origin = (cx,cy)
        remaining = radius
        seen = {prev}
        while node not in seen:
            seen.add(node)
            length = math.dist(origin,node)
            if length >= remaining:
                f = remaining/length
                return tuple(p+f*(q-p) for p,q in zip(origin,node))
            remaining -= length
            options = neighbors[node]-seen
            if not options:
                break
            # Continue along the straightest branch of this railway at a turnout.
            def alignment(q):
                return sum((node[i]-prev[i])*(q[i]-node[i]) for i in (0,1))/math.dist(node,q)
            forward = max(sorted(options),key=alignment)
            if alignment(forward) <= 0:
                break
            origin,prev,node = node,node,forward
        return node

    a,b = (ox,oy),(ox+dx,oy+dy)
    left,right = end_of_platform(b,a),end_of_platform(a,b)
    return round(math.degrees(math.atan2(right[0]-left[0],left[1]-right[1]))) % 180


def build_axes(graph):
    grid = defaultdict(list)
    cell = 32  # About four kilometres in eastern China.

    def index(name, points):
        for (x,y),(u,v) in zip(points, points[1:]):
            dx,dy = u-x,v-y
            length2 = dx*dx+dy*dy
            if not length2:
                continue
            segment = (name,x,y,dx,dy,length2)
            for i in range(math.floor(min(x,u)*cell), math.floor(max(x,u)*cell)+1):
                for j in range(math.floor(min(y,v)*cell), math.floor(max(y,v)*cell)+1):
                    grid[i,j].append(segment)

    for tile in (ROOT/'map/tiles/8').glob('*/*.pbf'):
        for layer,name,paths,_ in _named_features(tile, include_unnamed_rail=True):
            if layer == b'rail':
                for path in paths:
                    index(name,path)
    for feature in json.loads((ROOT/'map/rail-detail.geojson').read_text())['features']:
        geom = feature['geometry']
        if geom['type'] == 'LineString':
            index(feature['properties'].get('rail'), [tile_coordinate(*p) for p in geom['coordinates']])

    axes = {}
    for kid in graph.pois:
        coord = graph.position(kid)
        if not coord:
            continue
        x,y = tile_coordinate(*coord)
        i,j = math.floor(x*cell),math.floor(y*cell)
        lines = {name for eid,_ in graph.positions_for[kid] for name in graph.line_names[eid]}
        references = []
        for eid,pos in graph.positions_for[kid]:
            stops = graph.features[eid]['stations']
            for index in (pos-1,pos+1):
                if 0 <= index < len(stops):
                    adjacent = graph.position(stops[index].get('kid'))
                    if adjacent:
                        u,v = tile_coordinate(*adjacent)
                        if (u,v) != (x,y):
                            references.append(math.degrees(math.atan2(u-x,y-v)) % 180)
        best = None
        nearby = {s for a in range(i-1,i+2) for b in range(j-1,j+2)
                  for s in grid.get((a,b), ())}
        for segment in sorted(nearby):
            name,ox,oy,dx,dy,length2 = segment
            f = max(0,min(1,((x-ox)*dx+(y-oy)*dy)/length2))
            d = math.hypot(x-ox-f*dx,y-oy-f*dy)
            # Prefer a documented connection within the tile simplification tolerance.
            if d > .006:
                continue
            angle = math.degrees(math.atan2(dx,-dy)) % 180
            error = min((abs((angle-ref+90)%180-90) for ref in references), default=0)
            if name not in lines and error > 35:
                continue
            score = (d + (0 if name in lines else .0015), -length2)
            if best is None or score < best[0]:
                best = score,segment
        if best:
            axes[kid] = platform_axis(best[1],nearby,x,y)
    return axes


def build_sizes(graph, db, folder):
    # Group first by service and day offset, then expand the GTFS calendar.
    services = defaultdict(list)
    for service,day in db.execute("SELECT service,date FROM dates WHERE kind=1"):
        services[service].append(datetime.strptime(day,'%Y%m%d').date())
    calls = db.execute("""
        WITH bounds AS (SELECT trip,min(seq) first,max(seq) last FROM times GROUP BY trip),
        events AS (
            SELECT station,service,arrival AS t FROM times v JOIN bounds USING(trip)
            JOIN trips ON trips.id=v.trip WHERE seq<>first
            UNION ALL
            SELECT station,service,departure FROM times v JOIN bounds USING(trip)
            JOIN trips ON trips.id=v.trip WHERE seq<>last
        )
        SELECT station,service,cast(substr(t,1,instr(t,':')-1) AS INTEGER)/24,count(*)
        FROM events WHERE t IS NOT NULL GROUP BY 1,2,3
    """)
    groups = defaultdict(dict)
    for code,service,offset,n in calls:
        groups[service,offset][graph.kid_for.get(code,code)] = n
    totals = defaultdict(Counter)
    for (service,offset),counts in groups.items():
        for day in services[service]:
            totals[day+timedelta(days=offset)].update(counts)
    days = {}
    for day,counts in totals.items():
        tiers = {kid:min(16,round(16*(min(n,860)/860)**1.8)) for kid,n in counts.items()}
        body = json.dumps({k:v for k,v in tiers.items() if v},sort_keys=True,separators=(',',':'))
        key = hashlib.sha256(body.encode()).hexdigest()[:16]
        (folder/(key+'.json')).write_text(body)
        days[day.isoformat()] = key
    return days


def write_atlas(folder, sizes):
    sprites = []
    for row,ink in enumerate(('#24595a','#7139a4')):
        sprites.append(f'<rect x="1" y="{row*80+1}" width="9" height="9" rx="2" fill="{ink}" stroke="white"/><path d="M3 {row*80+5.5}h5" stroke="white"/>')
        for tier,(length,width) in enumerate(sizes):
            x,y = 40*tier+(40-width)/2,80*row+(80-length)/2
            tracks = max(2,round((width-3)/5))
            stripes = ''.join(f'<rect x="{x+(i+.5)*width/tracks-0.8}" y="{y+5}" width="1.6" height="{length-10}" rx=".8" fill="#edf7ef"/>' for i in range(tracks))
            sprites.append(f'<rect x="{x}" y="{y}" width="{width}" height="{length}" rx="3" fill="{ink}" stroke="white" stroke-width="2"/>{stripes}<rect x="{x+1}" y="{y+length/2-1.6}" width="{width-2}" height="3.2" fill="{ink}"/>')
    (folder/'platforms.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1360" height="320" viewBox="0 0 680 160">'+''.join(sprites)+'</svg>')


if __name__ == '__main__':
    from server import GRAPH, DB
    folder = ROOT/'map/station-symbols'
    folder.mkdir(exist_ok=True)
    with sqlite3.connect(DB) as db:
        days = build_sizes(GRAPH,db,folder)
    manifest = dict(axes=build_axes(GRAPH),days=days,
                    sizes=[[round(62+12*t/16),round(10+24*t/16)] for t in range(17)])
    write_atlas(folder, manifest['sizes'])
    (folder/'index.json').write_text(json.dumps(manifest,separators=(',',':')))
