#!/usr/bin/env python3
"""Run a precompiled operator bundle and render real RTL performance samples."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ncoral_paths import ROOT, UPSTREAM
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'multicore'))
from run import bounded, timestamp
from build_firmware import validate_rvv_firmware

CATALOG = {
    'add': 'RVV vector ADD; all four cores, optional dependent second invocation',
    'gemm': 'GEMM + bias + ReLU; all four cores split output rows',
    'conv': 'Conv3x3 + bias + ReLU; all four cores split output rows',
    'pair': 'GEMM on cores 0/1, Conv on cores 2/3; serial or parallel',
}


def load_bundle(binary):
    if binary.name != 'controller.bin':
        raise RuntimeError('--firmware must name the compiled controller.bin bundle, not a raw kernel ELF')
    report = validate_rvv_firmware(binary.parent, check_sources=False)
    if report.get('profile_abi') != 1:
        raise RuntimeError('bundle lacks profiling ABI 1; run ncoral.py build-code')
    return report


def validate_profile(profile):
    """Check measurements and summaries before presenting any numbers as valid."""
    if profile.get('schema_version') != 1 or profile.get('result') != 'PASS':
        raise RuntimeError('unsupported or unsuccessful profile')
    if profile.get('counter_source') != 'Coral hardware mcycle/minstret':
        raise RuntimeError('profile does not contain hardware counter measurements')
    cores, operators = profile.get('cores', []), profile.get('operators', [])
    if not cores or not operators:
        raise RuntimeError('empty profile')
    seen = set()
    for core in cores:
        fields=('invocation','core','work_items','start_cycle','end_cycle','sample_cycles','retired_instructions')
        if any(type(core.get(field)) is not int or core[field] < 0 for field in fields):
            raise RuntimeError('profile counters must be nonnegative integers')
        key = (core['operator'], core['invocation'], core['core'])
        if key in seen or core['operator'] not in ('add', 'gemm', 'conv') or not 0 <= core['core'] < 4:
            raise RuntimeError('duplicate or invalid core profile')
        seen.add(key)
        if not (0 <= core['start_cycle'] < core['end_cycle'] and
                0 < core['sample_cycles'] <= core['end_cycle']-core['start_cycle'] and
                0 < core['retired_instructions'] <= 4*(core['sample_cycles']+8)):
            raise RuntimeError('invalid cycle/instruction range')
    group_keys = set()
    for op in operators:
        fields=('invocation','start_cycle','end_cycle','elapsed_cycles','retired_instructions','cores')
        if any(type(op.get(field)) is not int or op[field] < 0 for field in fields):
            raise RuntimeError('operator summary counters must be nonnegative integers')
        key = (op['operator'], op['invocation'])
        if key in group_keys:
            raise RuntimeError('duplicate operator summary')
        group_keys.add(key)
        rows = [core for core in cores if (core['operator'], core['invocation']) == key]
        if (not rows or len(rows) != op['cores'] or
                op['start_cycle'] != min(row['start_cycle'] for row in rows) or
                op['end_cycle'] != max(row['end_cycle'] for row in rows) or
                op['elapsed_cycles'] != op['end_cycle']-op['start_cycle'] or
                op['retired_instructions'] != sum(row['retired_instructions'] for row in rows)):
            raise RuntimeError('operator summary does not match per-core measurements')
    if group_keys != {(row['operator'], row['invocation']) for row in cores}:
        raise RuntimeError('missing operator summary')


def render(profile, output):
    validate_profile(profile)
    template = Path(__file__).with_name('operator_report.html').read_text()
    data = json.dumps(profile, ensure_ascii=False).replace('<', '\\u003c')
    output.write_text(template.replace('__NCORAL_PROFILE_DATA__', data))


def positive_clock(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 1000000:
        raise argparse.ArgumentTypeError('clock MHz must be finite and in (0, 1000000]')
    return number



def execute_operator(args, *, cancel_event=None, on_started=None):
    """Run the same measured, correctness-checked path for CLI and local UI."""
    firmware = load_bundle(args.firmware.resolve())
    binary = UPSTREAM/'bazel-bin/experiments/multicore/smoke_die'
    if not binary.is_file():
        raise RuntimeError('simulator missing; run ncoral.py build-sim')
    scenario = {'add': 0, 'gemm': 3, 'conv': 4, 'pair': 1 if args.schedule == 'serial' else 2}[args.operator]
    output = args.output or ROOT/'die/logs/operators'/(
        args.operator+'_'+timestamp().replace(':', '-')+'_'+str(time.time_ns()))
    output.mkdir(parents=True, exist_ok=False)
    raw_path = output/'counters.json'
    command = [str(binary), '--boot', str(args.firmware.resolve()), '--scenario', str(scenario),
               '--rows', str(args.rows), '--elements', str(args.elements), '--rounds', str(args.rounds),
               '--stall-period', str(args.stall_period), '--profile-json', str(raw_path)]
    phase = bounded(command, args.run_timeout, output/'run.log', UPSTREAM,
                    cancel_event=cancel_event, on_started=on_started)
    (output/'execution.json').write_text(json.dumps(dict(command=command, **phase), indent=2)+'\n')
    if phase['exit_code']:
        raise RuntimeError('operator failed or timed out; no successful report was generated. See '+phase['log'])
    results = [json.loads(line) for line in (output/'run.log').read_text().splitlines()
               if line.startswith('{"result":')]
    if len(results) != 1 or results[0].get('result') != 'PASS' or not results[0].get('cpu_runs_runtime'):
        raise RuntimeError('missing CPU-controlled correctness PASS')
    profile = json.loads(raw_path.read_text())
    validate_profile(profile)
    profile.update(clock_mhz=args.clock_mhz, clock_is_assumed=True, simulation=results[0],
                   host_wall_seconds=phase_duration(phase), command=command,
                   firmware_sha256=firmware['outputs_sha256']['controller.bin'],
                   simulator_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                   created=timestamp())
    profile_path = output/'profile.json'
    profile_path.write_text(json.dumps(profile, indent=2)+'\n')
    html = output/'report.html'
    render(profile, html)
    return profile, profile_path, html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='list precompiled operators; never build or execute')
    listing.add_argument('--firmware', type=Path, default=ROOT/'die/logs/firmware/controller.bin')
    run = commands.add_parser('run', help='execute an already compiled operator; never invoke a compiler')
    run.add_argument('operator', choices=CATALOG)
    run.add_argument('--firmware', type=Path, default=ROOT/'die/logs/firmware/controller.bin')
    run.add_argument('--rows', type=int, choices=range(1, 9), default=7)
    run.add_argument('--elements', type=int, choices=range(1, 129), default=37)
    run.add_argument('--rounds', type=int, choices=(1, 2), default=1)
    run.add_argument('--schedule', choices=('serial', 'parallel'), default='parallel')
    run.add_argument('--stall-period', type=int, default=3)
    run.add_argument('--run-timeout', type=int, default=420)
    run.add_argument('--clock-mhz', type=positive_clock, default=500.0,
                     help='assumed frequency for cycle-to-time conversion only; default 500 MHz')
    run.add_argument('--output', type=Path, help='new result directory (must not already exist)')
    run.add_argument('--open', action='store_true', help='open the generated HTML in the system browser')
    view = commands.add_parser('view', help='render saved profile JSON without running or compiling')
    view.add_argument('profile', type=Path)
    view.add_argument('--output', type=Path)
    view.add_argument('--open', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'view':
            profile = json.loads(args.profile.read_text())
            output = args.output or args.profile.with_suffix('.html')
            if output.resolve() == args.profile.resolve():
                raise RuntimeError('HTML output must differ from the input JSON')
            render(profile, output)
            print('Report:', output.resolve())
            if args.open:
                webbrowser.open(output.resolve().as_uri())
            return 0
        firmware = load_bundle(args.firmware.resolve())
        if args.command == 'list':
            for name, description in CATALOG.items():
                print(f'{name:5} READY  {description}')
            print('Compiled bundle:', args.firmware.resolve())
            print('controller.bin SHA256:', firmware['outputs_sha256']['controller.bin'])
            return 0
        if args.run_timeout < 1 or not 0 <= args.stall_period <= 1000:
            parser.error('invalid timeout or stall range')
        if args.operator != 'add' and (args.elements != 37 or args.rounds != 1):
            parser.error('--elements and --rounds are ADD options')
        if args.operator == 'add' and args.rows != 7:
            parser.error('--rows applies to GEMM/Conv, not ADD')
        if args.operator != 'pair' and args.schedule != 'parallel':
            parser.error('--schedule applies only to pair')
        profile, profile_path, html = execute_operator(args)
        print('\nPASS  芯片周期 / Coral 退休指令（标量 + RVV）')
        for op in profile['operators']:
            print(f"  {op['operator']} #{op['invocation']}: {op['elapsed_cycles']:,} cycles, "
                  f"{op['elapsed_cycles']/args.clock_mhz:.3f} us @ assumed {args.clock_mhz:g} MHz, "
                  f"{op['retired_instructions']:,} retired instructions")
        print('宿主仿真耗时（不同于芯片时间）: %.3f s' % profile['host_wall_seconds'])
        print('Interactive report:', html.resolve())
        print('Profile JSON:', profile_path.resolve())
        if args.open:
            webbrowser.open(html.resolve().as_uri())
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        if args.command != 'view' and (not args.firmware.is_file() or not (args.firmware.parent/'report.json').is_file()):
            print('Build once with: python3 experiments/multicore/tools/ncoral.py build', file=sys.stderr)
        return 1


def phase_duration(phase):
    from datetime import datetime
    return (datetime.fromisoformat(phase['finished'])-datetime.fromisoformat(phase['started'])).total_seconds()


if __name__ == '__main__':
    raise SystemExit(main())
