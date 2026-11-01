"""Licensed real examples from a frozen independent study; no trade-success claim."""
import copy,hashlib,json,unittest
from test_analysis import engine,SKILL
from test_analysis_watch import W
BANK=SKILL/'assets/scenario-study-0.17'
def read(p):return json.loads(p.read_text(encoding='utf-8'))

def watch_updates(prefix,frozen):
    # The independent oracle stores per-step new_bars, while the journal wire
    # format groups exact new bars by series. Only the container is adapted.
    series=[]
    for tf,s in W.series_map(prefix).items():
        bars=[copy.deepcopy(b) for step in frozen['updates'] for b in step['new_bars'].get(tf,[])]
        assert len(bars)==len({b['id'] for b in bars})
        series.append({**W.metadata(s),'bars':bars})
    return {'series':series}

class ExtendedScenarios(unittest.TestCase):
    def test_frozen_real_labels_and_inputs_are_unchanged(self):
        for c in read(BANK/'manifest.json')['cases']:
            for name,digest in c['files'].items():
                self.assertEqual(hashlib.sha256((BANK/c['id']/name).read_bytes()).hexdigest(),digest)
        a=read(BANK/'manifest.json')['postfreeze_adjudication']
        self.assertEqual(hashlib.sha256((BANK/a['file']).read_bytes()).hexdigest(),a['sha256'])

    def test_geometric_confirmation_does_not_hide_context_blockers(self):
        for c in read(BANK/'manifest.json')['cases']:
            folder=BANK/c['id'];expected=read(folder/'expected.json')
            projection=next(x for x in read(BANK/'lifecycle-adjudication.json')['cases'] if x['case_id']==c['original_case_id'])
            for point,input_name in [('confirmation_snapshot','confirmation-input.json'),('final','horizon-input.json')]:
                with self.subTest(case=c['id'],point=point):
                    r=engine.analyze(read(folder/input_name));e=expected[point]
                    self.assertEqual(r['issues'],[])
                    adjudicated=next(x for x in projection['steps'] if x['as_of']==e['as_of'])
                    self.assertEqual(adjudicated['frozen_original_strict_status'],e['strict_status'])
                    self.assertEqual(r['assessment']['status'],adjudicated['projected_profile_status'])
                    event=e['historical_confirmation']
                    self.assertEqual(r['assessment']['confirmed_at'],event['close_time'] if event else None)
                    self.assertFalse(r['structural_context']['scenario_ready'])
                    self.assertIsNone(r['order'])

    def test_historical_replay_keeps_all_twelve_bars_and_never_grants_entry(self):
        for c in read(BANK/'manifest.json')['cases']:
            folder=BANK/c['id']
            p=read(folder/'prefix-input.json')
            r=W.evaluate(W.freeze(p,12),watch_updates(p,read(folder/'future-updates.json')))
            self.assertEqual(r['missing_entry_opens'],[])
            self.assertEqual(len(r['snapshots']),12)
            self.assertIsNone(r['activation'])
            self.assertFalse(r['entry_permission'])

if __name__=='__main__':unittest.main()
