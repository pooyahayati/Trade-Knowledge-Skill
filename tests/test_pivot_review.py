"""Causal source-linked pivot checks. Synthetic prices are not market evidence."""
import copy
import unittest
from test_levels_targets import engine, payload, level, ordinary
from test_target_hierarchy import fixture


def reviewed(noise=False):
    values=[('100','105','100','104')]*4 + [('100','105','100','104')]
    if noise:values += [('104','105','100','103.5')]
    values += [('104','109','104','108'),('108','113','108','112'),('112','114','106','106')]
    p=payload(values);bs=p['series']['bars'];m=len(bs)-2;c=len(bs)-1
    p['pivot_review']=dict(profile='source_linked_pivots_v1')
    p['levels']=[level(p,'pivot','112','114')]
    p['levels'][0].update(origin_at=bs[c]['open_time'],available_at=bs[c]['close_time'])
    p['level_analysis']['evidence']=[dict(level_id='pivot',profile='ordinary_master_cover_v1',master_bar_id=str(m),
        extreme_bar_id=str(c),context_source_ref='synthetic explicitly supplied wave',context_available_at=bs[m]['close_time'],
        preleg=dict(route='three_standard_bars',bar_ids=['4',str(m-1),str(m)]))]
    return p


def annotate(p,classification='no_pivot',index=5,**extra):
    b=p['series']['bars'][index]
    p['level_analysis']['evidence'][0]['wave_review']=dict(noise=[dict(bar_id=b['id'],classification=classification,
        source_ref='synthetic contextual review; not independent',available_at=b['available_at'],**extra)])


class PivotReviewTests(unittest.TestCase):
    def out(self,p):
        r=engine.analyze(p);self.assertFalse(r['issues'],r['issues']);return r
    def review(self,p):return self.out(p)['level_analysis']['levels'][0]['validation']

    def test_legacy_arithmetic_not_promoted_to_master_qualification(self):
        p=ordinary();p['pivot_review']=dict(profile='source_linked_pivots_v1')
        v=self.review(p)
        self.assertEqual(v['arithmetic_status'],'ordinary_contract_consistent')
        self.assertEqual(v['status'],'needs_pivot_review')
        self.assertIn('master_outside_strict_standard_dominant_profile',v['issues'])

    def test_clean_wave_and_last_master_pass_both_directions(self):
        for mirror in [False,True]:
            p=reviewed()
            if mirror:
                for b in p['series']['bars']:
                    o,h,l,c=[engine.Decimal(b[k]) for k in ['open','high','low','close']]
                    b.update(open=str(220-o),high=str(220-l),low=str(220-h),close=str(220-c))
                p['levels'][0].update(lower='106',upper='108',side='support')
            v=self.review(p)
            self.assertEqual(v['status'],'ordinary_contract_consistent',v)
            self.assertEqual(v['pivot_qualification']['latest_candidate_bar_id'],'6')

    def test_every_intervening_counter_bar_is_reviewed(self):
        p=reviewed(True);v=self.review(p)
        self.assertIn('unreviewed_counter_bar:5',v['pivot_qualification']['issues'])
        self.assertEqual(v['pivot_qualification']['wave']['inspected_bar_ids'],['4','5','6','7'])

    def test_shadow_master_colour_is_not_a_body_direction_rule(self):
        p=reviewed();p['series']['bars'][6].update(open='109',close='108.5')
        p['level_analysis']['evidence'][0]['preleg']['bar_ids']=['3','4','5']
        v=self.review(p)
        self.assertEqual(v['status'],'ordinary_contract_consistent',v)
        self.assertEqual(v['pivot_qualification']['master_geometry'],'dominant_shadow')
        self.assertEqual(v['pivot_qualification']['wave']['counter_bar_ids'],[])
        p.pop('pivot_review')
        self.assertEqual(self.review(p)['status'],'rejected_evidence')

    def test_shadow_master_is_not_a_third_directional_body(self):
        p=reviewed();p['series']['bars'][6].update(open='109',close='108.5')
        self.assertEqual(self.review(p)['status'],'rejected_evidence')

    def test_shadow_colour_branch_uses_same_numeric_boundary_tolerance(self):
        p=reviewed();p['series']['tick_size']='0.000000001'
        p['series']['bars'][6].update(open='109.000000001',close='108.5')
        p['level_analysis']['evidence'][0]['preleg']['bar_ids']=['3','4','5']
        self.assertEqual(self.review(p)['status'],'ordinary_contract_consistent')
        p['series']['bars'][6]['open']='109.000000010'
        self.assertNotEqual(self.review(p)['status'],'ordinary_contract_consistent')

    def test_late_cover_retains_level_but_not_timely_formation_label(self):
        from datetime import timedelta
        p=reviewed();bs=p['series']['bars'];event=copy.deepcopy(bs[-1]);start=engine.instant(event['open_time'],'t')
        bs[-1].update(high='113',low='109',close='110')
        for i in [8,9]:
            b=copy.deepcopy(bs[-1]);t=start+timedelta(minutes=15*(i-7))
            b.update(id=str(i),open_time=engine.stamp(t),close_time=engine.stamp(t+timedelta(minutes=15)),available_at=engine.stamp(t+timedelta(minutes=15)))
            bs.append(b)
        bs[-1].update(open=event['open'],high=event['high'],low=event['low'],close=event['close'])
        p['as_of']=bs[-1]['available_at'];p['levels'][0].update(origin_at=bs[-1]['open_time'],available_at=p['as_of'])
        p['level_analysis']['evidence'][0]['extreme_bar_id']='9'
        v=self.review(p);self.assertEqual(v['cover_timing'],'late');self.assertEqual(v['status'],'ordinary_contract_consistent')

    def test_nonconsecutive_is_allowed_with_explicit_no_pivot_context(self):
        p=reviewed(True);annotate(p)
        self.assertEqual(self.review(p)['status'],'ordinary_contract_consistent')

    def test_settlement_needs_actual_later_close_bypass(self):
        p=reviewed(True);annotate(p,'settlement_bypassed',bypass_bar_id='6')
        n=p['level_analysis']['evidence'][0]['wave_review']['noise'][0]
        n['available_at']=p['series']['bars'][6]['available_at']
        self.assertEqual(self.review(p)['status'],'ordinary_contract_consistent')
        n['bypass_bar_id']='5'
        self.assertIn('settlement_bypass_must_be_later_and_in_preleg:5',self.review(p)['issues'])

    def test_settlement_equality_and_wick_are_not_close_bypass(self):
        p=reviewed(True);annotate(p,'settlement_bypassed',bypass_bar_id='6')
        p['series']['bars'][5]['high']='108'
        p['level_analysis']['evidence'][0]['wave_review']['noise'][0]['available_at']=p['series']['bars'][6]['available_at']
        self.assertIn('settlement_bypass_not_proven_by_closed_price:5',self.review(p)['pivot_qualification']['issues'])

    def test_no_pivot_assertion_cannot_hide_strict_cover(self):
        p=reviewed(True);p['series']['bars'][5].update(low='99',close='99');annotate(p)
        self.assertIn('no_pivot_claim_conflicts_with_master_cover:5',self.review(p)['pivot_qualification']['issues'])

    def test_late_noise_review_changes_effective_clock(self):
        p=reviewed(True);annotate(p)
        # Use a relative timestamp; do not rely on the fixture calendar.
        from datetime import timedelta
        late=engine.stamp(engine.instant(p['as_of'],'t')+timedelta(hours=1))
        p['as_of']=late;p['level_analysis']['evidence'][0]['wave_review']['noise'][0]['available_at']=late
        v=self.review(p)
        self.assertEqual(v['effective_available_at'],late)
        self.assertIn('level_availability_predates_wave_review',v['issues'])

    def test_future_or_backdated_noise_cannot_qualify(self):
        from datetime import timedelta
        for delta in [-1,24]:
            p=reviewed(True);annotate(p);n=p['level_analysis']['evidence'][0]['wave_review']['noise'][0]
            n['available_at']=engine.stamp(engine.instant(n['available_at'],'t')+timedelta(hours=delta))
            self.assertEqual(self.review(p)['status'],'needs_pivot_review')

    def test_late_wave_review_cannot_remove_known_nearest_obstacle(self):
        from datetime import timedelta
        p=reviewed(True);annotate(p)
        late=engine.stamp(engine.instant(p['as_of'],'t')+timedelta(hours=1))
        p['as_of']=late;p['level_analysis']['evidence'][0]['wave_review']['noise'][0]['available_at']=late
        p['levels'].append(level(p,'far','120','125'))
        p['level_analysis']['target_request']=dict(direction='up',selection_bar_id=p['series']['bars'][-1]['id'])
        target=self.out(p)['level_analysis']['target_selection']
        self.assertEqual(target['selected']['id'],'pivot')
        self.assertEqual(target['status'],'needs_structure_review')
        self.assertFalse(any(e['id']=='pivot' for e in target['exclusions']))

    def test_duplicate_and_out_of_span_annotations(self):
        p=reviewed(True);annotate(p)
        ns=p['level_analysis']['evidence'][0]['wave_review']['noise'];ns.append(copy.deepcopy(ns[0]))
        self.assertEqual(engine.analyze(p)['issues'][0]['code'],'duplicate_noise_review')
        ns.pop();ns[0]['bar_id']='4'
        self.assertIn('noise_annotation_outside_counter_bar_span',self.review(p)['issues'])

    def test_stale_master_not_silently_replaced(self):
        p=reviewed();e=p['level_analysis']['evidence'][0];e['master_bar_id']='5'
        e['preleg']=dict(route='atr_displacement',start_bar_id='4',end_bar_id='5',atr_bar_id='4',threshold_atr='1')
        v=self.review(p)
        self.assertEqual(v['pivot_qualification']['latest_candidate_bar_id'],'6')
        self.assertIn('later_master_candidate_requires_reselection',v['pivot_qualification']['issues'])
        self.assertEqual(p['level_analysis']['evidence'][0]['master_bar_id'],'5')

    def test_custom_atr_threshold_is_not_trex_default(self):
        p=ordinary();p['pivot_review']=dict(profile='source_linked_pivots_v1')
        p['level_analysis']['evidence'][0]['preleg']['threshold_atr']='2.5'
        self.assertIn('custom_atr_threshold_requires_method_review',self.review(p)['issues'])

    def test_unreviewed_nearest_obstacle_is_preserved(self):
        p=fixture();p['pivot_review']=dict(profile='source_linked_pivots_v1')
        r=self.out(p);h=r['target_hierarchy']
        self.assertEqual(h['nearest_obstacle']['selected']['id'],'structural')
        self.assertFalse(h['direct_path_clear'])
        self.assertFalse(r['method_scope']['structural_target_contract_consistent'])

    def test_future_prices_do_not_replace_completed_reference(self):
        p=reviewed();original=self.review(p)
        future=copy.deepcopy(p['series']['bars'][-1])
        from datetime import timedelta
        t=engine.instant(future['close_time'],'t')
        future.update(id='future',open_time=engine.stamp(t),close_time=engine.stamp(t+timedelta(minutes=15)),
                      available_at=engine.stamp(t+timedelta(minutes=15)),open='200',high='210',low='190',close='209')
        p['series']['bars'].append(future)
        self.assertEqual(original,self.review(p))
        p['as_of']=future['available_at']
        self.assertEqual(original,self.review(p))

    def exception(self,family='self_cover'):
        p=reviewed();e=p['level_analysis']['evidence'][0]
        e.update(profile='exception_candidate_v1',event_bar_id='7',family=family)
        e.pop('master_bar_id');e.pop('extreme_bar_id')
        return p

    def test_exception_shape_never_grants_exemption(self):
        p=self.exception();v=self.review(p)
        self.assertEqual(v['status'],'needs_exception_review')
        self.assertFalse(v['confirmation_exemption_granted']);self.assertIsNone(v['confirmed_at'])

    def test_exception_does_not_count_itself_as_third_preleg_body(self):
        p=self.exception();p['level_analysis']['evidence'][0]['preleg']['bar_ids']=['5','6','7']
        self.assertIn('preleg_order_or_end_invalid',self.review(p)['issues'])

    def test_self_cover_colour_must_oppose_incoming_wave(self):
        p=self.exception();p['series']['bars'][7].update(open='106',close='112')
        v=self.review(p);self.assertFalse(v['shape_checks']['reverse_body'])
        self.assertFalse(v['shape_checks']['self_cover_screen'])

    def test_wick_to_body_and_half_atr_remain_explicit(self):
        p=self.exception('long_shadow');p['levels'][0]['lower']='111'
        self.assertIn('exception_zone_bounds_disagree',self.review(p)['issues'])

    def test_input_profile_and_wave_extension_cannot_be_silently_ignored(self):
        p=reviewed(True);annotate(p);p.pop('pivot_review')
        self.assertEqual(engine.analyze(p)['issues'][0]['code'],'wave_review_requires_pivot_review_profile')
        p['pivot_review']=dict(profile='unknown')
        self.assertEqual(engine.analyze(p)['status'],'unsupported')


if __name__=='__main__':unittest.main()
