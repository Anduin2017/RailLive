"""Inferred paths must remain attributable and must never bridge an unknown leg."""
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'build/app'))
from region_live import trip_display
from corridor_paths import CorridorNetwork
from departure_paths import conventional_trips, inherited_shapes
from line_live import indexed_profile
from shape_excursions import station_hits
from train_relations import TIMETABLE, inferred_route, metres, point, timetable


class InferredRoutesTest(unittest.TestCase):
    def test_provenance_and_gaps(self):
        with closing(sqlite3.connect(TIMETABLE)) as db:
            source = dict(db.execute("SELECT id,shape FROM trips"))
            inherited = inherited_shapes(db)
        runs = timetable()[0]
        for trip, (_,_,shape,stops) in runs.items():
            if not shape.startswith('inferred:'):
                continue
            self.assertFalse(source[trip],trip)
            path,legs = inferred_route(trip)
            self.assertEqual(len(legs),len(stops)-1)
            curve,times,_ = indexed_profile(trip)
            for i,leg in enumerate(legs):
                if leg:
                    a,b,donor = leg
                    self.assertTrue(0<=a<b<len(path) and source[donor],(trip,i))
                    self.assertNotIn(donor, inherited, (trip, i))
                elif curve and stops[i+1][1]>stops[i][2]:
                    k=bisect_right(times,stops[i][2])
                    self.assertIsNone(curve[k][1],(trip,i))

    def test_parallel_corridors_reject_high_speed_donor_geometry(self):
        network = CorridorNetwork()
        runs = timetable()[0]
        for number, pair, line, bounds, via in [
            ('K124', {'JBH','YWH'}, '沪昆线', (52000,55000), (119.973613,29.316180)),  # 义乌西
            ('K554', {'ECH','WHH'}, '皖赣线', (66000,70000), (118.570749,31.141146)),  # 湾沚
        ]:
            with self.subTest(number=number):
                trip = next(t for t,r in runs.items() if r[0]==number)
                stops = runs[trip][3]
                seq = next(i for i,(a,b) in enumerate(zip(stops,stops[1:])) if {a[0],b[0]}==pair)
                self.assertIsNone(inferred_route(trip)[1][seq])
                leg = next(row for row in trip_display(trip) if row['seq']==seq)
                self.assertEqual(leg['kind'],'底图推测')
                self.assertTrue(bounds[0]<leg['path'][-1][0]<bounds[1])
                geometry = [p[1:] for p in leg['path']]
                distances = [p[0] for p in leg['path']]
                self.assertTrue(any(station_hits(via,geometry,distances,150)))
                self.assertFalse(network.follows_parallel_high_speed(geometry,line))


    def test_jiangshan_station_access_in_both_directions(self):
        runs = timetable()[0]
        for number, origin in [('K209','QEH'), ('T102','JUH')]:
            with self.subTest(number=number):
                trip = next(t for t,r in runs.items() if r[0]==number)
                seq = next(i for i,stop in enumerate(runs[trip][3]) if stop[0]==origin)
                self.assertIsNone(inferred_route(trip)[1][seq])
                leg = next(row for row in trip_display(trip) if row['seq']==seq)
                self.assertEqual(leg['kind'],'底图推测')
                distances, path = [r[0] for r in leg['path']], [r[1:] for r in leg['path']]
                self.assertTrue(29800 < distances[-1] < 30200)
                # Follow the approach towards Jiangshan in either travel direction.
                axis = distances,path
                station = path[-1] if origin=='QEH' else path[0]
                offsets = [2000,1500,1000,500,0]
                gaps = [metres(station,point(axis,distances[-1]-d if origin=='QEH' else d))
                        for d in offsets]
                self.assertTrue(all(a>b for a,b in zip(gaps,gaps[1:])),gaps)

    def test_ordinary_routes_follow_the_trip_service_days(self):
        with closing(sqlite3.connect(TIMETABLE)) as db:
            eligible = conventional_trips(db)
            calendar = defaultdict(set)
            for number, trip, day in db.execute("""
                SELECT number, id, date FROM trips JOIN dates USING(service)
                WHERE kind=1 AND number IN ('1461','T109','V6061','N3')
            """):
                self.assertIn(trip, eligible)
                calendar[number].add(day)
            for old_number, new_number in [('1461','V6061'), ('T109','N3')]:
                self.assertEqual(max(calendar[old_number]), '20261124')
                self.assertEqual(min(calendar[new_number]), '20261125')
                rows = db.execute("""
                    SELECT number, min(departure), max(arrival)
                    FROM times JOIN trips ON trip=id
                    WHERE number IN (?,?) GROUP BY number
                """, (old_number, new_number)).fetchall()
                expected = ('11:59:00', '30:45:00') if new_number=='V6061' else ('20:04:00', '35:02:00')
                self.assertEqual([tuple(row[1:]) for row in rows], [expected, expected])
