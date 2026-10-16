#!/usr/bin/env python3
"""Compile explicit Coral Zve32x RVV kernels and an RV32IM controller."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ncoral_paths import ROOT
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'multicore'))
from run import UPSTREAM, bounded, timestamp
from build_kernel import find_tool


def validate_rvv_firmware():
    """Reject stale scalar boot artifacts even when a caller skips compilation."""
    output=ROOT/'die/logs/firmware'
    report=json.loads((output/'report.json').read_text())
    evidence=report.get('rvv_assembly_evidence',{})
    if (report.get('status')!='PASS' or report.get('compute_backend')!='RVV' or
            set(evidence)!=set(('coral','complex')) or
            any(not checks or not all(checks.values()) for checks in evidence.values())):
        raise RuntimeError('missing verified RVV firmware; run python3 die/build_firmware.py')
    for name,digest in report['outputs_sha256'].items():
        if hashlib.sha256((output/name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('RVV firmware artifact checksum mismatch: '+name)
    return report


def build(timeout=30, clang_path=None, lld_path=None):
    output = ROOT / 'die/logs/firmware'
    output.mkdir(parents=True, exist_ok=True)
    source = UPSTREAM / 'experiments/multicore'
    device = source / 'device'
    control = source / 'die/firmware'
    binary = output / 'controller.bin'
    binary.unlink(missing_ok=True)
    vector_isa='rv32im_zicsr_zve32x_zvl128b'
    report = {'status': 'STARTED', 'started': timestamp(), 'phases': [], 'commands': [],
              'controller_isa': 'rv32im', 'coral_isa': {'add':vector_isa,'complex':vector_isa},
              'compute_backend':'RVV','abi': 'ilp32','rvv_assembly_evidence':{}}
    def phase(name, command):
        report['commands'].append(command)
        result = bounded(command, timeout, output / (name + '.log'), ROOT)
        report['phases'].append(result)
        if result['exit_code']:
            raise RuntimeError('firmware phase failed: ' + name + '; see ' + result['log'])
    try:
        clang = find_tool(clang_path, ['clang-17', 'clang'])
        lld = find_tool(lld_path, ['ld.lld-17', 'ld.lld'])
        objcopy = find_tool(None, ['llvm-objcopy-17', 'llvm-objcopy'])
        objdump = find_tool(None, ['llvm-objdump-17', 'llvm-objdump'])
        base = ['--target=riscv32-none-elf', '-mabi=ilp32', '-O2', '-ffreestanding',
                '-fno-builtin', '-fno-stack-protector', '-fno-vectorize', '-fno-slp-vectorize',
                '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
                '-msmall-data-limit=0', '-mno-relax']
        link = ['-nostdlib', '-fuse-ld=' + lld, '-Wl,--no-relax,--build-id=none']
        phase('compiler', [clang, '--version'])
        for name, kernel, isa in [('coral','add.c',vector_isa),('complex','complex.c',vector_isa)]:
            phase(name+'_link', [clang, *base, '-march='+isa, *link,
                                '-Wl,-T,' + str(device / 'link.ld'),
                                str(device / 'start.S'), str(device / kernel),
                                '-o', str(output / (name+'.elf'))])
            phase(name+'_disassembly',[objdump,'-d','--mattr=+m,+zve32x,+zvl128b',
                                       str(output/(name+'.elf'))])
            disassembly=(output/(name+'_disassembly.log')).read_text()
            required=['vle32.v','vse32.v','vset','vadd.vv'] if name=='coral' else [
                'vle32.v','vlse32.v','vse32.v','vmacc.vx','vmadd.vx','vmax.vx','vset']
            evidence={mnemonic:mnemonic in disassembly for mnemonic in required}
            report['rvv_assembly_evidence'][name]=evidence
            if not all(evidence.values()):
                raise RuntimeError(name+' kernel is missing required RVV instructions')
            phase(name+'_binary', [objcopy, '-O', 'binary', str(output / (name+'.elf')),
                                   str(output / (name+'.bin'))])
            coral = (output / (name+'.bin')).read_bytes()
            elf = (output / (name+'.elf')).read_bytes()
            if not coral or len(coral) % 4 or len(coral) > 8192 or elf[0:4] != b'\x7fELF':
                raise RuntimeError('Coral bootstrap requires a word-aligned ITCM-only kernel <= 8KiB')
            entry = struct.unpack_from('<I', elf, 24)[0]
            if entry >= len(coral) or entry % 4:
                raise RuntimeError('invalid Coral entry')
            words = struct.unpack('<' + 'I' * (len(coral) // 4), coral)
            prefix=name.upper()
            (output / (name+'_image.h')).write_text(
                f'#ifndef {prefix}_DIE_IMAGE_H_\n#define {prefix}_DIE_IMAGE_H_\n#include <stdint.h>\n'
                f'#define {prefix}_IMAGE_WORDS {len(words)}u\n#define {prefix}_IMAGE_ENTRY {entry}u\n'
                f'static const uint32_t {name}_image[] = {{\n' +
                ',\n'.join('  0x%08xu' % word for word in words) + '\n};\n#endif\n')
        phase('controller_link', [clang, *base, '-march=rv32im', *link,
                                 '-I' + str(output), '-Wl,-T,' + str(control / 'link.ld'),
                                 '-Wl,-Map=' + str(output / 'controller.map'),
                                 str(control / 'start.S'), str(control / 'runtime.c'), str(control / 'main.c'),
                                 str(control / 'complex_demo.c'),
                                 '-o', str(output / 'controller.elf')])
        phase('controller_binary', [objcopy, '-O', 'binary', str(output / 'controller.elf'), str(binary)])
        if not binary.stat().st_size or binary.stat().st_size > 49152:
            raise RuntimeError('controller firmware exceeds its 48KiB linked memory region')
        inputs = [*control.glob('*'), *device.glob('*')]
        report['source_sha256'] = {str(path.relative_to(UPSTREAM)):
                                    hashlib.sha256(path.read_bytes()).hexdigest()
                                    for path in inputs if path.is_file()}
        report['outputs_sha256'] = {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                                    for name in ('coral.elf', 'coral.bin', 'complex.elf', 'complex.bin', 'controller.elf', 'controller.bin')}
        report.update(status='PASS', controller_binary=str(binary))
    except Exception as error:
        binary.unlink(missing_ok=True)
        report.update(status='FAIL', error=str(error))
    finally:
        report['finished'] = timestamp()
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if report['status'] != 'PASS':
        raise RuntimeError(report['error'])
    print('Control firmware:', binary, flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=30)
    parser.add_argument('--clang')
    parser.add_argument('--lld')
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error('timeout must be positive')
    try:
        build(args.timeout, args.clang, args.lld)
    except Exception as error:
        raise SystemExit(str(error))
