#!/usr/bin/env python3
"""Build and test the Coral functional/RTL simulators from this checkout."""
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile
from paths import BASE, ROOT, OUT, llvm_tool, find_clang
from coral_main import execute, require
from bootstrap import find_bazel, download_bazel


def positive(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return result


def install(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix='.install-', dir=destination.parent)
    os.close(fd)
    temporary = Path(filename)
    try:
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('iss', 'rtl', 'all'), default='iss')
    parser.add_argument('--check', action='store_true', help='check all required local tools without building/downloading')
    parser.add_argument('--offline', action='store_true', help='do not bootstrap a missing Bazel executable')
    parser.add_argument('--test', action='store_true', help='compile fixtures and run regressions')
    parser.add_argument('--timeout', type=positive, default=1800, help='deadline in seconds for each Bazel build')
    parser.add_argument('--jobs', type=positive, default=4)
    parser.add_argument('--make-jobs', type=positive, default=8)
    parser.add_argument('--bazel', default=os.environ.get('BAZEL'))
    parser.add_argument('--clang', default=None, help='Clang executable (default: auto-detect from PATH)')
    parser.add_argument('--java', type=Path, help='path to a Java 11+ executable')
    parser.add_argument('--output-base', type=Path)
    parser.add_argument('--distdir', type=Path)
    parser.add_argument('--zlib-repository', type=Path, help='optional existing Bazel net_zlib override')
    args = parser.parse_args()
    tools = ROOT.parent / '.tools'
    targets = ('iss', 'rtl') if args.target == 'all' else (args.target,)
    missing = []
    selected_tools = {}
    compiler = None
    if args.test or 'iss' in targets:
        try:
            compiler = find_clang(args.clang)
            print('Clang: ' + compiler, flush=True)
            os.environ['CORAL_SIM_CLANG'] = compiler
            for name in ('clang++', 'ld.lld', 'llvm-ar', *(['llvm-objcopy'] if args.test else [])):
                try:
                    selected_tools[name] = llvm_tool(name, compiler)
                    print(name + ': ' + selected_tools[name], flush=True)
                except RuntimeError as error:
                    missing.append(str(error))
        except RuntimeError as error:
            missing.append(str(error))
    for name in ('gcc', 'g++', 'make'):
        if not shutil.which(name):
            missing.append(name + ' not found in PATH')
    bazel = find_bazel(args.bazel)
    if missing:
        raise RuntimeError('missing build dependencies:\n  - ' + '\n  - '.join(missing) +
            '\nUbuntu/Debian: install build-essential, clang-XX, lld-XX and llvm-XX (matching your Clang).')
    if args.check:
        print('Bazel: ' + (bazel or 'not installed; build will download the pinned version automatically'))
        print('Local tool checks passed. Java is located/provisioned by Bazel when needed.')
        return 0
    if not bazel:
        if args.offline:
            raise RuntimeError('Bazel missing in offline mode; pass --bazel /path/to/bazel')
        bazel = download_bazel()
    print('Bazel: ' + bazel, flush=True)
    output_base = args.output_base
    bazel_out = ROOT / 'bazel-out'
    if output_base is None and bazel_out.is_symlink() and bazel_out.exists():
        candidate = bazel_out.resolve().parents[2]
        if (candidate / 'execroot/_main').is_dir():
            output_base = candidate
    command = [str(bazel), '--batch', '--host_jvm_args=-Xmx1536m']
    if (tools / 'bazel-cache').is_dir():
        command += ['--output_user_root=' + str(tools / 'bazel-cache')]
    if output_base:
        command += ['--output_base=' + str(output_base.resolve())]
    # Keep both repository and action environments explicit: .bazelrc defaults CC to clang.
    common = ['--repo_env=CC=gcc', '--action_env=CC=gcc',
              '--repo_env=CORALNPU_MAKE_JOBS=' + str(args.make_jobs),
              '--noshow_progress', '--color=no', '--curses=no']
    distdir = args.distdir or (tools / 'distfiles')
    if distdir.is_dir():
        common += ['--distdir=' + str(distdir.resolve())]
    zlib = args.zlib_repository
    # Reuse the optional local dependency workaround used by this workspace.
    # Fresh clones use the repository's pinned upstream dependency by default.
    if zlib is None and (tools / 'system-zlib/BUILD.bazel').is_file():
        zlib = tools / 'system-zlib'
    if zlib:
        common += ['--override_repository=+coralnpu_deps_ext+net_zlib=' + str(zlib.resolve())]
    OUT.mkdir(parents=True, exist_ok=True)
    for target in targets:
        flags = common + ['--jobs=' + str(min(args.jobs, 2) if target == 'rtl' else args.jobs)]
        if target == 'iss':
            shim = OUT / 'host-tools'
            shim.mkdir(exist_ok=True)
            for name, path in {'clang': compiler, **{name: selected_tools[name]
                               for name in ('clang++', 'ld.lld', 'llvm-ar')}}.items():
                link = shim / name
                link.unlink(missing_ok=True)
                link.symlink_to(path)
            java = args.java or shutil.which('java')
            if not java and output_base:
                matches = sorted((output_base / 'external').glob('*remotejdk11_linux/bin/java'))
                java = matches[0] if matches else None
            if not java:
                log = OUT / 'logs/java-home.log'
                require(execute(command + ['info', 'java-home'], 120, log, ROOT))
                for line in reversed(log.read_text().splitlines()):
                    candidate = Path(line.strip()) / 'bin/java'
                    if candidate.is_file():
                        java = candidate
                        break
            if not java or not Path(java).is_file():
                raise RuntimeError('Java required by MPACT instruction generation; pass --java /path/to/bin/java')
            link = shim / 'java'
            link.unlink(missing_ok=True)
            link.symlink_to(Path(java).resolve())
            path = str(shim) + os.pathsep + os.environ.get('PATH', '')
            flags += ['--platforms=//toolchain/host_clang:host_clang_platform',
                      '--cxxopt=-std=c++20', '--copt=-I.',
                      '--action_env=PATH=' + path, '--host_action_env=PATH=' + path]
            label, artifact, binary = '//experiments/functional:coral_iss', 'experiments/functional/coral_iss', 'coral-iss'
        else:
            label, artifact, binary = '//tests/verilator_sim:rvv_core_mini_axi_sim', 'tests/verilator_sim/rvv_core_mini_axi_sim', 'coral-rvv-sim'
        require(execute(command + ['build', *flags, label], args.timeout,
                        OUT / ('logs/build-' + target + '.log'), ROOT, live=True))
        install(ROOT / 'bazel-bin' / artifact, OUT / 'bin' / binary)
    for name in ('coral-sim', 'coral-rtl-sim'):
        link = OUT / 'bin' / name
        link.unlink(missing_ok=True)
        link.symlink_to(BASE / name)
    if args.test:
        require(execute([sys.executable, str(BASE / 'test_toolchain.py')], 30,
                        OUT / 'logs/test-toolchain.log', ROOT, live=True))
        if 'iss' in targets:
            require(execute([sys.executable, str(BASE / 'test_functional.py')], 300,
                            OUT / 'logs/test-iss.log', ROOT, live=True))
        if 'rtl' in targets:
            require(execute([sys.executable, str(BASE / 'coral_main.py'), 'test', '--clang', compiler],
                            300, OUT / 'logs/test-rtl.log', ROOT, live=True))
            require(execute([sys.executable, str(BASE / 'test_console.py')],
                            300, OUT / 'logs/test-console.log', ROOT, live=True))
    entry = 'coral-rtl-sim' if args.target == 'rtl' else 'coral-sim'
    print('Ready: ' + str(OUT / 'bin' / entry))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print('build: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
