"""SDAA occupancy overview obtained from the vendor's ``teco-smi`` utility."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass


_MEMORY = re.compile(r'([\d.]+)\s*([KMGT]?i?B)\s*/\s*([\d.]+)\s*([KMGT]?i?B)', re.I)
_PERCENT = re.compile(r'(\d+(?:\.\d+)?)\s*%')
_TEMPERATURE = re.compile(r'(-?\d+)\s*C\b', re.I)
_POWER = re.compile(r'(\d+(?:\.\d+)?)\s*W\b', re.I)
_PROCESS = re.compile(r'^\s*(\d+)\s+(\d+)\s+(.+?)\s+([\d.]+)\s*([KMGT]?i?B)\s*$', re.I)


class SdaaError(RuntimeError):
    """The SDAA management utility failed or returned unusable data."""


@dataclass(frozen=True)
class SdaaDevice:
    index: int
    name: str
    bus_id: str
    health: str
    memory_used: int
    memory_total: int
    utilization: float | None
    temperature: int | None
    power_watts: float | None


@dataclass(frozen=True)
class SdaaProcess:
    device_index: int
    pid: int
    name: str
    memory_used: int


@dataclass(frozen=True)
class SdaaSnapshot:
    devices: list[SdaaDevice]
    processes: list[SdaaProcess]


def _bytes(value: str, unit: str) -> int:
    powers = {
        'B': 0, 'KB': 1, 'KIB': 1, 'MB': 2, 'MIB': 2,
        'GB': 3, 'GIB': 3, 'TB': 4, 'TIB': 4,
    }
    unit = unit.upper()
    if unit not in powers:
        raise SdaaError(f'Unsupported teco-smi memory unit: {unit}')
    # teco-smi displays device capacity in MB, conventionally using binary units.
    return round(float(value) * 1024 ** powers[unit])


def parse_teco_smi(output: str) -> SdaaSnapshot:
    """Parse the table printed by ``teco-smi`` without relying on column widths."""
    devices = []
    processes = []
    pending = None
    in_processes = False

    for line in output.splitlines():
        if 'Processes:' in line:
            in_processes = True
            pending = None
            continue

        cells = [cell.strip() for cell in line.split('|')]
        if in_processes:
            if len(cells) >= 3:
                match = _PROCESS.fullmatch(cells[1])
                if match:
                    index, pid, name, amount, unit = match.groups()
                    processes.append(SdaaProcess(int(index), int(pid), name, _bytes(amount, unit)))
            continue

        if len(cells) < 3:
            continue
        first = cells[1]
        header = re.match(r'^(\d+)\s+(.+)$', first)
        if header and len(cells) >= 4 and not _MEMORY.search(line):
            index, name = header.groups()
            health = cells[3].split()
            header_utilization = _PERCENT.search(cells[3])
            pending = (
                int(index), name.strip(), cells[2], health[0] if health else 'N/A',
                float(header_utilization.group(1)) if header_utilization else None,
            )
            continue

        if pending is None:
            continue
        memory = _MEMORY.search(line)
        if memory is None:
            continue
        used, used_unit, total, total_unit = memory.groups()
        temperature = _TEMPERATURE.search(first)
        power = _POWER.search(first)
        utilization = _PERCENT.search(cells[-2] if cells[-1] == '' else cells[-1])
        index, name, bus_id, health, header_utilization = pending
        devices.append(SdaaDevice(
            index, name, bus_id, health,
            _bytes(used, used_unit),
            _bytes(total, total_unit),
            float(utilization.group(1)) if utilization else header_utilization,
            int(temperature.group(1)) if temperature else None,
            float(power.group(1)) if power else None,
        ))
        pending = None

    if not devices:
        raise SdaaError('teco-smi returned no readable SDAA devices')
    return SdaaSnapshot(devices, processes)


def query_teco_smi(command: str = 'teco-smi') -> SdaaSnapshot:
    """Take one SDAA snapshot. A timeout prevents a frozen driver from freezing nvitop."""
    try:
        result = subprocess.run(
            [command], capture_output=True, text=True, errors='replace', timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as ex:
        raise SdaaError(f'Cannot run teco-smi: {ex}') from ex
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f'exit code {result.returncode}'
        raise SdaaError(f'teco-smi failed: {detail}')
    return parse_teco_smi(result.stdout)


def _memory_label(value: int) -> str:
    return f'{value / 1024 ** 2:.0f} MiB'


def format_snapshot(snapshot: SdaaSnapshot, indices: set[int] | None = None,
                    pids: set[int] | None = None) -> str:
    """Render the device occupancy and the processes reported by teco-smi."""
    devices = [device for device in snapshot.devices if indices is None or device.index in indices]
    lines = [f'SDAA  {time.strftime("%Y-%m-%d %H:%M:%S")}',
             ' ID  Name                 SPE    Memory used / total   Temp  Power  Health']
    for device in devices:
        utilization = 'N/A' if device.utilization is None else f'{device.utilization:g}%'
        temperature = 'N/A' if device.temperature is None else f'{device.temperature}C'
        power = 'N/A' if device.power_watts is None else f'{device.power_watts:g}W'
        lines.append(
            f'{device.index:>3}  {device.name[:20]:<20} {utilization:>5}  '
            f'{_memory_label(device.memory_used):>9} / {_memory_label(device.memory_total):<9} '
            f'{temperature:>5}  {power:>5}  {device.health}',
        )
    if not devices:
        lines.append('No selected SDAA devices found.')
    lines.extend(('', 'Processes:', ' Card      PID  Process name                         Memory'))
    shown_indices = {device.index for device in devices}
    processes = [process for process in snapshot.processes
                 if process.device_index in shown_indices and (pids is None or process.pid in pids)]
    for process in processes:
        lines.append(f'{process.device_index:>5} {process.pid:>8}  '
                     f'{process.name[:36]:<36} {_memory_label(process.memory_used):>10}')
    if not processes:
        lines.append('No matching processes reported by teco-smi.')
    return '\n'.join(lines)


def _bar(label: str, percent: float | None, width: int) -> str:
    """Use the block-bar style of the NVIDIA panel in the optional wide column."""
    if width < 12:
        return f'{label}: {"N/A" if percent is None else f"{percent:g}%"}'[:width]
    bar_width = width - 12
    filled = 0 if percent is None else round(max(0.0, min(100.0, percent)) * bar_width / 100)
    bar = '█' * filled + ('░' if percent is None else ' ') * (bar_width - filled)
    value = 'N/A' if percent is None else f'{percent:g}%'
    return f'{label}: {bar} {value:>5}'[:width]


def _cells(left: str, middle: str, right: str, extra: str | None = None,
           extra_width: int = 0) -> str:
    """Render the 31/22/22-column layout used by nvitop's device panel."""
    line = f'│{left[:31]:<31}│{middle[:22]:<22}│{right[:22]:<22}│'
    if extra_width:
        line += f'{(extra or "")[:extra_width]:<{extra_width}}│'
    return line


def _rule(left: str, joint: str, right: str, char: str, extra_width: int) -> str:
    line = left + char * 31 + joint + char * 22 + joint + char * 22
    if extra_width:
        line += joint + char * extra_width
    return line + right


def _percent(numerator: int, denominator: int) -> float | None:
    return 100.0 * numerator / denominator if denominator else None


def monitor_lines(snapshot: SdaaSnapshot, width: int, indices: set[int] | None = None,
                  pids: set[int] | None = None) -> list[str]:
    """Build a nvitop-style device and process screen from an SDAA snapshot."""
    devices = [device for device in snapshot.devices if indices is None or device.index in indices]
    processes = [process for process in snapshot.processes
                 if (indices is None or process.device_index in indices)
                 and (pids is None or process.pid in pids)]
    panel_width = max(79, width - 1)
    extra_width = panel_width - 80 if panel_width >= 99 else 0
    panel_width = 80 + extra_width if extra_width else 79
    heading = f'NVITOP  SDAA  |  {time.strftime("%a %b %d %H:%M:%S %Y")}'
    lines = [time.strftime('%a %b %d %H:%M:%S %Y').ljust(panel_width - 28)
             + '(Press q to quit)'.rjust(28),
             '╒' + '═' * (panel_width - 2) + '╕',
             f'│ {heading:<{panel_width - 4}.{panel_width - 4}} │',
             _rule('├', '┬', '┤', '─', extra_width),
             _cells(' SDAA  Name', ' Bus-Id', ' Health', ' Occupancy', extra_width),
             _cells(' Temp       Pwr Usage', '         Memory-Usage', ' SPE-Util', '', extra_width),
             _rule('╞', '╪', '╡', '═', extra_width)]
    for device in devices:
        temp = 'N/A' if device.temperature is None else f'{device.temperature}C'
        power = 'N/A' if device.power_watts is None else f'{device.power_watts:g}W'
        util = 'N/A' if device.utilization is None else f'{device.utilization:g}%'
        memory = f'{_memory_label(device.memory_used)} / {_memory_label(device.memory_total)}'
        mem_percent = _percent(device.memory_used, device.memory_total)
        lines.extend((
            _cells(f' {device.index:>3}  {device.name}', f' {device.bus_id}',
                   f' {device.health}', _bar('MEM', mem_percent, extra_width), extra_width),
            _cells(f' {temp:>4}       {power:>5}', f'{memory:>21}',
                   f'{util:>20} ', _bar('UTL', device.utilization, extra_width), extra_width),
            _rule('├', '┼', '┤', '─', extra_width),
        ))
    if devices:
        lines[-1] = _rule('╘', '╧', '╛', '═', extra_width)
    else:
        lines.extend((_cells(' No visible SDAA devices found', '', '', '', extra_width),
                      _rule('╘', '╧', '╛', '═', extra_width)))

    lines.extend(('', '╒' + '═' * (panel_width - 2) + '╕',
                  f'│ {"Processes:":<{panel_width - 4}} │',
                  f'│ {"SDAA     PID     SDAA-MEM  PROCESS NAME":<{panel_width - 4}} │',
                  '╞' + '═' * (panel_width - 2) + '╡'))
    if processes:
        for process in processes:
            info = (f'{process.device_index:>4} {process.pid:>8} '
                    f'{_memory_label(process.memory_used):>12}  {process.name}')
            lines.append(f'│ {info[:panel_width - 4]:<{panel_width - 4}} │')
    else:
        lines.append(f'│ {"No running processes found":<{panel_width - 4}} │')
    lines.append('╘' + '═' * (panel_width - 2) + '╛')
    return lines


def _intensity(percent: float | None, thresholds: tuple[int, int]) -> int:
    if percent is None or percent < thresholds[0]:
        return 1
    return 2 if percent < thresholds[1] else 3


def monitor_teco_smi(command: str, interval: float, indices: set[int] | None = None,
                     pids: set[int] | None = None,
                     gpu_thresholds: tuple[int, int] = (10, 75),
                     memory_thresholds: tuple[int, int] = (10, 80)) -> None:
    """Refresh SDAA data in place with curses; q and Ctrl-C close the monitor."""
    import curses

    def loop(window: curses.window) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        colors = False
        try:
            if curses.has_colors():
                curses.start_color()
                try:
                    curses.use_default_colors()
                    background = -1
                except curses.error:
                    background = curses.COLOR_BLACK
                for pair, foreground in enumerate(
                    (curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_CYAN),
                    start=1,
                ):
                    curses.init_pair(pair, foreground, background)
                colors = True
        except curses.error:
            pass
        window.keypad(True)
        window.timeout(100)
        snapshot = query_teco_smi(command)
        next_refresh = time.monotonic() + interval
        scroll = 0
        while True:
            now = time.monotonic()
            if now >= next_refresh:
                snapshot = query_teco_smi(command)
                next_refresh = time.monotonic() + interval
            if indices is not None:
                invalid = indices.difference(device.index for device in snapshot.devices)
                if invalid:
                    raise SdaaError(f'Invalid SDAA device indices: {sorted(invalid)}')

            height, width = window.getmaxyx()
            lines = monitor_lines(snapshot, width, indices, pids)
            viewport = max(0, height - 1)
            scroll = max(0, min(scroll, max(0, len(lines) - viewport)))
            window.erase()
            if width > 1:
                for row, line in enumerate(lines[scroll:scroll + viewport]):
                    window.addnstr(row, 0, line, width - 1)
                if colors:
                    def color_cell(logical_row: int, start: int, length: int, pair: int) -> None:
                        row = logical_row - scroll
                        if 0 <= row < viewport and start < width - 1:
                            text = lines[logical_row][start:start + length]
                            window.addnstr(
                                row, start, text, min(length, width - 1 - start),
                                curses.color_pair(pair) | curses.A_BOLD,
                            )

                    color_cell(2, 2, min(len(lines[2]) - 4, 32), 4)
                    devices = [device for device in snapshot.devices
                               if indices is None or device.index in indices]
                    extra_width = len(lines[3]) - 80 if len(lines[3]) >= 99 else 0
                    for i, device in enumerate(devices):
                        first_row = 7 + 3 * i
                        util_color = _intensity(device.utilization, gpu_thresholds)
                        mem_color = _intensity(
                            _percent(device.memory_used, device.memory_total), memory_thresholds,
                        )
                        color_cell(first_row, 1, 31, util_color)
                        color_cell(first_row, 33, 22, util_color)
                        color_cell(first_row, 56, 22,
                                   3 if device.health.upper() != 'OK' else util_color)
                        color_cell(first_row + 1, 1, 31, util_color)
                        color_cell(first_row + 1, 33, 22, mem_color)
                        color_cell(first_row + 1, 56, 22, util_color)
                        if extra_width:
                            color_cell(first_row, 79, extra_width, mem_color)
                            color_cell(first_row + 1, 79, extra_width, util_color)
                    process_start = next(
                        i + 3 for i, line in enumerate(lines) if 'Processes:' in line
                    )
                    visible_processes = [process for process in snapshot.processes
                                         if (indices is None or process.device_index in indices)
                                         and (pids is None or process.pid in pids)]
                    devices_by_index = {device.index: device for device in devices}
                    for i, process in enumerate(visible_processes):
                        device = devices_by_index.get(process.device_index)
                        if device is not None:
                            color_cell(process_start + i, 2, 4,
                                       _intensity(device.utilization, gpu_thresholds))
                footer = 'q: quit  Up/Down: scroll  Ctrl-C: quit'
                window.addnstr(height - 1, 0, footer, width - 1)
            window.refresh()

            key = window.getch()
            if key in (ord('q'), ord('Q'), 3):
                return
            if key in (curses.KEY_DOWN, ord('j')):
                scroll += 1
            elif key in (curses.KEY_UP, ord('k')):
                scroll -= 1
            elif key == curses.KEY_NPAGE:
                scroll += viewport
            elif key == curses.KEY_PPAGE:
                scroll -= viewport

    curses.wrapper(loop)
