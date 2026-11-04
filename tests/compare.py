#!/usr/bin/env python3
"""Compare headless GPU output against original C reference, byte for byte."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from reference import HEADER, FIELDS, state_hash

def load_trace(directory):
    with (directory / 'ticks.csv').open(newline='') as f:
        reader = csv.reader(f)
        header = next(reader)
        if header != HEADER:
            raise AssertionError(f'{directory}: unexpected CSV header {header!r}')
        rows = [[int(item) for item in row] for row in reader]
    for index, row in enumerate(rows):
        if len(row) != len(HEADER):
            raise AssertionError(f'{directory}: tick {index + 1} has {len(row)} columns')
        if row[0] != index + 1 or row[2] != index + 2:
            raise AssertionError(f'{directory}: invalid progression at tick {index + 1}: {row}')
        if row[1] != state_hash(row[2:]):
            raise AssertionError(f'{directory}: invalid state hash at tick {index + 1}')
        if row[-1]:
            raise AssertionError(f'{directory}: engine exited at tick {index + 1}')
    return rows

def compare(reference_dir, actual_dir):
    expected = load_trace(reference_dir)
    actual = load_trace(actual_dir)
    if len(expected) != len(actual):
        raise AssertionError(f'Tick count differs: {len(expected)} expected, {len(actual)} actual')
    for want, got in zip(expected, actual):
        if want != got:
            differences = {HEADER[i]: {'expected': a, 'actual': b} for i, (a, b) in enumerate(zip(want, got)) if a != b}
            raise AssertionError(f'First state divergence at tick {want[0]}: {differences}')
    want_frame = (reference_dir / 'frame.rgba').read_bytes()
    got_frame = (actual_dir / 'frame.rgba').read_bytes()
    if len(want_frame) != 256000 or len(got_frame) != 256000:
        raise AssertionError(f'Invalid framebuffer lengths: reference={len(want_frame)}, actual={len(got_frame)}')
    if want_frame != got_frame:
        index = next(i for i, (a, b) in enumerate(zip(want_frame, got_frame)) if a != b)
        count = sum(a != b for a, b in zip(want_frame, got_frame))
        pixel, channel = divmod(index, 4)
        raise AssertionError(f'{count} framebuffer bytes differ; first at pixel ({pixel % 320},{pixel // 320}), BGRA channel {channel}: expected={want_frame[index]}, actual={got_frame[index]}')
    expected_summary = json.loads((reference_dir / 'summary.json').read_text())
    actual_summary = json.loads((actual_dir / 'summary.json').read_text())
    for key in ('ticks', 'width', 'height', 'state_hash'):
        if expected_summary[key] != actual_summary[key]:
            raise AssertionError(f'Summary {key} differs: {expected_summary[key]} vs {actual_summary[key]}')
    report = {'passed': True, 'ticks_compared': len(actual), 'diagnostic_values_compared': len(actual) * 16,
              'frame_bytes_compared': len(got_frame), 'frame_sha256': hashlib.sha256(got_frame).hexdigest(),
              'final_state_hash': actual_summary['state_hash']}
    return report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--actual', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.reference, args.actual), indent=2))

if __name__ == '__main__':
    main()
