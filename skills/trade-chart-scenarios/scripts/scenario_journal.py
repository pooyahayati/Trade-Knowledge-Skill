"""Fixed prospective price-condition watch; no orders, fills, PnL, or network.

Activation is a strict CLOSED-price condition, not a Trex breakout detector.
Before activation, a close through invalidation cancels the watch. Thereafter
target/invalidation are touches. An activation candle's earlier wick cannot
hit a post-activation target. Unordered dual touches remain ambiguous.
"""
import argparse,hashlib,importlib.util,json,re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

HERE=Path(__file__).resolve().parent
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,HERE/file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
J=module('scenario_base_journal','observation_journal.py')
A=module('scenario_analysis','analyze.py')
PROFILE='closed_activation_then_touch_v1'
SCOPE='diagnostic_price_conditions_not_full_trex_strategy'
LIMITS=dict(entry_permission=False,order=None,fill=None,pnl=None,profitability_claim=False)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()
def fields(value,names):
    if not isinstance(value,dict) or set(value)!=set(names.split()):raise ValueError('unexpected_fields')
def identity(value):
    for key in ['symbol','feed_id']:
        if not isinstance(value[key],str) or not value[key] or len(value[key])>150:raise ValueError('invalid_identity')
def source_hash(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('invalid_source_hash')

def validate_plan(value,clock):
    fields(value,'observation analysis_input side activation target invalidation target_open target_close')
    obs=J.observation(value['observation'],clock)
    if 'open_time' not in obs:raise ValueError('reference_bar_open_required')
    start=J.instant(value['target_open']);end=J.instant(value['target_close'])
    count=(end-start).total_seconds()/60
    if not clock<start or start-clock>timedelta(minutes=2) or start.second or start.microsecond or end.second or end.microsecond or not 1<=count<=60 or count!=int(count):
        raise ValueError('invalid_future_window')
    if value['side'] not in ['long','short']:raise ValueError('invalid_side')
    a,t,i=[J.number(value[k]) for k in ['activation','target','invalidation']]
    c=J.number(obs['close'])
    valid=i<c<=a<t if value['side']=='long' else t<a<=c<i
    if not valid:raise ValueError('invalid_barrier_order_or_initial_close')
    data=value['analysis_input'];series=data.get('series',{})
    if any(series.get(k)!=obs[k] for k in ['symbol','feed_id','timeframe']):raise ValueError('analysis_identity_mismatch')
    asof=J.instant(data['as_of'])
    if not J.instant(obs['received_at'])<=asof<=clock:raise ValueError('analysis_cutoff_mismatch')
    bars=series.get('bars',[])
    if not bars:raise ValueError('analysis_history_required')
    if any(not b.get('is_closed') or not J.instant(b['close_time'])<=J.instant(b['available_at'])<=asof for b in bars):raise ValueError('analysis_unavailable_bars')
    b=bars[-1]
    if J.instant(b['open_time'])!=J.instant(obs['open_time']) or J.instant(b['close_time'])!=J.instant(obs['last_closed_at']) or J.number(b['close'])!=c:
        raise ValueError('analysis_reference_mismatch')
    return int(count)

def record(directory,value,_clock=None):
    clock=_clock or J.now();count=validate_plan(value,clock)
    result=A.analyze(value['analysis_input'])
    if result.get('issues') or result.get('order') is not None:raise ValueError('analysis_failed')
    clock=_clock or J.now()
    validate_plan(value,clock)
    # Preserve the actual input/output together. Source-rule uncertainty remains
    # in the analysis; a price-condition watch never promotes it to permission.
    payload=dict(schema_version=1,kind='prospective_scenario_watch',profile=PROFILE,scope=SCOPE,
        recorded_at=J.stamp(clock),plan=value,bars_required=count,analysis=result,
        analysis_sha256=digest(result),plan_sha256=digest(value),
        engine_sha256={p.name:J.sha(p) for p in [HERE/'analyze.py',HERE/'pivots.py',HERE/'structure.py',Path(__file__)]},
        **LIMITS)
    path=Path(directory)/'decision.json';J.write_new(path,payload)
    # A second, non-replacing receipt timestamps completion of publication.
    # A late/partial publication remains preserved but cannot verify or settle.
    persisted=_clock or J.now()
    receipt=dict(schema_version=1,kind='scenario_publication_receipt',decision_sha256=J.sha(path),
        persisted_at=J.stamp(persisted),before_window=persisted<J.instant(value['target_open']))
    J.write_new(Path(directory)/'publication.json',receipt)
    if not receipt['before_window']:raise ValueError('publication_missed_future_deadline')
    return dict(path=str(path),sha256=J.sha(path),recorded_at=payload['recorded_at'],target_close=value['target_close'])

def validate_decision(d):
    fields(d,'schema_version kind profile scope recorded_at plan bars_required analysis analysis_sha256 plan_sha256 engine_sha256 entry_permission order fill pnl profitability_claim')
    if type(d['schema_version']) is not int or d['schema_version']!=1 or d['kind']!='prospective_scenario_watch' or d['profile']!=PROFILE or d['scope']!=SCOPE:raise ValueError('unsupported_decision')
    if any(d[k] is not v for k,v in LIMITS.items()):raise ValueError('unexpected_trade_claim')
    count=validate_plan(d['plan'],J.instant(d['recorded_at']))
    if type(d['bars_required']) is not int or d['bars_required']!=count:raise ValueError('invalid_bar_count')
    if digest(d['plan'])!=d['plan_sha256'] or digest(d['analysis'])!=d['analysis_sha256']:raise ValueError('snapshot_hash_mismatch')
    fields(d['engine_sha256'],'analyze.py pivots.py structure.py scenario_journal.py')
    for h in d['engine_sha256'].values():source_hash(h)
    r=d['analysis'];s=d['plan']['analysis_input']['series']
    if r.get('issues') or r.get('order') is not None or r.get('as_of')!=J.stamp(J.instant(d['plan']['analysis_input']['as_of'])) or any(r.get('data_scope',{}).get(k)!=s[k] for k in ['symbol','feed_id','timeframe']):raise ValueError('analysis_snapshot_mismatch')

def future_bars(d,value,clock):
    fields(value,'decision_sha256 symbol feed_id timeframe price_basis received_at source_sha256 bars')
    source_hash(value['decision_sha256']);source_hash(value['source_sha256']);identity(value)
    plan=d['plan'];obs=plan['observation']
    if any(value[k]!=obs[k] for k in ['symbol','feed_id','timeframe']):raise ValueError('feed_identity_mismatch')
    if value['price_basis']!=plan['analysis_input']['series']['price_basis']:raise ValueError('price_basis_mismatch')
    receipt=J.instant(value['received_at']);end=J.instant(plan['target_close'])
    if not end<=receipt<=clock or clock-receipt>timedelta(seconds=120) or receipt-end>timedelta(seconds=120):raise ValueError('stale_or_future_outcome')
    if not isinstance(value['bars'],list) or len(value['bars'])!=d['bars_required']:raise ValueError('incomplete_future_window')
    start=J.instant(plan['target_open']);rows=[]
    for n,b in enumerate(value['bars']):
        fields(b,'open_time close_time open high low close')
        opened=J.instant(b['open_time']);closed=J.instant(b['close_time'])
        if opened!=start+timedelta(minutes=n) or closed!=opened+timedelta(minutes=1):raise ValueError('gap_duplicate_or_wrong_bar')
        o,h,l,c=[J.number(b[k]) for k in ['open','high','low','close']]
        if not l<=min(o,c)<=max(o,c)<=h:raise ValueError('invalid_ohlc')
        rows.append((b,o,h,l,c))
    return rows

def resolve(d,value,clock):
    validate_decision(d);rows=future_bars(d,value,clock)
    plan=d['plan'];long=plan['side']=='long'
    a,t,i=[J.number(plan[k]) for k in ['activation','target','invalidation']]
    state='waiting';events=[];activated=None
    for b,o,h,l,c in rows:
        if state not in ['waiting','active']:continue
        if state=='waiting':
            cancel=c<=i if long else c>=i
            activate=c>a if long else c<a
            if cancel:
                state='cancelled_before_activation';events.append(dict(event=state,known_at=b['close_time'],time_basis='closed_price'))
            elif activate:
                state='active';activated=b['close_time']
                events.append(dict(event='activated_at_close',known_at=activated,time_basis='closed_price'))
                # A gap/large activation close already past the target means
                # there is no remaining path to that predeclared target.
                if (c>=t if long else c<=t):
                    state='target_already_passed_at_activation'
                    events.append(dict(event=state,known_at=activated,time_basis='closed_price',target_success=False))
                elif (h>=t if long else l<=t):
                    events.append(dict(event='target_touch_before_activation_not_counted',known_at=activated,time_basis='activation_bar_range',target_success=False))
            continue
        target=h>=t if long else l<=t
        invalid=l<=i if long else h>=i
        at_open_target=o>=t if long else o<=t
        at_open_invalid=o<=i if long else o>=i
        if at_open_target or at_open_invalid:
            state='target_touched' if at_open_target else 'invalidation_touched';basis='opening_price'
        elif target and invalid:state='ambiguous_touch_order';basis='unordered_ohlc_range'
        elif target or invalid:state='target_touched' if target else 'invalidation_touched';basis='ohlc_range'
        else:continue
        events.append(dict(event=state,bar_open=b['open_time'],known_at=b['close_time'],time_basis=basis))
    if state=='waiting':state='expired_without_activation'
    elif state=='active':state='active_at_horizon'
    for event in events:
        closed=event.pop('known_at')
        if event['time_basis'] in ['closed_price','activation_bar_range']:
            event['bar_close']=closed
            if event['time_basis']=='closed_price':event['occurrence_at']=closed
            else:event['occurrence_window']=[J.stamp(J.instant(closed)-timedelta(minutes=1)),closed]
        else:
            event['bar_close']=closed
            if event['time_basis']=='opening_price':event['occurrence_at']=event['bar_open']
            else:event['occurrence_window']=[event['bar_open'],closed]
        event['available_at']=value['received_at']
        event['evaluated_at']=J.stamp(clock)
    return dict(schema_version=1,kind='prospective_scenario_outcome',profile=PROFILE,scope=SCOPE,
        recorded_at=J.stamp(clock),decision_sha256=value['decision_sha256'],observation=value,
        state=state,activated_at=activated,activation_available_at=value['received_at'] if activated else None,events=events,
        evaluation='fixed_conditions_replayed_after_window_receipt',intrabar_order_assumed=False,**LIMITS)

def settle(directory,value,_clock=None):
    clock=_clock or J.now();path=Path(directory)/'decision.json'
    verify(directory)
    if value.get('decision_sha256')!=J.sha(path):raise ValueError('decision_hash_mismatch')
    payload=resolve(J.load(path),value,clock);out=Path(directory)/'outcome.json';J.write_new(out,payload)
    return dict(path=str(out),sha256=J.sha(out),state=payload['state'],activated_at=payload['activated_at'])

def verify(directory):
    path=Path(directory)/'decision.json';d=J.load(path);validate_decision(d)
    receipt=J.load(Path(directory)/'publication.json')
    fields(receipt,'schema_version kind decision_sha256 persisted_at before_window')
    if type(receipt['schema_version']) is not int or receipt['schema_version']!=1 or receipt['kind']!='scenario_publication_receipt' or receipt['decision_sha256']!=J.sha(path) or receipt['before_window'] is not True or not J.instant(d['recorded_at'])<=J.instant(receipt['persisted_at'])<J.instant(d['plan']['target_open']):raise ValueError('uncommitted_or_late_decision')
    out=Path(directory)/'outcome.json'
    if out.exists():
        value=J.load(out)
        if value['decision_sha256']!=J.sha(path) or value['observation']['decision_sha256']!=J.sha(path):raise ValueError('decision_hash_mismatch')
        expected=resolve(d,value['observation'],J.instant(value['recorded_at']))
        if expected!=value:raise ValueError('outcome_content_mismatch')
    return dict(status='verified',decision_sha256=J.sha(path),outcome_sha256=J.sha(out) if out.exists() else None,
        resolved=out.exists(),external_timestamp_authority=False,source_authenticity_verified=False,
        trade_performance_verified=False,scope=SCOPE)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['record','settle','verify']);p.add_argument('--directory',required=True,type=Path);p.add_argument('--input',type=Path);args=p.parse_args()
    if args.action=='verify':result=verify(args.directory)
    else:
        if not args.input:p.error('--input is required')
        result=(record if args.action=='record' else settle)(args.directory,J.load(args.input))
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
