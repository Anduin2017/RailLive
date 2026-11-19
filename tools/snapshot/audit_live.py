"""Build the offline coverage report over every trip and every covered service date."""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from contextlib import closing
import csv
from datetime import date
from functools import lru_cache
from html import escape
import json
import sqlite3

from line_live import CORRIDORS, corridor, indexed_profile
from train_relations import DAY, TIMETABLE, TRACKS, matched_spans, order_event, ordered_schedule, timetable

OUT = TIMETABLE.parent/'live-audit'
FIELDS = ['全程可推演','部分区间可推演','仅到发']
DAY_FIELDS = FIELDS + ['较早日期始发','到发矛盾班次','候选越行','换序地点待定']


def stamp(day):
    return date.fromordinal(day).isoformat()


def service_ranges(days):
    spans = []
    for day in sorted(days):
        if spans and spans[-1][1] == day-1:
            spans[-1][1] = day
        else:
            spans.append([day,day])
    return ' / '.join(stamp(a) if a==b else stamp(a)+'~'+stamp(b) for a,b in spans)


def writer(stack, directory, filename, header):
    file = stack.enter_context((directory/filename).open('w',encoding='utf-8-sig',newline=''))
    csv_writer = csv.writer(file)
    csv_writer.writerow(header)
    return csv_writer.writerow


def leg_coverage(trip, calls, shape, names):
    curve,times,anchors = indexed_profile(trip) if shape else ([],[],[])
    missing, errors = [], []
    for i,(s,a,d) in enumerate(calls):
        if d<a:
            errors.append(names[s]+' 发车早于到达')
        if not i:
            continue
        prev,_,dep = calls[i-1]
        interval = names[prev]+' → '+names[s]
        if a<dep or (a==dep and s!=prev):
            errors.append(interval+' 运行时间非正')
        knots = curve[bisect_right(times,dep):bisect_left(times,a)]
        if (d<a or a<=dep or len(anchors)!=len(calls) or anchors[i-1] is None or anchors[i] is None
                or anchors[i]<=anchors[i-1] or any(at is None for _,at in knots)):
            missing.append(interval)
    count = max(0,len(calls)-1)
    valid = count-len(missing)
    return FIELDS[2 if not valid else 0 if valid==count else 1], valid, missing, errors


def dated_intervals(calls, days):
    """Half-open runs. Each instance contributes once to each civil day it touches."""
    if not calls: return
    start,end = calls[0][2],calls[-1][1]
    if end<=start: return
    for day in days:
        for offset in range(start//DAY,(end-1)//DAY+1):
            yield day,day+offset


@lru_cache(maxsize=256)
def blocks(shape):
    with closing(sqlite3.connect(TRACKS)) as db:
        by_key=defaultdict(list)
        for k,a,b in db.execute('SELECT block,start,end FROM blocks WHERE shape=? ORDER BY start',(shape,)):
            by_key[k].append((a,b))
        return by_key


@lru_cache(maxsize=4096)
def pair_spans(shape, other):
    if not shape or not other or shape.startswith('inferred:') or other.startswith('inferred:'): return []
    a,b=blocks(shape),blocks(other)
    spans=sorted((x,z,y,w) for k in a.keys() & b.keys() for x,z in a[k] for y,w in b[k])
    return matched_spans(shape,other,spans)


def scan_orders(runs, calendar, names, emit, daily):
    # Index all pairs of ordered, unambiguous station visits. Unlike a scan of
    # consecutive stops, this includes express trains passing intermediate stops.
    pairs=defaultdict(list)
    positions={}
    for trip,(_,service,_,calls) in runs.items():
        if not calendar[service] or not ordered_schedule(trip): continue
        repeated=Counter(s for s,_,_ in calls)
        unique={s:i for i,(s,_,_) in enumerate(calls) if repeated[s]==1}
        positions[trip]=unique
        for s,i in unique.items():
            dep=calls[i][2]
            for j in range(i+1,len(calls)):
                target,arrival,_=calls[j]
                if target in unique and arrival>dep:
                    pairs[s,target].append((dep%DAY,arrival-dep,trip,i,j))
    @lru_cache(maxsize=None)
    def overlaps(s1,s2,offset):
        return tuple(sorted(d for d in calendar[s1] if d+offset in calendar[s2]))

    counts=Counter()
    for (s,target), rows in pairs.items():
        if len(rows)<2: continue
        rows.sort()
        starts=[r[0] for r in rows]
        for start,duration,slow,i,j in rows:
            num,service,shape,calls=runs[slow]
            for extra in range((start+duration)//DAY+1):
                begin=bisect_right(starts,start-extra*DAY)
                end=bisect_left(starts,start+duration-extra*DAY)
                for later,travel,fast,k,l in rows[begin:end]:
                    if later+extra*DAY+travel>=start+duration: continue
                    label,other_service,other_shape,visits=runs[fast]
                    # Adjacent common anchors only; nested station pairs describe
                    # the same change. Discard ambiguous reversed visit orders.
                    if any(u in positions[fast] and u in positions[slow] for u,_,_ in calls[i+1:j]): continue
                    if any(u in positions[slow] and u in positions[fast] for u,_,_ in visits[k+1:l]): continue
                    offset=calls[i][2]//DAY+extra-visits[k][2]//DAY
                    days=overlaps(service,other_service,offset)
                    if not days: continue
                    shift=offset*DAY
                    def anchor(a,b):
                        u,arr,dep=calls[a]
                        return a,b,u,arr,dep,visits[b][1]+shift,visits[b][2]+shift
                    p,q=anchor(i,k),anchor(j,l)
                    sites=[]
                    if j>i+1 and shape and other_shape:
                        spans=pair_spans(shape,other_shape)
                        if spans:
                            own,_,ax=indexed_profile(slow)
                            peer,_,bx=indexed_profile(fast)
                            ev=order_event(p,q,spans,calls,visits,own,ax,peer,bx,shift,names)
                            sites=ev.get('pass',[])
                    kind='候选越行' if sites else '换序地点待定'
                    counts[kind]+=1
                    counts[kind+'实例']+=len(days)
                    # Attribute an inversion to the day the later train departs
                    # the upstream anchor; its other anchor can be after midnight.
                    day_offset=p[6]//DAY
                    for d in days: daily[d+day_offset][kind]+=1
                    emit([kind,num,slow,label,fast,names[s],names[target],p[4],p[6],q[3],q[5],
                          sites[0] if sites else '',sites[1] if sites else '',offset,service_ranges_cached(days)])
    return {k:counts[k] for kind in DAY_FIELDS[-2:] for k in (kind,kind+"实例")}


@lru_cache(maxsize=2048)
def service_ranges_cached(days):
    return service_ranges(days)


def generate(directory):
    from contextlib import ExitStack
    runs,calendar,names=timetable()
    with closing(sqlite3.connect(TIMETABLE)) as db:
        meta=json.loads(db.execute('SELECT json FROM metadata').fetchone()[0])
        version=meta['feed_version']
    daily=defaultdict(Counter)
    for day in range(date.fromisoformat(meta["feed_start_date"]).toordinal(),date.fromisoformat(meta["feed_end_date"]).toordinal()+1):
        daily[day]
    for days in calendar.values():
        for day in days: daily[day]
    totals=Counter()
    with ExitStack() as stack:
        emit=writer(stack,directory,'trips.csv',['车次','trip_id','service_id','始发日期范围','始发秒','终到秒','位置覆盖','可推演区间数','全部区间数','径路状态','缺失区间','时刻矛盾'])
        for trip,(number,service,shape,calls) in runs.items():
            kind,valid,missing,errors=leg_coverage(trip,calls,shape,names)
            totals[kind]+=1
            if not shape: totals['缺少径路']+=1
            elif shape.startswith('inferred:'):
                totals['推算径路']+=1
                if valid:
                    totals['补全后可推演']+=1
                curve,times,_=indexed_profile(trip)
                depart=calls[0][2]
                # A moving sample strictly inside the first leg rules out a lone station anchor.
                i=bisect_right(times,depart)
                if i<len(curve) and curve[i][0]<calls[1][1] and curve[i][1] is not None:
                    totals['补全始发区间']+=1
            if errors: totals['到发矛盾班次']+=1
            emit([number,trip,service,service_ranges_cached(tuple(sorted(calendar[service]))),calls[0][2] if calls else "",calls[-1][1] if calls else "",kind,valid,max(len(calls)-1,0),
                  '参考车次推算' if shape.startswith('inferred:') else '有几何' if shape else '无几何',' / '.join(missing),' / '.join(errors)])
            for service_day,day in dated_intervals(calls,calendar[service]):
                daily[day][kind]+=1
                if service_day<day: daily[day]['较早日期始发']+=1
                if errors: daily[day]['到发矛盾班次']+=1
        emit=writer(stack,directory,'orders.csv',['结论','先发车次','先发trip_id','后发车次','后发trip_id','上游','下游','先发车开','后发车开','先发车到','后发车到','候选站','推算通过秒','后车始发日偏移','先发车始发日期范围'])
        orders=scan_orders(runs,calendar,names,emit,daily)
        emit=writer(stack,directory,'days.csv',['北京时间日期',*DAY_FIELDS])
        for day,counts in sorted(daily.items()):
            emit([stamp(day),*[counts[k] for k in DAY_FIELDS]])
    lines=[]
    for key,(line,*_) in CORRIDORS.items():
        _, directions=corridor(key)
        lines.append(dict(name=line,forward=len(directions[0]),reverse=len(directions[1])))
    from region_live import INDEX
    with closing(sqlite3.connect(INDEX)) as db:
        estimates=dict(db.execute('SELECT kind,count(*) FROM display_legs GROUP BY kind'))
    departures=sum(max(0,len(calls)-1) for _,_,_,calls in runs.values())
    report=dict(snapshot=version,firstDay=stamp(min(daily)),lastDay=stamp(max(daily)),
                days=len(daily),trips={k:totals[k] for k in [*FIELDS,"推算径路","补全后可推演","补全始发区间","缺少径路","到发矛盾班次"]},orders=orders,corridors=lines,display=dict(departures=departures,estimated=estimates))
    (directory/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    headline=''.join(f'<li>{k}：{totals[k]:,} 个班次模板</li>' for k in FIELDS)
    details=''.join(f'<li>{escape(l["name"])}</li>' for l in lines)
    (directory/'index.html').write_text(f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>直播覆盖报告</title><link rel="stylesheet" href="/audit.css"><main><a href="/">返回地图</a><h1>直播覆盖报告</h1>
<p>{version} 快照 · 北京时间 {report['firstDay']} 至 {report['lastDay']} · {len(daily)} 天</p>
<h2>地图发车展示</h2><p>{departures:,} 个停站间发车区间中，{departures-sum(estimates.values()):,} 个沿用原运动曲线。其余按区间显示{'、'.join(k+' '+format(v,',')+' 个' for k,v in estimates.items())}。底图推测沿本地轨道绘制，同类车径路推测借用同类车次的原始轨迹，底图接轨推测沿底图铁路并在局部接续断点，琼州海峡铁路轮渡按两端铁路港口推演。</p>
<h2>具备径路和运动曲线的班次</h2><ul>{headline}</ul><p>按每个相邻停站区间核验径路锚点、到发时间与运动曲线。其中 {totals['推算径路']:,} 个模板含参考车次推算的径路，{totals['补全始发区间']:,} 个可以从始发站驶出。另有 {totals['缺少径路']:,} 个缺少径路，检出到发时间矛盾的模板有 {totals['到发矛盾班次']:,} 个。</p>
<a download href="trips.csv">下载班次明细 CSV</a> · <a download href="days.csv">下载逐日扫描 CSV</a>
<h2>区间车序</h2><p>扫描同向先后访问两个共同车站的计划班次，统计严格后发先到；时刻矛盾班次、同分钟并列和重复访问的歧义站点不参与判定。
共 {orders.get('候选越行',0):,} 组有候选站，{orders.get('换序地点待定',0):,} 组地点待定。相同班次模板、区间和始发日偏移合并列出，适用日期随行保存。</p>
<a download href="orders.csv">下载换序证据 CSV</a><p>秒数相对先发列车服务日零时。逐日换序按后发列车离开上游站的日期计数。候选站基于同向共线、单次换序和公布停车窗口推演。</p>
<h2>已校验的线路车流数据</h2><ul>{details}</ul><a href="summary.json">查看机器可读摘要</a></main></html>""")
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    from tempfile import TemporaryDirectory
    from pathlib import Path
    from build_relations import fingerprint
    from region_live import verify
    with closing(sqlite3.connect(TRACKS)) as db:
        if db.execute('SELECT signature FROM meta').fetchone()[0] != fingerprint():
            raise ValueError('运动索引过期，请先重建直播索引')
    verify()
    OUT.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=OUT.parent) as staging:
        directory=Path(staging)
        generate(directory)
        for name in ('trips.csv','orders.csv','days.csv','summary.json','index.html'):
            (directory/name).replace(OUT/name)
