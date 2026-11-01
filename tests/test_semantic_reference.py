"""Behavioral checks from source review: causal eligibility and method scope."""
import copy
import importlib.util
import json
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('semantic_engine', ROOT/'skills/trade-chart-scenarios/scripts/analyze.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

def sample():
    return json.loads((ROOT/'skills/trade-chart-scenarios/assets/example-input.json').read_text())

class SemanticReferenceTests(unittest.TestCase):
    def test_late_knowledge_cannot_create_prospective_continuation(self):
        for part in ['anchor', 'target', 'context']:
            with self.subTest(part=part):
                p = sample()
                if part == 'context':
                    p['assessment']['context_available_at'] = '2026-10-25T01:30:00Z'
                else:
                    p['levels'][0 if part == 'anchor' else 1]['available_at'] = '2026-10-25T01:30:00Z'
                out = engine.analyze(p)
                self.assertEqual(out['status'], 'confirmed_under_selected_profile')
                self.assertNotIn('continuation', [x['kind'] for x in out['conditional_scenarios']])
                self.assertEqual(out['assessment']['historical_context_gate']['status'], 'retrospective_only')
                self.assertFalse(out['assessment']['historical_context_gate']['prospective_eligible'])

    def test_knowledge_exactly_at_origin_is_eligible_not_before_it(self):
        p = sample()
        for level in p['levels']: level['available_at'] = '2026-10-25T01:00:00Z'
        p['assessment']['context_available_at'] = '2026-10-25T01:00:00Z'
        out = engine.analyze(p)
        self.assertIn('continuation', [x['kind'] for x in out['conditional_scenarios']])
        self.assertTrue(out['assessment']['historical_context_gate']['prospective_eligible'])

    def test_bearish_late_target_does_not_bypass_time_gate(self):
        p = sample()
        for b in p['series']['bars']:
            old = dict(b)
            for k, other in [('open','open'),('close','close'),('high','low'),('low','high')]:
                b[k] = str(Decimal(200)-Decimal(old[other]))
        for level in p['levels']:
            lo, hi = level['lower'], level['upper']
            level.update(lower=str(200-Decimal(hi)), upper=str(200-Decimal(lo)), side='support')
        p['assessment']['direction'] = 'down'
        p['levels'][1]['available_at'] = '2026-10-25T01:30:00Z'
        out = engine.analyze(p)
        self.assertNotIn('continuation', [x['kind'] for x in out['conditional_scenarios']])

    def test_requested_future_or_missing_target_is_not_known_at_origin(self):
        for future in [False, True]:
            with self.subTest(future=future):
                p = sample()
                if future:
                    p['levels'][1]['available_at'] = '2026-10-25T03:00:00Z'
                else:
                    p['levels'] = p['levels'][:1]
                out = engine.analyze(p)
                gate = out['assessment']['historical_context_gate']
                self.assertEqual(gate['status'], 'dependencies_unavailable')
                self.assertFalse(gate['prospective_eligible'])
                self.assertIn('target', gate['unavailable_dependencies'])
                self.assertIsNone(out['assessment']['next_known_level'])
                self.assertNotIn('continuation', [x['kind'] for x in out['conditional_scenarios']])

    def test_invalidation_is_not_hidden_by_retrospective_context(self):
        p = sample()
        p['levels'][1]['available_at'] = '2026-10-25T01:30:00Z'
        b = dict(p['series']['bars'][-1], id='after', open_time='2026-10-25T01:45:00Z',
                 close_time='2026-10-25T02:00:00Z', available_at='2026-10-25T02:00:00Z',
                 open='105', high='106', low='90', close='91')
        p['series']['bars'].append(b); p['as_of'] = b['close_time']
        out = engine.analyze(p)
        self.assertEqual(out['assessment']['scenario_status'], 'invalidated')
        self.assertEqual(out['assessment']['historical_context_gate']['status'], 'retrospective_only')

    def test_confirmed_geometry_does_not_claim_full_method_validation(self):
        out = engine.analyze(sample())
        scope = out['method_scope']
        self.assertFalse(scope['full_method_validated'])
        self.assertEqual(scope['pivot_method'], 'trex_primary_macd_fibonacci_auxiliary')
        self.assertEqual(scope['target_review_scope'], 'same_timeframe_only')
        self.assertFalse(scope['structural_target_validated'])

    def test_aligned_real_roles_still_need_structural_target_review(self):
        p = json.loads((ROOT/'tests/structure_market/data/ETHUSDT.json').read_text())
        p['as_of'] = '2020-02-08T00:00:00Z'
        out = engine.analyze(p)
        self.assertEqual(out['structural_context']['status'], 'aligned')
        self.assertFalse(out['structural_context']['scenario_ready'])
        self.assertEqual(out['structural_context']['scenario_ready_scope'], 'engineering_profile_only')
        self.assertEqual(out['method_scope']['required_structural_target_timeframe'], 'D1')
        self.assertFalse(out['method_scope']['structural_target_validated'])

if __name__ == '__main__': unittest.main()
