"""Audit every scheduled departure against the two layers used by the map."""
from bisect import bisect_left,bisect_right
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'build/app'))
from departure_paths import geographic
from region_live import DAY, WINDOW, INDEX, station_locations, timetable, indexed_profile, unpack


class DepartureCoverageTest(unittest.TestCase):
    def test_every_departure(self):
        locations=station_locations()
        with closing(sqlite3.connect(INDEX)) as db:
            primary={(trip,bucket,offset):(w,s,e,n) for bucket,trip,offset,w,s,e,n in db.execute('SELECT * FROM windows')}
            display={(trip,seq,bucket,offset):(w,s,e,n) for bucket,offset,trip,seq,w,s,e,n in db.execute('SELECT * FROM display_windows')}
            legs={(trip,seq):(start,end,kind,unpack(blob)) for trip,seq,start,end,kind,blob in db.execute('SELECT * FROM display_legs')}
        self.assertFalse({leg[2] for leg in legs.values()} & {'站间示意','站序示意'})
        for trip,(num,_,shape,calls) in timetable()[0].items():
            curve,times,_=indexed_profile(trip) if shape else ([],[],[])
            for seq,((code,_,departure),(_,arrival,next_dep)) in enumerate(zip(calls,calls[1:])):
                end=min(arrival,next_dep) if seq+2<len(calls) else arrival
                mapped,gtfs=locations[code]
                lon,lat=geographic(mapped) if mapped else gtfs
                leg=legs.get((trip,seq))
                for t in range(departure//WINDOW*WINDOW,end,WINDOW):
                    bucket,offset=t%DAY,t//DAY
                    box=(display.get((trip,seq,bucket,offset)) if leg else primary.get((trip,bucket,offset)))
                    self.assertIsNotNone(box,(num,seq,t))
                    if t<=departure:
                        w,s,e,n=box
                        self.assertTrue(w-.04<=lon<=e+.04 and s-.025<=lat<=n+.025,(num,seq,box))
                if leg:
                    self.assertEqual((departure,end),leg[:2],(num,seq))
                    self.assertGreater(leg[3][-1][0],0,(num,seq))
                    self.assertTrue(all(a[0]<b[0] for a,b in zip(leg[3],leg[3][1:])),(num,seq))
                else:
                    a,b=bisect_right(times,departure)-1,bisect_left(times,end)
                    self.assertEqual(curve[a][0],departure,(num,seq))
                    self.assertTrue(all(v is not None for _,v in curve[a:b+1]),(num,seq))
                    self.assertGreater(curve[b][1],curve[a][1],(num,seq))
                    if end-departure>=60 and curve[b][1]-curve[a][1]>=1000:
                        self.assertTrue(any(curve[a][1]<v<curve[b][1] for _,v in curve[a+1:b]),
                                        (num,seq,'no movement between scheduled stops'))

    def test_repaired_jiangxi_and_ferry_corridors(self):
        from departure_paths import metres, project_segment
        from region_live import trip_display

        cases = {('RCG','NCG'): ('K1258','底图接轨推测',[(115.9,29.59)]),
                 ('HSN','NCG'): ('K1087','底图接轨推测',[(115.9,29.59)]),
                 ('VUQ','XJQ'): (None,'含铁路轮渡推测',[(110.1498,20.0394),(110.1241,20.2275)])}
        found = set()
        for trip,(number,_,_,calls) in timetable()[0].items():
            for seq,(a,b) in enumerate(zip(calls,calls[1:])):
                key=(a[0],b[0])
                if key not in cases: key=key[::-1]
                if key not in cases or key in found: continue
                train,kind,points=cases[key]
                if train and train!=number: continue
                leg=next(row for row in trip_display(trip) if row['seq']==seq)
                self.assertEqual(leg['kind'],kind)
                coords=[v[1:] for v in leg['path']]
                for p in points:
                    self.assertLess(min(metres(p,project_segment(p,x,y)[1])
                                        for x,y in zip(coords,coords[1:])),3500,key)
                found.add(key)
        self.assertEqual(found,set(cases))

    def test_every_qiongzhou_crossing_uses_both_rail_ports(self):
        from departure_paths import metres
        from region_live import trip_display

        checked=set()
        south=(110.149805,20.039405)
        north=(110.124136,20.227533)
        for trip,(number,_,_,calls) in timetable()[0].items():
            for seq,(a,b) in enumerate(zip(calls,calls[1:])):
                if {a[0],b[0]} not in ({'VUQ','XJQ'},{'VUQ','ZWQ'}): continue
                leg=next(row for row in trip_display(trip) if row['seq']==seq)
                self.assertEqual(leg['kind'],'含铁路轮渡推测',number)
                coords=[v[1:] for v in leg['path']]
                if a[0]!='VUQ': coords.reverse()
                across=[(x,y) for x,y in zip(coords,coords[1:]) if x[1]<20.14<=y[1]]
                self.assertEqual(len(across),1,number)
                x,y=across[0]
                self.assertLess(metres(x,south),2,number)
                self.assertLess(metres(y,north),2,number)
                self.assertLess(min(metres(p,(110.12684,20.24267)) for p in coords),600,number)
                checked.add(number)
        self.assertTrue({'K512','Z502','Z112'}<=checked)

    def test_t10_ankang_shiyan_display(self):
        from departure_paths import metres
        from region_live import region_window, trip_display

        trip='7700000T100A'
        leg=next(row for row in trip_display(trip) if row['seq']==6)
        self.assertEqual(leg['kind'],'底图推测')
        path=leg['path']
        self.assertTrue(185000 < path[-1][0] < 215000)
        checkpoints=[]
        for code in ('XUY','BIY'):
            mapped,gtfs=station_locations()[code]
            station=geographic(mapped) if mapped else gtfs
            stop=min(path,key=lambda p:metres(p[1:],station))
            self.assertLess(metres(stop[1:],station),1000)
            checkpoints.append(stop[0])
        self.assertLess(*checkpoints)
        midpoint=min(path,key=lambda p:abs(p[0]-path[-1][0]/2))
        _,x,y=midpoint
        region=region_window(dict(day=['2026-11-19'],start=[str(63780//WINDOW*WINDOW)],
                                  box=[f'{x-.2},{y-.2},{x+.2},{y+.2}']))
        run=next(row for row in region['display'] if row['id']==trip)
        self.assertEqual(run['kind'],leg['kind'])
        self.assertIn(midpoint,run['path'])

    def test_k7032_overnight_conventional_track(self):
        from departure_paths import metres
        from region_live import region_window, trip_display

        trip='01000K703200'
        leg=next(row for row in trip_display(trip) if row['seq']==3)
        self.assertEqual(leg['kind'],'底图推测')
        path=leg['path']
        self.assertTrue(115000 < path[-1][0] < 130000)
        positions=[]
        for code in ('XZB','HUB'):
            station=geographic(station_locations()[code][0])
            stop=min(path,key=lambda p:metres(station,p[1:]))
            self.assertLess(metres(station,stop[1:]),1000)
            positions.append(stop[0])
        self.assertLess(*positions)
        _,x,y=min(path,key=lambda p:abs(p[0]-path[-1][0]/2))
        region=region_window(dict(day=['2026-11-20'],start=[str((4*3600+20*60)//WINDOW*WINDOW)],
                                  box=[f'{x-.25},{y-.25},{x+.25},{y+.25}']))
        run=next(row for row in region['display'] if row['id']==trip)
        self.assertEqual(run['day'],'2026-11-19')
        self.assertEqual(run['length'],path[-1][0])

    def test_projected_stations_on_simplified_map(self):
        from departure_paths import SegmentNetwork
        # Stations lie inside long simplified edges. The route must follow
        # the bend and must not turn onto a separate line at its crossing.
        bend=(120.05,40)
        network=SegmentNetwork([[(120,40),bend,(120.05,40.06)],
                                [(120.02,39.95),(120.02,40.06)]])
        start,end=(120.025,40.0001),(120.0501,40.035)
        path=network.projected_route(start,end)
        self.assertIn(bend,path)
        self.assertEqual((start,end),(path[0],path[-1]))
        self.assertIsNone(network.projected_route(start,(120.02,40.04)))
