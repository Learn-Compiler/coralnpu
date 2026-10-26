#!/usr/bin/env python3
"""Check target UART stdout bytes independently from status and ELF symbols."""
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import selectors
import shutil
import subprocess
from zoneinfo import ZoneInfo
import coral_main as coral

base = coral.BASE
CLANG = coral.find_clang()
dest = coral.OUT / 'console-validation'
dest.mkdir(parents=True, exist_ok=True)
hello = b'Hello, world!\n7 + 5 = 12\nhex = 0x0000000c\n'
edges = (b'\nProgram result: this text comes from the ELF\n'
         b'CORAL_SIM_RESULT this is target text, not simulator metadata\n'
         b'signed=-42 min=-2147483648 unsigned=4294967295 hex=0000000c '
         b'upper=AB char=Z str=ok %\nA\x00B\nlast fragment')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--case', choices=['hello', 'verbose_stdout', 'stripped_hello',
                    'console_edges', 'console_fail', 'live_before_timeout'])
args = parser.parse_args()
previous = dest / 'verification.json'
records = [r for r in json.loads(previous.read_text())['results'] if r['name'] != args.case] \
    if args.case and previous.is_file() else []


def check(name, elf, expected, code=0, extra=()):
    if args.case and args.case != name:
        return
    seconds = 30 if name == 'console_edges' else 10
    deadline = dt.datetime.now(ZoneInfo('Asia/Shanghai')) + dt.timedelta(seconds=seconds + 5)
    print(f'{name}: deadline {deadline.isoformat()}, limit={seconds + 5}s', flush=True)
    result = subprocess.run([str(base / 'coral-rtl-sim'), str(elf), '--timeout', str(seconds), *extra],
                            capture_output=True, timeout=seconds + 5)
    (dest / (name + '.stdout')).write_bytes(result.stdout)
    (dest / (name + '.stderr')).write_bytes(result.stderr)
    sim = json.loads((coral.OUT / 'runs/latest/run.json').read_text())
    console = Path(sim['console']).read_bytes()
    count = re.search(rb'^Instructions: (\d+)$', result.stderr, re.M)
    passed = (result.stdout == expected and console == expected
              and result.returncode == code and count is not None and int(count[1]) > 0)
    records.append(dict(name=name, passed=passed, exit_code=result.returncode,
                        console_bytes=len(console), instructions=int(count[1]) if count else None))
    print(f'{name}: {"PASS" if passed else "FAIL"}', flush=True)
    if not passed:
        print(repr(result.stdout), result.stderr.decode(errors='replace'))


elf = coral.compile_c(base / 'examples/hello.c', CLANG)
check('hello', elf, hello)
check('verbose_stdout', elf, hello, extra=('--verbose',))
objcopy = coral.llvm_tool('llvm-objcopy', CLANG)
if not objcopy:
    raise RuntimeError('llvm-objcopy required for symbol-independent UART check')
stripped = dest / 'hello-stripped.elf'
coral.require(coral.execute([objcopy, '--strip-all', str(elf), str(stripped)],
                            10, dest / 'strip.log'))
check('stripped_hello', stripped, hello)
check('console_edges', coral.compile_c(base / 'examples/console_edges.c', CLANG), edges)
check('console_fail', coral.compile_c(base / 'examples/console_fail.c', CLANG),
      b'output before failure', code=1)
if not args.case or args.case == 'live_before_timeout':
    elf = coral.compile_c(base / 'examples/console_loop.c', CLANG)
    deadline = dt.datetime.now(ZoneInfo('Asia/Shanghai')) + dt.timedelta(seconds=8)
    print(f'live_before_timeout: deadline {deadline.isoformat()}, limit=8s', flush=True)
    with (dest / 'live_before_timeout.stderr').open('wb') as error:
        process = subprocess.Popen([str(base / 'coral-rtl-sim'), str(elf), '--timeout', '3',
                                    '--cycles', '2147483647'], stdout=subprocess.PIPE, stderr=error)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            ready = bool(selector.select(timeout=4))
            first = process.stdout.read1(4096) if ready else b''
            live = bool(first) and process.poll() is None
        tail, _ = process.communicate(timeout=4)
    output = first + tail
    (dest / 'live_before_timeout.stdout').write_bytes(output)
    records.append(dict(name='live_before_timeout', passed=live and process.returncode == 124
                        and output == b'running before timeout', exit_code=process.returncode,
                        console_bytes=len(output), output_arrived_before_exit=live))
    print(f'live_before_timeout: {"PASS" if records[-1]["passed"] else "FAIL"}', flush=True)
passed = all(r['passed'] for r in records)
(dest / 'verification.json').write_text(json.dumps(dict(passed=passed, results=records), indent=2) + '\n')
raise SystemExit(0 if passed else 1)
