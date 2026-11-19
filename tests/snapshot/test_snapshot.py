"""Cross-check the shipped report, live endpoints and dated train identities."""
import csv
from datetime import date
import json
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'build/app'))
from audit_live import DAY, OUT, DAY_FIELDS, FIELDS
from line_live import CORRIDORS, traffic_window
from server import DB, Handler
from snapshot_case import case_day
from train_relations import timetable, ordered_schedule
from region_live import INDEX
from line_live import indexed_profile


class SnapshotRegression(unittest.TestCase):
    def test_map_station_with_reused_telegraph(self):
        with closing(sqlite3.connect(DB)) as db:
            db.row_factory = sqlite3.Row
            def station(key):
                return Handler.query(db, '/api/station/'+key, {})
            suzhou = station('07716fd88114427883640c2d226dbc8d')
            self.assertEqual(suzhou, station('OHH'))
            self.assertTrue(suzhou['hasTimetable'] and suzhou['sourceCard'])
            self.assertIn('G1211', {call['number'] for call in suzhou['trains']})
            other = station('c780a88613ad4164a6fa90f876067bb6')
            self.assertEqual(other['name'], '双潮')
            self.assertNotEqual(other['id'], 'OHH')
            self.assertFalse(other['hasTimetable'])

    def test_long_distance_identity(self):
        day=date.fromisoformat(case_day()).toordinal()
        runs,calendar,names=timetable()
        intervals=[]
        for trip,(number,service,_,stops) in runs.items():
            if number!='Z164': continue
            for start_day in calendar[service]:
                shift=(start_day-day)*DAY
                a,b=shift+stops[0][2],shift+stops[-1][1]
                if a<DAY and b>0:
                    intervals.append((a,b,(trip,start_day),stops,shift))
        self.assertEqual({r[2][1] for r in intervals},{day,day-1,day-2})
        times={0,*[a for a,*_ in intervals if 0<=a<DAY]}
        self.assertEqual(max(sum(a<=t<b for a,b,*_ in intervals) for t in times),2)
        now=19*3600+22*60
        self.assertEqual({names[s] for a,b,_,stops,shift in intervals if a<=now<b
                          for s,arr,dep in stops if arr+shift<=now<dep+shift},{'西宁','苏州'})
        with closing(sqlite3.connect(DB)) as db:
            db.row_factory=sqlite3.Row
            for _,_,(trip,start),_,_ in intervals:
                data=Handler.query(db,'/api/trip/'+trip,{})
                self.assertIn(date.fromordinal(start).strftime('%Y%m%d'),data['dates'])

    def test_g1211_motion_and_stops(self):
        runs,_,names=timetable()
        trip,(_,_,_,calls)=next((i,v) for i,v in runs.items() if v[0]=='G1211')
        curve,_,anchors=indexed_profile(trip)
        for i,((s,arr,dep),(dest,end,_)) in enumerate(zip(calls,calls[1:])):
            knots=[(t,x) for t,x in curve if dep<=t<=end]
            self.assertEqual(knots[0],(dep,anchors[i]))
            self.assertEqual(knots[-1],(end,anchors[i+1]))
            self.assertTrue(all(x==anchors[i] for t,x in curve if arr<=t<=dep))
            speeds=[3.6*(y-x)/(u-t) for (t,x),(u,y) in zip(knots,knots[1:]) if t<u]
            self.assertGreaterEqual(min(speeds),0)
            self.assertLessEqual(max(speeds),352)
            if (names[s],names[dest])==('石家庄','衡水北'):
                self.assertLess(max(speeds),252)
            if (names[s],names[dest])==('济南西','南京南'):
                self.assertGreater(max(speeds),250)

    def test_corridor_entry_points(self):
        with closing(sqlite3.connect(DB)) as db:
            db.row_factory=sqlite3.Row
            for key,(name,*_) in CORRIDORS.items():
                info=Handler.query(db,'/api/line/'+name,{})
                self.assertEqual(info['live']['key'],key)
                runs=traffic_window(case_day(),'43200',key)['runs']
                self.assertEqual({s[2] for r in runs for s in r['spans']},{0,1})
                self.assertEqual(len({(r['id'],r['day']) for r in runs}),len(runs))

    def test_quarantined_profiles(self):
        runs=timetable()[0]
        invalid={trip for trip in runs if not ordered_schedule(trip)}
        for trip in invalid:
            self.assertEqual(indexed_profile(trip)[0],[])
        with closing(sqlite3.connect(INDEX)) as db:
            indexed={r[0] for r in db.execute('SELECT DISTINCT trip FROM windows')}
        self.assertFalse(invalid & indexed)

    def test_report(self):
        summary=json.loads((OUT/'summary.json').read_text())
        with (OUT/'trips.csv').open(encoding='utf-8-sig') as file:
            rows=list(csv.DictReader(file))
        self.assertEqual({r['trip_id'] for r in rows},set(timetable()[0]))
        self.assertEqual(len(rows),sum(summary['trips'][k] for k in FIELDS))
        self.assertTrue(all(r['位置覆盖']=='仅到发' for r in rows if r['时刻矛盾']))
        with (OUT/'orders.csv').open(encoding='utf-8-sig') as file:
            pairs={(r['先发车次'],r['后发车次'],r['上游'],r['下游']):r['候选站'] for r in csv.DictReader(file)}
        for fast in ('G7','G1211'):
            self.assertEqual(pairs['G817',fast,'济南西','南京南'],'蚌埠南')
        self.assertEqual(pairs['G6605','G1284','新乡东','郑州东'],'')
        with (OUT/'days.csv').open(encoding='utf-8-sig') as file:
            days=list(csv.DictReader(file))
        for kind in DAY_FIELDS[-2:]:
            self.assertEqual(sum(int(row[kind]) for row in days),summary['orders'][kind+'实例'])


if __name__=='__main__': unittest.main()
