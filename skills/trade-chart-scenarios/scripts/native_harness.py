"""Generate a self-contained fixed-data Pine/MQL arithmetic harness (stdout).

This is not a chart-feed test, a conformance native_export, or proof of runtime
origin. Frozen input eligibility is checked in Python before embedding bars.
No network, terminal launch, order functions, or output-file writes.
"""
import argparse
import importlib.util
import json
import sys
from decimal import Decimal, localcontext
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_conformance',Path(__file__).with_name('conformance.py'))
C=importlib.util.module_from_spec(spec); spec.loader.exec_module(C)
NAMES=C.PRICE+C.RATIO
ATTRS=['tr','atr','baseline','span','body','upper','lower','seed','raw','comp','bf','cl']
CATEGORIES=['not_computable','flat_bar','short','standard','long_range','very_long_range']
FLAGS=['has_tr','has_atr','has_baseline',None,None,None,None,'has_baseline','has_ratio','has_ratio','has_fraction','has_fraction']

def pine_core():
    source=(ROOT/'assets/native/pine/TradeKnowledgeFoundation.pine').read_text(encoding='utf-8')
    return source.split('// TK_CORE_BEGIN',1)[1].split('\n',1)[1].split('// TK_CORE_END',1)[0]

def prepare(fixture):
    with localcontext() as ctx:
        ctx.prec=100
        period,bars=C.fixture_bars(fixture)
        if not bars or len(bars)>500:C.A.fail('harness_requires_1_to_500_eligible_bars','input.series.bars')
        tick=Decimal(fixture['input']['series']['tick_size'])
        prices=[]
        for bar in bars:
            row=[]
            for name in ['open','high','low','close']:
                x=bar[name]; native=Decimal.from_float(float(x))
                if x/tick>Decimal('1e14'):
                    C.A.fail('native_price_quantum_precision_limit','input.series.bars.'+name)
                if abs(x-native)>max(tick*Decimal('1e-6'),abs(x)*Decimal('1e-12')):
                    C.A.fail('price_not_representable_within_tolerance','input.series.bars.'+name)
                row.append(format(x,'f'))
            # Binary price collapse would erase non-flat candle geometry.
            if float(row[1])==float(row[2]) and row[1]!=row[2] and Decimal(row[1])!=Decimal(row[2]):
                C.A.fail('binary_price_range_collapsed','input.series.bars')
            prices.append(row)
        expected=[]
        for r in fixture['expected_rows']:
            expected.append([r[n] for n in NAMES]+[str(CATEGORIES.index(r['length_class'])-1)]+
                [str(-1 if r[n] is None else int(r[n])) for n in ['standard_dominant_candidate','pinbar_candidate']])
        return period,prices,expected,format(tick*Decimal('1e-6'),'f')

def generate(fixture,target):
    period,prices,expected,price_abs=prepare(fixture)
    title=json.dumps('TK fixture '+fixture['fixture_id'],ensure_ascii=True)
    note='// Fixture SHA256: '+C.digest(fixture)+'\n// Scope: 12 numeric fields and 3 category fields; fixed eligible input only.\n'
    if target=='pine_v6':
        def array(rows):return ', '.join('na' if x is None else (x if '.' in x else x+'.0') for r in rows for x in r)
        source='//@version=6\nindicator('+title+', overlay = false, precision = 16)\n'+note+pine_core()
        source+='\nvar array<float> prices = array.from('+array(prices)+')\n'
        source+='var array<float> expected = array.from('+array(expected)+')\n'
        source+='''tk_equal(float actual, float wanted, float absolute) =>
    bool match = false
    if na(wanted)
        match := na(actual)
    else if not na(actual)
        match := tk_cmp(math.abs(actual - wanted), math.max(absolute, math.abs(wanted) * 1e-12)) <= 0
    match
var TKState state = TKState.new()
var int failures = 0
var int processed = 0
TKRow r = TKRow.new()
int i = bar_index
'''
        source+=f'''if barstate.isconfirmed and i < {len(prices)}
    r := tk_step(state, {period}, {fixture['input']['series']['tick_size']}, array.get(prices, i * 4), array.get(prices, i * 4 + 1), array.get(prices, i * 4 + 2), array.get(prices, i * 4 + 3))
    array<float> actual = array.from({', '.join('r.'+a for a in ATTRS)}, float(r.length), float(r.dominant), float(r.pin))
    for j = 0 to 14
        float absolute = j < 7 ? {price_abs} : (j < 12 ? 1e-12 : 0.0)
        bool matched = j < 12 ? tk_equal(array.get(actual, j), array.get(expected, i * 15 + j), absolute) : tk_cmp(array.get(actual, j), array.get(expected, i * 15 + j)) == 0
        if not matched
            failures := failures + 1
    processed := processed + 1
'''
        for attr,name in zip(ATTRS+['length','dominant','pin'],NAMES+['length_code','dominant_code','pin_code']):
            source+=f'plot(i < {len(prices)} and barstate.isconfirmed ? r.{attr} : na, "{name}")\n'
        source+='plot(processed, "Processed rows")\nplot(failures, "Mismatch count")\n'
        return source
    if target not in ['mql4','mql5']:raise ValueError('unsupported target')
    source='#property strict\n'+note+(ROOT/'assets/native/common/TradeKnowledgeCore.mqh').read_text(encoding='utf-8')
    source+='\n// Self-contained script: attach to any chart; reads no market/account data.\n'
    for name,rows,width in [('prices',prices,4),('expected',expected,15)]:
        source+=f'double {name}[][{width}]={{\n'+',\n'.join('{'+','.join('0' if x is None else x for x in r)+'}' for r in rows)+'\n};\n'
    masks=[sum(1<<i for i,x in enumerate(row[:12]) if x is None) for row in expected]
    source+='int null_masks[]={'+','.join(map(str,masks))+'};\n'
    source+=f'''int TKFixtureCheck() {{
   TKState state; TKReset(state,{period},{fixture['input']['series']['tick_size']}); int failures=0;
   for(int i=0;i<{len(prices)};i++) {{
      TKRow r;
      if(!TKStep(state,prices[i][0],prices[i][1],prices[i][2],prices[i][3],r)) return -1;
      double actual[15];
'''
    for i,a in enumerate(ATTRS+['length','dominant','pin']):source+=f'      actual[{i}]=r.{a};\n'
    source+='      int missing=0;\n'
    for i,f in enumerate(FLAGS):
        if f:source+=f'      if(!r.{f}) missing|={1<<i};\n'
    source+=f'''      for(int j=0;j<15;j++) {{
         bool want_null=(null_masks[i] & (1<<j))!=0;
         bool is_null=(missing & (1<<j))!=0;
         if(want_null!=is_null) {{ failures++; continue; }}
         if(want_null) continue;
         double absolute=j<7 ? {price_abs} : (j<12 ? 1e-12 : 0);
         double tolerance=j<12 ? MathMax(absolute,MathAbs(expected[i][j])*1e-12) : 0;
         if(MathAbs(actual[j]-expected[i][j])>tolerance) failures++;
      }}
   }}
   return failures;
}}
void OnStart() {{
   int failures=TKFixtureCheck();
   Print({title}," rows={len(prices)} mismatches=",failures);
}}
'''
    return source

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True)
    parser.add_argument('--target',choices=['pine_v6','mql4','mql5'],required=True)
    args=parser.parse_args()
    try:source=generate(C.load_json(args.fixture),args.target)
    except (C.A.InputIssue,OSError,ValueError,RecursionError) as exc:
        print('Invalid fixture: '+str(exc),file=sys.stderr);return 2
    print(source,end='');return 0

if __name__=='__main__':sys.exit(main())
