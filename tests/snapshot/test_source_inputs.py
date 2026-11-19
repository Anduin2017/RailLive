"""Regression coverage for the GTFS input correction workflow."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from build_from_source import apply_stop_time_corrections


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
