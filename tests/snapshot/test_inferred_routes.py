"""Inferred paths must remain attributable and must never bridge an unknown leg."""
from bisect import bisect_right
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'build/app'))
from region_live import trip_display
from corridor_paths import CorridorNetwork
from departure_paths import conventional_prefixes
from line_live import indexed_profile
from shape_excursions import station_hits
from train_relations import TIMETABLE, inferred_route, timetable


class InferredRoutesTest(unittest.TestCase):
    def test_provenance_and_gaps(self):
        with closing(sqlite3.connect(TIMETABLE)) as db:
            source = dict(db.execute("SELECT id,shape FROM trips"))
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


    def test_parallel_line_checks_follow_the_numbering_edition(self):
        self.assertIn('T',conventional_prefixes('20261117'))
        self.assertNotIn('K',conventional_prefixes('20261125'))
        self.assertIn('V',conventional_prefixes('20261125'))
