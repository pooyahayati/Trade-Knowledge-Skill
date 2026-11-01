"""Synthetic causal counterexamples, not market-performance evidence."""
import copy,importlib.util,tempfile,unittest
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('scenario_journal_test',ROOT/'skills/trade-chart-scenarios/scripts/scenario_journal.py')
S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)
NOW=datetime(2026,10,31,18,0,20,tzinfo=timezone.utc)
def plan(side='long'):
    bars=[]
    for n in range(40):
        start=NOW.replace(second=0)-timedelta(minutes=40-n)
        bars.append(dict(id=str(n),open_time=S.J.stamp(start),close_time=S.J.stamp(start+timedelta(minutes=1)),
            available_at=S.J.stamp(NOW-timedelta(seconds=10)),is_closed=True,open='100',high='101',low='99',close='100'))
    series=dict(symbol='SYNTHETIC',market='crypto',feed_id='synthetic_unit_test_only',price_basis='last',timeframe='M1',
        chart_type='standard_ohlc',source_timezone='UTC',atr_period=14,tick_size='0.01',session_definition='synthetic unit test',bars=bars)
    obs=dict(symbol=series['symbol'],feed_id=series['feed_id'],timeframe='M1',source_sha256='a'*64,
        received_at=S.J.stamp(NOW-timedelta(seconds=10)),last_closed_at=bars[-1]['close_time'],open_time=bars[-1]['open_time'],close='100')
    return dict(observation=obs,analysis_input=dict(schema_version=1,as_of=S.J.stamp(NOW),series=series),
        side=side,activation='101' if side=='long' else '99',target='104' if side=='long' else '96',
        invalidation='98' if side=='long' else '102',target_open='2026-10-31T18:01:00Z',target_close='2026-10-31T18:04:00Z')
class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='scenario-',dir=ROOT/'tests');self.path=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def outcome(self,prices):
        bars=[]
        for n,(o,h,l,c) in enumerate(prices):
            start=NOW.replace(second=0)+timedelta(minutes=1+n)
            bars.append(dict(open_time=S.J.stamp(start),close_time=S.J.stamp(start+timedelta(minutes=1)),
                **dict(zip(['open','high','low','close'],map(str,[o,h,l,c])))))
        return dict(decision_sha256=S.J.sha(self.path/'decision.json'),symbol='SYNTHETIC',feed_id='synthetic_unit_test_only',
            timeframe='M1',price_basis='last',received_at='2026-10-31T18:04:10Z',source_sha256='b'*64,bars=bars)
    def settle(self,prices):
        S.settle(self.path,self.outcome(prices),NOW+timedelta(minutes=4));self.assertTrue(S.verify(self.path)['resolved'])
        return S.J.load(self.path/'outcome.json')
    def test_activation_then_target_has_no_fill_or_profit(self):
        S.record(self.path,plan(),NOW)
        out=self.settle([(100,103,99,102),(102,104,100,103),(103,105,101,104)])
        self.assertEqual(out['state'],'target_touched');self.assertEqual(out['activated_at'],'2026-10-31T18:02:00Z')
        self.assertIsNone(out['fill']);self.assertIsNone(out['pnl']);self.assertFalse(out['entry_permission'])
    def test_activation_wick_is_not_post_activation_target(self):
        S.record(self.path,plan(),NOW)
        out=self.settle([(100,105,99,102),(102,103,100,102),(102,103,100,102)])
        self.assertEqual(out['state'],'active_at_horizon')
        self.assertEqual(out['events'][1]['event'],'target_touch_before_activation_not_counted')
    def test_close_already_past_target_does_not_count_as_success(self):
        S.record(self.path,plan(),NOW)
        out=self.settle([(100,106,99,105),(105,106,103,104),(104,105,101,102)])
        self.assertEqual(out['state'],'target_already_passed_at_activation');self.assertFalse(out['events'][-1]['target_success'])
    def test_dual_touch_remains_ambiguous_in_both_directions(self):
        for side,prices in [('long',[(100,103,99,102),(102,105,97,100),(100,102,99,101)]),
                            ('short',[(100,101,97,98),(98,103,95,100),(100,101,99,100)])]:
            with self.subTest(side=side),tempfile.TemporaryDirectory(dir=self.path) as folder:
                original=self.path;self.path=Path(folder)
                try:
                    S.record(self.path,plan(side),NOW);self.assertEqual(self.settle(prices)['state'],'ambiguous_touch_order')
                finally:self.path=original
    def test_gap_open_proves_first_barrier_but_not_fill_price(self):
        S.record(self.path,plan(),NOW)
        out=self.settle([(100,103,99,102),(105,106,97,100),(100,102,99,101)])
        self.assertEqual(out['state'],'target_touched');self.assertEqual(out['events'][-1]['time_basis'],'opening_price')
    def test_pre_activation_cancellation_and_strict_activation_boundary(self):
        S.record(self.path,plan(),NOW)
        out=self.settle([(100,103,99,101),(101,102,97,98),(98,106,97,105)])
        self.assertEqual(out['state'],'cancelled_before_activation');self.assertIsNone(out['activated_at'])
    def test_no_activation_is_valid_expiry(self):
        S.record(self.path,plan(),NOW)
        self.assertEqual(self.settle([(100,102,99,101)]*3)['state'],'expired_without_activation')
    def test_decision_and_outcome_cannot_be_overwritten(self):
        first=S.record(self.path,plan(),NOW)
        with self.assertRaises(FileExistsError):S.record(self.path,plan(),NOW)
        self.assertEqual(S.J.sha(self.path/'decision.json'),first['sha256'])
        prices=[(100,102,99,101)]*3;self.settle(prices)
        with self.assertRaises(FileExistsError):self.settle(prices)
    def test_reject_incomplete_or_foreign_or_unclosed_future_data(self):
        S.record(self.path,plan(),NOW);good=self.outcome([(100,102,99,101)]*3)
        variants=[]
        for key,value in [('feed_id','another_feed'),('symbol','another_symbol'),('timeframe','M5'),('price_basis','bid'),('source_sha256','wrong'),('received_at','2026-10-31T18:03:10Z')]:
            b=copy.deepcopy(good);b[key]=value;variants.append(b)
        b=copy.deepcopy(good);b['bars'].pop();variants.append(b)
        b=copy.deepcopy(good);b['bars'][1]=b['bars'][0];variants.append(b)
        b=copy.deepcopy(good);b['bars'][0]['high']='99';variants.append(b)
        for b in variants:
            with self.subTest(value=b),self.assertRaises(ValueError):S.settle(self.path,b,NOW+timedelta(minutes=4))
        self.assertFalse((self.path/'outcome.json').exists())
    def test_reject_future_history_and_analysis_identity_mismatch(self):
        for kind in ['clock','feed','close','future','barrier']:
            b=plan()
            if kind=='clock':b['target_open']='2026-10-31T18:00:00Z'
            elif kind=='feed':b['analysis_input']['series']['feed_id']='other'
            elif kind=='close':b['analysis_input']['series']['bars'][-1]['close']='100.5'
            elif kind=='future':b['analysis_input']['series']['bars'][0]['available_at']='2026-10-31T18:01:00Z'
            else:b['target']='99'
            with self.subTest(kind=kind),self.assertRaises(ValueError):S.record(self.path,b,NOW)
        self.assertFalse((self.path/'decision.json').exists())
    def test_verifier_detects_changed_result_and_frozen_snapshot(self):
        S.record(self.path,plan(),NOW);self.settle([(100,102,99,101)]*3)
        out=self.path/'outcome.json';original=out.read_bytes();v=S.J.load(out);v['state']='target_touched';out.write_text(__import__('json').dumps(v))
        with self.assertRaises(ValueError):S.verify(self.path)
        out.write_bytes(original);decision=self.path/'decision.json';d=S.J.load(decision);d['analysis']['status']='made_up';decision.write_text(__import__('json').dumps(d))
        with self.assertRaises(ValueError):S.verify(self.path)
    def test_slow_calculation_and_late_publication_cannot_verify(self):
        future=NOW+timedelta(minutes=1)
        with patch.object(S.J,'now',side_effect=[NOW,future]),self.assertRaises(ValueError):S.record(self.path,plan())
        self.assertFalse((self.path/'decision.json').exists())
        with patch.object(S.J,'now',side_effect=[NOW,NOW,future]),self.assertRaises(ValueError):S.record(self.path,plan())
        self.assertTrue((self.path/'decision.json').exists())
        with self.assertRaises(ValueError):S.verify(self.path)
    def test_occurrence_and_receipt_are_separate(self):
        S.record(self.path,plan(),NOW)
        out=self.settle([(100,103,99,102),(102,104,100,103),(103,105,101,104)])
        self.assertEqual(out['activated_at'],'2026-10-31T18:02:00Z')
        self.assertEqual(out['activation_available_at'],'2026-10-31T18:04:10Z')
        for e in out['events']:
            self.assertNotIn('known_at',e)
            self.assertGreaterEqual(S.J.instant(e['available_at']),S.J.instant(e['bar_close']))
            self.assertGreaterEqual(S.J.instant(e['evaluated_at']),S.J.instant(e['available_at']))
if __name__=='__main__':unittest.main()
