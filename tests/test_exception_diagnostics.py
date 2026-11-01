"""Exceptional-pivot triage, not automatic method confirmation or trade signals."""
import copy
import unittest
from datetime import timedelta
from test_levels_targets import engine, ordinary
from test_exception_shadow import shadow_event, review


def flat_history():
    p=shadow_event()
    for b in p['series']['bars'][:-1]:
        b.update(open='100',high='100',low='100',close='100')
    p['series']['bars'][-1].update(open='100',high='105',low='100',close='100')
    p['levels'][0].update(lower='100',upper='105')
    p['level_analysis']['evidence'][0].update(family='long_shadow',preleg=dict(
        route='atr_displacement',start_bar_id='5',end_bar_id='6',atr_bar_id='5',threshold_atr='3'))
    return p


class ExceptionDiagnosticsTests(unittest.TestCase):
    def test_zero_atr_cannot_qualify_ordinary_wave(self):
        p=ordinary()
        for b in p['series']['bars'][:4]:
            b.update(open='100',high='100',low='100',close='100')
        v=engine.analyze(p)['level_analysis']['levels'][0]['validation']
        self.assertEqual(v['preleg_measurement']['baseline_atr'],'0')
        self.assertEqual(v['status'],'rejected_evidence')

    def test_zero_atr_is_not_a_valid_long_shadow_threshold(self):
        v=review(flat_history())
        self.assertIsNone(v['shape_checks']['long_shadow'])
        d=v['exception_review']
        self.assertEqual(d['gates']['shape']['status'],'insufficient_data')
        self.assertIn('positive_prior_atr',d['unknown_conditions'])
        self.assertIsNone(d['measurements']['incoming_shadow_atr'])

    def test_positive_displacement_cannot_use_zero_atr(self):
        p=flat_history();p['series']['bars'][6].update(high='101',close='101')
        v=review(p)
        self.assertIn('preleg_atr_not_positive',v['wave']['issues'])
        self.assertEqual(v['exception_review']['gates']['incoming_wave']['status'],'insufficient_data')

    def test_all_local_prerequisites_still_require_method_evidence(self):
        v=review(shadow_event());d=v['exception_review']
        self.assertEqual(d['decision'],'needs_method_confirmation')
        self.assertEqual(d['unresolved_gates'],['method_confirmation'])
        self.assertFalse(d['automatic_confirmation_allowed'])
        self.assertFalse(d['intrabar_path_known'])
        self.assertFalse(v['confirmation_exemption_granted'])
        self.assertIsNone(v['confirmed_at'])

    def test_four_source_diagram_relationships_in_both_directions(self):
        # Normalized synthetic geometry, not reconstructed source prices/ATR.
        shapes=[('112','112','99','106'),('106','112','99','112'),
                ('112','119','106','106'),('106','119','106','112')]
        for mirror in [False,True]:
            for j,(o,h,l,c) in enumerate(shapes):
                p=shadow_event()
                # Mirror the incoming rise to match the source's falling wave.
                for b in p['series']['bars']:
                    old=[engine.Decimal(b[k]) for k in ['open','high','low','close']]
                    b.update(open=str(240-old[0]),high=str(240-old[2]),low=str(240-old[1]),close=str(240-old[3]))
                p['levels'][0]['side']='support'
                p['series']['bars'][-1].update(open=o,high=h,low=l,close=c)
                if mirror:
                    for b in p['series']['bars']:
                        old=[engine.Decimal(b[k]) for k in ['open','high','low','close']]
                        b.update(open=str(240-old[0]),high=str(240-old[2]),low=str(240-old[1]),close=str(240-old[3]))
                    p['levels'][0]['side']='resistance'
                v=review(p);d=v['exception_review']
                self.assertEqual(v['shape_checks']['self_cover_screen'],j==1)
                if j in [0,2]:self.assertIn('reverse_body',d['failed_conditions'])
                if j==3:self.assertIn('incoming_shadow_present',d['failed_conditions'])
                self.assertFalse(v['confirmation_exemption_granted'])

    def test_long_shadow_does_not_inherit_self_cover_body_rule(self):
        p=shadow_event();e=p['level_analysis']['evidence'][0]
        e['family']='long_shadow';e['preleg']['end_bar_id']='6'
        for o,c in [('112','106'),('106','112')]:
            p['series']['bars'][-1].update(open=o,close=c)
            d=review(p)['exception_review']
            self.assertEqual(d['gates']['shape']['status'],'pass')
            self.assertNotIn('reverse_body',d['conditions'])

    def test_failed_wave_is_separate_from_matching_shape(self):
        p=shadow_event();p['level_analysis']['evidence'][0]['preleg']['end_bar_id']='6'
        d=review(p)['exception_review']
        self.assertEqual(d['gates']['shape']['status'],'pass')
        self.assertEqual(d['gates']['incoming_wave']['status'],'fail')
        self.assertEqual(d['decision'],'selected_profile_not_met')

    def test_missing_noise_context_is_not_a_proven_geometry_failure(self):
        p=shadow_event();p['series']['bars'][6]['close']='108'
        d=review(p)['exception_review']
        self.assertEqual(d['gates']['shape']['status'],'pass')
        self.assertEqual(d['gates']['incoming_wave']['status'],'needs_review')
        self.assertEqual(d['decision'],'needs_context_review')

    def test_bounds_and_origin_are_separate_from_shape(self):
        p=shadow_event();p['levels'][0].update(lower='111',origin_at=p['series']['bars'][6]['open_time'])
        d=review(p)['exception_review']
        self.assertEqual(d['gates']['shape']['status'],'pass')
        self.assertEqual(d['gates']['level_construction']['status'],'fail')
        self.assertEqual(d['measurements']['expected_bounds'],['112','119'])

    def test_future_context_withholds_geometry(self):
        p=shadow_event();e=p['level_analysis']['evidence'][0]
        e['context_available_at']=engine.stamp(engine.instant(p['as_of'],'t')+timedelta(hours=1))
        d=review(p)['exception_review']
        self.assertEqual(d['decision'],'insufficient_evidence')
        self.assertNotIn('measurements',d)

    def test_open_and_wrong_timeframe_events_withhold_geometry(self):
        for kind in ['open','timeframe','history']:
            p=shadow_event()
            if kind=='open':p['series']['bars'][-1]['is_closed']=False
            elif kind=='timeframe':p['levels'][0]['timeframe']='H1'
            else:p['level_analysis']['evidence'][0]['event_bar_id']='0'
            d=review(p)['exception_review']
            self.assertEqual(d['decision'],'insufficient_evidence')
            self.assertNotIn('measurements',d)

    def test_unavailable_atr_does_not_become_a_negative_shape_label(self):
        p=flat_history();p['series']['atr_period']=50
        v=review(p);d=v['exception_review']
        self.assertIsNone(v['shape_checks']['long_shadow'])
        self.assertIn('preleg_atr_unavailable',v['wave']['issues'])
        self.assertNotIn('requested_exception_shape_not_matched',v['issues'])
        self.assertIsNone(d['measurements']['incoming_shadow_atr'])

    def test_flat_event_cannot_pass_on_zero_threshold(self):
        p=flat_history();p['series']['bars'][-1]['high']='100';p['levels'][0]['upper']='100'
        d=review(p)['exception_review']
        self.assertIn('positive_range',d['failed_conditions'])
        self.assertEqual(d['decision'],'selected_profile_not_met')
        self.assertIsNone(d['measurements']['body_fraction'])

    def test_future_prices_do_not_change_review(self):
        p=shadow_event();before=review(p)
        b=copy.deepcopy(p['series']['bars'][-1]);t=engine.instant(p['as_of'],'t')
        b.update(id='future',open_time=engine.stamp(t),close_time=engine.stamp(t+timedelta(minutes=15)),
            available_at=engine.stamp(t+timedelta(minutes=15)),open='200',high='300',low='100',close='250')
        p['series']['bars'].append(b)
        self.assertEqual(before,review(p))
        p['as_of']=b['available_at'];self.assertEqual(before,review(p))

    def test_late_review_retains_obstacle_clock_and_flags_backdating(self):
        p=shadow_event();p['series']['bars'][6]['close']='108'
        late=engine.stamp(engine.instant(p['as_of'],'t')+timedelta(hours=1));known=p['as_of']
        p['as_of']=late;p['level_analysis']['evidence'][0]['wave_review']=dict(noise=[dict(
            bar_id='6',classification='no_pivot',source_ref='synthetic late contextual review',available_at=late)])
        v=review(p)
        self.assertEqual(v['obstacle_available_at'],known)
        self.assertEqual(v['effective_available_at'],late)
        self.assertEqual(v['exception_review']['gates']['availability']['status'],'fail')

    def test_geometric_pass_does_not_validate_order_node(self):
        p=shadow_event();p['levels'][0]['kind']='order_node';d=review(p)['exception_review']
        self.assertEqual(d['gates']['level_kind']['status'],'needs_review')
        self.assertFalse(d['automatic_confirmation_allowed'])


if __name__=='__main__':unittest.main()
