"""Behavioral tests: source evidence, causal discovery and nearest-obstacle decisions."""
import copy
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('level_engine', ROOT/'skills/trade-chart-scenarios/scripts/analyze.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def payload(values=None):
    values = values or [('100','102','98','101')]*9
    bars = []
    t = datetime(2020,1,1,tzinfo=timezone.utc)
    iso = lambda v:v.isoformat().replace('+00:00','Z')
    for i, (o,h,l,c) in enumerate(values):
        start, end = t+timedelta(minutes=15*i), t+timedelta(minutes=15*(i+1))
        bars.append(dict(id=str(i),open_time=iso(start),close_time=iso(end),available_at=iso(end),is_closed=True,
                         open=o,high=h,low=l,close=c))
    return dict(schema_version=1,as_of=bars[-1]['close_time'],series=dict(symbol='TEST',market='forex',feed_id='synthetic',
        price_basis='bid',timeframe='M15',chart_type='standard_ohlc',tick_size='0.1',atr_period=2,
        session_definition='contiguous test bars',source_timezone='UTC',bars=bars),
        level_analysis=dict(profile='levels_targets_v1'))


def level(p,lid,lo,hi=None,side='resistance',tf='M15',at=0):
    return dict(id=lid,kind='classical_line' if hi is None or lo==hi else 'pivot_zone',side=side,timeframe=tf,
        lower=lo,upper=hi or lo,origin_at=p['series']['bars'][0]['open_time'],
        available_at=p['series']['bars'][at]['open_time'],source_refs=['synthetic:explicit'],construction_method='wick_to_body')


def ordinary():
    p=payload([('100','102','98','101')]*4 + [('100','105','99','104'),('104','109','103','108'),
        ('108','113','107','112'),('112','114','106','106'),('106','108','103','105')])
    p['levels']=[level(p,'pivot','112','114')]
    l=p['levels'][0]; l['origin_at']=p['series']['bars'][7]['open_time']; l['available_at']=p['series']['bars'][7]['close_time']
    p['level_analysis']['evidence']=[dict(level_id='pivot',profile='ordinary_master_cover_v1',master_bar_id='6',extreme_bar_id='7',
        context_source_ref='synthetic:reviewed-master-and-timeframe',context_available_at=p['series']['bars'][6]['close_time'],
        preleg=dict(route='atr_displacement',start_bar_id='4',end_bar_id='6',atr_bar_id='4',threshold_atr='3'))]
    return p


class LevelTests(unittest.TestCase):
    def analyze(self,p):
        r=engine.analyze(p)
        self.assertFalse(r['issues'],r)
        return r['level_analysis']

    def test_swing_is_not_visible_before_right_bar_confirmation(self):
        p=payload([('100','102','98','101'),('101','104','99','103'),('103','108','101','106'),
                   ('106','107','102','104'),('104','105','100','101')])
        p['level_analysis']['discovery']=dict(left_bars=2,right_bars=2,construction_method='wick_to_body')
        p['as_of']=p['series']['bars'][3]['close_time']
        self.assertEqual(self.analyze(p)['levels'],[])
        p['as_of']=p['series']['bars'][4]['close_time']
        l=self.analyze(p)['levels'][0]
        self.assertEqual((l['lower'],l['upper']),('106','108'))
        self.assertEqual(l['available_at'],p['as_of'])
        self.assertEqual(l['validation']['status'],'needs_structure_review')
        self.assertFalse(l['validation']['structural_context_verified'])

    def test_tied_extrema_are_not_arbitrarily_promoted(self):
        p=payload([('100','104','98','101')]*6)
        p['level_analysis']['discovery']=dict(left_bars=1,right_bars=1,construction_method='wick_to_body')
        self.assertEqual(self.analyze(p)['levels'],[])

    def test_cover_and_exact_bounds_inside_supplied_context(self):
        p=ordinary();v=self.analyze(p)['levels'][0]['validation']
        self.assertEqual(v['status'],'ordinary_contract_consistent')
        self.assertEqual(v['cover_timing'],'timely')
        self.assertEqual(v['confirmation_bar_id'],'7')
        self.assertFalse(v['structural_context_verified'])

    def test_bad_boundary_and_backdated_availability_are_diagnosed(self):
        p=ordinary();p['levels'][0]['lower']='111';p['levels'][0]['available_at']=p['series']['bars'][6]['close_time']
        v=self.analyze(p)['levels'][0]['validation']
        self.assertEqual(v['status'],'rejected_evidence')
        self.assertIn('zone_bounds_disagree_with_selected_construction',v['issues'])
        self.assertIn('level_availability_predates_confirmation_or_context',v['issues'])

    def test_wrong_extreme_is_rejected(self):
        p=ordinary();p['level_analysis']['evidence'][0]['extreme_bar_id']='6'
        self.assertIn('extreme_not_in_preleg_through_cover_span',self.analyze(p)['levels'][0]['validation']['issues'])

    def test_preleg_routes_are_alternatives_not_cumulative_requirements(self):
        p=ordinary()
        self.assertEqual(self.analyze(p)['levels'][0]['validation']['status'],'ordinary_contract_consistent')
        p['level_analysis']['evidence'][0]['preleg']=dict(route='three_standard_bars',bar_ids=['4','5','6'])
        self.assertEqual(self.analyze(p)['levels'][0]['validation']['status'],'rejected_evidence')

    def test_zero_or_missing_atr_does_not_produce_half_atr_zone(self):
        p=payload([('100','102','98','101'),('101','104','99','103'),('103','108','101','106'),('106','107','102','104')])
        p['series']['atr_period']=10
        p['level_analysis']['discovery']=dict(left_bars=1,right_bars=1,construction_method='half_atr')
        r=self.analyze(p)
        self.assertEqual(r['levels'],[]);self.assertTrue(r['skipped_discovery'])

    def test_touch_episodes_close_break_and_no_automatic_role_flip(self):
        p=payload([('100','103','99','101'),('101','104','100','102'),('102','102','99','100'),
                   ('100','103','99','101'),('101','105','100','104'),('104','104','99','100')])
        p['levels']=[level(p,'r','103')]
        l=self.analyze(p)['levels'][0]
        self.assertEqual(l['lifecycle']['touch_episodes'],2)
        self.assertEqual(l['lifecycle']['broken_at'],p['series']['bars'][4]['close_time'])
        self.assertEqual(l['lifecycle']['status'],'broken_original_role')
        self.assertEqual(l['side'],'resistance');self.assertEqual(l['timeframe'],'M15')

    def test_half_atr_uses_frozen_previous_bar_value_without_tick_rounding(self):
        p=ordinary();p['levels'][0]['construction_method']='half_atr'
        baseline=Decimal(engine.analyze(p)['candles'][7]['baseline_atr'])
        p['levels'][0]['lower']=format(Decimal('114')-baseline/2,'f')
        v=self.analyze(p)['levels'][0]['validation']
        self.assertEqual(v['status'],'ordinary_contract_consistent')
        self.assertEqual(Decimal(v['expected_bounds'][0]),Decimal('114')-baseline/2)

    def test_recurring_half_atr_boundary_is_accepted_without_rounding(self):
        p=ordinary();p['series']['atr_period']=3
        baseline=Decimal(engine.analyze(p)['candles'][7]['baseline_atr'])
        with localcontext() as ctx:
            ctx.prec=50
            p['levels'][0].update(construction_method='half_atr',lower=format(Decimal('114')-baseline/2,'f'))
        self.assertGreater(len(p['levels'][0]['lower'].split('.')[1]),18)
        self.assertEqual(self.analyze(p)['levels'][0]['validation']['status'],'ordinary_contract_consistent')

    def test_close_break_is_observable_when_level_arrives_mid_bar(self):
        p=payload([('100','102','98','101')]*4+[('101','108','100','107')])
        p['levels']=[level(p,'late','105')]
        p['levels'][0]['available_at']='2020-01-01T01:05:00Z'
        life=self.analyze(p)['levels'][0]['lifecycle']
        self.assertEqual(life['status'],'broken_original_role')
        self.assertEqual(life['touch_episodes'],0)
        self.assertEqual(life['partial_bar_intersection_ids'],['4'])

    def test_zero_width_ordinary_wick_requires_method_review(self):
        p=ordinary();b=p['series']['bars'][7];b['open']='114';p['levels'][0].update(lower='114',kind='classical_line')
        v=self.analyze(p)['levels'][0]['validation']
        self.assertNotEqual(v['status'],'ordinary_contract_consistent')
        self.assertIn('zero_width_wick_requires_method_review',v['issues'])

    def test_three_standard_bars_route_can_qualify_on_its_own(self):
        p=ordinary()
        values=[('100','103','99','102'),('102','105','101','104'),('104','107','103','106'),('106','108','102','102')]
        for b,v in zip(p['series']['bars'][4:8],values):b.update(dict(zip(['open','high','low','close'],v)))
        p['levels'][0].update(lower='106',upper='108')
        p['level_analysis']['evidence'][0]['preleg']=dict(route='three_standard_bars',bar_ids=['4','5','6'])
        self.assertEqual(self.analyze(p)['levels'][0]['validation']['status'],'ordinary_contract_consistent')

    def test_late_cover_preserves_level_but_does_not_become_entry(self):
        p=ordinary();p['series']['bars'][7]['close']='108';p['series']['bars'][8]['close']='108'
        b=dict(p['series']['bars'][8]);b.update(id='9',open_time=b['close_time'],close_time='2020-01-01T02:30:00Z',
            available_at='2020-01-01T02:30:00Z',open='108',high='109',low='105',close='106')
        p['series']['bars'].append(b);p['as_of']=b['close_time'];p['levels'][0]['available_at']=b['close_time']
        r=engine.analyze(p);v=r['level_analysis']['levels'][0]['validation']
        self.assertEqual(v['status'],'ordinary_contract_consistent');self.assertEqual(v['cover_timing'],'late')
        self.assertEqual(v['cover_bar_count'],3);self.assertIsNone(r['order'])


class TargetTests(unittest.TestCase):
    analyze=LevelTests.analyze
    def map(self,p,**kw):
        p['level_analysis']['target_request']=dict(direction='up',selection_bar_id=p['series']['bars'][-1]['id'],**kw)
        return self.analyze(p)['target_selection']

    def test_nearest_unreviewed_obstacle_cannot_be_skipped(self):
        p=payload();p['levels']=[level(p,'far','120'),level(p,'near','105')]
        r=self.map(p)
        self.assertEqual(r['selected']['id'],'near');self.assertEqual(r['status'],'needs_structure_review')
        self.assertIsNone(r['risk_reward']);self.assertEqual(r['further_targets'],[])

    def test_zone_containing_reference_blocks_farther_target(self):
        p=payload();p['levels']=[level(p,'inside','100','104'),level(p,'far','120')]
        r=self.map(p)
        self.assertEqual(r['status'],'needs_zone_review');self.assertIsNone(r['selected'])

    def test_overlapping_anchor_is_not_a_second_independent_target(self):
        p=payload();p['levels']=[level(p,'anchor','103','105'),level(p,'overlap','104','107'),level(p,'far','120')]
        r=self.map(p,anchor_level_id='anchor')
        self.assertEqual(r['status'],'needs_zone_review');self.assertIsNone(r['selected'])
        self.assertEqual(r['anchor_overlap_ids'],['overlap'])
        self.assertEqual(p['levels'][0]['upper'],'105')

    def test_consumed_target_does_not_move_to_farther_level(self):
        p=payload([('100','102','98','101')]*4+[('101','108','100','107'),('107','108','100','101')])
        p['levels']=[level(p,'anchor','102'),level(p,'near','105'),level(p,'far','120')]
        r=self.map(p,anchor_level_id='anchor',scenario_start_bar_id='4')
        self.assertEqual(r['selected']['id'],'near');self.assertEqual(r['status'],'consumed_reassess')
        self.assertEqual(r['first_reach']['bar_id'],'4')
        self.assertEqual(r['first_reach']['kind'],'range_intersection')

    def test_gap_over_target_is_not_evidence_of_traded_touch_or_fill(self):
        p=payload([('100','102','98','101')]*4+[('110','112','109','111')])
        p['levels']=[level(p,'anchor','102'),level(p,'near','105','106')]
        r=self.map(p,anchor_level_id='anchor',scenario_start_bar_id='4')
        self.assertEqual(r['status'],'consumed_reassess')
        self.assertEqual(r['first_reach']['kind'],'gap_beyond_zone');self.assertFalse(r['first_reach']['execution_fill_proven'])

    def test_target_born_after_scenario_start_is_not_backfilled(self):
        p=payload();p['levels']=[level(p,'anchor','103'),level(p,'new','105',at=6)]
        r=self.map(p,anchor_level_id='anchor',scenario_start_bar_id='4')
        self.assertEqual(r['status'],'no_known_target')
        self.assertIn({'id':'new','reason':'not_known_at_target_freeze_time'},r['exclusions'])

    def test_original_broken_role_cannot_be_resurrected_after_return(self):
        p=payload([('100','102','98','101'),('101','106','100','105'),('105','106','99','100')])
        p['levels']=[level(p,'old','104')]
        self.assertEqual(self.map(p)['status'],'no_known_target')

    def test_rejection_is_symmetric_for_support(self):
        p=payload();p['levels']=[level(p,'far','80',side='support'),level(p,'near','95',side='support')]
        p['level_analysis']['target_request']=dict(direction='down',selection_bar_id='8')
        r=self.analyze(p)['target_selection'];self.assertEqual(r['selected']['id'],'near')
        self.assertEqual(Decimal(r['remaining_distance']),6)

    def test_level_order_does_not_change_target(self):
        p=payload();p['levels']=[level(p,'a','105','107'),level(p,'b','105','108')]
        r=self.map(p);p['levels'].reverse();s=self.map(p)
        self.assertEqual(r,s)

    def test_new_target_book_cannot_create_post_invalidation_events(self):
        from test_analysis import sample, append_bar
        p=sample();append_bar(p,c='91');p['series']['bars'][-1]['id']='invalidated'
        append_bar(p,o='91',h='125',l='90',c='123')
        p['level_analysis']=dict(profile='levels_targets_v1',target_request=dict(direction='up',selection_bar_id='after',
            anchor_level_id='resistance-100',scenario_start_bar_id='b0'))
        r=engine.analyze(p);t=r['level_analysis']['target_selection']
        self.assertEqual(r['assessment']['scenario_status'],'invalidated')
        self.assertIsNone(t['first_reach'])
        self.assertEqual(t['status'],'scenario_invalidated')

    def test_selection_time_includes_delayed_preceding_bar(self):
        p=payload();p['series']['bars'][7]['available_at']='2020-01-01T03:00:00Z';p['as_of']='2020-01-01T03:00:00Z'
        p['levels']=[level(p,'r','105')]
        r=self.map(p)
        self.assertEqual(r['selection_available_at'],p['as_of'])

    def test_consistent_target_is_conditional_without_order_or_rr(self):
        p=ordinary();r=self.map(p)
        self.assertEqual(r['status'],'conditional_target');self.assertEqual(r['selected']['near_edge'],'112')
        self.assertIsNone(r['risk_reward']);self.assertIsNone(r['order'])

    def test_past_selection_requires_a_causal_snapshot(self):
        p=payload();p['levels']=[level(p,'r','105')]
        p['level_analysis']['target_request']=dict(direction='up',selection_bar_id='4')
        r=self.analyze(p)['target_selection'];self.assertIsNone(r['selected'])
        self.assertEqual(r['status'],'needs_context')

    def test_lower_timeframe_close_cannot_erase_higher_timeframe_obstacle(self):
        p=payload([('100','102','98','101'),('101','108','100','107'),('107','108','99','101')])
        p['levels']=[level(p,'higher','105',tf='H4'),level(p,'far','120')]
        r=self.map(p)
        self.assertEqual(r['selected']['id'],'higher')
        self.assertEqual(r['status'],'needs_timeframe_review')
        l=self.analyze(p)['levels'][0]
        self.assertEqual(l['lifecycle']['status'],'needs_timeframe_series')


if __name__=='__main__':unittest.main()
