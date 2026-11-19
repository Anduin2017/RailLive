"""Regressions for timetable legs that used a long detour in the shape graph."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'build/app'))
from region_live import trip_display
from train_relations import timetable


class PeakRouteTests(unittest.TestCase):
    def test_routes_used_for_difficult_connections(self):
        runs,_,names=timetable()
        by_number={run[0]:(trip,run[3]) for trip,run in runs.items()}
        cases=[
            ('G345','荆门西','荆州',77,81),
            ('Z272','胶州北','潍坊',95,105),
            ('G8396','安庆西','合肥南',155,167),
            ('G6594','梅州西','惠州北',226,238),
            ('G1741','合肥西','金寨',124,135),
            ('D2567','中川机场东','天祝西',72,82),
            ('D8888','天祝西','兰州新区',76,86),
            ('D2638','天祝西','兰州新区',76,86),
        ]
        for number,first,last,lo,hi in cases:
            with self.subTest(number=number):
                trip,calls=by_number[number]
                stops=[names[code] for code,_,_ in calls]
                seq=next(i for i in range(len(stops)-1) if stops[i:i+2]==[first,last])
                leg=next(row for row in trip_display(trip) if row['seq']==seq)
                self.assertEqual(leg['kind'],'底图接轨推测')
                self.assertTrue(lo < leg['path'][-1][0]/1000 < hi)
                self.assertEqual((leg['start'],leg['end']),(calls[seq][2],calls[seq+1][1]))
