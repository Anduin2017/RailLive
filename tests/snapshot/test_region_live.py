"""Regional map regression cases for the offline snapshot."""
from bisect import bisect_right
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'build/app'))
from region_live import region_window
from snapshot_case import case_day
from datetime import date, timedelta

DAY = case_day()


def window(start, box='119,30,122,33', day=DAY):
    return region_window(dict(start=[str(start)], box=[box], day=[day]))


def distance(curve, time):
    times = [t for t,_ in curve]
    i = bisect_right(times,time)-1
    t, a = curve[i]
    if t == time:
        return a
    u, b = curve[i+1]
    return a + (b-a)*(time-t)/(u-t)


class RegionLiveTest(unittest.TestCase):
    def test_neighbor_windows_agree_at_boundary(self):
        before = window(47100)['runs']
        after = window(47400)['runs']
        for number in ('G1211','G7'):
            a,b = [next(r for r in runs if r['number']==number) for runs in (before,after)]
            self.assertEqual((a['id'],a['day']), (b['id'],b['day']))
            self.assertEqual(distance(a['curve'],47400), distance(b['curve'],47400))

    def test_enters_viewport_during_window(self):
        # The train is still west of this small box when the window begins.
        train = next(r for r in window(47100,'120.60,31.40,120.70,31.45')['runs'] if r['number']=='G1211')
        stop = next(s for s in train['stops'] if s[0]=='苏州北')
        self.assertEqual(stop[1:4], [47220,47340,'OHH'])
        self.assertEqual(len(stop[4] or stop[5]), 2)
        self.assertLess(distance(train['curve'],47100),distance(train['curve'],47220))
        wider = next(r for r in window(47100)['runs'] if r['id']==train['id'])
        self.assertEqual(train,wider)

    def test_midnight_and_query_limits(self):
        runs=window(0)['runs']
        self.assertTrue(any(r['day']==(date.fromisoformat(DAY)-timedelta(days=1)).isoformat() for r in runs))
        self.assertEqual(len(runs),len({(r['id'],r['day']) for r in runs}))
        self.assertEqual(window(0,day='2000-01-01'),{'covered':False,'runs':[]})
        self.assertIsNone(window(0,'-180,-90,180,90'))
        self.assertIsNone(window(1))


if __name__=='__main__':
    unittest.main()
