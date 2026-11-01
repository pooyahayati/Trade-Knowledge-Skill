import copy
import importlib.util
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('context_engine',ROOT/'skills/trade-chart-scenarios/scripts/analyze.py')
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)


def bars(values, minutes=1):
    start=datetime(2020,1,1,tzinfo=timezone.utc)
    result=[]
    for i,v in enumerate(values):
        t=start+timedelta(minutes=minutes*i);v=Decimal(str(v))
        result.append(dict(id=str(i),open_time=t,close_time=t+timedelta(minutes=minutes),
                           available_at=t+timedelta(minutes=minutes),open=v,high=v+1,low=v-1,close=v))
    return result


def series(data,tf):
    s=dict(symbol='TEST',market='forex',feed_id='synthetic',price_basis='bid',timeframe=tf,
           chart_type='standard_ohlc',tick_size='1',atr_period=2,session_definition='UTC continuous test',
           source_timezone='UTC',bars=[])
    for b in data:
        s['bars'].append({k:engine.stamp(v) if k in ['open_time','close_time','available_at'] else
                         str(v) if k in ['open','high','low','close'] else v for k,v in b.items()}|{'is_closed':True})
    return s


def aggregate(data,n):
    result=[]
    for j in range(0,len(data)-n+1,n):
        g=data[j:j+n]
        result.append(dict(id=str(j),open_time=g[0]['open_time'],close_time=g[-1]['close_time'],
             available_at=max(b['available_at'] for b in g),open=g[0]['open'],high=max(b['high'] for b in g),
             low=min(b['low'] for b in g),close=g[-1]['close']))
    return result


def payload():
    # Sufficient history and exact common-source aggregation for clock/identity tests.
    values=[100+i//30+abs((i%30)-15) for i in range(180)]
    data=bars(values)
    return dict(schema_version=1,as_of=engine.stamp(data[-1]['close_time']),series=series(data,'M1'),
        structure_analysis=dict(profile='confirmed_swings_mtf_v1',structure_timeframe='M15',
           left_bars=1,right_bars=1,max_pivot_age_bars=30,max_close_lag_seconds={'M15':900,'M5':300,'M1':60},
           alignment_source_ref='synthetic exact aggregation',higher_series=[series(aggregate(data,15),'M15'),series(aggregate(data,5),'M5')]))


class StructureTests(unittest.TestCase):
    def scan(self,values,**kwargs):
        return engine.STRUCTURE.scan(bars(values),1,1,kwargs.get('max_age',30))

    def test_confirmation_waits_for_right_hand_close(self):
        self.assertEqual(self.scan([100,110])['pivots'],[])
        p=self.scan([100,110,105])['pivots'][0]
        self.assertEqual(p['bar_id'],'1');self.assertEqual(p['confirmed_at'],'2020-01-01T00:03:00Z')

    def test_guard_is_predecessor_of_higher_high_not_latest_pullback(self):
        r=self.scan([100,110,102,115,108,109])
        self.assertEqual(r['state'],'uptrend')
        self.assertEqual(r['protected']['bar_id'],'2')
        self.assertEqual(r['driver']['bar_id'],'3')

    def test_internal_lower_high_low_does_not_reverse_protected_trend(self):
        r=self.scan([100,110,102,115,108,109,105,106])
        self.assertEqual(r['state'],'uptrend')
        self.assertEqual(r['protected']['bar_id'],'2')

    def test_wick_breach_and_equality_are_not_close_break(self):
        data=bars([100,110,102,115,108,109,110]);data[-1].update(low=Decimal('99'),close=Decimal('101'))
        r=engine.STRUCTURE.scan(data,1,1,30)
        self.assertEqual(r['state'],'uptrend')

    def test_close_breach_is_transition_not_reversal_and_does_not_resurrect(self):
        for tail in [[100],[100,110]]:
            r=self.scan([100,110,102,115,108,109]+tail)
            self.assertEqual(r['state'],'transition');self.assertIsNone(r['direction'])
            self.assertEqual(r['break_bar_id'],'6')

    def test_mirror_bearish_structure(self):
        r=self.scan([200-v for v in [100,110,102,115,108,109]])
        self.assertEqual(r['state'],'downtrend');self.assertEqual(r['protected']['bar_id'],'2')

    def test_outside_bar_does_not_invent_pivot_order(self):
        data=bars([100,101,102]);data[1].update(high=Decimal('110'),low=Decimal('90'))
        r=engine.STRUCTURE.scan(data,1,1,30)
        self.assertEqual(r['state'],'ambiguous');self.assertEqual(r['recent_sequence'],[])

    def test_equal_extrema_not_arbitrarily_selected(self):
        self.assertEqual(self.scan([100,110,110,100])['pivots'],[])

    def test_ambiguous_internal_extreme_does_not_erase_intact_guard(self):
        data=bars([100,110,102,115,108,109,110,111,110])
        data[7].update(high=Decimal('120'),low=Decimal('103'))
        r=engine.STRUCTURE.scan(data,1,1,30)
        self.assertEqual(r['state'],'uptrend');self.assertEqual(r['protected']['bar_id'],'2')
        self.assertTrue(r['swing_order_review_pending'])

    def test_guard_moves_only_when_new_higher_high_confirmed(self):
        data=bars([100,110,102,115,108,109,120,115])
        before=engine.STRUCTURE.scan(data[:-1],1,1,30)
        after=engine.STRUCTURE.scan(data,1,1,30)
        self.assertEqual(before['protected']['bar_id'],'2')
        self.assertEqual(after['protected']['bar_id'],'4')
        self.assertEqual(after['driver']['available_at'],'2020-01-01T00:08:00Z')

    def test_new_opposite_structure_requires_post_break_swings(self):
        r=self.scan([100,110,102,115,108,109,100,108,95,105,90,93])
        self.assertEqual(r['state'],'downtrend')
        self.assertEqual(r['protected']['bar_id'],'9')

    def test_post_break_containment_is_range_candidate_not_reversal(self):
        r=self.scan([100,110,102,115,108,109,100,106,95,105,96,100])
        self.assertEqual(r['state'],'range_candidate')
        self.assertIsNone(r['direction'])
        self.assertEqual(r['prior_structure_break']['break_bar_id'],'6')

    def test_confirmed_geometry_cannot_bypass_missing_context_and_levels(self):
        import json
        p=json.loads((ROOT/'skills/trade-chart-scenarios/assets/example-input.json').read_text())
        p['structure_analysis']=dict(profile='confirmed_swings_mtf_v1',structure_timeframe='H4',left_bars=1,right_bars=1,
            max_pivot_age_bars=30,max_close_lag_seconds={'H4':14400,'H1':3600,'M15':900},
            alignment_source_ref='missing higher series fixture',higher_series=[])
        r=engine.analyze(p)
        self.assertEqual(r['status'],'confirmed_under_selected_profile')
        self.assertFalse(r['structural_context']['scenario_ready'])
        self.assertFalse(any(c['kind']=='continuation' for c in r['conditional_scenarios']))

    def test_context_gate_recognizes_actual_confirmation_status(self):
        # An integration seam: earlier modules have their own source/target tests.
        import json
        p=json.loads((ROOT/'skills/trade-chart-scenarios/assets/example-input.json').read_text())
        p['structure_analysis']={}
        real=engine.evaluate
        def assessed(*args):
            value=real(*args);value['level_and_target_gate']='consistent_with_supplied_context';return value
        with patch.object(engine,'context_review',return_value={'status':'aligned','direction':'up'}),patch.object(engine,'evaluate',side_effect=assessed):
            r=engine.analyze(p)
        self.assertTrue(r['structural_context']['scenario_ready'])

    def test_quiet_old_structure_is_not_current_direction(self):
        r=self.scan([100,110,102,115,108,109]+[109]*10,max_age=4)
        self.assertEqual(r['state'],'stale_structure');self.assertIsNone(r['direction'])

    def test_prefix_events_immutable_under_later_swings(self):
        data=bars([100,110,102,115,108,109,105,106,100,110,95,100,90,94])
        full=engine.STRUCTURE.scan(data,1,1,30)
        for n in range(1,len(data)+1):
            r=engine.STRUCTURE.scan(data[:n],1,1,30)
            self.assertEqual(r['events'],[e for e in full['events'] if e['observed_at']<=engine.stamp(data[n-1]['close_time'])])

    def test_exact_aggregates_and_roles_reported(self):
        r=engine.analyze(payload());self.assertEqual(r['issues'],[])
        c=r['structural_context'];self.assertEqual(set(c['roles']),{'structure','pattern','trigger'})
        self.assertTrue(all(x['status']=='consistent_overlap' for x in c['alignment_checks']))
        self.assertFalse(c['scenario_ready'])

    def test_higher_future_prices_and_open_bar_not_used(self):
        p=payload();p['as_of']=p['series']['bars'][168]['close_time']
        a=engine.analyze(p)['structural_context']
        for s in p['structure_analysis']['higher_series']:
            for b in s['bars']:
                if b['close_time']>p['as_of']:
                    b.update(open='9999',high='invalid future',low='0',close='9999')
        self.assertEqual(a,engine.analyze(p)['structural_context'])

    def test_higher_delayed_receipt_is_excluded(self):
        p=payload();p['structure_analysis']['higher_series'][0]['bars'][-1]['available_at']='2020-01-01T04:00:00Z'
        r=engine.analyze(p)['structural_context']
        self.assertEqual(r['roles']['structure']['excluded_tail_bars'],1)
        self.assertEqual(r['status'],'stale_data')

    def test_price_basis_or_session_mismatch_rejected(self):
        for key,value in [('price_basis','ask'),('session_definition','shifted session'),('symbol','OTHER')]:
            p=payload();p['structure_analysis']['higher_series'][0][key]=value
            self.assertEqual(engine.analyze(p)['issues'][0]['code'],'incompatible_series_identity')

    def test_inconsistent_higher_prices_block_alignment(self):
        p=payload();p['structure_analysis']['higher_series'][0]['bars'][-1]['high']='999'
        self.assertEqual(engine.analyze(p)['structural_context']['status'],'needs_alignment_review')

    def test_missing_timeframe_cannot_be_inferred(self):
        p=payload();p['structure_analysis']['higher_series'].pop(0)
        r=engine.analyze(p)['structural_context'];self.assertEqual(r['status'],'missing_timeframes')
        self.assertEqual(r['missing_timeframes'],['M15'])

    def test_incomplete_ladder_and_duplicate_roles_rejected(self):
        p=payload();p['structure_analysis']['structure_timeframe']='M5'
        self.assertEqual(engine.analyze(p)['status'],'unsupported')
        p=payload();p['structure_analysis']['higher_series']*=2
        self.assertEqual(engine.analyze(p)['status'],'invalid_input')

    def test_stale_current_request_does_not_reuse_old_trigger(self):
        p=payload();p['as_of']='2020-01-01T03:01:00Z'
        r=engine.analyze(p)['structural_context'];self.assertEqual(r['status'],'stale_data')
        self.assertEqual(r['decision_at'],'2020-01-01T03:00:00Z')

    def test_future_information_not_joined_to_older_trigger(self):
        p=payload();p['series']['bars']=p['series']['bars'][:-5]
        r=engine.analyze(p)['structural_context']
        self.assertEqual(r['decision_at'],'2020-01-01T02:55:00Z')
        self.assertEqual(r['roles']['structure']['structure']['last_close_at'],'2020-01-01T02:45:00Z')

    def test_role_conflict_is_pullback_not_majority_reversal(self):
        def role(d):return {'fresh':True,'structure':{'direction':d,'state':'uptrend' if d=='up' else 'downtrend'}}
        self.assertEqual(engine.STRUCTURE.combine(dict(structure=role('up'),pattern=role('down'),trigger=role('down'))),('higher_timeframe_pullback','up'))
        self.assertEqual(engine.STRUCTURE.combine(dict(structure=role('up'),pattern=role('up'),trigger=role('down'))),('trigger_countermove','up'))

    def test_higher_level_uses_its_own_closes_and_unknown_history_stays_unknown(self):
        p=payload();p['levels']=[dict(id='higher',kind='classical_line',side='resistance',timeframe='M15',
            lower='999',upper='999',origin_at='2020-01-01T00:00:00Z',available_at='2020-01-01T00:00:00Z',
            source_refs=['synthetic'],construction_method='explicit')]
        r=engine.analyze(p)['structural_context']['level_role_checks'][0]
        self.assertEqual(r['lifecycle']['status'],'active_original_role');self.assertFalse(r['master_context_verified'])
        p['levels'][0].update(origin_at='2019-12-31T00:00:00Z',available_at='2019-12-31T00:00:00Z')
        r=engine.analyze(p)['structural_context']['level_role_checks'][0]
        self.assertEqual(r['lifecycle']['status'],'needs_level_history')

    def test_level_known_after_trigger_decision_not_backfilled(self):
        p=payload();p['series']['bars']=p['series']['bars'][:-5]
        p['levels']=[dict(id='later',kind='classical_line',side='resistance',timeframe='M15',lower='999',upper='999',
            origin_at='2020-01-01T00:00:00Z',available_at='2020-01-01T02:59:00Z',source_refs=['synthetic'],construction_method='explicit')]
        r=engine.analyze(p)['structural_context']['level_role_checks'][0]
        self.assertEqual(r['lifecycle']['status'],'not_available_at_decision')

    def test_unexplained_higher_missing_bar_rejected(self):
        p=payload();p['structure_analysis']['higher_series'][0]['bars'].pop(2)
        self.assertEqual(engine.analyze(p)['issues'][0]['code'],'unexplained_missing_expected_bars')

    def test_session_closure_age_not_double_subtracted(self):
        p=payload();p['as_of']='2020-01-01T04:00:00Z'
        for s in [p['series']]+p['structure_analysis']['higher_series']:
            s['declared_session_gaps']=[dict(from_close='2020-01-01T03:00:00Z',to_open='2020-01-01T04:00:00Z',reason='scheduled_session_closure',source_ref='synthetic closure')]*2
        r=engine.analyze(p)['structural_context']
        self.assertEqual(r['roles']['trigger']['close_lag_seconds'],0)

    def test_calendar_shift_without_matching_constituents_blocks_alignment(self):
        p=payload();s=p['structure_analysis']['higher_series'][0]
        for b in s['bars']:
            for key in ['open_time','close_time','available_at']:
                b[key]=engine.stamp(engine.instant(b[key],key)+timedelta(minutes=1))
        r=engine.analyze(p)['structural_context']
        self.assertEqual(r['status'],'needs_alignment_review')


if __name__=='__main__':unittest.main()
