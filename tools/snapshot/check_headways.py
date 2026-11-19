"""Audit scheduled order reversals for sections departed on 2026-11-18 (local time)."""
import sqlite3
from collections import Counter, defaultdict
from datetime import date

from server import GRAPH, DB

db = sqlite3.connect(DB)
day = date(2026, 11, 18)
names = dict(db.execute("select id,name from stops"))
edges = defaultdict(set)
for eid, feature in GRAPH.features.items():
    points = feature.get("stations", [])
    for p, q in zip(points, points[1:]):
        a, b = (GRAPH.stop_for.get(point.get("kid")) for point in (p, q))
        if a and b and a != b:
            edges[a,b].add(eid)
            edges[b,a].add(eid)

def seconds(value):
    h, m, s = map(int, value.split(":"))
    return 3600*h + 60*m + s

calendar = defaultdict(list)
for service, value in db.execute("select service,date from dates where kind=1"):
    calendar[service].append((date.fromisoformat(value) - day).days)
bins = defaultdict(list)
z = []
for trip, number, service, shape in db.execute("select * from trips"):
    rows = [(code, seconds(arr), seconds(dep)) for code, arr, dep in db.execute(
        "select station,arrival,departure from times where trip=? order by seq", (trip,))]
    if not rows:
        continue
    for offset in calendar[service]:
        shift = offset * 86400
        if number == "Z164":
            z.append((rows[0][2]+shift, rows[-1][1]+shift))
        if rows[0][2]+shift >= 86400 or rows[-1][1]+shift < 0:
            continue
        for (a, _, start), (b, end, _) in zip(rows, rows[1:]):
            if a != b and 0 <= start+shift < 86400 and end > start:
                bins[a,b].append((start+shift, end+shift, number, shape))
counts = Counter()
examples = []
for edge, visits in bins.items():
    visits.sort(key=lambda v:v[0])
    for i, (start,end,num,shape) in enumerate(visits):
        for later,arrival,other,path in visits[i+1:]:
            if later >= end:
                break
            if later == start or arrival >= end or num == other:
                continue
            counts['inversions'] += 1
            if edge not in edges:
                continue
            counts['adjacent'] += 1
            line_names = sorted(GRAPH.features[e]['name'] for e in edges[edge])
            examples.append((names[edge[0]], names[edge[1]], num, start//60, end//60,
                             other, later//60, arrival//60, line_names))
print(counts)
for row in examples:
    print(*row)
events = sorted((t, delta) for start,end in z for t,delta in [(start,1),(end,-1)])
active = peak = 0
for _,delta in events:
    active += delta
    peak = max(peak, active)
print("Z164 duration minutes, snapshot peak, trips touching day", sorted({(b-a)//60 for a,b in z}),peak,sum(a<86400 and b>0 for a,b in z))
