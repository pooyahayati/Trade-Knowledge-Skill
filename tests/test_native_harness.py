"""Generator boundaries and portable native-source delivery checks."""
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'skills/trade-chart-scenarios/scripts/native_harness.py'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
H=load('native_test_harness',SCRIPT);O=load('native_test_oracle',ROOT/'tests/conformance_oracle.py')
FIX=ROOT/'skills/trade-chart-scenarios/assets/conformance'
def fixture(name='range-12-exact'):return json.loads((FIX/(name+'.json')).read_text(encoding='utf-8'))

class NativeHarnessTests(unittest.TestCase):
    def test_all_frozen_fixtures_preserved_and_generatable(self):
        index=json.loads((FIX/'index.json').read_text(encoding='utf-8'))
        for item in index['fixtures']:
            self.assertEqual(hashlib.sha256((FIX/item['file']).read_bytes()).hexdigest(),item['sha256'])
            f=fixture(item['id']);before=copy.deepcopy(f)
            for target in ['pine_v6','mql4','mql5']:
                code=H.generate(f,target);self.assertIn(H.C.digest(f),code)
                self.assertNotIn('native_export',code)
            self.assertEqual(f,before)
    def test_pine_harness_uses_exact_product_core(self):
        self.assertIn(H.pine_core(),H.generate(fixture(),'pine_v6'))
    def test_mql_harness_is_self_contained_and_shared(self):
        f=fixture();core=(H.ROOT/'assets/native/common/TradeKnowledgeCore.mqh').read_text(encoding='utf-8')
        a=H.generate(f,'mql4');self.assertIn(core,a);self.assertEqual(a,H.generate(f,'mql5'))
        self.assertNotIn('#include',a)
    def test_open_bar_is_not_embedded(self):
        f=fixture('open-tail');_,prices,expected,_=H.prepare(f)
        self.assertLess(len(prices),len(f['input']['series']['bars']));self.assertEqual(len(prices),len(expected))
    def test_unavailable_middle_halts_entire_suffix(self):
        f=fixture('unavailable-middle');_,prices,_,_=H.prepare(f)
        self.assertEqual(len(prices),len(f['expected_rows']));self.assertLess(len(prices),len(f['input']['series']['bars'])-1)
    def test_empty_eligible_input_rejected(self):
        f=fixture();f['input']['as_of']=f['input']['series']['bars'][0]['open_time'];f['expected_rows']=[]
        with self.assertRaises(H.C.A.InputIssue):H.generate(f,'pine_v6')
    def test_oversized_native_harness_rejected(self):
        f=fixture();b=f['input']['series']['bars'][0];bars=[];start=datetime(2020,1,1,tzinfo=timezone.utc)
        for i in range(501):
            c=copy.deepcopy(b);c.update(id=str(i),open_time=O.stamp(start+timedelta(minutes=15*i)),
                 close_time=O.stamp(start+timedelta(minutes=15*(i+1))),available_at=O.stamp(start+timedelta(minutes=15*(i+1))))
            bars.append(c)
        f['input']['series']['bars']=bars;f['input']['as_of']=bars[-1]['close_time'];f['expected_rows']=O.oracle(f['input'])
        with self.assertRaises(H.C.A.InputIssue):H.generate(f,'mql4')
    def test_resolution_beyond_double_limit_rejected(self):
        f=fixture();f['input']['series']['tick_size']='0.000000000000000001'
        with self.assertRaises(H.C.A.InputIssue):H.generate(f,'mql5')
    def test_invalid_ohlc_rejected_before_code_generation(self):
        f=fixture();f['input']['series']['bars'][0]['close']='999'
        with self.assertRaises(H.C.A.InputIssue):H.generate(f,'pine_v6')
    def test_foreign_fixture_contract_rejected(self):
        f=fixture();f['contract_id']='foreign'
        with self.assertRaises(H.C.A.InputIssue):H.generate(f,'mql5')
    def test_wrong_expected_identity_rejected(self):
        f=fixture();f['expected_rows'][0]['id']='different'
        with self.assertRaises(H.C.A.InputIssue):H.generate(f,'pine_v6')
    def test_unknown_target_rejected(self):
        with self.assertRaises(ValueError):H.generate(fixture(),'other')
    def test_hostile_title_is_escaped(self):
        f=fixture();f['fixture_id']='x"\nalert("bad")'
        for target in ['pine_v6','mql5']:
            code=H.generate(f,target);self.assertNotIn('\nalert("bad")',code)
            self.assertIn(json.dumps('TK fixture '+f['fixture_id']),code)
    def test_cli_stdout_only_and_failure_code(self):
        r=subprocess.run([sys.executable,'-X','utf8','-B',str(SCRIPT),'--fixture',str(FIX/'range-12-exact.json'),'--target','pine_v6'],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(r.stdout.startswith('//@version=6'))
        r=subprocess.run([sys.executable,'-X','utf8','-B',str(SCRIPT),'--fixture',str(FIX/'missing.json'),'--target','mql5'],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(r.returncode,2);self.assertEqual(r.stdout,'')

if __name__=='__main__':unittest.main()
