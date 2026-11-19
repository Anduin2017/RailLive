"""Inferred paths must remain attributable and must never bridge an unknown leg."""
from bisect import bisect_right
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'build/app'))
from line_live import indexed_profile
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
