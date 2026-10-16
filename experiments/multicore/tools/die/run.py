#!/usr/bin/env python3
"""Bounded real-CPU + four-Coral single-die RTL build and execution."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ncoral_paths import ROOT
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'multicore'))
from run import UPSTREAM, bazel_build_command, bounded, timestamp
from build_firmware import build,validate_rvv_firmware


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-build', action='store_true', help='reuse RTL simulator, still compile firmware')
    parser.add_argument('--skip-firmware', action='store_true', help='reuse existing controller.bin')
    parser.add_argument('--build-timeout', type=int, default=1800)
    parser.add_argument('--run-timeout', type=int, default=60)
    parser.add_argument('--firmware-timeout', type=int, default=30)
    parser.add_argument('--max-cycles', type=int, default=2000000)
    parser.add_argument('--elements', type=int, default=37)
    parser.add_argument('--rounds', type=int, choices=(1, 2), default=1)
    parser.add_argument('--stall-period', type=int, default=3)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    if min(args.build_timeout,args.run_timeout,args.firmware_timeout,args.jobs)<1:
        parser.error('timeouts/jobs must be positive')
    if not 1<=args.elements<=128 or not 0<=args.stall_period<=1000 or args.max_cycles<40:
        parser.error('invalid elements/stall/cycle range')
    logs = ROOT / 'die/logs'
    logs.mkdir(parents=True,exist_ok=True)
    report = {'status':'STARTED','started':timestamp(),'options':vars(args),'phases':[]}
    try:
        vendor = UPSTREAM / 'experiments/multicore/die/vendor/picorv32'
        identity = json.loads((vendor/'manifest.json').read_text())
        for name,digest in identity['files'].items():
            if hashlib.sha256((vendor/name).read_bytes()).hexdigest()!=digest:
                raise RuntimeError('control CPU source checksum mismatch: '+name)
        report['controller_source']=identity
        if not args.skip_firmware:
            report['firmware']=build(args.firmware_timeout)
        report['firmware']=validate_rvv_firmware()
        if not args.skip_build:
            command,deps=bazel_build_command('die',args.jobs)
            report['build_dependencies']=deps
            previous=logs/'build.log'
            if previous.exists():
                previous.replace(logs/('build_'+timestamp().replace(':','-')+'.log'))
            phase=bounded(command,args.build_timeout,logs/'build.log',UPSTREAM)
            report['phases'].append(phase)
            if phase['exit_code']:
                raise RuntimeError('single-die RTL build failed/timed out; see '+phase['log'])
        binary=UPSTREAM/'bazel-bin/experiments/multicore/smoke_die'
        boot=logs/'firmware/controller.bin'
        if not binary.is_file() or not boot.is_file():
            raise RuntimeError('missing compiled single-die simulator or controller firmware')
        report['simulator_sha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
        report['controller_binary_sha256']=hashlib.sha256(boot.read_bytes()).hexdigest()
        command=[str(binary),'--boot',str(boot),'--elements',str(args.elements),
                 '--rounds',str(args.rounds),'--stall-period',str(args.stall_period),
                 '--max-cycles',str(args.max_cycles)]
        phase=bounded(command,args.run_timeout,logs/'run.log',UPSTREAM)
        report['phases'].append(phase)
        if phase['exit_code']:
            raise RuntimeError('single-die RTL execution failed/timed out; see '+phase['log'])
        results=[json.loads(line) for line in (logs/'run.log').read_text().splitlines()
                 if line.startswith('{"result":')]
        if (len(results)!=1 or results[0].get('result')!='PASS' or
                results[0].get('compute_backend')!='RVV' or
                results[0].get('coral_cores')!=4 or not results[0].get('cpu_runs_runtime') or
                not results[0].get('rtl_shared_sram') or results[0].get('testbench_dispatch') is not False):
            raise RuntimeError('missing unambiguous CPU-controlled die PASS')
        report.update(status='PASS',simulation=results[0])
        print(json.dumps(results[0],indent=2),flush=True)
    except Exception as error:
        report.update(status='FAIL',error=str(error))
        print(str(error),file=sys.stderr)
    finally:
        report['finished']=timestamp()
        (logs/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print('Report:',logs/'report.json',flush=True)
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
