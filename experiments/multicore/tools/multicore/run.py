#!/usr/bin/env python3
"""Build and run 2/4 real Coral RTL instances with explicit phase deadlines."""
import argparse
import datetime as dt
import hashlib
import ctypes.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ncoral_paths import ROOT, UPSTREAM
TOOLS = ROOT / ".tools"
BAZEL_NAME = "bazel-9.1.0-linux-x86_64"
BAZEL_SHA256 = "a667454f3f4f8878df8199136b82c199f6ada8477b337fae3b1ef854f01e4e2f"


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def target_name(cores, variant):
    return ('quad_' if cores == 4 else '') + variant


def bazel_binary():
    TOOLS.mkdir(exist_ok=True)
    path = TOOLS / BAZEL_NAME
    if not path.exists():
        part = path.with_suffix(".part")
        try:
            url = "https://releases.bazel.build/9.1.0/release/" + BAZEL_NAME
            with urllib.request.urlopen(url, timeout=60) as response, part.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if hashlib.sha256(part.read_bytes()).hexdigest() != BAZEL_SHA256:
                raise RuntimeError("Bazel SHA256 mismatch")
            part.replace(path)
        finally:
            part.unlink(missing_ok=True)
    if hashlib.sha256(path.read_bytes()).hexdigest() != BAZEL_SHA256:
        raise RuntimeError("local Bazel binary SHA256 mismatch")
    path.chmod(0o755)
    return path


def bounded(command, seconds, log, cwd, *, cancel_event=None, on_started=None):
    start = timestamp()
    expected_end = (dt.datetime.fromisoformat(start) + dt.timedelta(seconds=seconds)).isoformat()
    print(f"Starting {log.name}; limit={seconds}s; deadline={expected_end}", flush=True)
    if on_started:
        on_started({"started": start, "deadline": expected_end, "deadline_seconds": seconds})
    with log.open("w") as stream:
        process = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            end = time.monotonic() + seconds
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("cancel requested")
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, seconds)
                try:
                    code = process.wait(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except (subprocess.TimeoutExpired, KeyboardInterrupt, InterruptedError) as error:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 130 if isinstance(error, InterruptedError) else 124
    return {"started": start, "finished": timestamp(), "deadline": expected_end, "deadline_seconds": seconds,
            "exit_code": code, "log": str(log)}


def bazel_build_command(target, jobs):
    bazel = bazel_binary()
    # The upstream zlib 1.2.11 archive URL returns 404. Keep the IP
    # unchanged and use the system's ABI-compatible zlib for Verilator.
    if not Path('/usr/include/zlib.h').is_file() or not ctypes.util.find_library('z'):
        raise RuntimeError('system zlib development headers/library are required')
    zlib = TOOLS / 'system-zlib'
    zlib.mkdir(exist_ok=True)
    include = zlib / 'include'
    include.mkdir(exist_ok=True)
    header = include / 'lz4.h'
    header_hash = 'c1614ecf7ada7b0be1acb560d4239595f96fbb7aa6a79a7c40cb358753830be6'
    if not header.exists():
        data = urllib.request.urlopen(
            'https://raw.githubusercontent.com/lz4/lz4/v1.9.4/lib/lz4.h', timeout=30).read()
        if hashlib.sha256(data).hexdigest() != header_hash:
            raise RuntimeError('LZ4 header checksum mismatch')
        header.write_bytes(data)
    if hashlib.sha256(header.read_bytes()).hexdigest() != header_hash:
        raise RuntimeError('local LZ4 header checksum mismatch')
    library = next((p for p in (
        Path('/lib/x86_64-linux-gnu/liblz4.so.1'),
        Path('/usr/lib/x86_64-linux-gnu/liblz4.so.1')) if p.exists()), None)
    if library is None:
        raise RuntimeError('system LZ4 runtime library is required')
    libdir = zlib / 'lib'
    libdir.mkdir(exist_ok=True)
    link = libdir / 'liblz4.so'
    if not link.exists():
        link.symlink_to(library.resolve())
    (zlib / 'BUILD.bazel').write_text(
        'cc_library(name="zlib", hdrs=["include/lz4.h"], includes=["include"], '
        'linkopts=["-lz", "-L' + str(libdir) + '"], visibility=["//visibility:public"])\n')
    (zlib / 'WORKSPACE.bazel').write_text('workspace(name="net_zlib")\n')
    dependencies = {'zlib': 'system', 'lz4_header_sha256': header_hash,
                                     'lz4_library': str(library.resolve())}
    command = [str(bazel), "--batch", f"--output_user_root={TOOLS / 'bazel-cache'}",
               "--host_jvm_args=-Xmx1536m", "build", f"--jobs={jobs}",
               "--repo_env=CC=gcc", "--action_env=CC=gcc",
               f"--repo_env=CORALNPU_MAKE_JOBS={jobs}",
               f"--override_repository=+coralnpu_deps_ext+net_zlib={zlib}",
               f"--distdir={TOOLS / 'distfiles'}",
               "--noshow_progress", "--color=no", "--curses=no",
               f"//experiments/multicore:smoke_{target}"]
    if os.environ.get("NCORAL_BAZEL_OUTPUT_USER_ROOT")=="default":
        command=[arg for arg in command if not arg.startswith("--output_user_root=")]
    return command, dependencies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("scalar", "rvv"), default="scalar")
    parser.add_argument("--cores", type=int, choices=(2, 4), default=4)
    parser.add_argument("--elf", type=Path, help="LLVM-compiled scalar ELF instead of built-in firmware")
    parser.add_argument("--build-timeout", type=int, default=1200)
    parser.add_argument("--run-timeout", type=int, default=60)
    parser.add_argument("--max-cycles", type=int, default=200000)
    parser.add_argument("--elements", type=int, default=37)
    parser.add_argument("--stall-period", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    if min(args.build_timeout, args.run_timeout, args.jobs) < 1:
        parser.error("timeouts and jobs must be positive")
    if not 1 <= args.elements <= 128 or args.max_cycles < 40 or not 0 <= args.stall_period <= 1000:
        parser.error("invalid elements/cycle/stall range")
    if args.elf:
        args.elf = str(args.elf.resolve())
        if args.variant != 'scalar' or not Path(args.elf).is_file():
            parser.error('--elf requires scalar variant and an existing ELF file')
    target = target_name(args.cores, args.variant)
    logs = ROOT / "multicore" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    report_path = logs / f"report_{target}{'_elf' if args.elf else ''}.json"
    report = {"status": "STARTED", "started": timestamp(), "variant": args.variant,
              "cores": args.cores,
              "upstream_commit": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=UPSTREAM, text=True).strip(),
              "options": vars(args), "phases": []}
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    try:
        if not args.skip_build:
            command, dependencies = bazel_build_command(target, args.jobs)
            report['build_dependencies'] = dependencies
            previous = logs / f"build_{target}.log"
            if previous.exists():
                previous.replace(logs / f"build_{target}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
            phase = bounded(command, args.build_timeout, logs / f"build_{target}.log", UPSTREAM)
            report["phases"].append(phase)
            if phase["exit_code"]:
                raise RuntimeError("RTL build failed or timed out; see " + phase["log"])
        binary = UPSTREAM / "bazel-bin/experiments/multicore" / f"smoke_{target}"
        if not binary.is_file():
            raise RuntimeError("compiled RTL simulator missing: " + str(binary))
        command = [str(binary), "--elements", str(args.elements),
                   "--max-cycles", str(args.max_cycles), "--stall-period", str(args.stall_period)]
        if args.elf:
            command.extend(['--elf', args.elf])
            report['elf_sha256'] = hashlib.sha256(Path(args.elf).read_bytes()).hexdigest()
        report['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        phase = bounded(command, args.run_timeout,
                        logs / f"run_{target}{'_elf' if args.elf else ''}.log", UPSTREAM)
        report["phases"].append(phase)
        if phase["exit_code"]:
            raise RuntimeError("RTL simulation failed or timed out; see " + phase["log"])
        lines = Path(phase["log"]).read_text().splitlines()
        results = [json.loads(line) for line in lines if line.startswith('{"result":')]
        if (len(results) != 1 or results[0].get("result") != "PASS" or
                results[0].get('cores') != args.cores or
                results[0].get('completion_mask') != (1 << args.cores) - 1):
            raise RuntimeError("missing unambiguous PASS result")
        report.update(status="PASS", simulation=results[0])
        print(json.dumps(results[0], indent=2), flush=True)
    except Exception as error:
        report.update(status="FAIL", error=str(error))
        print(str(error), file=sys.stderr)
    finally:
        report["finished"] = timestamp()
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print("Report:", report_path, flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
