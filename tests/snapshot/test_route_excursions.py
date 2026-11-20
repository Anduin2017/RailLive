"""Check real station visits and false excursions in the effective offline routes."""
import gzip
import json
from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'build/app'))
from line_live import indexed_profile
from region_live import trip_display
from train_relations import timetable
from shape_excursions import cumulative, repair_shapes, simplify_leg, station_hits


class RouteExcursionTest(unittest.TestCase):
    def test_shijiazhuang_terminal(self):
        trip = '24000G672104'
        stops = timetable()[0][trip][3]
        self.assertEqual([s for s, _, _ in stops][-2:], ['DOP', 'SJP'])
        anchors = indexed_profile(trip)[2]
        self.assertGreater(anchors[-1]-anchors[-2], 77500)
        self.assertLess(anchors[-1]-anchors[-2], 82500)
        self.assertFalse(any(leg['seq']==len(stops)-2 for leg in trip_display(trip)))

    def test_known_excess_mileage(self):
        for number,seq,lo,hi in [('S5127',0,2.8,4),('C2601',0,20,24),('C1158',4,27,34),('D962',3,390,440)]:
            trip = next(i for i,r in timetable()[0].items() if r[0]==number)
            override = next((l for l in trip_display(trip) if l['seq']==seq),None)
            anchors = indexed_profile(trip)[2]
            km = (override['path'][-1][0] if override else anchors[seq+1]-anchors[seq])/1000
            self.assertTrue(lo<km<hi,(number,km))

    def test_nanjing_to_hefei_fallback_uses_direct_corridor(self):
        trip = '55000G726860'
        self.assertEqual([s for s, _, _ in timetable()[0][trip][3][5:7]], ['NKH', 'HFH'])
        leg = next(leg for leg in trip_display(trip) if leg['seq']==5)
        self.assertEqual(leg['kind'], '底图接轨推测')
        path = [p[1:] for p in leg['path']]
        distances = [p[0] for p in leg['path']]
        self.assertTrue(150000<distances[-1]<158000)
        quanjiao = next(station_hits((118.277426,32.0623412),path,distances,1500))
        feidong = next(station_hits((117.4799073,31.8640865),path,distances,1500))
        self.assertLess(quanjiao[1],feidong[1])
        self.assertFalse(any(station_hits((118.3860019,31.349784),path,distances,2000)))

    def test_documented_detour_is_kept(self):
        # A scheduled in-and-out branch with matching mileage stays intact.
        path = [(115,39),(115.04,39),(115.04,39.1),(115.04,39),(115.10,39)]
        self.assertIsNone(simplify_leg(path,1800,220,[cumulative(path)[-1]]))

    def test_shared_shape_preserves_scheduled_turnback(self):
        a, fork, turnback, b = [(115,35),(115.04,35),(115.04,35.1),(115.12,35)]
        coords = [a,fork,turnback,fork,b]
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript("""
                CREATE TABLE shapes(id TEXT PRIMARY KEY,path BLOB);
                CREATE TABLE trips(id TEXT PRIMARY KEY,number TEXT,shape TEXT);
                CREATE TABLE times(trip TEXT,seq INTEGER,station TEXT,arrival TEXT,departure TEXT);
                CREATE TABLE stops(id TEXT PRIMARY KEY,lon REAL,lat REAL);
                CREATE TABLE line_stations(line TEXT,code TEXT,mileage TEXT);
                INSERT INTO trips VALUES ('direct','G1','shared'),('stopping','G2','shared');
                INSERT INTO line_stations VALUES ('main','A','0 km'),('main','B','11 km');
            """)
            db.execute('INSERT INTO shapes VALUES (?,?)',('shared',gzip.compress(json.dumps(coords).encode())))
            db.executemany('INSERT INTO stops VALUES (?,?,?)',[('A',*a),('X',*turnback),('B',*b)])
            for trip,visits in [('direct',[('A','08:00:00'),('B','08:08:00')]),
                                ('stopping',[('A','08:00:00'),('X','08:20:00'),('B','08:40:00')])]:
                db.executemany('INSERT INTO times VALUES (?,?,?,?,?)',
                               [(trip,i,code,t,t) for i,(code,t) in enumerate(visits)])
            self.assertEqual(repair_shapes(db),1)
            paths = {trip:json.loads(gzip.decompress(blob)) for trip,blob in db.execute(
                'SELECT trips.id,path FROM trips JOIN shapes ON shape=shapes.id')}
            self.assertEqual(paths['stopping'],list(map(list,coords)))
            self.assertEqual(paths['direct'],list(map(list,[a,fork,b])))
