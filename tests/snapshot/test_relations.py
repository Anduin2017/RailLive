"""Snapshot acceptance cases for reciprocal passes and split-route order changes."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'build/app'))
from train_relations import relations, timetable
from snapshot_case import case_day


class TrainRelationsTest(unittest.TestCase):
    day = case_day()

    def view(self, number):
        trip = next(i for i, row in timetable()[0].items() if row[0] == number)
        return relations(trip, self.day)

    def pair(self, number, other):
        view = self.view(number)
        idx, peer = next((i, p) for i, p in enumerate(view['peers'])
                         if p['number'] == other and p['offset'] == 0)
        return peer, [e for e in view['events'] if e['peer'] == idx]

    def test_g7_headways(self):
        peer, _ = self.pair('G1211', 'G7')
        gaps = {name: (arr, dep) for name, _, arr, _, dep in peer['gaps']}
        self.assertEqual(gaps['济南西'][1], 7 * 60)
        self.assertEqual(gaps['南京南'], (8 * 60, 4 * 60))
        self.assertEqual(gaps['苏州北'], (4 * 60, 4 * 60))

    def test_bengbu_reciprocal_event(self):
        times = []
        for train in ('G1211', 'G7'):
            _, (forward,) = self.pair(train, 'G817')
            _, (reverse,) = self.pair('G817', train)
            self.assertTrue(forward['fast'])
            self.assertFalse(reverse['fast'])
            self.assertEqual(forward['pass'], reverse['pass'])
            site, at, arrival, departure = forward['pass']
            self.assertEqual(site, '蚌埠南')
            self.assertLess(arrival, at)
            self.assertLess(at, departure)
            times.append(at)
        self.assertLess(times[0], times[1])

    def test_divergent_geometry(self):
        for train, other in [('G6605', 'G1284'), ('G1284', 'G6605')]:
            _, (event,) = self.pair(train, other)
            self.assertEqual((event['up'], event['down']), ('新乡东', '郑州东'))
            self.assertNotIn('pass', event)


if __name__ == '__main__':
    unittest.main()
