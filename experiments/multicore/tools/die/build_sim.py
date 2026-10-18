#!/usr/bin/env python3
"""Build the CPU + four RVV Coral RTL simulator without compiling firmware or running it."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ncoral_paths import ROOT, UPSTREAM
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "multicore"))
from run import bazel_build_command, bounded, timestamp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=1800, help="Bazel build phase deadline, in seconds")
    args = parser.parse_args()
    if min(args.jobs, args.timeout) < 1:
        parser.error("jobs and timeout must be positive")
    output = ROOT / "die/logs/simulator"
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "STARTED", "started": timestamp(), "options": vars(args),
              "target": "//experiments/multicore:smoke_die", "phases": [],
              "model": {"control_cpu": "PicoRV32 RV32IM", "coral_cores": 4,
                        "compute_backend": "RVV", "vlen_bits": 128, "vme_enabled": False}}
    try:
        source = UPSTREAM / "experiments/multicore"
        vendor = source / "die/vendor/picorv32"
        manifest = json.loads((vendor / "manifest.json").read_text())
        for name, digest in manifest["files"].items():
            if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("control CPU source checksum mismatch: " + name)
        report["controller_source"] = manifest
        command, dependencies = bazel_build_command("die", args.jobs)
        report.update(command=command, build_dependencies=dependencies)
        print("Command:", shlex.join(command), flush=True)
        phase = bounded(command, args.timeout, output / "build.log", UPSTREAM)
        report["phases"].append(phase)
        if phase["exit_code"]:
            raise RuntimeError("simulator build failed/timed out; see " + phase["log"])
        binary = UPSTREAM / "bazel-bin/experiments/multicore/smoke_die"
        if not binary.is_file():
            raise RuntimeError("Bazel succeeded but simulator is missing: " + str(binary))
        report.update(status="PASS", simulator=str(binary),
                      simulator_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        inputs = ["BUILD.bazel", "Cluster.scala", "die/Die.scala", "die/smoke_die.cc",
                  "die/complex_check.h", "die/profile_check.h", "device/complex_abi.h", "device/profile.h",
                  "die/vendor/picorv32/picorv32.v"]
        report["integration_source_sha256"] = {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in inputs}
        print("Simulator:", binary, flush=True)
        print("Firmware is built separately with ncoral.py build-code.", flush=True)
    except Exception as error:
        report.update(status="FAIL", error=str(error))
        print(str(error), file=sys.stderr)
    finally:
        report["finished"] = timestamp()
        path = output / "report.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        print("Report:", path, flush=True)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
