#!/usr/bin/env python3
"""Entry point for the real RISC-V controller + four-Coral die model."""
import argparse
import ctypes.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from ncoral_paths import ROOT, UPSTREAM

HERE = Path(__file__).resolve().parent


def doctor(argv):
    parser = argparse.ArgumentParser(description="Check local prerequisites without downloading tools.")
    parser.parse_args(argv)
    checks = []

    def check(name, ok, detail):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)

    check("host", platform.system() == "Linux" and platform.machine() in ("x86_64", "amd64"),
          f"{platform.system()} {platform.machine()}; pinned Bazel bootstrap requires Linux x86_64")
    check("python", sys.version_info >= (3, 9), sys.version.split()[0])
    for name, candidates in (
        ("gcc", ("gcc",)), ("g++", ("g++",)), ("make", ("make",)),
        ("clang", ("clang-17", "clang")), ("lld", ("ld.lld-17", "ld.lld")),
        ("objcopy", ("llvm-objcopy-17", "llvm-objcopy")),
        ("objdump", ("llvm-objdump-17", "llvm-objdump")),
    ):
        executable = next((path for candidate in candidates if (path := shutil.which(candidate))), None)
        check(name, executable is not None, executable or "install " + " / ".join(candidates))
    check("zlib", Path("/usr/include/zlib.h").is_file() and ctypes.util.find_library("z"),
          "system zlib header and shared library (Debian/Ubuntu: zlib1g-dev)")
    lz4 = next((path for path in (Path("/lib/x86_64-linux-gnu/liblz4.so.1"),
                                Path("/usr/lib/x86_64-linux-gnu/liblz4.so.1")) if path.exists()), None)
    check("lz4", lz4 is not None, str(lz4) if lz4 else "install liblz4-1")
    check("sources", (UPSTREAM / "MODULE.bazel").is_file() and
          (UPSTREAM / "experiments/multicore/die/Die.scala").is_file(), str(UPSTREAM))
    print("Artifact root:", ROOT)
    print("Bazel 9.1.0 and Verilator/Chisel dependencies are bootstrapped by build-sim; no system Java/Verilator is required.")
    print("First build requires network; doctor does not check network or compile a target program.")
    print("Free disk: %.1f GiB" % (shutil.disk_usage(UPSTREAM).free / 2**30))
    report = {"status": "PASS" if all(item["passed"] for item in checks) else "FAIL",
              "repository": str(UPSTREAM), "artifact_root": str(ROOT), "checks": checks}
    output = ROOT / "die/logs/doctor.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print("Report:", output)
    return 0 if report["status"] == "PASS" else 1


def build(argv):
    parser = argparse.ArgumentParser(description="Compile device/controller code, then build the RTL simulator.")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--build-timeout", type=int, default=1800)
    parser.add_argument("--firmware-timeout", type=int, default=30)
    parser.add_argument("--clang")
    parser.add_argument("--lld")
    args = parser.parse_args(argv)
    if min(args.jobs, args.build_timeout, args.firmware_timeout) < 1:
        parser.error("jobs and timeouts must be positive")
    code_args = ["--timeout", str(args.firmware_timeout)]
    for option in ("clang", "lld"):
        if getattr(args, option):
            code_args.extend(["--" + option, getattr(args, option)])
    # Each child announces and enforces its own phase deadlines.
    for script, options in (
        ("build_firmware.py", code_args),
        ("build_sim.py", ["--jobs", str(args.jobs), "--timeout", str(args.build_timeout)]),
    ):
        result = subprocess.run([sys.executable, str(HERE / "die" / script), *options])
        if result.returncode:
            return result.returncode
    print("Build complete. Run: python3 experiments/multicore/tools/ncoral.py demo", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Use COMMAND --help for options. run/demo/test reuse built artifacts; build first.",
    )
    parser.add_argument("command", choices=("doctor", "build", "build-sim", "build-code", "run", "demo", "test", "op"))
    # Parse only the command so --help belongs to the selected subcommand.
    args = parser.parse_args(sys.argv[1:2])
    remaining = sys.argv[2:]
    if args.command == "doctor":
        return doctor(remaining)
    if args.command == "build":
        return build(remaining)
    scripts = {"build-sim": "build_sim.py", "build-code": "build_firmware.py",
               "run": "run.py", "demo": "demo_complex.py", "test": "check.py", "op": "operators.py"}
    if args.command in ("run", "demo"):
        remaining = ["--skip-build", "--skip-firmware", *remaining]
    os.execv(sys.executable, [sys.executable, str(HERE / "die" / scripts[args.command]), *remaining])


if __name__ == "__main__":
    raise SystemExit(main())
