#!/usr/bin/env python3
"""Execute an existing ELF with the Coral instruction model or Linux QEMU."""
import argparse
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from paths import BASE, OUT


def elf_info(path):
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b'\x7fELF' or data[5] != 1 or data[18:20] != b'\xf3\x00':
        raise ValueError('requires a little-endian RISC-V ELF executable')
    bits = {1: 32, 2: 64}.get(data[4])
    minimum = 52 if bits == 32 else 64
    if bits is None or len(data) < minimum:
        raise ValueError('truncated or unsupported ELF header')
    kind = struct.unpack_from('<H', data, 16)[0]
    if kind not in (2, 3):
        raise ValueError('ELF must be executable, not a relocatable .o file')
    if bits == 32:
        offset, flags = struct.unpack_from('<I', data, 28)[0], struct.unpack_from('<I', data, 36)[0]
        stride, count = struct.unpack_from('<HH', data, 42)
        entry = struct.unpack_from('<I', data, 24)[0]
    else:
        offset, flags = struct.unpack_from('<Q', data, 32)[0], struct.unpack_from('<I', data, 48)[0]
        stride, count = struct.unpack_from('<HH', data, 54)
        entry = struct.unpack_from('<Q', data, 24)[0]
    if not count or stride < (32 if bits == 32 else 56) or offset + stride * count > len(data):
        raise ValueError('invalid ELF program header table')
    interpreter = any(struct.unpack_from('<I', data, offset + i * stride)[0] == 3 for i in range(count))
    return dict(bits=bits, flags=flags, kind=kind, interpreter=interpreter, entry=entry)


def positive(value):
    value = int(value, 0)
    if value <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return value


def main():
    argv = sys.argv[1:]
    guest_args = []
    if '--' in argv:
        split = argv.index('--')
        argv, guest_args = argv[:split], argv[split + 1:]
    engine_parser = argparse.ArgumentParser(add_help=False)
    engine_parser.add_argument('--engine', choices=('iss', 'rtl'), default='iss')
    engine, remaining = engine_parser.parse_known_args(argv)
    if engine.engine == 'rtl':
        os.execv(sys.executable, [sys.executable, str(BASE / 'coral_main.py'), 'run', *remaining])
    parser = argparse.ArgumentParser(prog='coral-sim', description=__doc__)
    parser.add_argument('elf', type=Path)
    parser.add_argument('--engine', choices=('iss', 'rtl'), default='iss')
    parser.add_argument('--abi', choices=('baremetal', 'linux'), default='baremetal')
    parser.add_argument('--cpu', choices=('coral-v2', 'coral-m3', 'coral-m4'), default='coral-v2')
    parser.add_argument('--timeout', type=positive, default=30, help='wall-clock deadline in seconds')
    parser.add_argument('--max-instructions', type=positive, default=100000000)
    parser.add_argument('--stack-top', type=positive, default=0x7fffe000)
    parser.add_argument('--sysroot', type=Path, help='Linux guest dynamic-library sysroot (QEMU -L)')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)
    elf = args.elf.resolve()
    info = elf_info(elf)
    if args.stack_top > 0xffffffff:
        raise ValueError('--stack-top must fit in the RV32 address space')
    run = OUT / 'functional-runs' / (elf.stem + '_' + str(time.time_ns()))
    run.mkdir(parents=True)
    if args.abi == 'linux':
        executable = shutil.which(f'qemu-riscv{info["bits"]}-static') or shutil.which(f'qemu-riscv{info["bits"]}')
        if not executable:
            raise ValueError(f'qemu-riscv{info["bits"]} is not installed')
        command = [executable]
        if args.sysroot:
            command += ['-L', str(args.sysroot.resolve())]
        command += [str(elf), *guest_args]
        backend = 'qemu-linux-user'
    else:
        if info['bits'] != 32:
            raise ValueError('Coral is RV32; for a Linux RV64 ELF use --abi linux')
        if info['interpreter'] or info['kind'] == 3:
            raise ValueError('Linux/dynamic ELF requires --abi linux (and --sysroot when needed)')
        if info['flags'] & 1:
            raise ValueError('Coral does not implement compressed C instructions; use a matching -march, or --abi linux for Linux programs')
        if guest_args or args.sysroot:
            raise ValueError('process arguments/sysroot require --abi linux; bare-metal startup is defined by the ELF')
        executable = OUT / 'bin/coral-iss'
        if not executable.is_file():
            raise ValueError('Coral ISS missing; build with: python3 ' + str(BASE / 'build.py'))
        command = [str(executable), str(elf), '--max-instructions=' + str(args.max_instructions),
                   '--stack-top=' + str(args.stack_top), '--cpu=' + args.cpu, '--json=' + str(run / 'model.json')]
        backend = 'coral-mpact'
    started = dt.datetime.now(ZoneInfo('Asia/Shanghai'))
    deadline = started + dt.timedelta(seconds=args.timeout)
    print(f'Deadline: {deadline.isoformat()} ({args.timeout}s)', file=sys.stderr, flush=True)
    if args.verbose:
        print(f'Engine: {backend}; ELF{info["bits"]}; entry=0x{info["entry"]:x}', file=sys.stderr, flush=True)
    # Inherit standard streams: output is generated by the ELF, including
    # interactive stdin, binary stdout and stderr. No symbol/profiling protocol.
    process = subprocess.Popen(command, start_new_session=True)
    try:
        code = process.wait(timeout=args.timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        code = 124 if isinstance(error, subprocess.TimeoutExpired) else 130
        print(f'Exit status: {code} (wall-clock deadline or interrupt)', file=sys.stderr)
    if code < 0:
        code = 128 - code
    record = dict(engine=backend, elf=str(elf), command=command, elf_info=info,
                  started=started.isoformat(), deadline=deadline.isoformat(),
                  finished=dt.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), exit_code=code)
    model = run / 'model.json'
    if model.is_file():
        record['model'] = json.loads(model.read_text())
    if backend == 'qemu-linux-user':
        # Installed QEMU static build has no plugin support: do not fabricate a
        # count from disassembly, translated blocks or elapsed host time.
        print('Instructions: unavailable (this QEMU build has no instruction-count plugin)', file=sys.stderr)
        print(f'Exit status: {code}', file=sys.stderr)
    (run / 'run.json').write_text(json.dumps(record, indent=2) + '\n')
    if args.verbose:
        print('Run record: ' + str(run / 'run.json'), file=sys.stderr)
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print('coral-sim: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
