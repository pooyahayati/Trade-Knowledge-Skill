import copy,importlib.util,json,os,subprocess,sys,tempfile,unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal,localcontext
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('market_research',ROOT/'skills/trade-chart-scenarios/scripts/research.py')
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
A,M=R.A,R.M

def fixture():
    start=datetime(2020,1,1,tzinfo=timezone.utc)
    bars=[]
    for i in range(38):
        prices=['100','101','99','100'] if i<28 else ['103','104','102','103']
        prices={28:['100','102','100','102'],29:['102','102.5','101.5','102'],30:['102','103','102','103']}.get(i,prices)
        bars.append(dict(id=str(i),open_time=A.stamp(start+timedelta(hours=i)),close_time=A.stamp(start+timedelta(hours=i+1)),
            available_at=A.stamp(start+timedelta(hours=i+1)),is_closed=True,**dict(zip(['open','high','low','close'],prices))))
    first,last=bars[0]['open_time'],bars[-1]['close_time']
    s=dict(symbol='AAAUSD',market='crypto',feed_id='test',price_basis='last',timeframe='H1',chart_type='standard_ohlc',tick_size='0.1',
        session_definition='UTC continuous',source_timezone='UTC',bars=bars)
    c=dict(evidence_status='hypothetical',source_ref='test assumptions',observed_at=last,valid_from=first,valid_until=last,
        spread_price='0.2',slippage_price='0.1',fee_bps_per_side='10',carry_model='none_spot',carry_events=[],carry_schedule_complete=True)
    p=dict(schema_version=1,profile_id=M.PROFILE_ID,symbol=s['symbol'],market=s['market'],venue='test',feed_id='test',price_basis='last',
        contract_type='crypto_spot',base_currency='AAA',quote_currency='USD',settlement_currency='USD',valid_from=first,valid_until=last,
        data_tick_size='0.1',order_tick_size='1',order_tick_evidence='test only',costs=c)
    return dict(schema_version=1,as_of=last,series=s,instrument=p,setup=dict(model=R.MODEL,lookback=12,target_r='2',max_holding_bars=2,directions=['long']))

def parsed():
    x=fixture();_,_,b,_=A.parse_series(x['series'],A.instant(x['as_of'],'as_of'))
    return x,b,M.validate(x['instrument'],x['series'],b[0]['open_time'],b[-1]['close_time'])

class MarketProfiles(unittest.TestCase):
    def test_frozen_breakout_produces_one_time_exit(self):
        r=R.run(fixture());self.assertEqual(r['status'],'research_only');self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['trades'][0]['outcomes'][0]['reason'],'time_exit');self.assertFalse(r['profitability_validated'])
    def test_identity_contract_and_missing_cost_rejected(self):
        for path,value in [('symbol','BBBUSD'),('contract_type','inverse'),('settlement_currency','AAA')]:
            with self.subTest(path=path):
                x=fixture();x['instrument'][path]=value;self.assertNotEqual(R.run(x)['status'],'research_only')
        x=fixture();del x['instrument']['costs']['spread_price'];self.assertEqual(R.run(x)['status'],'insufficient_data')
    def test_spot_short_is_unsupported(self):
        x=fixture();x['setup']['directions']=['long','short'];self.assertEqual(R.run(x)['status'],'unsupported')
    def test_different_order_grid_is_not_data_grid_error(self):
        self.assertEqual(R.run(fixture())['status'],'research_only')
        x=fixture();x['instrument']['data_tick_size']='1';self.assertEqual(R.run(x)['issues'][0]['code'],'data_grid_mismatch')
    def test_unknown_order_grid_is_retained_without_changing_research_fills(self):
        x=fixture();before=R.run(x);x['instrument']['order_tick_size']=None
        x['instrument']['order_tick_evidence']='historical executable grid unknown'
        after=R.run(x)
        self.assertEqual(after['status'],'research_only')
        self.assertEqual(before['trades'],after['trades'])
        self.assertIsNone(after['instrument']['order_tick_size'])
        self.assertIn('historical_order_grid_unknown_no_executable_order_validation',after['limits'])
        self.assertFalse(R.analyze_with_instrument(x)['order_tick_known'])
        del x['instrument']['order_tick_size']
        self.assertEqual(R.run(x)['issues'][0]['path'],'instrument.order_tick_size')
    def test_cost_coverage_and_claimed_verification(self):
        x=fixture();x['instrument']['costs']['valid_until']=x['series']['bars'][32]['close_time']
        self.assertEqual(R.run(x)['issues'][0]['code'],'cost_coverage_incomplete')
        x=fixture();x['instrument']['costs']['evidence_status']='verified';self.assertEqual(R.run(x)['status'],'invalid_input')
    def test_unknown_fields_nonfinite_and_bool_bounds(self):
        for field,value in [('lookback',True),('max_holding_bars',201),('target_r','NaN')]:
            with self.subTest(field=field):
                x=fixture();x['setup'][field]=value;self.assertEqual(R.run(x)['status'],'invalid_input')
        x=fixture();x['instrument']['leverage']=100;self.assertEqual(R.run(x)['status'],'invalid_input')
    def test_price_basis_translation(self):
        _,_,p=parsed();p.update(spread=Decimal('2'),slippage=Decimal('0.5'))
        for basis,buy,sell in [('bid','102.5','99.5'),('ask','100.5','97.5'),('mid','101.5','98.5'),('last','101.5','98.5')]:
            p['price_basis']=basis
            self.assertEqual(M.execution_price(Decimal(100),True,p),Decimal(buy));self.assertEqual(M.execution_price(Decimal(100),False,p),Decimal(sell))
    def test_exact_hand_calculated_fee_and_round_trip(self):
        _,b,p=parsed();p.update(spread=Decimal('2'),slippage=Decimal('0.5'),fee_bps=Decimal('10'))
        entry=M.execution_price(Decimal(100),True,p);exit=M.execution_price(Decimal(110),False,p)
        result=M.pnl(entry,exit,True,p,b[0]['open_time'],b[1]['open_time'],b[1]['close_time'],True)
        self.assertEqual(result['gross'],Decimal('7'));self.assertEqual(result['fees'],Decimal('.210'));self.assertEqual(result['net_lower'],Decimal('6.790'))
    def test_negative_fill_rejected(self):
        _,_,p=parsed();p['spread']=Decimal(1000)
        with self.assertRaises(A.InputIssue):M.execution_price(Decimal(1),False,p)
    def test_carry_order_duplicates_and_missing_schedule(self):
        x=fixture();x['instrument']['contract_type']='crypto_perpetual_linear'
        x['instrument']['costs']['carry_model']='explicit_signed_entry_notional_bps'
        self.assertEqual(R.run(x)['status'],'insufficient_data')
        event=dict(at=x['series']['bars'][1]['close_time'],long_bps='1',short_bps='-1')
        x['instrument']['costs']['carry_events']=[event,event]
        self.assertEqual(R.run(x)['issues'][0]['code'],'unordered_duplicate_or_uncovered_carry')
    def test_signed_carry_exit_time_bounds(self):
        _,b,p=parsed();p['fee_bps']=Decimal(0)
        p['carry']=[dict(at=b[1]['open_time']+timedelta(minutes=30),long_bps=Decimal(10),short_bps=Decimal(-10))]
        lo=M.pnl(Decimal(100),Decimal(100),True,p,b[0]['open_time'],b[1]['open_time'],b[1]['close_time'],False)
        sh=M.pnl(Decimal(100),Decimal(100),False,p,b[0]['open_time'],b[1]['open_time'],b[1]['close_time'],False)
        self.assertEqual((lo['net_lower'],lo['net_upper']),(Decimal('-.1'),Decimal(0)))
        self.assertEqual((sh['net_lower'],sh['net_upper']),(Decimal(0),Decimal('.1')))
    def test_both_barriers_interval_and_gap_priority(self):
        _,b,p=parsed();b[31].update(open=Decimal(103),high=Decimal(110),low=Decimal(98))
        t=R.simulate(b,31,Decimal(100),Decimal(109),True,2,p)
        self.assertTrue(t['ambiguous']);self.assertLess(Decimal(t['net_r_lower']),Decimal(t['net_r_upper']))
        b[31].update(open=Decimal(103),high=Decimal(104),low=Decimal(102));b[32].update(open=Decimal(98),high=Decimal(110),low=Decimal(97))
        t=R.simulate(b,31,Decimal(100),Decimal(109),True,2,p)
        self.assertEqual(t['outcomes'][0]['reason'],'gap_stop');self.assertEqual(t['outcomes'][0]['chart_price'],'98')
    def test_short_barriers_and_cost_symmetry(self):
        _,b,p=parsed();b[31].update(open=Decimal(103),high=Decimal(107),low=Decimal(97))
        t=R.simulate(b,31,Decimal(106),Decimal(97),False,2,p)
        self.assertTrue(t['ambiguous']);self.assertEqual(len(t['outcomes']),2)
    def test_late_confirmation_cannot_backfill_entry(self):
        x=fixture();x['series']['bars'][30]['available_at']=x['series']['bars'][31]['close_time']
        r=R.run(x);self.assertEqual(r['trades'],[]);self.assertEqual(r['rejected']['confirmation_unavailable_at_entry'],1)
    def test_late_prior_price_cannot_backdate_level(self):
        x=fixture();x['series']['bars'][27]['available_at']=x['series']['bars'][28]['close_time']
        r=R.run(x);self.assertEqual(r['trades'],[]);self.assertGreater(r['rejected']['late_level_or_atr'],0)
    def test_open_tail_and_future_mutation_preserve_closed_trade(self):
        x=fixture();base=R.run(x)['trades'];x['series']['bars'][-1]['is_closed']=False
        x['series']['bars'][-1]['high']='9999';self.assertEqual(R.run(x)['trades'],base)
    def test_asof_and_prefix_identical_decisions(self):
        x=fixture();x['as_of']=x['series']['bars'][33]['close_time'];a=R.run(x)
        x['series']['bars']=x['series']['bars'][:34];b=R.run(x)
        self.assertEqual(a['trades'],b['trades'])
    def test_censor_not_zero_pnl(self):
        x=fixture();x['setup']['max_holding_bars']=12;x['series']['bars']=x['series']['bars'][:32];x['as_of']=x['series']['bars'][-1]['close_time']
        r=R.run(x);self.assertEqual(r['trades'][0]['status'],'censored');self.assertIsNone(r['trades'][0]['net_r_lower']);self.assertIsNone(r['summary']['mean_r_lower'])
    def test_boundary_purge_includes_outcome_availability(self):
        x=fixture();t=R.run(x)['trades'];end=t[0]['outcome_available_at']
        fold=R.chronological(t,x['series']['bars'][0]['open_time'],end)
        self.assertEqual(fold['summary']['closed'],0);self.assertEqual(fold['purged_boundary'],1)
    def test_delayed_held_bar_defers_first_hit_outcome(self):
        x,b,p=parsed();b[32]['available_at']=b[36]['close_time']
        t=R.simulate(b,31,Decimal(100),Decimal(109),True,3,p)
        self.assertEqual(t['outcome_available_at'],A.stamp(b[36]['close_time']))
        t['signal_available_at']=x['series']['bars'][30]['close_time']
        self.assertEqual(R.chronological([t],x['series']['bars'][0]['open_time'],x['series']['bars'][35]['close_time'])['purged_boundary'],1)
    def test_carry_completeness_is_required_not_inferred(self):
        x=fixture();x['instrument']['costs']['carry_schedule_complete']=False
        self.assertEqual(R.run(x)['issues'][0]['code'],'explicit_complete_carry_schedule_required')
    def test_no_trade_statistics_are_unknown_not_winners(self):
        s=R.stats([]);self.assertIsNone(s['mean_r_lower']);self.assertIsNone(s['win_fraction_lower']);self.assertIsNone(s['profit_factor_lower'])
    def test_precision_scope_does_not_change_with_callers_context(self):
        x=fixture();expected=R.run(x)
        with localcontext() as c:
            c.prec=9;self.assertEqual(R.run(x),expected)
    def test_cli_rejects_exponent_in_excluded_tail_and_duplicate_keys(self):
        x=fixture();x['series']['bars'][-1]['is_closed']=False;x['series']['bars'][-1]['open']='REPLACE_EXPONENT'
        bad=json.dumps(x).replace('"REPLACE_EXPONENT"','1e999')
        with tempfile.TemporaryDirectory(dir=ROOT/'tests/market_adaptation') as tmp:
            self.assertTrue(Path(tmp).resolve().is_relative_to(ROOT.resolve()))
            p=Path(tmp)/'input.json'
            for content in [bad,'{"schema_version":1,"schema_version":1}']:
                p.write_text(content,encoding='utf-8')
                proc=subprocess.run([sys.executable,'-X','utf8','-B',str(ROOT/'skills/trade-chart-scenarios/scripts/research.py'),str(p)],capture_output=True,text=True,encoding='utf-8',timeout=10)
                self.assertEqual(proc.returncode,2);self.assertEqual(json.loads(proc.stdout)['status'],'invalid_input')
    def test_selector_does_not_use_holdout_and_requires_all_symbols(self):
        x=fixture();template=R.run(x)['trades'][0]
        dev=dict(start='2020-01-01T00:00:00Z',end='2020-01-02T00:00:00Z')
        val=dict(start='2020-01-02T00:00:00Z',end='2020-01-03T00:00:00Z')
        def t(day,value):
            return {**template,'signal_available_at':f'2020-01-0{day}T01:00:00Z','entry_at':f'2020-01-0{day}T02:00:00Z',
                'outcome_available_at':f'2020-01-0{day}T03:00:00Z','net_r_lower':str(value),'net_r_upper':str(value)}
        cs=[{'A':[t(1,1),t(2,1),t(3,999)],'B':[t(1,1),t(2,1)]},
            {'A':[t(1,1),t(2,2),t(3,-999)],'B':[t(1,1),t(2,2)]}]
        result=R.select_on_validation(cs,dev,val,1,1);self.assertEqual(result['selected_index'],1)
        cs[1]['A'][-1]['net_r_lower']='999999';self.assertEqual(R.select_on_validation(cs,dev,val,1,1),result)
        cs[1]['B']=[];self.assertEqual(R.select_on_validation(cs,dev,val,1,1)['selected_index'],0)
        cs[0]['B']=[];self.assertIsNone(R.select_on_validation(cs,dev,val,1,1)['selected_index'])
    def test_selector_rejects_overlapping_folds(self):
        with self.assertRaises(A.InputIssue):R.select_on_validation([{'A':[]}],dict(start='2020-01-01T00:00:00Z',end='2020-01-03T00:00:00Z'),dict(start='2020-01-02T00:00:00Z',end='2020-01-04T00:00:00Z'))
    def test_profiled_analysis_bridge_preserves_old_output(self):
        x=fixture();result=R.analyze_with_instrument(x)
        old=A.analyze({k:v for k,v in x.items() if k not in ['instrument','setup']})
        self.assertEqual(result['analysis'],old);self.assertFalse(result['entry_permission']);self.assertFalse(result['setup_executed'])
    def test_profiled_bridge_rejects_unknown_fields_and_mismatch(self):
        x=fixture();x['unexpected_instruction']='buy now';self.assertEqual(R.analyze_with_instrument(x)['status'],'invalid_input')
        x=fixture();x['instrument']['feed_id']='wrong';self.assertEqual(R.analyze_with_instrument(x)['status'],'invalid_input')
    def test_cli_persian_profile_under_ascii_stdout(self):
        x=fixture();x['instrument']['venue']='صرافی پژوهشی'
        with tempfile.TemporaryDirectory(dir=ROOT/'tests/market_adaptation') as tmp:
            self.assertTrue(Path(tmp).resolve().is_relative_to(ROOT.resolve()))
            p=Path(tmp)/'input.json';p.write_text(json.dumps(x,ensure_ascii=False),encoding='utf-8')
            proc=subprocess.run([sys.executable,'-B',str(ROOT/'skills/trade-chart-scenarios/scripts/research.py'),str(p),'--mode','analysis'],
                env={**os.environ,'PYTHONIOENCODING':'ascii'},capture_output=True,timeout=10)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertEqual(json.loads(proc.stdout.decode('utf-8'))['instrument']['venue'],'صرافی پژوهشی')

if __name__=='__main__':unittest.main()
