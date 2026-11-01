"""Source scope, subtype preconditions and causal pinbar diagnostics."""
import copy
import unittest
from datetime import timedelta
from decimal import Decimal as D
from test_levels_targets import engine
from test_exception_shadow import shadow_event,review


def pinbar(noise='0',mirror=False):
    p=shadow_event();b=p['series']['bars'][-1]
    b.update(open=str(D('100')+D(noise)),high='110',low='100',close='101.9')
    e=p['level_analysis']['evidence'][0]
    e.update(family='pinbar',preleg=dict(route='three_standard_bars',bar_ids=['4','5','6']))
    p['levels'][0].update(lower=str(max(D(b['open']),D(b['close']))),upper='110')
    if mirror:
        for b in p['series']['bars']:
            o,h,l,c=[D(b[k]) for k in ['open','high','low','close']]
            b.update(open=str(240-o),high=str(240-l),low=str(240-h),close=str(240-c))
        l=p['levels'][0];l.update(side='support',lower=str(240-D(l['upper'])),upper=str(240-D(l['lower'])))
    return p


def result(p):return review(p)['pinbar_review']


class PinbarConfirmationTests(unittest.TestCase):
    def test_two_directions_have_same_noise_free_subtype(self):
        for mirror in [False,True]:
            v=review(pinbar(mirror=mirror));d=v['pinbar_review']
            self.assertEqual(d['subtype'],'noise_free_pinbar')
            self.assertEqual(d['noise_state'],'zero')
            self.assertTrue(v['noise_free_pin_requires_confirmation_review'])
            self.assertEqual(d['confirmation_requirement'],'unresolved')

    def test_small_nonzero_noise_is_never_rounded_to_zero(self):
        p=pinbar('0.000000001');p['series']['tick_size']='0.000000001'
        d=result(p)
        self.assertEqual(d['subtype'],'pinbar_with_noise')
        self.assertEqual(d['noise_state'],'present_within_limit')

    def test_tenth_boundary_and_just_above(self):
        for mirror in [False,True]:
            for noise,expected in [('0.9','pinbar_with_noise'),('1','pinbar_with_noise'),('1.1',None)]:
                with self.subTest(mirror=mirror,noise=noise):
                    d=result(pinbar(noise,mirror));self.assertEqual(d['subtype'],expected)
                    if expected is None:self.assertIn('opposite_shadow_at_most_tenth',d['failed_conditions'])

    def test_noise_limit_uses_candle_range_not_atr(self):
        p=pinbar('0.9');v=review(p)
        m=v['exception_review']['measurements'];self.assertGreater(D(m['opposite_shadow']),D(m['baseline_atr'])/10)
        self.assertEqual(v['pinbar_review']['classification'],'matched_geometry')

    def test_preexisting_numerical_tolerance_is_preserved(self):
        p=pinbar('1.000000005');p['series']['tick_size']='0.000000001'
        v=review(p);self.assertTrue(v['shape_checks']['workbook_pinbar'])
        self.assertEqual(v['pinbar_review']['subtype'],'pinbar_with_noise')
        p['series']['bars'][-1]['open']='101.00000002'
        self.assertEqual(result(p)['noise_state'],'over_limit')

    def test_body_color_does_not_change_subtype(self):
        for mirror in [False,True]:
            p=pinbar('0.5',mirror);before=result(p)
            b=p['series']['bars'][-1];b['open'],b['close']=b['close'],b['open']
            self.assertEqual(before,result(p))

    def test_doji_is_not_invented_as_an_exemption(self):
        p=pinbar();p['series']['bars'][-1]['close']='100'
        d=result(p);self.assertEqual(d['subtype'],'noise_free_pinbar')
        self.assertFalse(d['confirmation_exemption_granted'])

    def test_non_pinbar_zero_noise_does_not_receive_subtype_flag(self):
        p=pinbar();p['series']['bars'][-1]['close']='105'
        v=review(p);self.assertTrue(v['no_noise_wick'])
        self.assertFalse(v['shape_checks']['workbook_pinbar'])
        self.assertFalse(v['noise_free_pin_requires_confirmation_review'])
        self.assertIsNone(v['pinbar_review']['subtype'])
        self.assertIn('open_close_in_side_extreme_fifth',v['pinbar_review']['failed_conditions'])

    def test_zero_and_missing_atr_are_unknown_not_subtypes(self):
        for kind in ['zero','missing']:
            p=pinbar()
            if kind=='missing':p['series']['atr_period']=50
            else:
                for b in p['series']['bars'][:-1]:b.update(open='100',high='100',low='100',close='100')
            v=review(p);d=v['pinbar_review']
            self.assertEqual(d['classification'],'insufficient_data')
            self.assertIsNone(d['subtype']);self.assertFalse(v['noise_free_pin_requires_confirmation_review'])

    def test_flat_event_cannot_be_a_noise_free_pinbar(self):
        p=pinbar();p['series']['bars'][-1].update(open='100',high='100',low='100',close='100')
        p['levels'][0].update(lower='100',upper='100')
        d=result(p);self.assertEqual(d['noise_state'],'not_computable');self.assertIsNone(d['subtype'])

    def test_wrong_level_direction_cannot_be_a_matched_pinbar(self):
        p=pinbar();p['levels'][0]['side']='support'
        d=result(p);self.assertIsNone(d['subtype'])
        self.assertIn('incoming_shadow_dominates',d['failed_conditions'])

    def test_future_context_open_event_and_wrong_timeframe_are_withheld(self):
        for kind in ['context','open','timeframe','history']:
            p=pinbar()
            if kind=='context':p['level_analysis']['evidence'][0]['context_available_at']='2020-01-02T00:00:00Z'
            elif kind=='open':p['series']['bars'][-1]['is_closed']=False
            elif kind=='timeframe':p['levels'][0]['timeframe']='H1'
            else:p['level_analysis']['evidence'][0]['event_bar_id']='0'
            d=result(p);self.assertEqual(d['classification'],'insufficient_data')
            self.assertEqual(d['noise_state'],'not_computable');self.assertIsNone(d['subtype'])

    def test_later_close_cannot_supply_an_undefined_confirmation_rule(self):
        p=pinbar();before=result(p);t=engine.instant(p['as_of'],'t')
        b=copy.deepcopy(p['series']['bars'][-1]);b.update(id='future',open_time=engine.stamp(t),
            close_time=engine.stamp(t+timedelta(minutes=15)),available_at=engine.stamp(t+timedelta(minutes=15)),
            open='100',high='101',low='89',close='90')
        p['series']['bars'].append(b)
        self.assertEqual(result(p),before)
        p['as_of']=b['available_at'];v=review(p);self.assertEqual(v['pinbar_review'],before)
        self.assertIsNone(v['confirmed_at']);self.assertFalse(v['confirmation_exemption_granted'])

    def test_shape_pass_never_proves_trigger_entry(self):
        for noise in ['0','0.5']:
            d=result(pinbar(noise));self.assertFalse(d['trigger_entry_verified'])
            self.assertFalse(d['intrabar_path_known']);self.assertFalse(d['confirmation_rule_verified'])
            self.assertIn('closed_trigger_timeframe_entry_setup',d['next_checks'])

    def test_correct_workbook_reference_is_in_shape_gate(self):
        d=review(pinbar())['exception_review']['gates']['shape']
        self.assertIn('X-CANDLE-B8',d['source_refs']);self.assertNotIn('X-CANDLE-B7',d['source_refs'])

    def test_other_families_do_not_receive_pinbar_conditions(self):
        for family in ['self_cover','long_shadow']:
            p=pinbar();p['level_analysis']['evidence'][0]['family']=family;v=review(p)
            self.assertNotIn('pinbar_review',v)
            self.assertNotIn('opposite_shadow_at_most_tenth',v['exception_review']['conditions'])


if __name__=='__main__':unittest.main()
