"""Reject misleading performance summaries and unsafe report data."""
import copy
from argparse import ArgumentTypeError
import json
from pathlib import Path
import tempfile
import unittest

from operators import positive_clock, render, validate_profile


def fixture():
    # Synthetic validation fixture only; hardware results come from RTL tests.
    return {'schema_version':1, 'result':'PASS', 'scenario':'gemm',
            'counter_source':'Coral hardware mcycle/minstret',
            'cores':[{'operator':'gemm','invocation':0,'core':0,'work_items':1,
                      'start_cycle':100,'end_cycle':300,'sample_cycles':150,'retired_instructions':80}],
            'operators':[{'operator':'gemm','invocation':0,'start_cycle':100,'end_cycle':300,
                          'elapsed_cycles':200,'retired_instructions':80,'cores':1}]}


class ReportValidation(unittest.TestCase):
    def test_rejects_failed_or_dispatch_based_measurements(self):
        for key,value in [('result','FAIL'),('counter_source','dispatch counter')]:
            data=fixture();data[key]=value
            with self.assertRaises(RuntimeError):validate_profile(data)

    def test_rejects_double_counting_and_mismatched_summaries(self):
        data=fixture();data['cores'].append(copy.deepcopy(data['cores'][0]))
        with self.assertRaises(RuntimeError):validate_profile(data)
        data=fixture();data['operators'][0]['retired_instructions']+=1
        with self.assertRaises(RuntimeError):validate_profile(data)

    def test_rejects_negative_or_impossible_counter_ranges(self):
        for key,value in [('end_cycle',99),('sample_cycles',201),('retired_instructions',-1)]:
            data=fixture();data['cores'][0][key]=value
            with self.assertRaises(RuntimeError):validate_profile(data)

    def test_embedded_json_cannot_close_script_element(self):
        data=fixture();data['command']=['</script><img src=x onerror=alert(1)>']
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'report.html';render(data,output)
            html=output.read_text()
            self.assertNotIn(data['command'][0],html)
            payload=html.split('<script id="profile-data" type="application/json">')[1].split('</script>')[0]
            self.assertEqual(json.loads(payload)['command'],data['command'])

    def test_rejects_non_finite_clock_values(self):
        for value in ('nan','inf','0','-2'):
            with self.assertRaises(ArgumentTypeError):positive_clock(value)
        self.assertEqual(positive_clock('500'),500)


if __name__=='__main__':unittest.main()
