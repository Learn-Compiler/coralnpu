#!/usr/bin/env python3
"""Real CPU/Coral RTL: validate and print two independent complex operators."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ncoral_paths import ROOT
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'multicore'))
from run import UPSTREAM,bounded,bazel_build_command,timestamp
from build_firmware import build,validate_rvv_firmware


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--skip-build',action='store_true')
    p.add_argument('--skip-firmware',action='store_true')
    p.add_argument('--rows',type=int,default=7,choices=range(1,9))
    p.add_argument('--stall-period',type=int,default=3)
    p.add_argument('--build-timeout',type=int,default=1800)
    p.add_argument('--run-timeout',type=int,default=420)
    p.add_argument('--jobs',type=int,default=2)
    args=p.parse_args()
    if min(args.build_timeout,args.run_timeout,args.jobs)<1 or not 0<=args.stall_period<=1000:
        p.error('invalid timeout/jobs/stall range')
    output=ROOT/'die/logs/complex'/f'rows{args.rows}_stall{args.stall_period}'
    output.mkdir(parents=True,exist_ok=True)
    report={'status':'STARTED','started':timestamp(),'options':vars(args),'phases':[]}
    try:
        vendor=UPSTREAM/'experiments/multicore/die/vendor/picorv32'
        manifest=json.loads((vendor/'manifest.json').read_text())
        for name,digest in manifest['files'].items():
            if hashlib.sha256((vendor/name).read_bytes()).hexdigest()!=digest:
                raise RuntimeError('control CPU source checksum mismatch: '+name)
        if not args.skip_firmware:
            report['firmware']=build(30)
        report['firmware']=validate_rvv_firmware()
        if not args.skip_build:
            command,deps=bazel_build_command('die',args.jobs)
            phase=bounded(command,args.build_timeout,output/'build.log',UPSTREAM)
            report['phases'].append(phase)
            report['build_dependencies']=deps
            if phase['exit_code']:
                raise RuntimeError('RTL build failed; see '+phase['log'])
        binary=UPSTREAM/'bazel-bin/experiments/multicore/smoke_die'
        boot=ROOT/'die/logs/firmware/controller.bin'
        report['simulator_sha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
        report['controller_binary_sha256']=hashlib.sha256(boot.read_bytes()).hexdigest()
        traces=[]
        for scenario,name in [(1,'serial'),(2,'parallel')]:
            trace=output/(name+'.json')
            trace.unlink(missing_ok=True)
            command=[str(binary),'--boot',str(boot),'--scenario',str(scenario),'--rows',str(args.rows),
                     '--stall-period',str(args.stall_period),'--trace-json',str(trace)]
            phase=bounded(command,args.run_timeout,output/(name+'.log'),UPSTREAM)
            report['phases'].append(phase)
            text=Path(phase['log']).read_text()
            print('\n'+name.upper()+' (device AXI trace):\n'+text,flush=True)
            if phase['exit_code']==124:
                raise RuntimeError(name+' host deadline exceeded; see '+phase['log'])
            results=[json.loads(line) for line in text.splitlines() if line.startswith('{"result":')]
            if (phase['exit_code'] or len(results)!=1 or results[0].get('result')!='PASS' or
                    results[0].get('scenario')!=name or results[0].get('cpu_runs_runtime') is not True or
                    results[0].get('compute_backend')!='RVV' or results[0].get('vlen_bits')!=128 or
                    results[0].get('testbench_dispatch') is not False):
                raise RuntimeError(name+' correctness/ordering failed; see '+phase['log'])
            phase['simulation']=results[0]
            traces.append(json.loads(trace.read_text()))
        if traces[0]['outputs']!=traces[1]['outputs']:
            raise RuntimeError('serial and parallel output arrays differ')
        report.update(status='PASS',outputs_identical=True,
                      checked_elements=[len(x) for x in traces[0]['outputs']],
                      trace_files=[str(output/(name+'.json')) for name in ('serial','parallel')])
    except Exception as error:
        report.update(status='FAIL',error=str(error))
        print(str(error),file=sys.stderr)
    finally:
        report['finished']=timestamp()
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(report['status'],'Report:',output/'report.json',flush=True)
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
