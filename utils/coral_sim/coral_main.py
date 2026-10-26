#!/usr/bin/env python3
"""Run an existing ELF on Coral RTL; compilation is a separate command."""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import threading
from zoneinfo import ZoneInfo
import feedback

from paths import BASE, ROOT as SOURCE, OUT, llvm_tool
SIM = OUT / "bin/coral-rvv-sim"


def process_tree(root):
    """Bazel may create another process group; retain all descendant PIDs."""
    parents = {}
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            parent = next(line for line in path.read_text().splitlines()
                          if line.startswith('PPid:'))
            parents[int(path.parent.name)] = int(parent.split()[1])
        except (OSError, StopIteration, ValueError):
            continue
    result = {root}
    while True:
        expanded = result | {pid for pid, parent in parents.items() if parent in result}
        if expanded == result:
            return result
        result = expanded


def stop_processes(pids, sig):
    for pid in sorted(pids, reverse=True):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def execute(command, seconds, log, cwd=BASE, live=False, announce_end=True, console=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    start = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    deadline = start + dt.timedelta(seconds=seconds)
    print(f"{log.name}: deadline {deadline.isoformat()}, limit={seconds}s", file=sys.stderr, flush=True)
    console_done = threading.Event()
    console_reader = None
    if console is not None:
        console.write_bytes(b'')
        def relay_console():
            with console.open('rb') as stream:
                while True:
                    chunk = stream.read(4096)
                    if chunk:
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.buffer.flush()
                    elif console_done.is_set():
                        break
                    else:
                        console_done.wait(0.01)
        console_reader = threading.Thread(target=relay_console, daemon=True)
        console_reader.start()
    with log.open("w") as output:
        process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE if live else output,
                                   stderr=subprocess.STDOUT, start_new_session=True,
                                   text=True, bufsize=1)
        reader = None
        if live:
            def relay():
                for line in process.stdout:
                    output.write(line)
                    output.flush()
                    print(line, end='', file=sys.stderr, flush=True)
            reader = threading.Thread(target=relay, daemon=True)
            reader.start()
        try:
            code = process.wait(timeout=seconds)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            descendants = process_tree(process.pid)
            stop_processes(descendants, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stop_processes(descendants, signal.SIGKILL)
                process.wait()
            # Detached descendants can outlive a launcher which has exited.
            stop_processes(descendants - {process.pid}, signal.SIGKILL)
            code = 124 if isinstance(error, subprocess.TimeoutExpired) else 130
        if reader:
            reader.join(timeout=2)
    console_done.set()
    if console_reader:
        console_reader.join()
    record = dict(command=[str(x) for x in command], started=start.isoformat(),
                  deadline=deadline.isoformat(),
                  finished=dt.datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                  exit_code=code, log=str(log))
    log.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n")
    if announce_end:
        print(f"exit={code}; {log}", file=sys.stderr, flush=True)
    if code and not live and announce_end:
        print('\n'.join(log.read_text(errors='replace').splitlines()[-15:]), file=sys.stderr, flush=True)
    return record


def require(result):
    if result["exit_code"]:
        raise RuntimeError("command failed; see " + result["log"])


def build_runtime(compiler):
    root = BASE / 'third_party/compiler-rt-builtins'
    sources = [BASE / 'runtime/console.c', BASE / 'third_party/mpaland-printf/printf.c']
    sources += [root / name for name in json.loads((root / 'UPSTREAM.json').read_text())['sources']]
    flags = ['--target=riscv32-none-elf', '-march=rv32imf_zicsr_zve32x_zvl128b',
             '-mabi=ilp32', '-Oz', '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
             '-msmall-data-limit=0', '-mno-relax', '-ffunction-sections', '-fdata-sections',
             '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
             '-I' + str(BASE / 'runtime/include')]
    digest = hashlib.sha256(json.dumps([compiler, flags]).encode())
    for path in sorted(set(sources) | set(root.glob('*.h')) | set(root.glob('*.inc'))
                       | {BASE / 'runtime/include/stdio.h', BASE / 'third_party/mpaland-printf/printf.h'}):
        digest.update(path.read_bytes())
    dest = OUT / 'build/runtime' / digest.hexdigest()[:16]
    archive = dest / 'libcoral-runtime.a'
    if archive.is_file():
        return archive
    dest.mkdir(parents=True, exist_ok=True)
    require(execute([compiler, *flags, '-c', *map(str, sources)], 60,
                    dest / 'compile.log', dest))
    ar = llvm_tool('llvm-ar', compiler)
    if not ar:
        raise RuntimeError('llvm-ar required to build the target runtime')
    require(execute([ar, 'rcs', str(archive), *map(str, sorted(dest.glob('*.o')))],
                    10, dest / 'archive.log', dest))
    return archive


def compile_c(source, clang):
    source = source.resolve()
    if not source.is_file():
        raise RuntimeError("source missing: " + str(source))
    compiler = shutil.which(clang)
    linker = llvm_tool("ld.lld", clang)
    if compiler is None or linker is None:
        raise RuntimeError("requires Clang and LLD (default clang-17 / ld.lld-17)")
    runtime = build_runtime(compiler)
    dest = OUT / "elf" / source.stem
    dest.mkdir(parents=True, exist_ok=True)
    elf = dest / (source.stem + ".elf")
    command = [compiler, "--target=riscv32-none-elf",
               "-march=rv32imf_zicsr_zve32x_zvl128b", "-mabi=ilp32", "-O2",
               "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
               "-fno-vectorize", "-fno-slp-vectorize", "-msmall-data-limit=0",
               "-mno-relax", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
               "-nostdlib", "-fuse-ld=" + linker,
               "-I" + str(BASE / "runtime/include"),
               "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections",
               "-Wl,--no-relax,--build-id=none",
               "-Wl,-T," + str(BASE / "examples/link.ld"),
               "-Wl,-Map," + str(dest / (source.stem + ".map")),
               str(BASE / "examples/start.S"), str(source), str(runtime), "-o", str(elf)]
    result = execute(command, 30, dest / 'compile.log', announce_end=False)
    external = False
    if result['exit_code'] and 'will not fit in region' in (dest / 'compile.log').read_text():
        # Full printf plus RV32 software double helpers exceed the 8 KiB ITCM.
        # Use the existing RTL AXI instruction-fetch path and external RAM.
        command = [x.replace(str(BASE / 'examples/link.ld'),
                             str(BASE / 'examples/link-external.ld')) for x in command]
        result = execute(command, 30, dest / 'compile-external.log')
        external = True
    require(result)
    (dest / 'layout.json').write_text(json.dumps(dict(
        external_text=external, runtime=str(runtime),
        linker_script=str(BASE / ('examples/link-external.ld' if external else 'examples/link.ld'))),
        indent=2) + '\n')
    if external:
        print('Code exceeds 8 KiB ITCM: using external AXI RAM at 0x20000000.', file=sys.stderr)
    manifest = dest / 'feedback.json'
    if source == (BASE / 'examples/rvv_smoke.c').resolve():
        manifest.write_text(json.dumps(dict(
            elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest(),
            show=['a:u32', 'b:u32', 'c:u32'], check_add_u32=True), indent=2) + '\n')
    else:
        manifest.unlink(missing_ok=True)
    print("ELF:", elf, flush=True)
    return elf


def run_elf(elf, seconds, cycles, trace=False, show=(), check_add=False, limit=32, verbose=False):
    elf = elf.resolve()
    if not SIM.is_file():
        raise RuntimeError("simulator missing: " + str(SIM))
    with elf.open("rb") as stream:
        header = stream.read(20)
    if header[:6] != b"\x7fELF\x01\x01" or header[18:20] != b"\xf3\x00":
        raise RuntimeError("requires little-endian ELF32 RISC-V")
    dest = OUT / "runs" / (elf.stem + "_" + str(time.time_ns()))
    dest.mkdir(parents=True, exist_ok=True)
    dump = dest / 'memory.bin'
    console = dest / 'console.bin'
    elf_sha = hashlib.sha256(elf.read_bytes()).hexdigest()
    if verbose:
        print(f"[装载 ELF] {elf}\n[模型] 单核 Coral，标量 + RVV，VLEN=128 bit", file=sys.stderr, flush=True)
    start_time = time.monotonic()
    result = execute([str(SIM), str(elf), "--cycles=" + str(cycles),
                      "--trace=" + str(trace).lower(), '--dump_memory=' + str(dump),
                      '--console_file=' + str(console)],
                     seconds, dest / "run.log", BASE, live=verbose, announce_end=verbose, console=console)
    result.update(elf_sha256=elf_sha, native_exit_code=result['exit_code'],
                  console=str(console), console_bytes=console.stat().st_size,
                  wall_seconds=time.monotonic() - start_time,
                  simulator_sha256=hashlib.sha256(SIM.read_bytes()).hexdigest())
    messages = [json.loads(line[len('CORAL_SIM_RESULT '):])
                for line in (dest / 'run.log').read_text(errors='replace').splitlines()
                if line.startswith('CORAL_SIM_RESULT ')]
    result['simulation'] = messages[0] if len(messages) == 1 else None
    if result['simulation'] and verbose:
        sim = result['simulation']
        print(f"[硬件状态] halted={sim['halted']} fault={sim['fault']}", file=sys.stderr, flush=True)
        print(f"[整个仿真] {sim['simulation_time_ns']} ns（包含装载/复位）；"
              f"宿主机耗时 {result['wall_seconds']:.3f} s", file=sys.stderr, flush=True)
    if not result['exit_code'] and (
            not result['simulation'] or result['simulation']['status'] != 'PASS'):
        result.update(exit_code=2, error='缺少明确的 RTL PASS 完成记录')
    if dump.is_file() and result['simulation'] and result['simulation'].get('dump_ok'):
        try:
            data = feedback.inspect(elf, dump, show, check_add)
            result['feedback'] = data
            if show:
                with contextlib.redirect_stdout(sys.stderr):
                    feedback.display(data, limit)
            profile = data['profile']
            if not result['exit_code'] and (
                    (data['validation'] and not data['validation']['passed']) or
                    (profile and (not profile['completed'] or profile['main_return'] != 0))):
                result.update(exit_code=2, error='RTL 数据读回校验失败')
        except (ValueError, OSError) as error:
            result['error'] = str(error)
            if not result['exit_code']:
                result['exit_code'] = 2
    elif not result['exit_code']:
        result.update(exit_code=2, error='缺少 RTL SRAM 数据文件')
    state = 'PASS' if result['exit_code'] == 0 else ('TIMEOUT' if result['exit_code'] == 124 else 'FAIL')
    result['status'] = state
    sim = result['simulation'] or {}
    print(f"Instructions: {sim.get('retired_instructions', 'unavailable')}", file=sys.stderr, flush=True)
    print(f"Cycles: {sim.get('core_cycles', 'unavailable')}", file=sys.stderr, flush=True)
    profile = (result.get('feedback') or {}).get('profile')
    returned = f"; main returned {profile['main_return']}" if profile and profile['completed'] else ''
    print(f"Exit status: {result['exit_code']}{returned}", file=sys.stderr, flush=True)
    if sim.get('fault'):
        print(f"Fault: mcause=0x{sim.get('mcause', 0):x}, mepc=0x{sim.get('mepc', 0):x}", file=sys.stderr, flush=True)
    if result.get('error'):
        print('[原因] ' + result['error'], file=sys.stderr, flush=True)
    if verbose or state != 'PASS':
        print(f"Log: {dest / 'run.log'}", file=sys.stderr, flush=True)
    (dest / "run.json").write_text(json.dumps(result, indent=2) + "\n")
    feedback.write_report(dest / 'report.md', result)
    latest = dest.parent / 'latest'
    if not latest.exists() or latest.is_symlink():
        temporary = dest.parent / ('.latest-' + str(time.time_ns()))
        temporary.symlink_to(dest.name, target_is_directory=True)
        temporary.replace(latest)
    if verbose:
        print(f"[可读报告] {dest / 'report.md'}", file=sys.stderr, flush=True)
    return result


def positive(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("build", help="build the RTL simulator from this checkout")
    build.add_argument("--timeout", type=positive, default=1800)
    compile_cmd = sub.add_parser("compile")
    compile_cmd.add_argument("source", type=Path)
    compile_cmd.add_argument("--clang", default="clang-17")
    run = sub.add_parser("run", prog="coral-sim")
    run.add_argument("elf", type=Path)
    run.add_argument("--trace", action="store_true")
    run.add_argument('--verbose', action='store_true', help='显示原始仿真日志和报告位置')
    run.add_argument('--show', action='append', default=[], help='ELF 符号名[:u32|i32|f32]，可多次指定')
    run.add_argument('--check-add-u32', action='store_true', help='独立检查 a+b=c，需要读取这三个数组')
    run.add_argument('--limit', type=positive, default=32, help='终端最多显示的数组元素数；校验仍覆盖全部元素')
    demo = sub.add_parser('demo', help='编译并演示 RVV 输入、输出和校验')
    demo.add_argument('--clang', default='clang-17')
    demo.add_argument('--trace', action='store_true')
    test = sub.add_parser("test")
    test.add_argument("--clang", default="clang-17")
    for cmd in (run, test, demo):
        cmd.add_argument("--timeout", type=positive, default=30)
        cmd.add_argument("--cycles", type=positive, default=2000000)
    args = parser.parse_args()
    if args.action == "build":
        return subprocess.call([sys.executable, str(BASE / "build.py"), "--target", "rtl", "--timeout", str(args.timeout)])
    elif args.action == "compile":
        compile_c(args.source, args.clang)
    elif args.action == "run":
        code = run_elf(args.elf, args.timeout, args.cycles, args.trace,
                       args.show, args.check_add_u32, args.limit, args.verbose)["exit_code"]
        return code if code >= 0 else 128 - code
    elif args.action == 'demo':
        elf = compile_c(BASE / 'examples/rvv_smoke.c', args.clang)
        code = run_elf(elf, args.timeout, args.cycles, args.trace,
                       show=['a:u32', 'b:u32', 'c:u32'], check_add=True)['exit_code']
        return code if code >= 0 else 128 - code
    else:
        results = []
        for name, expected in (("rvv_smoke", 0), ("fail", 1), ("illegal", 1)):
            elf = compile_c(BASE / "examples" / (name + ".c"), args.clang)
            result = run_elf(elf, args.timeout, args.cycles,
                             show=['a:u32', 'b:u32', 'c:u32'] if name == 'rvv_smoke' else [],
                             check_add=name == 'rvv_smoke')
            result.update(sample=name, expected_exit=expected,
                          passed=result["exit_code"] == expected and
                          result['simulation']['retired_instructions'] > 0)
            results.append(result)
        elf = compile_c(BASE / 'examples/rvv_bad_result.c', args.clang)
        result = run_elf(elf, args.timeout, args.cycles,
                         show=['a:u32', 'b:u32', 'c:u32'], check_add=True)
        result.update(sample='rvv_bad_result', expected_exit=2,
                      passed=result['exit_code'] == 2 and result['native_exit_code'] == 0
                      and result['feedback']['validation']['mismatches'] == [7])
        results.append(result)
        elf = compile_c(BASE / 'examples/loop.c', args.clang)
        result = run_elf(elf, 1, 2147483647)
        result.update(sample='loop_timeout', expected_exit=124,
                      passed=result['exit_code'] == 124)
        results.append(result)
        passed = all(x["passed"] for x in results)
        (OUT / "verification.json").write_text(json.dumps(
            dict(passed=passed, results=results), indent=2) + "\n")
        print("ALL PASS" if passed else "FAILED", flush=True)
        return 0 if passed else 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
