"""Adversarial export checks; native platforms are never simulated as executed."""
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'skills/trade-chart-scenarios/scripts/conformance.py'
spec=importlib.util.spec_from_file_location('conformance',SCRIPT)
C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)
FIX=ROOT/'skills/trade-chart-scenarios/assets/conformance'


def fixture(name='BTCUSDT-H1'):
    return json.loads((FIX/(name+'.json')).read_text(encoding='utf-8'))


class ConformanceTests(unittest.TestCase):
    def pair(self,name='BTCUSDT-H1'):
        f=fixture(name);return f,C.reference_export(f)
    def test_all_golden_vectors_and_file_hashes(self):
        index=json.loads((FIX/'index.json').read_text(encoding='utf-8'))
        for item in index['fixtures']:
            with self.subTest(fixture=item['id']):
                path=FIX/item['file'];self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),item['sha256'])
                f,e=self.pair(item['id']);r=C.compare(f,e)
                self.assertEqual(r['status'],'pass',r)
                self.assertEqual(r['compared_rows'],item['rows'])
                self.assertFalse(r['native_runtime_execution_verified'])

    def test_hand_calculated_gap_zero_seed_and_previous_baseline(self):
        r=fixture('zero-seed-gap-flat')['expected_rows']
        self.assertIsNone(r[0]['true_range']);self.assertEqual(r[2]['atr_after_bar'],'0')
        self.assertEqual(r[3]['true_range'],'32');self.assertEqual(r[3]['baseline_atr'],'0')
        self.assertEqual(r[3]['atr_after_bar'],'16');self.assertEqual(r[3]['length_class'],'not_computable')
        self.assertEqual(r[4]['baseline_atr'],'16');self.assertEqual(r[4]['atr_after_bar'],'8')
        self.assertEqual(r[4]['length_class'],'flat_bar');self.assertFalse(r[4]['pinbar_candidate'])

    def test_hand_labeled_pin_limits_and_orientation(self):
        for name,expected in [('pin-upper',True),('pin-lower',True),('pin-opposite-too-large',False),('doji',False)]:
            self.assertIs(fixture(name)['expected_rows'][-1]['pinbar_candidate'],expected,name)

    def test_snap_boundaries_are_decisions_not_float_tolerance(self):
        for boundary,exact,below,above in [('8','standard','short','standard'),('12','standard','standard','long_range'),
                                         ('25','long_range','long_range','very_long_range')]:
            for suffix,want in [('exact',exact),('near-below',exact),('near-above',exact),('outside-below',below),('outside-above',above)]:
                self.assertEqual(fixture('range-'+boundary+'-'+suffix)['expected_rows'][-1]['length_class'],want)

    def test_history_metadata_and_ohlc_must_match_not_just_claimed_hash(self):
        for field,value in [('feed_id','other'),('price_basis','ask'),('session_definition','other'),('source_timezone','UTC+01:00'),
                            ('atr_period',25),('tick_size','0.0001'),('price_grid_basis','instrument_tick')]:
            f,e=self.pair();e['input']['series'][field]=value
            r=C.compare(f,e);self.assertEqual(r['status'],'mismatch');self.assertEqual(r['compared_rows'],0)
        f,e=self.pair();e['input']['series']['bars'][10]['close']='1.3'
        self.assertEqual(C.compare(f,e)['issues'][0]['reason'],'different_input_history_or_metadata')

    def test_cutoff_and_history_start_are_part_of_identity(self):
        f,e=self.pair();e['input']['series']['bars'].pop(0)
        self.assertEqual(C.compare(f,e)['status'],'mismatch')
        f,e=self.pair();e['input']['as_of']='2018-02-01T00:00:00Z'
        self.assertEqual(C.compare(f,e)['status'],'mismatch')

    def test_fixture_id_and_hash_cannot_be_reused(self):
        for field in ['fixture_id','fixture_sha256']:
            f,e=self.pair();e[field]='wrong'
            self.assertEqual(C.compare(f,e)['status'],'mismatch')

    def test_missing_extra_and_reversed_rows_rejected(self):
        for mode in ['missing','extra','reverse']:
            f,e=self.pair()
            if mode=='missing':e['rows'].pop()
            elif mode=='reverse':e['rows'].reverse()
            else:
                b=copy.deepcopy(e['rows'][-1]);b['id']='extra';e['rows'].append(b)
            self.assertEqual(C.compare(f,e)['status'],'mismatch')

    def test_duplicate_row_and_open_row_are_invalid(self):
        f,e=self.pair();e['rows'][1]['id']=e['rows'][0]['id']
        self.assertEqual(C.compare(f,e)['status'],'invalid_input')
        f,e=self.pair();e['rows'][-1]['is_closed']=False
        self.assertEqual(C.compare(f,e)['status'],'invalid_input')

    def test_baseline_shift_current_atr_and_wrong_seed_caught(self):
        for mode in ['baseline','seed','baseline_id']:
            f,e=self.pair();r=e['rows'][25]
            if mode=='baseline':r['baseline_atr']=r['atr_after_bar']
            elif mode=='seed':e['rows'][24]['atr_after_bar']='0.001'
            else:r['baseline_bar_id']=r['id']
            self.assertEqual(C.compare(f,e)['status'],'mismatch')

    def test_null_zero_and_false_are_not_interchangeable(self):
        for field,value in [('true_range','0'),('standard_dominant_candidate',False)]:
            f,e=self.pair();self.assertIsNone(e['rows'][0][field]);e['rows'][0][field]=value
            self.assertEqual(C.compare(f,e)['status'],'mismatch')

    def test_boolean_integer_substitution_rejected(self):
        f,e=self.pair();e['rows'][30]['pinbar_candidate']=0
        self.assertEqual(C.compare(f,e)['status'],'invalid_input')

    def test_unbounded_exponent_nonfinite_float_numbers_rejected(self):
        for value in ['NaN','Infinity','1e999999','1'*19,'0.'+'1'*81,0.1]:
            f,e=self.pair();e['rows'][30]['baseline_atr']=value
            self.assertEqual(C.compare(f,e)['status'],'invalid_input')

    def test_numerical_roundoff_allowed_but_decision_not_waived(self):
        f,e=self.pair();x=C.Decimal(e['rows'][30]['baseline_atr'])
        e['rows'][30]['baseline_atr']=format(x+C.Decimal('0.000000000001'),'f')
        self.assertEqual(C.compare(f,e)['status'],'pass')
        e['rows'][30]['pinbar_candidate']=not e['rows'][30]['pinbar_candidate']
        self.assertEqual(C.compare(f,e)['status'],'mismatch')

    def test_future_prices_do_not_enter_reference_prefix(self):
        f,e=self.pair('unavailable-middle')
        self.assertEqual(len(e['rows']),4)
        for b in f['input']['series']['bars'][4:]:b.update(open='1000',high='2000',low='900',close='1500')
        self.assertEqual(C.reference_export(f)['rows'],e['rows'])

    def test_delayed_receipt_clock_preserved(self):
        f,e=self.pair('delayed-receipt');r=e['rows'][0]
        self.assertNotEqual(r['available_at'],r['close_time']);r['available_at']=r['close_time']
        self.assertEqual(C.compare(f,e)['status'],'mismatch')

    def test_native_platform_name_does_not_prove_native_execution(self):
        for platform in ['pine_v6','mql4','mql5']:
            f,e=self.pair();e['implementation']=dict(platform=platform,version='metadata-only-test',run_kind='native_export')
            r=C.compare(f,e);self.assertEqual(r['status'],'pass');self.assertFalse(r['native_runtime_execution_verified'])

    def test_unknown_keys_and_run_kinds_rejected(self):
        f,e=self.pair();e['tolerance']='999'
        self.assertEqual(C.compare(f,e)['status'],'invalid_input')
        f,e=self.pair();e['implementation']['run_kind']='native_export'
        self.assertEqual(C.compare(f,e)['status'],'invalid_input')

    def test_structured_error_for_malformed_root_and_fixture(self):
        f,e=self.pair()
        self.assertEqual(C.compare(f,[])['status'],'invalid_input')
        f['schema_version']=True
        self.assertEqual(C.compare(f,e)['status'],'invalid_input')

    def test_duplicate_json_keys_and_nonfinite_literals_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='conformance-test-') as d:
            self.assertTrue(Path(d).resolve().is_relative_to(ROOT.resolve()))
            path=Path(d)/'bad.json'
            for body in ['{"x":1,"x":2}','{"x":NaN}','{"x":1e300}','{"x":1234567890123456789}']:
                path.write_text(body)
                with self.assertRaises(C.A.InputIssue):C.load_json(path)

    def test_oversized_files_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='conformance-test-') as d:
            self.assertTrue(Path(d).resolve().is_relative_to(ROOT.resolve()))
            path=Path(d)/'large.json'
            with path.open('wb') as f:f.truncate(C.SPEC['limits']['max_file_bytes']+1)
            with self.assertRaises(C.A.InputIssue):C.load_json(path)

    def test_cli_success_mismatch_and_invalid_exit_codes(self):
        f,e=self.pair('pin-upper')
        with tempfile.TemporaryDirectory(dir=ROOT,prefix='conformance-test-') as d:
            self.assertTrue(Path(d).resolve().is_relative_to(ROOT.resolve()))
            path=Path(d)/'export.json'
            for mode,code in [('good',0),('mismatch',1),('invalid',2)]:
                bad=copy.deepcopy(e)
                if mode=='mismatch':bad['rows'][-1]['pinbar_candidate']=False
                if mode=='invalid':bad['rows'][-1]['pinbar_candidate']=0
                path.write_text(json.dumps(bad),encoding='utf-8')
                p=subprocess.run([sys.executable,'-B',str(SCRIPT),'compare','--fixture',str(FIX/'pin-upper.json'),'--export',str(path)],capture_output=True,text=True)
                self.assertEqual(p.returncode,code,p.stderr);json.loads(p.stdout)


if __name__=='__main__':unittest.main()
