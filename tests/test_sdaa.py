"""Tests based on the teco-smi output published by Tecorigin."""

import importlib.util
import pathlib
import sys
import unittest
from unittest import mock


# Load this dependency-free backend without importing nvitop's NVML modules.
PATH = pathlib.Path(__file__).resolve().parents[1] / 'nvitop' / 'sdaa.py'
SPEC = importlib.util.spec_from_file_location('nvitop_sdaa_test', PATH)
sdaa = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sdaa
SPEC.loader.exec_module(sdaa)


SAMPLE = '''
Wed Jun  5 02:46:48 2024
+-----------------------------------------------------------------------------+
|  TCML: 1.10.0        SDAADriver: 1.1.2b1        SDAARuntime: 1.1.2b0        |
|-------------------------------+----------------------+----------------------|
| Index  Name                   | Bus-Id               | Health      Volatile |
|        Temp          Pwr Usage|          Memory-Usage|             SPE-Util |
|=============================================================================|
|   0    TECO_AICARD_01         | 00000000:01:00.0     | OK                   |
|        35C                90W |        0MB / 15296MB |                   0% |
|-------------------------------+----------------------+----------------------|
|   1    TECO_AICARD_01         | 00000000:02:00.0     | OK                   |
|        41C               110W |      165MB / 15296MB |                  25% |
+-------------------------------+----------------------+----------------------+
| Processes:                                                                  |
|  Tcaicard     PID      Process name                            Memory Usage |
|=============================================================================|
|     1       76262      python train.py                              165 MB |
+-----------------------------------------------------------------------------+
'''


class TestSdaa(unittest.TestCase):
    def test_parse_and_filter_snapshot(self):
        snapshot = sdaa.parse_teco_smi(SAMPLE)
        self.assertEqual(len(snapshot.devices), 2)
        self.assertEqual(snapshot.devices[1].memory_used, 165 * 1024 ** 2)
        self.assertEqual(snapshot.devices[1].memory_total, 15296 * 1024 ** 2)
        self.assertEqual(snapshot.devices[1].utilization, 25)
        self.assertEqual(snapshot.devices[1].temperature, 41)
        self.assertEqual(snapshot.devices[1].power_watts, 110)
        self.assertEqual(snapshot.processes[0].pid, 76262)
        self.assertEqual(snapshot.processes[0].name, 'python train.py')

        rendered = sdaa.format_snapshot(snapshot, {1}, {76262})
        self.assertIn('165 MiB / 15296 MiB', rendered)
        self.assertIn('python train.py', rendered)
        self.assertNotIn('  0  TECO_AICARD_01', rendered)

    def test_bad_output_is_error(self):
        with self.assertRaises(sdaa.SdaaError):
            sdaa.parse_teco_smi('driver failed')

    def test_query_failure_is_error(self):
        with mock.patch.object(sdaa.subprocess, 'run', side_effect=OSError('missing')):
            with self.assertRaisesRegex(sdaa.SdaaError, 'Cannot run'):
                sdaa.query_teco_smi()


if __name__ == '__main__':
    unittest.main()
