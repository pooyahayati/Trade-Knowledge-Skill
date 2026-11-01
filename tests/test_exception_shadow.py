"""Trex 256: event shadow extends ATR measurement, never a third body."""
import copy
import unittest
from datetime import timedelta
from test_pivot_review import reviewed
from test_levels_targets import engine


def shadow_event():
    p=reviewed(); b=p['series']['bars'][-1]; b['high']='119'
    e=p['level_analysis']['evidence'][0]
    e.update(profile='exception_candidate_v1',event_bar_id='7',family='self_cover',
        preleg=dict(route='atr_displacement',start_bar_id='5',end_bar_id='7',atr_bar_id='5',threshold_atr='3'))
    e.pop('master_bar_id'); e.pop('extreme_bar_id')
    p['levels'][0]['upper']='119'
    return p


def review(p):
    r=engine.analyze(p)
    assert not r['issues'],r['issues']
    return r['level_analysis']['levels'][0]['validation']


class ExceptionShadowTests(unittest.TestCase):
    def test_event_shadow_reaches_three_atr_both_directions(self):
        for mirror in [False,True]:
            p=shadow_event()
            if mirror:
                for b in p['series']['bars']:
                    o,h,l,c=[engine.Decimal(b[k]) for k in ['open','high','low','close']]
                    b.update(open=str(240-o),high=str(240-l),low=str(240-h),close=str(240-c))
                p['levels'][0].update(side='support',lower='121',upper='128')
            v=review(p); w=v['wave']
            self.assertEqual(w['status'],'consistent_with_supplied_context',w)
            self.assertEqual(w['inspected_bar_ids'],['5','6'])
            self.assertEqual(w['measurement']['end_bar_id'],'7')
            self.assertEqual(engine.Decimal(w['measurement']['distance']),15)
            self.assertTrue(v['shape_checks']['self_cover_screen'])
            self.assertEqual(v['status'],'needs_exception_review')
            self.assertFalse(v['confirmation_exemption_granted'])
            self.assertIsNone(v['confirmed_at'])

    def test_event_shadow_is_opted_into_by_endpoint(self):
        p=shadow_event();p['level_analysis']['evidence'][0]['preleg']['end_bar_id']='6'
        self.assertIn('preleg_does_not_meet_selected_route',review(p)['issues'])

    def test_event_cannot_supply_third_directional_body(self):
        p=shadow_event();p['level_analysis']['evidence'][0]['preleg']=dict(route='three_standard_bars',bar_ids=['5','6','7'])
        self.assertIn('preleg_order_or_end_invalid',review(p)['issues'])

    def test_event_cannot_be_entire_incoming_wave(self):
        p=shadow_event();p['level_analysis']['evidence'][0]['preleg']['start_bar_id']='7'
        self.assertIn('preleg_order_or_end_invalid',review(p)['issues'])

    def test_other_exception_families_do_not_inherit_source_exemption(self):
        for family in ['pinbar','long_shadow']:
            p=shadow_event();p['level_analysis']['evidence'][0]['family']=family
            self.assertIn('preleg_order_or_end_invalid',review(p)['issues'])

    def test_event_without_incoming_tail_cannot_extend_wave(self):
        p=shadow_event();p['series']['bars'][-1]['high']='112'
        self.assertIn('event_shadow_requires_incoming_tail',review(p)['issues'])

    def test_intervening_counter_body_still_needs_review(self):
        p=shadow_event();p['series']['bars'][6]['close']='108'
        self.assertIn('unreviewed_counter_bar:6',review(p)['issues'])

    def test_event_does_not_bypass_settlement_inside_wave(self):
        p=shadow_event();p['series']['bars'][6]['close']='108'
        p['level_analysis']['evidence'][0]['wave_review']=dict(noise=[dict(bar_id='6',classification='settlement_bypassed',
            bypass_bar_id='7',source_ref='synthetic',available_at=p['as_of'])])
        self.assertIn('settlement_bypass_must_be_later_and_in_preleg:6',review(p)['issues'])

    def test_open_or_unavailable_event_cannot_contribute(self):
        for field,value in [('is_closed',False),('available_at','2020-01-02T00:00:00Z')]:
            p=shadow_event();p['series']['bars'][-1][field]=value
            self.assertIn('closed_own_timeframe_exception_and_context_required',review(p)['issues'])

    def test_measurement_clock_and_future_invariance(self):
        p=shadow_event();v=review(p)
        self.assertEqual(v['wave']['measurement']['available_at'],p['as_of'])
        self.assertEqual(v['effective_available_at'],p['as_of'])
        b=copy.deepcopy(p['series']['bars'][-1]);start=engine.instant(p['as_of'],'t')
        b.update(id='future',open_time=engine.stamp(start),close_time=engine.stamp(start+timedelta(minutes=15)),
                 available_at=engine.stamp(start+timedelta(minutes=15)),open='200',high='220',low='199',close='210')
        p['series']['bars'].append(b)
        self.assertEqual(v,review(p))
        p['as_of']=b['available_at']
        self.assertEqual(v,review(p))


if __name__=='__main__':unittest.main()
