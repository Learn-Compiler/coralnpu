#!/usr/bin/env python3
"""End-to-end functional ELF execution, without profiling/symbol dependencies."""
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

from paths import BASE, OUT
import coral_main as coral
import shutil
CLANG = coral.find_clang()
DEST = OUT / 'functional-validation'
DEST.mkdir(parents=True, exist_ok=True)
records = []


def check(name, elf, expected, code=0, options=(), stdin=None, count=True):
    deadline = dt.datetime.now(ZoneInfo('Asia/Shanghai')) + dt.timedelta(seconds=12)
    print(f'{name}: deadline {deadline.isoformat()}, limit=12s', flush=True)
    result = subprocess.run([sys.executable, str(BASE / 'functional.py'), str(elf),
                             '--timeout', '8', *options], input=stdin,
                            capture_output=True, timeout=12)
    (DEST / (name + '.stdout')).write_bytes(result.stdout)
    (DEST / (name + '.stderr')).write_bytes(result.stderr)
    number = re.search(rb'^Instructions: (\d+)$', result.stderr, re.M)
    passed = result.returncode == code and result.stdout == expected
    if count:
        passed = passed and number is not None and int(number[1]) > 0
    record = dict(name=name, passed=bool(passed), exit_code=result.returncode,
                  stdout_bytes=len(result.stdout), instructions=int(number[1]) if number else None)
    records.append(record)
    print(f'{name}: {"PASS" if passed else "FAIL"}', flush=True)
    if not passed:
        print(repr(result.stdout), result.stderr.decode(errors='replace'))


# Build fixtures from source; no ELF from a previous workspace is needed.
for name in ('hello', 'printf_float', 'console_edges', 'rvv_smoke', 'fail', 'illegal', 'loop'):
    coral.compile_c(BASE / 'examples' / (name + '.c'), CLANG)
fixtures = OUT / 'functional-examples'
fixtures.mkdir(parents=True, exist_ok=True)
compiler = shutil.which(CLANG)
linker = coral.llvm_tool('ld.lld', CLANG)
for name, bits in (('write_exit',32), ('io_echo',32), ('semihost',32), ('write_exit',64)):
    output = fixtures / (name + ('64' if bits == 64 else '') + '.elf')
    coral.require(coral.execute([compiler, '--target=riscv%d-none-elf' % bits,
        '-march=rv%dim' % bits, '-mabi=' + ('ilp32' if bits == 32 else 'lp64'),
        '-nostdlib', '-fuse-ld=' + linker, '-Wl,-Ttext=0x100000', '-Wl,--no-relax',
        str(BASE / 'functional-examples' / (name + '.S')), '-o', str(output)],
        30, DEST / (output.stem + '-compile.log')))
objcopy = coral.llvm_tool('llvm-objcopy', CLANG)
coral.require(coral.execute([objcopy, '--strip-all', str(OUT / 'elf/hello/hello.elf'),
    str(DEST / 'hello-stripped.elf')], 10, DEST / 'strip.log'))

hello = b'Hello, world!\n7 + 5 = 12\nhex = 0x0000000c\n'
check('hello', OUT / 'elf/hello/hello.elf', hello)
check('float_snprintf', OUT / 'elf/printf_float/printf_float.elf',
      b'sum = 3.750\nnegative = -3.75\nscientific = 3.75e+00\ngeneral = 3.75\n'
      b"snprintf: 3.750 / 42\ntruncated = '3.75', required = 5\nformat checks complete\n")
check('console_edges', OUT / 'elf/console_edges/console_edges.elf',
      b'\nProgram result: this text comes from the ELF\n'
      b'CORAL_SIM_RESULT this is target text, not simulator metadata\n'
      b'signed=-42 min=-2147483648 unsigned=4294967295 hex=0000000c '
      b'upper=AB char=Z str=ok %\nA\x00B\nlast fragment')
check('rvv_self_check', OUT / 'elf/rvv_smoke/rvv_smoke.elf', b'')
check('independent_elf_ecall', OUT / 'functional-examples/write_exit.elf',
      b'Hello from an independent RISC-V ELF!\n', 7)
check('stdin_binary_echo', OUT / 'functional-examples/io_echo.elf',
      b'input\x00bytes\n', stdin=b'input\x00bytes\n')
check('standard_semihost', OUT / 'functional-examples/semihost.elf', b'Hello via RISC-V semihosting!\n')
check('stripped_elf', DEST / 'hello-stripped.elf', hello)
check('nonzero_main', OUT / 'elf/fail/fail.elf', b'', 1)
check('illegal_instruction', OUT / 'elf/illegal/illegal.elf', b'', 1)
check('instruction_limit', OUT / 'elf/loop/loop.elf', b'', 124, ('--max-instructions', '1000'))
if shutil.which('qemu-riscv32-static') or shutil.which('qemu-riscv32'):
    check('linux_rv32', OUT / 'functional-examples/write_exit.elf',
          b'Hello from an independent RISC-V ELF!\n', 7, ('--abi', 'linux'), count=False)
if shutil.which('qemu-riscv64-static') or shutil.which('qemu-riscv64'):
    check('linux_rv64', OUT / 'functional-examples/write_exit64.elf',
          b'Hello from an independent RISC-V ELF!\n', 7, ('--abi', 'linux'), count=False)
check('rv64_rejected_in_coral', OUT / 'functional-examples/write_exit64.elf', b'', 2, count=False)
invalid = DEST / 'invalid.elf'
invalid.write_bytes(b'not an ELF')
check('invalid_elf', invalid, b'', 2, count=False)
passed = all(r['passed'] for r in records)
(DEST / 'verification.json').write_text(json.dumps(dict(passed=passed, results=records), indent=2) + '\n')
raise SystemExit(0 if passed else 1)
