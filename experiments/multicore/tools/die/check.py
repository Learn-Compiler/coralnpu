#!/usr/bin/env python3
"""Bounded regression of the actual CPU-controlled single-die RTL."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ncoral_paths import ROOT
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'multicore'))
from run import UPSTREAM,bounded,timestamp
from build_firmware import validate_rvv_firmware
from operators import validate_profile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs',type=int,choices=range(1,5),default=1,
                        help='independent simulator processes; each keeps its own deadline/log')
    args=parser.parse_args()
    binary=UPSTREAM/'bazel-bin/experiments/multicore/smoke_die'
    boot=ROOT/'die/logs/firmware/controller.bin'
    if not binary.is_file() or not boot.is_file():
        raise SystemExit('Build first: python3 experiments/multicore/tools/die/demo_complex.py')
    firmware=validate_rvv_firmware()
    output=ROOT/'die/logs/regression'
    output.mkdir(parents=True,exist_ok=True)
    cases=[
        ('one_element',['--elements','1','--stall-period','0'],None),
        ('empty_shards',['--elements','3'],None),
        ('one_per_core',['--elements','4','--stall-period','0'],None),
        ('unequal_shards',['--elements','37'],None),
        ('backpressure',['--elements','128','--stall-period','5'],None),
        ('two_operators',['--elements','37','--rounds','2'],None),
        ('two_operators_backpressure',['--elements','128','--rounds','2','--stall-period','5'],None),
        ('decode_error_recovery',['--mode','1'],None),
        ('bad_configuration',['--elements','0'],'controller reported failure code=0xbad00001'),
        ('coral_fault',['--mode','2'],'controller reported failure code=0xbad00002 coral_fault=0x0 trap_mask=0x1'),
        ('runtime_wait_timeout',['--mode','3','--wait-cycles','2000'],'controller reported failure code=0xbad00004'),
        ('cycle_deadline',['--max-cycles','40'],'simulated cycle deadline exceeded'),
        ('memory_not_ready',['--stall-period','1','--max-cycles','100000'],'simulated cycle deadline exceeded'),
        ('complex_serial_empty_shard',['--scenario','1','--rows','1','--stall-period','0'],None),
        ('complex_parallel_empty_shard',['--scenario','2','--rows','1','--stall-period','0'],None),
        ('complex_serial_unequal_rows',['--scenario','1','--rows','3'],None),
        ('complex_parallel_unequal_rows',['--scenario','2','--rows','3'],None),
        ('complex_serial_full_backpressure',['--scenario','1','--rows','8','--stall-period','5'],None),
        ('complex_parallel_full_backpressure',['--scenario','2','--rows','8','--stall-period','5'],None),
        ('gemm_four_core_empty_shards',['--scenario','3','--rows','1','--stall-period','0'],None),
        ('gemm_four_core_unequal_rows',['--scenario','3','--rows','7'],None),
        ('conv_four_core_empty_shards',['--scenario','4','--rows','1','--stall-period','0'],None),
        ('conv_four_core_unequal_rows',['--scenario','4','--rows','7'],None),
    ]
    report={'status':'STARTED','started':timestamp(),'cases':[],
            'jobs':args.jobs,
            'compute_backend':firmware['compute_backend'],
            'simulator_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'controller_binary_sha256':hashlib.sha256(boot.read_bytes()).hexdigest()}
    def run_case(case):
        name,options,expected_error=case
        profile_path=output/(name+'.profile.json')
        profile_path.unlink(missing_ok=True)
        phase=bounded([str(binary),'--boot',str(boot),*options,'--profile-json',str(profile_path)],
                      420 if '--scenario' in options else 120,output/(name+'.log'),UPSTREAM)
        text=Path(phase['log']).read_text()
        results=[json.loads(line) for line in text.splitlines() if line.startswith('{"result":')]
        if expected_error:
            passed=phase['exit_code']==1 and expected_error in text and not results
        else:
            passed=(phase['exit_code']==0 and len(results)==1 and results[0].get('result')=='PASS'
                    and results[0].get('control_cpu')=='PicoRV32 RV32IM RTL'
                    and results[0].get('coral_cores')==4
                    and results[0].get('compute_backend')=='RVV'
                    and results[0].get('cpu_runs_runtime') is True
                    and results[0].get('testbench_dispatch') is False
                    and results[0].get('rtl_shared_sram') is True)
            if results:
                phase['simulation']=results[0]
            if passed:
                try:
                    profile=json.loads(profile_path.read_text())
                    validate_profile(profile)
                    phase['retired_instructions']=sum(core['retired_instructions'] for core in profile['cores'])
                    phase['profile']=str(profile_path)
                except (OSError,ValueError,KeyError,RuntimeError) as error:
                    passed=False
                    phase['profile_error']=str(error)
        return dict(name=name,passed=passed,expected_error=expected_error,**phase)
    try:
        # Processes share only immutable binaries. Logs and DUT reset/state are private.
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for result in pool.map(run_case,cases):
                report['cases'].append(result)
                print(('PASS' if result['passed'] else 'FAIL')+' '+result['name'],flush=True)
        failed=[case['name'] for case in report['cases'] if not case['passed']]
        if failed:
            raise RuntimeError('regression failed: '+', '.join(failed))
        by_name={case['name']:case for case in report['cases']}
        # More work must retire more instructions: catches constant/stale counters.
        for small,large in [('one_element','backpressure'),
                            ('gemm_four_core_empty_shards','gemm_four_core_unequal_rows'),
                            ('conv_four_core_empty_shards','conv_four_core_unequal_rows')]:
            if by_name[small]['retired_instructions']>=by_name[large]['retired_instructions']:
                raise RuntimeError('retired instruction count did not increase with work: '+large)
        report['instruction_scaling_checks']='PASS'
        report['status']='PASS'
    except Exception as error:
        report.update(status='FAIL',error=str(error))
        print(str(error),file=sys.stderr)
    finally:
        report['finished']=timestamp()
        path=output/'report.json'
        path.write_text(json.dumps(report,indent=2)+'\n')
        print(report['status'],len(report['cases']),'cases;',path,flush=True)
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
