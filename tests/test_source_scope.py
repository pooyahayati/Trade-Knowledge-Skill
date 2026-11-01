"""Source-profile divergence cannot silently promote geometry to a trade."""
import unittest
from decimal import Decimal as D
from test_exception_shadow import shadow_event,review
from test_pinbar_confirmation import pinbar


class SourceScopeTests(unittest.TestCase):
    def test_between_text_and_workbook_thresholds_stays_unconfirmed(self):
        p=shadow_event();p['level_analysis']['evidence'][0]['family']='long_shadow'
        baseline=D(review(p)['exception_review']['measurements']['baseline_atr'])
        b=p['series']['bars'][-1]
        b.update(open='110',close='109',low='108',high=str(D('110')+baseline*D('.8')))
        p['series']['tick_size']='0.000000000000000001'
        p['levels'][0].update(lower='110',upper=b['high'])
        v=review(p);s=v['source_scope_review']
        self.assertFalse(v['shape_checks']['long_shadow'])
        self.assertEqual(s['textual_variant']['relation_to_0_6_atr'],'above')
        self.assertFalse(s['profile_failure_rejects_whole_method'])
        self.assertFalse(v['confirmation_exemption_granted'])
        self.assertIn('X-CANDLE-B7',v['exception_review']['gates']['shape']['source_refs'])

    def test_textual_threshold_has_no_invented_inclusivity(self):
        # Test the semantic boundary independently of price/grid fixtures.
        import importlib.util
        from pathlib import Path
        path=Path(__file__).resolve().parents[1]/'skills/trade-chart-scenarios/scripts/pivots.py'
        spec=importlib.util.spec_from_file_location('scope_pivots',path)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        for tail,expected in [('0.59','below'),('0.6','equal'),('0.61','above')]:
            d=m.source_scope_review('long_shadow',D(tail),D(1))['textual_variant']
            self.assertEqual(d['relation_to_0_6_atr'],expected)
            self.assertFalse(d['threshold_inclusivity_verified'])
            self.assertFalse(d['automatic_confirmation_allowed'])

    def test_known_exemption_is_distinct_from_candidate_membership(self):
        v=review(pinbar());s=v['source_scope_review']
        self.assertTrue(s['exemption_principle']['source_supported'])
        self.assertFalse(s['exemption_principle']['candidate_membership_verified'])
        self.assertFalse(v['confirmation_exemption_granted'])
        self.assertEqual(s['contextual_exclusion']['status'],'not_evaluated')
        self.assertFalse(s['contextual_exclusion']['blanket_post_spike_ban'])

    def test_unavailable_geometry_has_no_variant_measurement(self):
        p=shadow_event();p['level_analysis']['evidence'][0]['family']='long_shadow'
        p['level_analysis']['evidence'][0]['context_available_at']='2030-01-01T00:00:00Z'
        s=review(p)['source_scope_review']
        self.assertIsNone(s['textual_variant']['shadow_atr_ratio'])
        self.assertIsNone(s['textual_variant']['relation_to_0_6_atr'])

if __name__=='__main__':unittest.main()
