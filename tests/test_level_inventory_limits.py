"""Long-history obstacle inventory stays complete and bounded (synthetic)."""
import copy
import unittest
from datetime import datetime, timezone
from test_levels_targets import engine, level, ordinary, payload


class LevelInventoryLimits(unittest.TestCase):
    def test_101st_nearer_obstacle_is_not_dropped(self):
        p=payload();p['levels']=[level(p,'anchor','105')]
        p['levels'] += [level(p,f'far-{i}',str(200+i)) for i in range(99)]
        p['levels'].append(level(p,'nearest-last','110'))
        p['level_analysis']['target_request']=dict(direction='up',anchor_level_id='anchor',selection_bar_id='8')
        r=engine.analyze(p)
        self.assertEqual(r['issues'],[])
        self.assertEqual(len(r['level_analysis']['levels']),101)
        self.assertEqual(r['level_analysis']['target_selection']['selected']['id'],'nearest-last')
        self.assertFalse(r['level_analysis']['levels'][-1]['validation']['structural_context_verified'])

    def test_every_supplied_evidence_survives_above_100(self):
        p=ordinary();one=p['levels'][0];ev=p['level_analysis']['evidence'][0]
        p['levels']=[dict(copy.deepcopy(one),id=f'pivot-{i}') for i in range(101)]
        p['level_analysis']['evidence']=[dict(copy.deepcopy(ev),level_id=f'pivot-{i}') for i in range(101)]
        r=engine.analyze(p)
        self.assertEqual(r['issues'],[])
        self.assertEqual(len(r['level_analysis']['levels']),101)
        self.assertTrue(all(v['validation']['status']=='ordinary_contract_consistent' for v in r['level_analysis']['levels']))

    def test_parser_accepts_exact_2000_and_rejects_2001(self):
        p=payload();items=[level(p,f'level-{i}',str(200+i)) for i in range(2000)]
        at=datetime(2020,1,2,tzinfo=timezone.utc)
        self.assertEqual(len(engine.parse_levels(items,at,'M15')),2000)
        with self.assertRaises(engine.InputIssue) as issue:
            engine.parse_levels(items+[level(p,'extra','3000')],at,'M15')
        self.assertEqual(issue.exception.code,'invalid_level_list')

    def test_evidence_remains_bounded(self):
        p=ordinary();ev=p['level_analysis']['evidence'][0]
        p['level_analysis']['evidence']=[ev]*2001
        r=engine.analyze(p)
        self.assertEqual(r['issues'][0]['code'],'invalid_level_evidence_list')

    def test_discovery_cannot_exceed_combined_inventory_budget(self):
        p=payload([('100','104','98','101'),('101','104','99','103'),
                   ('103','108','101','106'),('106','107','102','104'),('104','105','100','101')])
        p['levels']=[level(p,f'level-{i}',str(200+i)) for i in range(2000)]
        p['level_analysis']['discovery']=dict(left_bars=2,right_bars=2,construction_method='wick_to_body')
        r=engine.analyze(p)
        self.assertEqual(r['issues'][0]['code'],'too_many_combined_levels')

if __name__=='__main__':unittest.main()
