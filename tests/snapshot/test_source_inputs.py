"""Regression coverage for the GTFS input correction workflow."""
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from build_from_source import apply_stop_time_corrections
from snapshot.import_gtfs import repair_overnight_arrivals


class SourceInputsTest(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.feed = Path(scratch.name) / 'output_gtfs.zip'
        with zipfile.ZipFile(self.feed, 'w') as archive:
            archive.writestr('feed_info.txt', 'original metadata')
            archive.writestr('stop_times.txt',
                             'trip_id,stop_id,stop_sequence,arrival_time,departure_time\n'
                             'T,A,1,24:00:00,24:02:00\n'
                             'T,B,2,24:14:00,24:15:00\n')
        self.change = dict(trip_id='T', stop_id='B', stop_sequence=2,
                           before=['24:14:00', '24:15:00'], after=['24:16:00', '24:17:00'],
                           source='Verified schedule')

    def test_cross_midnight_correction(self):
        apply_stop_time_corrections(self.feed, [self.change])
        with zipfile.ZipFile(self.feed) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read('feed_info.txt'), b'original metadata')
            self.assertIn(b'24:16:00,24:17:00', archive.read('stop_times.txt'))

    def test_stale_correction_keeps_original(self):
        original = self.feed.read_bytes()
        self.change['before'] = ['24:12:00', '24:15:00']
        with self.assertRaisesRegex(ValueError, 'Stale'):
            apply_stop_time_corrections(self.feed, [self.change])
        self.assertEqual(original, self.feed.read_bytes())

    def test_impossible_schedule_is_rejected(self):
        self.change['after'] = ['24:01:00', '24:02:00']
        with self.assertRaisesRegex(ValueError, 'chronological'):
            apply_stop_time_corrections(self.feed, [self.change])

    def test_overnight_arrivals_require_a_unique_day_offset(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE times(trip, seq, arrival, departure)')
            db.executemany('INSERT INTO times VALUES (?, ?, ?, ?)', [
                ('wrapped', 1, '22:29:00', '22:35:00'),
                ('wrapped', 2, '47:59:00', '24:10:00'),
                ('wrapped', 3, '26:48:00', '26:58:00'),
                ('invalid', 1, '22:29:00', '22:35:00'),
                ('invalid', 2, '24:14:00', '24:10:00'),
                ('invalid', 3, '26:48:00', '26:58:00'),
                ('ambiguous', 1, '22:29:00', '22:35:00'),
                ('ambiguous', 2, '71:59:00', '49:10:00'),
                ('ambiguous', 3, '50:48:00', '50:58:00'),
            ])
            self.assertEqual(repair_overnight_arrivals(db), 1)
            self.assertEqual(repair_overnight_arrivals(db), 0)
            self.assertEqual(db.execute('SELECT arrival FROM times WHERE seq=2 ORDER BY trip').fetchall(),
                             [('71:59:00',), ('24:14:00',), ('23:59:00',)])
