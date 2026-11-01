import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('journal',ROOT/'skills/trade-chart-scenarios/scripts/observation_journal.py')
J=importlib.util.module_from_spec(spec);spec.loader.exec_module(J)
NOW=datetime(2026,10,26,12,0,20,tzinfo=timezone.utc)
def example():
    return dict(observation=dict(symbol='TEST',feed_id='synthetic_test_only',timeframe='M1',source_sha256='a'*64,
        received_at='2026-10-26T12:00:10Z',last_closed_at='2026-10-26T12:00:00Z',close='100'),
        lower='99',upper='101',target_open='2026-10-26T12:01:00Z',target_close='2026-10-26T12:02:00Z')
class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='journal-test-',dir=ROOT/'tests')
        self.path=Path(self.temp.name).resolve();assert self.path.parent==(ROOT/'tests').resolve()
    def tearDown(self):self.temp.cleanup()
    def future(self,close='102'):
        return dict(decision_sha256=J.sha(self.path/'decision.json'),observation=dict(symbol='TEST',feed_id='synthetic_test_only',timeframe='M1',
            source_sha256='b'*64,received_at='2026-10-26T12:02:05Z',last_closed_at='2026-10-26T12:02:00Z',
            open_time='2026-10-26T12:01:00Z',close=close))
    def test_append_and_equality_boundary_without_trade(self):
        decision=J.record(self.path,example(),NOW);J.settle(self.path,self.future('101'),NOW+timedelta(minutes=2))
        self.assertTrue(J.verify(self.path)['resolved']);out=J.load(self.path/'outcome.json')
        self.assertEqual(out['outcome'],'within');self.assertIsNone(out['pnl']);self.assertFalse(out['entry_permission'])
        self.assertEqual(J.sha(self.path/'decision.json'),decision['sha256'])
    def test_cannot_replace_original_decision_or_outcome(self):
        J.record(self.path,example(),NOW);original=(self.path/'decision.json').read_bytes()
        with self.assertRaises(FileExistsError):J.record(self.path,example(),NOW)
        self.assertEqual((self.path/'decision.json').read_bytes(),original)
        J.settle(self.path,self.future(),NOW+timedelta(minutes=2))
        with self.assertRaises(FileExistsError):J.settle(self.path,self.future(),NOW+timedelta(minutes=2))
    def test_past_or_current_target_cannot_be_prospective(self):
        x=example();x['target_open']='2026-10-26T12:00:00Z';x['target_close']='2026-10-26T12:01:00Z'
        with self.assertRaises(ValueError):J.record(self.path,x,NOW)
    def test_stale_quotes_and_future_receipt_rejected(self):
        for stamp in ['2026-10-26T11:57:00Z','2026-10-26T12:00:30Z']:
            x=example();x['observation']['received_at']=stamp
            with self.assertRaises(ValueError):J.record(self.path,x,NOW)
    def test_cannot_resolve_before_future_bar_closes(self):
        J.record(self.path,example(),NOW)
        with self.assertRaises(ValueError):J.settle(self.path,self.future(),NOW)
    def test_mutated_decision_detected(self):
        J.record(self.path,example(),NOW);x=self.future();d=J.load(self.path/'decision.json');d['upper']='103'
        (self.path/'decision.json').write_text(json.dumps(d))
        with self.assertRaises(ValueError):J.settle(self.path,x,NOW+timedelta(minutes=2))
    def test_wrong_symbol_feed_or_minute_rejected(self):
        J.record(self.path,example(),NOW)
        for key,value in [('symbol','OTHER'),('feed_id','OTHER'),('open_time','2026-10-26T12:00:00Z')]:
            x=self.future();x['observation'][key]=value
            with self.assertRaises(ValueError):J.settle(self.path,x,NOW+timedelta(minutes=2))
    def test_serialization_failure_does_not_publish_partial_file(self):
        with self.assertRaises(ValueError):J.write_new(self.path/'failure.json',dict(x=float('nan')))
        self.assertFalse((self.path/'failure.json').exists())
        x=example();x['observation']['extra']=float('nan')
        with self.assertRaises(ValueError):J.record(self.path,x,NOW)
        J.record(self.path,example(),NOW)
    def test_equivalent_utc_forms_are_accepted(self):
        J.record(self.path,example(),NOW);x=self.future()
        for key in ['open_time','last_closed_at','received_at']:x['observation'][key]=x['observation'][key].replace('Z','+00:00')
        J.settle(self.path,x,NOW+timedelta(minutes=2));self.assertTrue(J.verify(self.path)['resolved'])
    def test_verify_recomputes_outcome_and_rejects_all_inconsistent_fields(self):
        J.record(self.path,example(),NOW);J.settle(self.path,self.future(),NOW+timedelta(minutes=2));path=self.path/'outcome.json'
        original=J.load(path)
        cases=[('outcome','below'),('order','BUY'),('fill','102'),('profitability_claim',True),('pnl','1')]
        for key,value in cases:
            bad=copy.deepcopy(original);bad[key]=value;path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):J.verify(self.path)
        for key,value in [('symbol','WRONG'),('feed_id','WRONG'),('source_sha256','bad'),('open_time','2026-10-26T12:00:00Z')]:
            bad=copy.deepcopy(original);bad['observation'][key]=value;path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):J.verify(self.path)
if __name__=='__main__':unittest.main()
