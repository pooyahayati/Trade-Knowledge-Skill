"""Synthetic observer tests; mocked eligibility tests are explicitly isolated.

The real engine is used for confirmation/wait, receipt, input and frozen-state
checks. A labeled fake engine covers observer transitions without pretending
that the fake ready state validates a real Trex chart.
"""
import copy,importlib.util,json,os,subprocess,sys,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('analysis_watch_test',ROOT/'skills/trade-chart-scenarios/scripts/analysis_watch.py')
W=importlib.util.module_from_spec(spec);spec.loader.exec_module(W)
def fixtures():
    start=datetime(2020,1,1,tzinfo=timezone.utc);bars=[]
    values=[('99','100','98','99')]*39+[('99','102.1','99','102'),('102','104','101','103'),('103','105','102','104'),('104','106','103','105'),('105','112','104','111')]
    for n,v in enumerate(values):
        t=start+timedelta(minutes=n)
        bars.append(dict(id=str(n),open_time=W.J.stamp(t),close_time=W.J.stamp(t+timedelta(minutes=1)),available_at=W.J.stamp(t+timedelta(minutes=1)),is_closed=True,**dict(zip(['open','high','low','close'],v))))
    s=dict(symbol='SYNTHETIC',market='forex',feed_id='synthetic_unit_only',price_basis='bid',timeframe='M1',chart_type='standard_ohlc',
        tick_size='0.1',atr_period=2,session_definition='synthetic contiguous test',source_timezone='UTC',availability_basis='simulated_bar_close',price_grid_basis='provided_unverified',bars=bars[:40])
    levels=[dict(id=lid,kind='pivot_zone',side='resistance',timeframe='M1',lower=lo,upper=hi,origin_at=bars[10]['open_time'],
        available_at=bars[20]['close_time'],source_refs=['synthetic_supplied_unverified'],construction_method='wick_to_body') for lid,lo,hi in [('anchor','99','100'),('target','110','111')]]
    p=dict(schema_version=1,as_of=bars[39]['close_time'],series=s,levels=levels,pivot_review=dict(profile='source_linked_pivots_v1'),
        assessment=dict(model='strict_three_bar_confirmation_v1',candidate_bar_id='39',direction='up',level_id='anchor',target_level_id='target',context_source_ref='synthetic',context_available_at=bars[38]['close_time']),
        level_analysis=dict(profile='levels_targets_v1',target_request=dict(direction='up',anchor_level_id='anchor',scenario_start_bar_id='39',selection_bar_id='39')))
    update=copy.deepcopy(s);update['bars']=bars[40:]
    return p,dict(series=[update])
def fake_engine(payload):
    """Only for testing observer control flow, not real engine validation."""
    at=W.J.instant(payload['as_of']);usable=[]
    for b in payload['series']['bars']:
        if W.J.instant(b['available_at'])>at:break
        usable.append(b)
    n=int(usable[-1]['id']);ready=n>=41
    a=dict(status='confirmed_under_selected_profile' if ready else 'pending_confirmation',scenario_status='pending_confirmation',
        confirmed_at='2020-01-01T00:42:00Z' if ready else None,available_at=payload['as_of'],invalidated_at=None,
        invalidation_available_at=None,target_touch=None,target_touched=False,level_and_target_gate='consistent_with_supplied_context',
        historical_context_gate=dict(prospective_eligible=True))
    return dict(status=a['status'],issues=[],assessment=a,structural_context=dict(scenario_ready=ready,status='aligned',direction='up'),
        target_hierarchy=dict(structural_target_contract_consistent=True,direct_path_clear=True,status='conditional_structural_target'),conditional_scenarios=[],source_refs=['synthetic_mock_only'])
class WatchTests(unittest.TestCase):
    def test_frozen_decision_reloads_across_process_hash_seeds(self):
        p,_=fixtures()
        for i in range(12):
            old=copy.deepcopy(p['levels'][0]);old.update(id=f'broken-{i}',lower=str(80+i),upper=str(80+i))
            old['kind']='classical_line';p['levels'].append(old)
        script='''import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('persisted_watch',sys.argv[1])
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
if sys.argv[4]=='freeze':
    payload=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    d=w.freeze(payload,4)
    assert len(d['initial_analysis']['level_analysis']['target_selection']['exclusions'])>=12
    Path(sys.argv[3]).write_text(json.dumps(d),encoding='utf-8')
else:
    w.validate_decision(json.loads(Path(sys.argv[3]).read_text(encoding='utf-8')))
'''
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            input_path=Path(folder)/'input.json';decision_path=Path(folder)/'decision.json'
            input_path.write_text(json.dumps(p),encoding='utf-8')
            for mode,seed in [('freeze','1'),('validate','2'),('validate','3')]:
                result=subprocess.run([sys.executable,'-X','utf8','-B','-c',script,str(ROOT/'skills/trade-chart-scenarios/scripts/analysis_watch.py'),str(input_path),str(decision_path),mode],
                    capture_output=True,text=True,encoding='utf-8',env={**os.environ,'PYTHONHASHSEED':seed},timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_real_engine_confirmation_without_context_remains_wait(self):
        p,u=fixtures();d=W.freeze(p,4);r=W.evaluate(d,u)
        self.assertIsNone(r['activation']);self.assertFalse(r['entry_permission']);self.assertIsNone(r['pnl'])
        self.assertTrue(any(s['assessment_status']=='confirmed_under_selected_profile' for s in r['snapshots']))
        self.assertIn('structure',r['snapshots'][1]['unmet_gates'])
    def test_prefix_is_unaffected_by_later_prices_or_missing_tail(self):
        p,u=fixtures()
        with patch.object(W.A,'analyze',side_effect=fake_engine):
            d=W.freeze(p,4);full=W.evaluate(d,u)
            reduced=copy.deepcopy(u);reduced['series'][0]['bars']=reduced['series'][0]['bars'][:2]
            prefix=W.evaluate(d,reduced)
            self.assertEqual(prefix['snapshots'],full['snapshots'][:2])
            self.assertEqual(prefix['activation'],full['activation'])
            self.assertEqual(prefix['state'],'incomplete_future_window')
            changed=copy.deepcopy(u);changed['series'][0]['bars'][-1].update(high='900',close='500')
            self.assertEqual(W.evaluate(d,changed)['snapshots'][:2],full['snapshots'][:2])
    def test_each_missing_gate_prevents_mock_readiness(self):
        p,u=fixtures()
        for gate in ['source','history','level','structure','target','path']:
            def fake(x):
                r=fake_engine(x)
                if gate=='history':r['assessment']['historical_context_gate']['prospective_eligible']=False
                if gate=='level':r['assessment']['level_and_target_gate']='needs_review'
                if gate=='structure':r['structural_context']['scenario_ready']=False
                if gate=='target':r['target_hierarchy']['structural_target_contract_consistent']=False
                if gate=='path':r['target_hierarchy']['direct_path_clear']=False
                return r
            q=copy.deepcopy(p)
            if gate=='source':q.pop('pivot_review')
            with self.subTest(gate=gate),patch.object(W.A,'analyze',side_effect=fake):self.assertIsNone(W.evaluate(W.freeze(q,4),u)['activation'])
    def test_delayed_group_never_backdates_activation(self):
        p,u=fixtures()
        for b in u['series'][0]['bars']:b['available_at']='2020-01-01T00:43:00Z' if int(b['id'])<43 else '2020-01-01T00:44:00Z'
        with patch.object(W.A,'analyze',side_effect=fake_engine):
            r=W.evaluate(W.freeze(p,4),u)
            self.assertEqual(len(r['snapshots']),2)
            self.assertEqual(r['activation']['confirmation_occurred_at'],'2020-01-01T00:42:00Z')
            self.assertEqual(r['activation']['usable_at'],'2020-01-01T00:43:00Z')
    def test_after_horizon_information_is_not_an_activation(self):
        p,u=fixtures()
        for b in u['series'][0]['bars']:b['available_at']='2020-01-01T00:50:00Z'
        with patch.object(W.A,'analyze',side_effect=fake_engine):self.assertIsNone(W.evaluate(W.freeze(p,4),u)['activation'])
    def test_frozen_prices_context_and_identity_cannot_change(self):
        p,u=fixtures();d=W.freeze(p,4)
        for field,value in [('feed_id','other'),('price_basis','ask'),('atr_period',3),('session_definition','other')]:
            bad=copy.deepcopy(u);bad['series'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):W.evaluate(d,bad)
        bad=copy.deepcopy(d);bad['input']['levels'][0]['upper']='100.5'
        with self.assertRaises(ValueError):W.evaluate(bad,u)
        bad=copy.deepcopy(u);bad['series'][0]['bars'][0]=copy.deepcopy(p['series']['bars'][-1])
        with self.assertRaises(ValueError):W.evaluate(d,bad)
    def test_future_initial_data_and_open_candles_rejected(self):
        p,u=fixtures()
        for kind in ['bar','level','context','open']:
            q=copy.deepcopy(p)
            if kind=='bar':q['series']['bars'].append(u['series'][0]['bars'][0])
            elif kind=='level':q['levels'][0]['available_at']='2020-01-01T00:50:00Z'
            elif kind=='context':q['assessment']['context_available_at']='2020-01-01T00:50:00Z'
            else:q['series']['bars'][-1]['is_closed']=False
            with self.subTest(kind=kind),self.assertRaises(ValueError):W.freeze(q,4)
    def test_malformed_future_duration_and_off_grid_price_rejected(self):
        p,u=fixtures();d=W.freeze(p,4)
        for key,value in [('close_time','2020-01-01T00:40:30Z'),('high','104.01')]:
            bad=copy.deepcopy(u);bad['series'][0]['bars'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):W.evaluate(d,bad)
    def test_target_after_context_termination_does_not_revive_scenario(self):
        p,u=fixtures()
        def fake(x):
            r=fake_engine(x);n=int(x['series']['bars'][-1]['id'])
            if n>=42:r['structural_context']['scenario_ready']=False
            if n>=43:
                r['assessment']['target_touched']=True
                r['assessment']['target_touch']=dict(bar_id='43',bar_open_at='2020-01-01T00:43:00Z',bar_close_at='2020-01-01T00:44:00Z',available_at=x['as_of'],price='110')
            return r
        with patch.object(W.A,'analyze',side_effect=fake):
            r=W.evaluate(W.freeze(p,4),u)
            self.assertEqual(r['state'],'context_no_longer_ready')
            self.assertEqual(r['events'][-1]['kind'],'post_termination_price_observation')
            self.assertFalse(r['events'][-1]['active_before_observation'])
    def test_grouped_target_before_later_close_invalidation_keeps_order(self):
        p,u=fixtures();u['series'][0]['bars'][2]['available_at']='2020-01-01T00:44:00Z'
        def fake(x):
            r=fake_engine(x)
            if int(x['series']['bars'][-1]['id'])>=43:
                r['assessment'].update(status='invalidated',scenario_status='invalidated',invalidated_at='2020-01-01T00:44:00Z',invalidation_available_at=x['as_of'],target_touched=True,
                    target_touch=dict(bar_id='42',bar_open_at='2020-01-01T00:42:00Z',bar_close_at='2020-01-01T00:43:00Z',available_at=x['as_of'],price='110'))
                r['structural_context']['scenario_ready']=False
            return r
        with patch.object(W.A,'analyze',side_effect=fake):
            r=W.evaluate(W.freeze(p,4),u)
            self.assertEqual(r['state'],'target_observed_after_activation')
            self.assertEqual(r['events'][1]['kind'],'target_touch')
            self.assertEqual(r['events'][2]['observation_kind'],'invalidation')
    def test_actual_receipt_mode_rejects_historical_availability(self):
        p,u=fixtures()
        with self.assertRaises(ValueError):W.freeze(p,4,'prospective_observation',W.J.instant(p['as_of']))
    def test_late_publication_cannot_verify_or_overwrite(self):
        p,u=fixtures();p['series']['availability_basis']='observed_receipt'
        t=W.J.instant(p['as_of'])
        with tempfile.TemporaryDirectory(dir=ROOT/'tests') as folder:
            with patch.object(W.J,'now',side_effect=[t,t,t+timedelta(minutes=1)]),self.assertRaises(ValueError):W.record(folder,dict(analysis_input=p,horizon_bars=4))
            self.assertTrue((Path(folder)/'decision.json').exists())
            with self.assertRaises(ValueError):W.verify(folder)
            with self.assertRaises(FileExistsError):W.record(folder,dict(analysis_input=p,horizon_bars=4),t)
    def test_real_engine_delayed_middle_bar_never_compresses_confirmation(self):
        p,u=fixtures();u['series'][0]['bars'][0]['available_at']='2020-01-01T00:43:00Z'
        r=W.evaluate(W.freeze(p,4),u)
        early=next(s for s in r['snapshots'] if s['knowledge_at']=='2020-01-01T00:42:00Z')
        self.assertNotEqual(early['assessment_status'],'confirmed_under_selected_profile')
        self.assertIsNone(r['activation'])
    def test_already_ready_is_not_new_prospective_activation(self):
        p,u=fixtures();p['series']['bars']+=u['series'][0]['bars'][:2];p['as_of']='2020-01-01T00:42:00Z';u['series'][0]['bars']=u['series'][0]['bars'][2:]
        with patch.object(W.A,'analyze',side_effect=fake_engine):
            d=W.freeze(p,2);r=W.evaluate(d,u)
            self.assertIn('already_ready_at_record_not_a_new_activation',d['excludes_new_activation'])
            self.assertIsNone(r['activation'])
if __name__=='__main__':unittest.main()
