"""Compare dated runs on continuous, directed parts of their published GTFS shapes."""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date
from functools import lru_cache
import gzip
import json
import math
from pathlib import Path
import sqlite3
import zlib

ROOT = Path(__file__).resolve().parent
TRACKS = ROOT/'data/relations.sqlite'
TIMETABLE = ROOT/'data/trains.sqlite'
DAY = 86400


def seconds(value):
    h,m,s = map(int,value.split(':'))
    return h*3600+m*60+s


@lru_cache(maxsize=1)
def timetable():
    with closing(sqlite3.connect(TIMETABLE.as_uri()+'?mode=ro',uri=True)) as db:
        runs = {i:(number,service,shape,[]) for i,number,service,shape in db.execute('SELECT * FROM trips')}
        with closing(sqlite3.connect(TRACKS.as_uri()+'?mode=ro',uri=True)) as tracks:
            has_inferred = tracks.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inferred'").fetchone()
            for trip, in tracks.execute('SELECT trip FROM inferred') if has_inferred else ():
                number,service,_,calls=runs[trip]
                runs[trip]=(number,service,'inferred:'+trip,calls)
        for trip,stop,arrival,departure in db.execute('SELECT trip,station,arrival,departure FROM times ORDER BY trip,seq'):
            runs[trip][3].append((stop,seconds(arrival),seconds(departure)))
        calendar=defaultdict(set)
        for service,d in db.execute('SELECT service,date FROM dates WHERE kind=1'):
            calendar[service].add(date.fromisoformat(d).toordinal())
        return runs,calendar,dict(db.execute('SELECT id,name FROM stops'))


def unpack(value):
    return json.loads(zlib.decompress(value))


def inferred_route(trip):
    with closing(sqlite3.connect(TRACKS.as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inferred'").fetchone():
            return None
        row=db.execute('SELECT path,legs FROM inferred WHERE trip=?',(trip,)).fetchone()
        return (unpack(row[0]),unpack(row[1])) if row else None


@lru_cache(maxsize=160)
def shape_path(shape):
    if shape.startswith('inferred:'):
        points=inferred_route(shape.split(':',1)[1])[0]
    else:
        with closing(sqlite3.connect(TIMETABLE.as_uri()+'?mode=ro',uri=True)) as db:
            points=json.loads(gzip.decompress(db.execute('SELECT path FROM shapes WHERE id=?',(shape,)).fetchone()[0]))
    distances=[0.]
    for a,b in zip(points,points[1:]):
        distances.append(distances[-1]+metres(a,b))
    return distances,points


def metres(a,b):
    return math.pi*6371008.8/180*math.hypot((a[0]-b[0])*math.cos((a[1]+b[1])*math.pi/360),a[1]-b[1])


def point(path, at):
    distances,coords=path
    j=min(max(bisect_right(distances,at),1),len(coords)-1)
    frac=(at-distances[j-1])/(distances[j]-distances[j-1] or 1)
    return [a+(b-a)*frac for a,b in zip(coords[j-1],coords[j])]


def station_gap(shape,other,a,b,c,d):
    length=max(c-a,d-b)
    if length>8000 or min(c-a,d-b)<=0 or abs((c-a)-(d-b))>500:
        return False
    # Short platform variants between exact shared blocks: both polylines
    # must remain close throughout, including their approach and exit.
    p,q=shape_path(shape),shape_path(other)
    steps=max(2,math.ceil(length/250))
    return all(metres(point(p,a+(c-a)*i/steps),point(q,b+(d-b)*i/steps))<180 for i in range(steps+1))


def matched_spans(shape,other,spans):
    acount=Counter(s[0] for s in spans)
    bcount=Counter(s[2] for s in spans)
    blocks=[]
    for a,z,b,y in spans:
        if acount[a]>1 or bcount[b]>1: continue
        if blocks:
            last=blocks[-1][-1]
            if abs(a-last[0])<0.1 and abs(b-last[1])<0.1:
                blocks[-1][-1]=[z,y]
                continue
            if station_gap(shape,other,*last,a,b):
                blocks[-1].extend([[a,b],[z,y]])
                continue
        blocks.append([[a,b],[z,y]])
    return [p for p in blocks if p[-1][0]-p[0][0]>=1500]


@lru_cache(maxsize=12)
def shared_routes(shape):
    if not shape: return {}
    with closing(sqlite3.connect(TRACKS.as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute("""SELECT b.shape,a.start,a.end,b.start,b.end FROM blocks a
            JOIN blocks b ON b.block=a.block WHERE a.shape=? ORDER BY b.shape,a.start""",(shape,))
        groups=defaultdict(list)
        for other,a,z,b,y in rows: groups[other].append((a,z,b,y))
    result={}
    for other,spans in groups.items():
        good=matched_spans(shape,other,spans)
        if good: result[other]=good
    return result


def profile(db,trip):
    row=db.execute('SELECT knots,anchors FROM profiles WHERE trip=?',(trip,)).fetchone()
    if not row: return [],[]
    return unpack(row[0]),unpack(row[1])


def pass_time(knots, at):
    # Do not interpolate across holes in the motion profile.
    valid=[(s,t) for t,s in knots if s is not None]
    i=bisect_left(valid,(at,-math.inf))
    if i==0 or i==len(valid): return None
    x,t=valid[i-1]; y,u=valid[i]
    if u-t>21 or y<=x: return None
    return t+(at-x)/(y-x)*(u-t)


def translate(span,at,side):
    for p,q in zip(span,span[1:]):
        if p[side]<=at<=q[side]:
            f=(at-p[side])/(q[side]-p[side])
            return p[1-side]+f*(q[1-side]-p[1-side])
    return None


def feasible_window(points, choice, start, end, cap):
    """One order change during a published dwell, with a generous speed ceiling.

    Before the chosen dwell the fast train must pass after the slow departure;
    after it, before the slow arrival. Propagate the bounds in both directions.
    """
    bounds=[[start[0],start[1],start[1]]]
    for j,(at,arrival,departure) in enumerate(points):
        lower = departure if j<choice else arrival if j==choice else -math.inf
        upper = arrival if j>choice else departure if j==choice else math.inf
        bounds.append([at,lower,upper])
    bounds.append([end[0],end[1],end[1]])
    for p,q in zip(bounds,bounds[1:]):
        if q[0]<p[0]: return None
        q[1]=max(q[1],p[1]+(q[0]-p[0])/cap)
    for j in range(len(bounds)-2,-1,-1):
        p,q=bounds[j:j+2]
        p[2]=min(p[2],q[2]-(q[0]-p[0])/cap)
    if any(p[1]>p[2] for p in bounds): return None
    return bounds[choice+1][1:]


def order_event(p,q,shared,calls,visits,own,anchors,curves,other_anchors,shift,names):
    fast_is_self=p[4]>p[6]
    candidates=[]
    feasible=[]
    for span in shared:
        if not anchors or not other_anchors: continue
        if not all(at is not None and span[0][side]-1200<=at<=span[-1][side]+1200
                   for side,values in enumerate(([anchors[p[0]],anchors[q[0]]],
                                                 [other_anchors[p[1]],other_anchors[q[1]]])) for at in values):
            continue
        slow,ax,lo,hi,delta,side=(visits,other_anchors,p[1],q[1],shift,1) if fast_is_self else (calls,anchors,p[0],q[0],0,0)
        fast_stops={r[0] for r in (calls if fast_is_self else visits)}
        points=[]
        labels=[]
        for idx in range(lo+1,hi):
            s,arrival,departure=slow[idx]
            if ax[idx] is None: continue
            at=translate(span,ax[idx],side)
            if at is None: continue
            points.append((at,arrival+delta,departure+delta))
            labels.append(s)
        if len(points)!=hi-lo-1: continue
        fast_axes=anchors if fast_is_self else other_anchors
        side_idx=0 if fast_is_self else 1
        first=(fast_axes[p[side_idx]],p[4] if fast_is_self else p[6])
        last=(fast_axes[q[side_idx]],q[3] if fast_is_self else q[5])
        for idx,(at,arrival,departure) in enumerate(points):
            s=labels[idx]
            if departure<=arrival or s in fast_stops: continue
            window=feasible_window(points,idx,first,last,350/3.6)
            if not window: continue
            feasible.append(names[s])
            crossing=pass_time(own if fast_is_self else curves,at)
            if crossing is None: continue
            crossing+=0 if fast_is_self else shift
            if window[0]<=crossing<=window[1] and arrival<crossing<departure:
                candidates.append([names[s],round(crossing),arrival,departure])
    ev=dict(fast=fast_is_self,up=names[p[2]],down=names[q[2]],
            departure=[p[4],p[6]],arrival=[q[3],q[5]])
    ev['sites']=feasible
    if len(feasible)==len(candidates)==1: ev['pass']=candidates[0]
    return ev


@lru_cache(maxsize=None)
def ordered_schedule(trip):
    calls=timetable()[0][trip][3]
    return bool(calls) and all(d>=a and (not i or a>calls[i-1][2] or
        (a==calls[i-1][2] and s==calls[i-1][0])) for i,(s,a,d) in enumerate(calls))


@lru_cache(maxsize=1)
def comparison_index():
    by_stop, by_shape = defaultdict(set), defaultdict(set)
    for trip, (_, _, shape, calls) in timetable()[0].items():
        by_shape[shape].add(trip)
        for station, _, _ in calls:
            by_stop[station].add(trip)
    return by_stop, by_shape, {trip: i for i, trip in enumerate(timetable()[0])}


def overview(db, trip, day):
    """With day=None, compile all relative departures for later calendar filtering."""
    runs,calendar,names=timetable()
    number,service,shape,calls=runs[trip]
    if day is not None and day not in calendar[service]: return None
    if not ordered_schedule(trip): return dict(peers=[],events=[])
    own,anchors=profile(db,trip)
    routes=shared_routes(shape)
    lookup={s:(i,a,d) for i,(s,a,d) in enumerate(calls) if sum(p[0]==s for p in calls)==1}
    start,end=calls[0][2],calls[-1][1]
    peers=[]; events=[]
    by_stop, by_shape, source_order = comparison_index()
    common_counts = Counter(other for stop in lookup for other in by_stop[stop])
    candidates = {other for other, count in common_counts.items() if count >= 2}
    for other_shape in routes:
        candidates.update(by_shape[other_shape])
    for other in sorted(candidates, key=source_order.__getitem__):
        label,other_service,other_shape,visits = runs[other]
        if not ordered_schedule(other): continue
        paths=routes.get(other_shape,[])
        common=[(lookup[s][0],j,s) for j,(s,_,_) in enumerate(visits) if s in lookup]
        if not paths and len(common)<2: continue
        low=(start-visits[-1][1])//DAY+1
        high=(end-1-visits[0][2])//DAY
        dates=[offset for offset in range(low,high+1) if (day is None or day+offset in calendar[other_service])
               and (offset or other!=trip)]
        if not dates: continue
        repeated=Counter(s for _,_,s in common)
        common=[x for x in common if repeated[x[2]]==1]
        curves=other_anchors=None
        for offset in dates:
            shift=offset*DAY
            times=[(i,j,s,calls[i][1],calls[i][2],visits[j][1]+shift,visits[j][2]+shift)
                   for i,j,s in common]
            paired=list(zip(times,times[1:]))
            inversions=[(p,q) for p,q in paired if p[0]<q[0] and (p[4]-p[6])*(q[3]-q[5])<0]
            if not paths and not inversions: continue
            if curves is None: curves,other_anchors=profile(db,other) if paths else ([],[])
            # Only send portions where both trains can occupy the same corridor at once.
            shared=[]
            for span in paths:
                if not own or not curves: continue
                def occupancy(samples,side):
                    first,last=span[0][side]-1,span[-1][side]+1
                    inside=[t for t,s in samples if s is not None and first<=s<=last]
                    return (inside[0]-20,inside[-1]+20) if inside else None
                a,b=occupancy(own,0),occupancy(curves,1)
                if a and b and max(a[0],b[0]+shift)<=min(a[1],b[1]+shift):
                    shared.append(span)
            if not shared and not inversions: continue
            peer=len(peers)
            gaps=[]
            for i,j,s,aa,ad,ba,bd in times:
                gaps.append([names[s],aa if i and j else None,ba-aa if i and j else None,
                             ad if i<len(calls)-1 and j<len(visits)-1 else None,
                             bd-ad if i<len(calls)-1 and j<len(visits)-1 else None])
            peers.append(dict(id=other,number=label,offset=offset,spans=shared,gaps=gaps))
            if shared:
                ranges=[(s[0][1]-1,s[-1][1]+1) for s in shared]
                # Sparse absolute samples; keep neighbors of each retained point for interpolation.
                keep=set()
                for idx,(t,s) in enumerate(curves):
                    if start-21<=t+shift<=end+21 and s is not None and any(lo<=s<=hi for lo,hi in ranges):
                        keep.update(range(max(0,idx-1),min(len(curves),idx+2)))
                samples=[]; previous=-2
                for idx in sorted(keep):
                    t,s=curves[idx]
                    if idx!=previous+1 and samples: samples.append([samples[-1][0]+0.001,None])
                    samples.append([t+shift,s]); previous=idx
                peers[-1]['curve']=thin(samples)
            for p,q in inversions:
                ev=order_event(p,q,shared,calls,visits,own,anchors,curves,other_anchors,shift,names)
                ev['peer']=peer
                events.append(ev)
    return dict(peers=peers,events=events)


def thin(samples):
    """Bound distance error of the transmitted piecewise-linear trace to 3 m."""
    result=[]
    i=0
    while i<len(samples):
        if samples[i][1] is None:
            result.append(samples[i]); i+=1; continue
        j=i+1
        low,high=-math.inf,math.inf
        while j<len(samples) and samples[j][1] is not None:
            dt=samples[j][0]-samples[i][0]
            if dt<=0: break
            slope=(samples[j][1]-samples[i][1])/dt
            if not low<=slope<=high: break
            low=max(low,slope-3/dt); high=min(high,slope+3/dt)
            j+=1
        result.append(samples[i])
        if j>i+1: result.append(samples[j-1])
        i=j
    return result


@lru_cache(maxsize=24)
def relations(trip,service_day):
    try: day=date.fromisoformat(service_day).toordinal()
    except ValueError: return None
    if trip not in timetable()[0]: return None
    with closing(sqlite3.connect(TRACKS.as_uri()+'?mode=ro',uri=True)) as db:
        return overview(db,trip,day)
