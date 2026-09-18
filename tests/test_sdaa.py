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

CURRENT_SAMPLE = '''
Fri Sep 18 08:50:22 2026
+-----------------------------------------------------------------------------+
|  TECO-SMI: 1.15.0        SDAADriver: 3.1.0        SDAARuntime: 3.2.0        |
|-------------------------------+----------------------+----------------------|
| Index  Name                   | Bus-Id               | Health      SPE-Util |
|        Temp          Pwr Usage|          Memory-Usage|                      |
|=============================================================================|
|   0    TECO_AICARD_01         | 00000000:22:00.0     | OK                0% |
|        33C                94W |        0MB / 65536MB |                      |
+-------------------------------+----------------------+----------------------+
+-----------------------------------------------------------------------------+
| Processes:                                                                  |
|  Device       PID      Process name                            Memory Usage |
|=============================================================================|
| No Process Running                                                          |
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

    def test_current_teco_smi_layout(self):
        snapshot = sdaa.parse_teco_smi(CURRENT_SAMPLE)
        self.assertEqual(snapshot.devices[0].utilization, 0)
        self.assertEqual(snapshot.devices[0].memory_total, 65536 * 1024 ** 2)
        self.assertEqual(snapshot.devices[0].temperature, 33)
        self.assertEqual(snapshot.devices[0].power_watts, 94)
        self.assertEqual(snapshot.processes, [])
        loaded = sdaa.parse_teco_smi(CURRENT_SAMPLE.replace('OK                0%',
                                                            'OK               72%'))
        self.assertEqual(loaded.devices[0].utilization, 72)

    def test_monitor_layout_matches_panel_width(self):
        snapshot = sdaa.parse_teco_smi(CURRENT_SAMPLE)
        for width, expected in ((80, 79), (120, 119)):
            lines = sdaa.monitor_lines(snapshot, width)
            self.assertEqual(len(lines[1]), expected)
            self.assertEqual(len(lines[3]), expected)
            self.assertEqual(len(lines[7]), expected)
            self.assertEqual(len(lines[8]), expected)
            self.assertIn('╒', lines[1])
            self.assertIn('Processes:', '\n'.join(lines))

    def test_query_failure_is_error(self):
        with mock.patch.object(sdaa.subprocess, 'run', side_effect=OSError('missing')):
            with self.assertRaisesRegex(sdaa.SdaaError, 'Cannot run'):
                sdaa.query_teco_smi()

    def test_monitor_redraws_in_place_and_quits(self):
        class Window:
            def __init__(self):
                self.lines = []
                self.erases = 0
                self.attributes = []

            def keypad(self, value):
                pass

            def timeout(self, value):
                pass

            def getmaxyx(self):
                return 24, 80

            def erase(self):
                self.erases += 1

            def addnstr(self, row, column, value, length, *attributes):
                self.lines.append(value[:length])
                self.attributes.extend(attributes)

            def refresh(self):
                pass

            def getch(self):
                return ord('q')

        window = Window()
        with mock.patch.object(sdaa, 'query_teco_smi', return_value=sdaa.parse_teco_smi(SAMPLE)):
            with mock.patch('curses.wrapper', side_effect=lambda callback: callback(window)):
                with mock.patch('curses.has_colors', return_value=True):
                    with mock.patch('curses.start_color'), mock.patch('curses.use_default_colors'):
                        with mock.patch('curses.init_pair'), mock.patch('curses.color_pair',
                                                                       side_effect=lambda pair: pair):
                            sdaa.monitor_teco_smi('teco-smi', 2.0)
        self.assertEqual(window.erases, 1)
        self.assertTrue(any('SPE' in line for line in window.lines))
        self.assertTrue(any('q: quit' in line for line in window.lines))
        self.assertTrue(window.attributes)


if __name__ == '__main__':
    unittest.main()
