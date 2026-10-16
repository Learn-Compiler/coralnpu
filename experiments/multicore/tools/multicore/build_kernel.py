#!/usr/bin/env python3
"""Compile the learning kernel to bare-metal RV32I ELF using local Clang/LLD."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from run import ROOT, UPSTREAM, bounded, timestamp


def find_tool(explicit, names):
    for name in ([explicit] if explicit else names):
        if path := shutil.which(str(name)):
            # LLD selects its driver from argv[0]; preserve the ld.lld symlink name.
            return str(Path(path).absolute())
    raise RuntimeError('missing tool: ' + ', '.join(str(name) for name in names))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clang')
    parser.add_argument('--lld')
    parser.add_argument('--timeout', type=int, default=30, help='deadline for each compiler phase')
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('timeout must be positive')
    output = ROOT / 'multicore/logs/kernel'
    output.mkdir(parents=True, exist_ok=True)
    elf = output / 'add.elf'
    report = {'status': 'STARTED', 'started': timestamp(), 'phases': [],
              'isa': 'rv32i', 'abi': 'ilp32', 'argument_abi_version': 1}
    try:
        # Do not leave an old ELF looking like a successful new compilation.
        elf.unlink(missing_ok=True)
        clang = find_tool(args.clang, ['clang-17', 'clang'])
        lld = find_tool(args.lld, ['ld.lld-17', 'ld.lld'])
        source = UPSTREAM / 'experiments/multicore/device'
        flags = ['--target=riscv32-none-elf', '-march=rv32i', '-mabi=ilp32',
                 '-O2', '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
                 '-fno-vectorize', '-fno-slp-vectorize', '-fno-unwind-tables',
                 '-fno-asynchronous-unwind-tables', '-msmall-data-limit=0', '-mno-relax']
        commands = [
            ('version', [clang, '--version']),
            ('ir', [clang, *flags, '-S', '-emit-llvm', str(source / 'add.c'), '-o', str(output / 'add.ll')]),
            ('asm', [clang, *flags, '-S', str(source / 'add.c'), '-o', str(output / 'add.s')]),
            ('link', [clang, *flags, '-nostdlib', '-fuse-ld=' + lld,
                      '-Wl,--no-relax,--build-id=none,-T,' + str(source / 'link.ld'),
                      '-Wl,-Map=' + str(output / 'add.map'),
                      str(source / 'start.S'), str(source / 'add.c'), '-o', str(elf)])]
        report['commands'] = [command for _, command in commands]
        report['source_sha256'] = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                                   for name in ('add.c', 'start.S', 'link.ld', 'kernel_abi.h')}
        for name, command in commands:
            phase = bounded(command, args.timeout, output / (name + '.log'), ROOT)
            report['phases'].append(phase)
            if phase['exit_code']:
                raise RuntimeError('compiler phase failed: ' + name + '; see ' + phase['log'])
        report.update(status='PASS', elf=str(elf), elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest())
        print('ELF:', elf)
    except Exception as error:
        elf.unlink(missing_ok=True)
        report.update(status='FAIL', error=str(error))
        print(str(error), file=sys.stderr)
    finally:
        report['finished'] = timestamp()
        path = output / 'report.json'
        path.write_text(json.dumps(report, indent=2) + '\n')
        print('Report:', path)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
