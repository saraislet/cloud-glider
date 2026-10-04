import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
from cloud_glider.timing import emit, span


class TimingTests(unittest.TestCase):
    def test_span_preserves_failure_and_uses_monotonic_duration(self):
        records = []
        with self.assertRaisesRegex(ValueError, 'original'):
            with span('imports', clock=iter([10, 12.5]).__next__, sink=records.append):
                raise ValueError('original')
        record = json.loads(records[0])
        self.assertEqual(record['duration_seconds'], 2.5)
        self.assertEqual(record['outcome'], 'FAILED')

    def test_missing_proc_is_explicit_and_sink_failure_is_nonfatal(self):
        records = []
        with patch('cloud_glider.timing.Path.read_text', side_effect=OSError):
            emit('boot_timing', sink=records.append)
        self.assertIsNone(json.loads(records[0])['boot_elapsed_seconds'])
        def broken(value):
            raise OSError('full disk')
        with span('initialization', sink=broken):
            pass
