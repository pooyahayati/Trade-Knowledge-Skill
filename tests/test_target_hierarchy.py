"""Own-timeframe targets: causal evidence, role integrity and no skipped obstacle."""
import copy
import unittest
from unittest.mock import patch
from datetime import timedelta
from decimal import Decimal
from test_levels_targets import engine, ordinary, level
from test_structure import aggregate, series


def fixture(direction='up'):
    source = ordinary()
    source['series']['bars'] += [dict(source['series']['bars'][-1]) for _ in range(3)]
    data = []
    start = engine.instant(source['series']['bars'][0]['open_time'], 'test')
    for i, raw in enumerate(source['series']['bars']):
        for j in range(15):
            t = start + timedelta(minutes=i*15+j)
            values = {k:Decimal(raw[k]) for k in ['open','high','low','close']}
            if direction == 'down':
                values = dict(open=220-values['open'],high=220-values['low'],low=220-values['high'],close=220-values['close'])
            data.append(dict(id=str(i*15+j),open_time=t,close_time=t+timedelta(minutes=1),
                             available_at=t+timedelta(minutes=1),**values))
    p = dict(schema_version=1,as_of=engine.stamp(data[-1]['close_time']),series=series(data,'M1'),
        structure_analysis=dict(profile='confirmed_swings_mtf_v1',structure_timeframe='M15',
            left_bars=1,right_bars=1,max_pivot_age_bars=30,max_close_lag_seconds={'M15':900,'M5':300,'M1':60},
            alignment_source_ref='synthetic exact aggregation',
            higher_series=[series(aggregate(data,15),'M15'),series(aggregate(data,5),'M5')]),
        target_hierarchy=dict(profile='own_timeframe_targets_v1'),
        level_analysis=dict(profile='levels_targets_v1',evidence=copy.deepcopy(source['level_analysis']['evidence']),
            target_request=dict(direction=direction,selection_bar_id=str(len(data)-1))))
    p['levels'] = copy.deepcopy(source['levels'])
    p['levels'][0]['id'] = 'structural'
    if direction == 'down':
        p['levels'][0].update(side='support',lower='106',upper='108')
    e = p['level_analysis']['evidence'][0]
    e.update(level_id='structural',master_bar_id='90',extreme_bar_id='105')
    e['preleg'].update(start_bar_id='60',end_bar_id='90',atr_bar_id='60')
    return p


def scenario_fixture():
    p=fixture(); tail=ordinary()['series']['bars']
    values=[{k:str(Decimal(b[k])-14) for k in ['open','high','low','close']} for b in tail]
    values += [dict(zip(['open','high','low','close'],v)) for v in
               [('92','104','92','103'),('103','106','101','103.5'),('103.5','107','103','105')]]
    for b,v in zip(p['series']['bars'][-12:],values): b.update(v)
    raw=p['series'];raw['tick_size']='0.1'
    data=engine.parse_series(raw,engine.instant(p['as_of'],'test'))[2]
    for i,n in enumerate([15,5]):
        p['structure_analysis']['higher_series'][i]=series(aggregate(data,n),'M15' if n==15 else 'M5')
        p['structure_analysis']['higher_series'][i]['tick_size']='0.1'
    anchor=level(p,'anchor','98','100',tf='M1',at=176)
    anchor['origin_at']=raw['bars'][175]['open_time']
    p['levels'].append(anchor)
    p['level_analysis']['evidence'].append(dict(level_id='anchor',profile='ordinary_master_cover_v1',
        master_bar_id='174',extreme_bar_id='175',context_source_ref='synthetic supplied context',
        context_available_at=raw['bars'][174]['close_time'],preleg=dict(route='atr_displacement',
        start_bar_id='172',end_bar_id='174',atr_bar_id='172',threshold_atr='2.5')))
    p['level_analysis']['target_request'].update(anchor_level_id='anchor',scenario_start_bar_id='177')
    p['assessment']=dict(model='strict_three_bar_confirmation_v1',candidate_bar_id='177',direction='up',level_id='anchor',
        target_level_id='structural',context_available_at=raw['bars'][176]['close_time'],context_source_ref='synthetic scenario context')
    return p


class HierarchyTests(unittest.TestCase):
    def run_case(self,p):
        r=engine.analyze(p)
        self.assertFalse(r['issues'],r['issues'])
        return r

    def test_own_timeframe_evidence_validates_both_directions(self):
        for direction in ['up','down']:
            with self.subTest(direction=direction):
                r=self.run_case(fixture(direction))
                h=r['target_hierarchy']
                self.assertEqual(h['status'],'conditional_structural_target')
                self.assertEqual(h['structural_target']['selected']['id'],'structural')
                self.assertEqual(h['structural_target']['selected']['timeframe'],'M15')
                self.assertTrue(r['method_scope']['structural_target_contract_consistent'])
                self.assertFalse(r['method_scope']['structural_target_validated'])
                self.assertFalse(r['method_scope']['full_method_validated'])
                self.assertFalse(r['structural_context']['scenario_ready'])  # No trigger assessment.

    def test_nearest_unreviewed_obstacle_cannot_be_skipped(self):
        p=fixture();p['levels'].append(level(p,'near','109','110',tf='M1',at=135))
        r=self.run_case(p);h=r['target_hierarchy']
        self.assertEqual(h['nearest_obstacle']['selected']['id'],'near')
        self.assertEqual(h['structural_target']['selected']['id'],'structural')
        self.assertEqual(h['status'],'needs_nearest_obstacle_review')
        self.assertFalse(h['direct_path_clear'])
        self.assertIn('near',[x['id'] for x in h['intermediate_obstacles']])

    def test_unknown_own_timeframe_still_blocks_nearest(self):
        p=fixture();p['levels'].append(level(p,'unknown','109','110',tf='H1'))
        h=self.run_case(p)['target_hierarchy']
        self.assertEqual(h['nearest_obstacle']['status'],'needs_timeframe_review')
        self.assertFalse(h['direct_path_clear'])

    def test_confirmation_known_after_origin_not_a_historical_target(self):
        p=fixture();p['level_analysis']['target_request']['scenario_start_bar_id']='100'
        h=self.run_case(p)['target_hierarchy']
        self.assertIsNone(h['structural_target']['selected'])
        self.assertIn({'id':'structural','reason':'not_known_at_target_freeze_time'},h['structural_target']['exclusions'])

    def test_nearest_reached_never_rolls_to_further_structural_level(self):
        p=fixture();p['levels'].append(level(p,'near','107','108',tf='M1',at=135))
        p['level_analysis']['target_request']['scenario_start_bar_id']='165'
        h=self.run_case(p)['target_hierarchy']
        self.assertEqual(h['nearest_obstacle']['selected']['id'],'near')
        self.assertEqual(h['nearest_obstacle']['status'],'consumed_reassess')
        self.assertEqual(h['status'],'reassess_nearest_obstacle')
        self.assertEqual(h['structural_target']['selected']['id'],'structural')

    def test_consumed_structural_target_stays_selected(self):
        p=fixture();p['level_analysis']['target_request']['scenario_start_bar_id']='165'
        b=p['series']['bars'][166];b['high']='113'
        for s in p['structure_analysis']['higher_series']:
            for b in s['bars']:
                if b['id']=='165': b['high']='113'
        h=self.run_case(p)['target_hierarchy']
        self.assertEqual(h['structural_target']['status'],'consumed_reassess')
        self.assertEqual(h['structural_target']['selected']['id'],'structural')
        self.assertFalse(h['direct_path_clear'])

    def test_lower_timeframe_close_does_not_break_higher_level(self):
        p=fixture()
        p['series']['bars'][150].update(high='116',close='115')
        for s in p['structure_analysis']['higher_series']:
            next(b for b in s['bars'] if b['id']=='150')['high']='116'
        r=self.run_case(p)
        own=r['level_analysis']['levels'][0]['lifecycle']
        self.assertEqual(own['status'],'active_original_role')
        self.assertEqual(r['target_hierarchy']['status'],'conditional_structural_target')

    def test_own_timeframe_strict_close_break_excludes_original_role(self):
        p=fixture()
        for i in range(150,165): p['series']['bars'][i].update(high='116',close='115')
        for s in p['structure_analysis']['higher_series']:
            for b in s['bars']:
                if 150<=int(b['id'])<165: b.update(high='116',close='115')
        h=self.run_case(p)['target_hierarchy']
        self.assertIsNone(h['structural_target']['selected'])
        self.assertIn({'id':'structural','reason':'original_role_broken_before_target_freeze'},h['structural_target']['exclusions'])

    def test_missing_structure_series_is_not_validation(self):
        p=fixture();p['structure_analysis']['higher_series']=p['structure_analysis']['higher_series'][1:]
        r=self.run_case(p)
        self.assertFalse(r['method_scope']['structural_target_validated'])
        self.assertFalse(r['method_scope']['structural_target_contract_consistent'])
        self.assertEqual(r['target_hierarchy']['status'],'needs_timeframe_context')

    def test_stale_data_cannot_qualify_target(self):
        p=fixture();p['as_of']='2020-01-01T04:00:00Z'
        r=self.run_case(p)
        self.assertFalse(r['method_scope']['structural_target_validated'])
        self.assertFalse(r['method_scope']['structural_target_contract_consistent'])
        self.assertEqual(r['target_hierarchy']['status'],'needs_timeframe_context')

    def test_misaligned_higher_data_cannot_qualify_target(self):
        p=fixture();p['structure_analysis']['higher_series'][0]['bars'][-1]['high']='119'
        r=self.run_case(p)
        self.assertFalse(r['method_scope']['structural_target_validated'])
        self.assertFalse(r['method_scope']['structural_target_contract_consistent'])
        self.assertEqual(r['target_hierarchy']['status'],'needs_timeframe_context')

    def test_no_structure_level_does_not_promote_trigger_level(self):
        p=fixture();p['levels']=[];p['level_analysis']['evidence']=[]
        p['levels'].append(level(p,'trigger','109','110',tf='M1'))
        h=self.run_case(p)['target_hierarchy']
        self.assertEqual(h['structural_target']['status'],'no_known_target')
        self.assertFalse(h['direct_path_clear'])

    def test_declared_late_context_overrides_early_level_timestamp(self):
        p=fixture();p['level_analysis']['target_request']['scenario_start_bar_id']='150'
        p['level_analysis']['evidence'][0]['context_available_at']='2020-01-01T02:40:00Z'
        r=self.run_case(p)
        self.assertIsNone(r['target_hierarchy']['structural_target']['selected'])
        self.assertIn('level_availability_predates_confirmation_or_context',r['level_analysis']['levels'][0]['validation']['issues'])

    def test_hierarchy_requires_explicit_structure_and_target_request(self):
        for field in ['structure_analysis','level_analysis']:
            p=fixture();del p[field]
            self.assertEqual(engine.analyze(p)['status'],'invalid_input')

    def test_bad_profile_and_extra_fields_are_rejected(self):
        p=fixture();p['target_hierarchy']['profile']='unrecognized'
        self.assertEqual(engine.analyze(p)['status'],'unsupported')
        p=fixture();p['target_hierarchy']['force_target']='structural'
        self.assertEqual(engine.analyze(p)['status'],'invalid_input')

    def test_higher_information_after_trigger_decision_is_excluded(self):
        p=fixture();p['as_of']='2020-01-01T03:00:02Z'
        p['structure_analysis']['higher_series'][0]['bars'][-1]['available_at']='2020-01-01T03:00:01Z'
        r=self.run_case(p)
        self.assertEqual(r['structural_context']['decision_at'],'2020-01-01T03:00:00Z')
        self.assertFalse(r['method_scope']['structural_target_validated'])
        self.assertFalse(r['method_scope']['structural_target_contract_consistent'])

    def test_future_higher_ohlc_does_not_affect_hierarchy(self):
        p=fixture();old=self.run_case(p)['target_hierarchy']
        b=copy.deepcopy(p['structure_analysis']['higher_series'][0]['bars'][-1])
        b.update(id='future',open_time='2020-01-01T03:00:00Z',close_time='2020-01-01T03:15:00Z',
                 available_at='2020-01-01T03:15:00Z',high='unusable-future-value')
        p['structure_analysis']['higher_series'][0]['bars'].append(b)
        self.assertEqual(self.run_case(p)['target_hierarchy'],old)

    def test_missing_level_history_blocks_nearest_even_with_series(self):
        p=fixture();p['levels'].append(level(p,'old','109','110',tf='M5'))
        p['structure_analysis']['higher_series'][1]['bars']=p['structure_analysis']['higher_series'][1]['bars'][25:]
        h=self.run_case(p)['target_hierarchy']
        self.assertEqual(h['nearest_obstacle']['selected']['id'],'old')
        self.assertEqual(h['nearest_obstacle']['status'],'needs_timeframe_review')

    def test_same_edge_unverified_annotation_does_not_hide_behind_valid_target(self):
        p=fixture();p['levels'].append(level(p,'unreviewed','112','115',tf='M1',at=135))
        h=self.run_case(p)['target_hierarchy']
        self.assertEqual(h['nearest_obstacle']['status'],'needs_structure_review')
        self.assertFalse(h['direct_path_clear'])

    def test_timeframe_filter_does_not_erase_overlap_bridge_to_anchor(self):
        p=fixture();p['levels'] += [level(p,'anchor','99','100',tf='M1',at=135),
                                   level(p,'bridge','99','113',side='support',tf='M5',at=135)]
        p['level_analysis']['target_request'].update(anchor_level_id='anchor',scenario_start_bar_id='135')
        h=self.run_case(p)['target_hierarchy']
        self.assertEqual(h['structural_target']['status'],'needs_zone_review')
        self.assertFalse(h['structural_target_contract_consistent'])
        self.assertFalse(h['direct_path_clear'])

    def test_assessment_can_reference_higher_target_but_keeps_context_gate(self):
        p=scenario_fixture();r=self.run_case(p)
        self.assertEqual(r['assessment']['status'],'confirmed_under_selected_profile')
        self.assertEqual(r['assessment']['level_and_target_gate'],'consistent_with_supplied_context')
        self.assertEqual(r['assessment']['target_hierarchy_gate'],'conditional_structural_target')
        self.assertFalse(r['structural_context']['scenario_ready'])  # Real synthetic structure is unresolved.
        original=engine.context_review
        def aligned(*args):
            result=original(*args);result.update(status='aligned',direction='up');return result
        with patch.object(engine,'context_review',side_effect=aligned):
            self.assertTrue(self.run_case(p)['structural_context']['scenario_ready'])
            p['levels'].append(level(p,'near','108','109',tf='M1',at=176))
            r=self.run_case(p)
            self.assertFalse(r['structural_context']['scenario_ready'])
            self.assertFalse(any(c['kind']=='continuation' for c in r['conditional_scenarios']))

    def test_legacy_cross_timeframe_assessment_still_rejected(self):
        p=scenario_fixture();del p['target_hierarchy']
        self.assertEqual(engine.analyze(p)['status'],'invalid_input')

    def test_invalidation_ends_structural_target_observation(self):
        p=scenario_fixture();raw=p['series']['bars'];t=engine.instant(p['as_of'],'test')
        for i,v in enumerate([('105','106','89','90'),('90','120','90','118')]):
            raw.append(dict(id=str(180+i),open_time=engine.stamp(t+timedelta(minutes=i)),
                close_time=engine.stamp(t+timedelta(minutes=i+1)),available_at=engine.stamp(t+timedelta(minutes=i+1)),
                is_closed=True,**dict(zip(['open','high','low','close'],v))))
        p['as_of']=raw[-1]['close_time'];p['level_analysis']['target_request']['selection_bar_id']='181'
        r=self.run_case(p);h=r['target_hierarchy']
        self.assertEqual(r['assessment']['scenario_status'],'invalidated')
        self.assertEqual(h['status'],'scenario_invalidated')
        self.assertIsNone(h['structural_target']['first_reach'])
        self.assertFalse(h['structural_target_contract_consistent'])


if __name__=='__main__': unittest.main()
