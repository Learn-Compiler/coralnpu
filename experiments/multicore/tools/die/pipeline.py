#!/usr/bin/env python3
"""Run CPU event-queue -> AXI DMA -> RVV/Zvt -> AXI DMA RTL regressions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ncoral_paths import ROOT,UPSTREAM
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'multicore'))
from run import bounded,timestamp
from build_firmware import validate_rvv_firmware

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test',action='store_true',help='Run queue host tests and all pipeline RTL cases')
    parser.add_argument('--zvt',action='store_true',help='Use the VME RTL target and signed INT8 matrix kernel')
    parser.add_argument('--elements',type=int,default=37)
    parser.add_argument('--stall-period',type=int,default=3)
    parser.add_argument('--jobs',type=int,choices=(1,2,3,4),default=1)
    parser.add_argument('--timeout',type=int,default=180)
    args=parser.parse_args()
    if not 1<=args.elements<=128 or not 0<=args.stall_period<=1000 or args.timeout<1:parser.error('invalid size, stall or timeout')
    firmware=validate_rvv_firmware()
    if args.zvt and (not firmware.get('vme_enabled') or not all(firmware.get('zvt_assembly_evidence',{}).values()) or not firmware.get('zvt_assembly_evidence')):
        raise SystemExit('Build verified Zvt firmware first: ncoral.py build-code --zvt --clang PATH --lld PATH')
    binary=UPSTREAM/('bazel-bin/experiments/multicore/smoke_die_vme' if args.zvt else 'bazel-bin/experiments/multicore/smoke_die')
    boot=ROOT/'die/logs/firmware/controller.bin'
    output=ROOT/'die/logs/pipeline'
    output.mkdir(parents=True,exist_ok=True)
    report={'status':'STARTED','started':timestamp(),'cases':[],
            'simulator_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'controller_sha256':hashlib.sha256(boot.read_bytes()).hexdigest(),
            'vme_enabled':args.zvt,'dma':'independent AXI RTL master','queue':'control CPU firmware'}
    cases=[('zvt' if args.zvt else 'rvv',6 if args.zvt else 5,args.elements,args.stall_period,0)]
    if args.test:
        source=UPSTREAM/'experiments/multicore/die'
        for name,command in [
            ('queue_compile_c',['gcc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-g','-c',str(source/'firmware/queue.c'),'-o',str(output/'queue.o')]),
            ('queue_compile_test',['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-g',str(source/'queue_test.cc'),str(output/'queue.o'),'-o',str(output/'queue-test')]),
            ('queue_sanitizers',[str(output/'queue-test')])]:
            phase=bounded(command,60,output/(name+'.log'),UPSTREAM)
            if phase['exit_code']:raise RuntimeError('queue test failed: '+phase['log'])
        cases=[('one',5,1,0,0),('empty_shards',5,3,3,0),('tail',5,37,3,0),
               ('full_backpressure',5,128,5,0),('bad_dma_address',5,37,3,1),('overlap_rejected',5,37,3,2),('busy_snapshot',5,128,5,3)]
        if args.zvt:cases += [('zvt_signed',6,37,0,0),('zvt_backpressure',6,37,5,0)]
    def run(case):
        name,scenario,n,stalls,mode=case
        command=[str(binary),'--boot',str(boot),'--scenario',str(scenario),'--elements',str(n),
                 '--stall-period',str(stalls),'--mode',str(mode),'--max-cycles','3000000']
        phase=bounded(command,args.timeout,output/(name+'.log'),UPSTREAM)
        text=Path(phase['log']).read_text()
        results=[json.loads(line) for line in text.splitlines() if line.startswith('{"result":')]
        passed=phase['exit_code']==0 and len(results)==1 and results[0].get('result')=='PASS' and results[0].get('scenario')==scenario
        return dict(name=name,passed=passed,simulation=results[0] if results else None,**phase)
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for result in pool.map(run,cases):
                report['cases'].append(result)
                print(('PASS ' if result['passed'] else 'FAIL ')+result['name'],flush=True)
        if not all(c['passed'] for c in report['cases']):raise RuntimeError('pipeline RTL regression failed')
        report['status']='PASS'
    except Exception as error:
        report.update(status='FAIL',error=str(error))
    report['finished']=timestamp()
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Report:',output/'report.json')
    return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
