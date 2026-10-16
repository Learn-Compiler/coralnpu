#!/usr/bin/env python3
"""Bounded 2/4-core RTL regression, optionally exercising LLVM-compiled ELF."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from run import ROOT, UPSTREAM, bounded, target_name, timestamp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=('scalar', 'rvv'), default='scalar')
    parser.add_argument('--cores', choices=(2, 4), type=int, default=4)
    parser.add_argument('--elf', type=Path, help='also test the LLVM ELF loading/execution path')
    args = parser.parse_args()
    target = target_name(args.cores, args.variant)
    binary = UPSTREAM / 'bazel-bin/experiments/multicore' / ('smoke_' + target)
    if not binary.is_file():
        parser.error('Build the simulator first with multicore/run.py')
    if args.elf:
        args.elf = args.elf.resolve()
        if args.variant != 'scalar' or not args.elf.is_file():
            parser.error('--elf requires scalar variant and an existing ELF')
    output = ROOT / 'multicore/logs/regression' / (target + ('_elf' if args.elf else ''))
    output.mkdir(parents=True, exist_ok=True)
    report = {'started': timestamp(), 'variant': args.variant, 'cores': args.cores,
              'status': 'FAIL', 'cases': [],
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    cases = [('minimum', ['--elements', '1', '--stall-period', '0']),
             ('partial_shards', ['--elements', str(args.cores - 1), '--stall-period', '3']),
             ('one_per_core', ['--elements', str(args.cores), '--stall-period', '0']),
             ('unequal_shards', ['--elements', '37', '--stall-period', '3']),
             ('larger_backpressure', ['--elements', '128', '--stall-period', '5'])]
    tests = [(name, options, None, 'builtin') for name, options in cases]
    tests.append(('cycle_deadline', ['--max-cycles', '40'], 'simulated cycle deadline exceeded', None))
    if args.elf:
        report['elf_sha256'] = hashlib.sha256(args.elf.read_bytes()).hexdigest()
        tests.extend(('elf_' + name, options + ['--elf', str(args.elf)], None, 'elf')
                     for name, options in cases)
        tests.append(('elf_cycle_deadline', ['--elf', str(args.elf), '--max-cycles', '40'],
                      'simulated cycle deadline exceeded', None))
        # Malformed images must fail before any core is launched.
        raw = args.elf.read_bytes()
        invalid = output / 'truncated.elf'
        invalid.write_bytes(raw[:16])
        tests.append(('elf_truncated', ['--elf', str(invalid)], 'ELF missing or invalid file size', None))
        invalid = output / 'wrong_machine.elf'
        wrong = bytearray(raw)
        struct.pack_into('<H', wrong, 18, 62)
        invalid.write_bytes(wrong)
        tests.append(('elf_wrong_machine', ['--elf', str(invalid)], 'expected static ELF32', None))
        invalid = output / 'out_of_range.elf'
        wrong = bytearray(raw)
        phoff = struct.unpack_from('<I', raw, 28)[0]
        for index in range(struct.unpack_from('<H', raw, 44)[0]):
            ph = phoff + 32 * index
            if struct.unpack_from('<I', raw, ph)[0] == 1:
                struct.pack_into('<II', wrong, ph + 8, 0x30000, 0x30000)
                break
        invalid.write_bytes(wrong)
        tests.append(('elf_out_of_range', ['--elf', str(invalid)], 'ELF segment outside permitted', None))
    try:
        for name, options, expected_error, firmware_source in tests:
            log = output / (name + '.log')
            phase = bounded([str(binary), *options], 30, log, UPSTREAM)
            text = log.read_text()
            if expected_error:
                passed = phase['exit_code'] == 1 and expected_error in text and '"result":"PASS"' not in text
            else:
                lines = [json.loads(line) for line in text.splitlines() if line.startswith('{"result":')]
                passed = (phase['exit_code'] == 0 and len(lines) == 1 and lines[0].get('result') == 'PASS'
                          and lines[0].get('cores') == args.cores
                          and lines[0].get('completion_mask') == (1 << args.cores) - 1
                          and lines[0].get('firmware_source') == firmware_source)
                if lines:
                    phase['simulation'] = lines[0]
                    n = int(options[options.index('--elements') + 1])
                    shards = [(int(core), int(first), int(size)) for core, first, size in
                              re.findall(r'runtime: core=(\d+) first=(\d+) count=(\d+)', text)]
                    expected = [(core, core * (n // args.cores) + min(core, n % args.cores),
                                 n // args.cores + (core < n % args.cores)) for core in range(args.cores)]
                    passed = passed and shards == expected and lines[0].get('elements') == n
                    if args.variant == 'scalar':
                        passed = (passed and lines[0].get('read_beats') == 2 * n
                                  and lines[0].get('write_beats') == n + args.cores)
            report['cases'].append(dict(name=name, passed=passed, expected_error=expected_error, **phase))
            if not passed:
                raise RuntimeError('regression failed: ' + name + '; see ' + str(log))
        report['status'] = 'PASS'
    except Exception as error:
        report['error'] = str(error)
        print(str(error), file=sys.stderr)
    finally:
        report['finished'] = timestamp()
        path = output / 'report.json'
        path.write_text(json.dumps(report, indent=2) + '\n')
        print(report['status'], len(report['cases']), 'cases;', path, flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
