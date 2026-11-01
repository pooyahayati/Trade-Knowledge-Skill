"""Regressions from teacher-media review and fresh-chart failure reproductions."""
import copy
import unittest
from datetime import timedelta

from test_analysis import engine, sample, append_bar
from test_levels_targets import payload, level
from test_pivot_review import reviewed, annotate


def validation(p):
    r=engine.analyze(p)
    assert not r['issues'], r['issues']
    return r['level_analysis']['levels'][0]['validation']


class MediaRevisionTests(unittest.TestCase):
    def test_split_shadows_are_reviewed_without_changing_legacy_geometry(self):
        for opposite in [False,True]:
            p=reviewed()
            p['series']['bars'][6].update(open='110.6' if opposite else '110.5',
                                         close='110.5' if opposite else '110.6')
            p['level_analysis']['evidence'][0]['preleg']['bar_ids']=['3','4','5']
            r=engine.analyze(p);v=r['level_analysis']['levels'][0]['validation']
            self.assertFalse(r['candles'][6]['standard_dominant_candidate'])
            self.assertEqual(v['status'],'needs_pivot_review')
            m=v['pivot_qualification']['media_shape_review']
            self.assertTrue(m['split_shadow_candidate'])
            self.assertFalse(m['automatic_master_selection_allowed'])
            self.assertFalse(m['source_threshold_inclusivity_verified'])
            self.assertFalse(m['profile_failure_rejects_whole_method'])

    def test_intervening_split_shadow_cannot_be_silently_skipped(self):
        p=reviewed();p['series']['bars'][6].update(open='110.5',close='110.6')
        p['series']['bars'][7].update(low='102',close='103')
        e=p['level_analysis']['evidence'][0]
        e.update(master_bar_id='5',preleg=dict(route='three_standard_bars',bar_ids=['3','4','5']))
        v=validation(p)
        self.assertEqual(v['arithmetic_status'],'ordinary_contract_consistent')
        self.assertEqual(v['status'],'needs_pivot_review')
        self.assertEqual(v['pivot_qualification']['split_shadow_candidate_ids'],['6'])

    def test_no_pivot_claim_needs_review_after_split_shadow_cover(self):
        p=reviewed(True)
        p['series']['bars'][4].update(open='100',close='100.2',low='98',high='103')
        p['series']['bars'][5].update(close='97',low='96')
        annotate(p)
        self.assertIn('no_pivot_claim_requires_split_shadow_review:5',
                      validation(p)['pivot_qualification']['wave']['issues'])

    def test_media_diagnostic_needs_positive_range_and_standard_class(self):
        for baseline in [None,engine.Decimal(0),engine.Decimal(100)]:
            row=engine.candle_geometry(dict(open=engine.Decimal(100),high=engine.Decimal(103),
                low=engine.Decimal(98),close=engine.Decimal('100.2')),baseline)
            self.assertFalse(engine.PIVOTS.media_master_review(row)['split_shadow_candidate'])

    def test_missing_level_history_is_unknown_and_obstacle_is_retained(self):
        p=payload();p['levels']=[level(p,'old','110'),level(p,'far','120')]
        p['levels'][0].update(origin_at='2019-12-31T00:00:00Z',available_at='2019-12-31T01:00:00Z')
        p['level_analysis']['target_request']=dict(direction='up',selection_bar_id='8')
        r=engine.analyze(p)['level_analysis']
        self.assertEqual(r['levels'][0]['lifecycle']['status'],'needs_level_history')
        t=r['target_selection']
        self.assertEqual(t['selected']['id'],'old')
        self.assertEqual(t['status'],'needs_timeframe_review')
        self.assertIsNone(r['levels'][0]['lifecycle']['touch_episodes'])

    def test_invalidation_clock_waits_for_delayed_prefix(self):
        p=sample();append_bar(p,o='110',h='111',l='105',c='110')
        p['series']['bars'][-1]['id']='delayed-middle'
        append_bar(p,l='90',c='91')
        nominal=p['as_of']
        late=engine.stamp(engine.instant(nominal,'t')+timedelta(hours=1))
        p['series']['bars'][-2]['available_at']=late
        early=engine.analyze(p)
        self.assertIsNone(early['assessment']['invalidated_at'])
        p['as_of']=late;r=engine.analyze(p)
        self.assertEqual(r['assessment']['invalidation_available_at'],late)
        self.assertEqual(r['assessment']['invalidated_at'],nominal)

    def test_target_touch_before_usable_confirmation_is_not_after_confirmation(self):
        p=sample();p['series']['bars'][-1]['available_at']='2026-10-25T02:05:00Z'
        append_bar(p,o='110',h='121',l='109',c='120')
        p['as_of']='2026-10-25T02:05:00Z'
        a=engine.analyze(p)['assessment']
        self.assertEqual(a['target_touch']['phase'],'by_confirmation')
        self.assertEqual(a['target_touch']['available_at'],p['as_of'])
        self.assertFalse(any(c['kind']=='continuation' for c in a['conditional_scenarios']))

    def test_level_role_at_earlier_cutoff_cannot_skip_a_delayed_middle_bar(self):
        p=payload();p['levels']=[level(p,'known-resistance','110')]
        p['series']['bars'][5]['available_at']=p['as_of']
        p['series']['bars'][6].update(open='109',high='112',low='108',close='111')
        as_of=engine.instant(p['as_of'],'t')
        _,_,bars,_=engine.parse_series(p['series'],as_of)
        parsed=engine.parse_levels(p['levels'],as_of,'M15')['known-resistance']
        cutoff=engine.instant(p['series']['bars'][6]['close_time'],'t')
        state=engine.level_lifecycle(parsed,bars,cutoff,'M15')
        self.assertEqual(state['status'],'needs_level_history')
        self.assertIsNone(state['broken_at'])
        later=engine.level_lifecycle(parsed,bars,as_of,'M15')
        self.assertEqual(later['status'],'broken_original_role')
        self.assertEqual(later['break_available_at'],p['as_of'])

    def test_reference_quotes_are_not_renamed_to_executable_price_bases(self):
        for basis in ['indicative_quote','aggregate_reference']:
            p=sample();p['series']['price_basis']=basis;r=engine.analyze(p)
            self.assertFalse(r['issues'])
            self.assertEqual(r['data_scope']['price_basis'],basis)
            self.assertEqual(r['data_scope']['price_basis_scope'],'non_executable_reference')
            self.assertFalse(r['data_scope']['execution_quote_verified'])
            self.assertIsNone(r['order'])
        p=sample();p['series']['price_basis']='guessed_bid'
        self.assertEqual(engine.analyze(p)['issues'][0]['code'],'unsupported_market_or_price_basis')


if __name__=='__main__':unittest.main()
