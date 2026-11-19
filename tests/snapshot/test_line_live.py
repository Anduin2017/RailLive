"""Acceptance checks for the 2026-11-18 offline corridor."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'build/app'))
from line_live import traffic_window, WINDOW
from snapshot_case import case_day

DAY = case_day()


def window(second):
    return traffic_window(DAY, str(second // WINDOW * WINDOW))['runs']


class CorridorLiveTest(unittest.TestCase):
    def test_bengbu(self):
        runs = window(42300)
        self.assertEqual(len(runs), len({(r['id'], r['day']) for r in runs}))
        self.assertEqual({span[2] for r in runs for span in r['spans']}, {0, 1})
        by_number = {r['number']: r for r in runs}
        self.assertIn(['蚌埠南', 41580, 42780], by_number['G817']['stops'])
        for n in ('G1211', 'G7'):
            self.assertEqual(by_number[n]['stops'], [])

    def test_cross_line_and_dwell(self):
        self.assertNotIn('G1211', {r['number'] for r in window(32000)})
        jinan = next(r for r in window(36900) if r['number'] == 'G1211')
        self.assertIn(['济南西', 36840, 37080], jinan['stops'])
        for number, arrival in [('G1211', 47220), ('G7', 47460)]:
            train = next(r for r in window(arrival) if r['number'] == number)
            self.assertIn(['苏州北', arrival, arrival+120], train['stops'])
        self.assertNotIn('G817', {r['number'] for r in window(49200)})

    def test_outside_coverage(self):
        self.assertEqual(traffic_window('2000-01-01', '0'), {'runs': [], 'covered': False})
        self.assertIsNone(traffic_window(DAY, '86400'))


if __name__ == '__main__':
    unittest.main()
