"""Observe an explicit engine scenario without changing its levels or rules.

Historical replay uses declared archive availability, never a live receipt claim.
Prospective recording must finish before the next entry candle CLOSES. That
candle may already be open; its partial prices are not inputs. Activation means
engineering-profile readiness observed after recording, not an order or fill.
"""
import argparse,copy,hashlib,importlib.util,json
from datetime import timedelta
from pathlib import Path

HERE=Path(__file__).resolve().parent;SKILL=HERE.parent
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,HERE/file)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
A=module('watch_engine','analyze.py');J=module('watch_storage','observation_journal.py')
PROFILE='engine_linked_scenario_watch_v1'
SECONDS={'M1':60,'M5':300,'M15':900,'H1':3600}
LIMITS=dict(entry_permission=False,order=None,fill=None,pnl=None,profitability_claim=False)
def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()
def fields(value,required,optional=''):
    if not isinstance(value,dict) or not set(required.split())<=set(value) or set(value)-set((required+' '+optional).split()):raise ValueError('unexpected_fields')
def engines():
    files=[HERE/name for name in ['analyze.py','pivots.py','structure.py','observation_journal.py','analysis_watch.py']]
    files+=sorted((SKILL/'references').glob('*contract*.json'))
    return {p.relative_to(SKILL).as_posix():J.sha(p) for p in files}
def series_map(payload):
    all_series=[payload['series']]+payload.get('structure_analysis',{}).get('higher_series',[])
    result={}
    for s in all_series:
        tf=s['timeframe']
        if tf in result:raise ValueError('duplicate_timeframe')
        result[tf]=s
    return result
def metadata(s):return {k:v for k,v in s.items() if k!='bars'}
def eligible(payload,result):
    a=result.get('assessment',{});ctx=result.get('structural_context',{});h=result.get('target_hierarchy',{})
    checks=dict(valid_data=not result.get('issues') and result.get('status') not in ['invalid_input','insufficient_data','unsupported'],
        source_linked_review=payload.get('pivot_review',{}).get('profile')=='source_linked_pivots_v1',
        strict_confirmation=a.get('status')=='confirmed_under_selected_profile',
        historical_context=a.get('historical_context_gate',{}).get('prospective_eligible') is True,
        level_target=a.get('level_and_target_gate')=='consistent_with_supplied_context',
        structure=ctx.get('scenario_ready') is True,
        structural_target=h.get('structural_target_contract_consistent') is True,
        direct_path=h.get('direct_path_clear') is True,
        unconsumed_target=a.get('target_touched') is False,
        unterminated=a.get('scenario_status') not in ['invalidated','reassess','retrospective_only'])
    return all(checks.values()),[k for k,v in checks.items() if not v]
def snapshot(payload,result,at):
    a=result.get('assessment',{});ctx=result.get('structural_context',{});h=result.get('target_hierarchy',{})
    ready,missing=eligible(payload,result)
    return dict(knowledge_at=J.stamp(at),analysis_sha256=digest(result),analysis_status=result['status'],issues=result.get('issues',[]),
        ready=ready,unmet_gates=missing,assessment_status=a.get('status'),scenario_status=a.get('scenario_status'),
        confirmed_at=a.get('confirmed_at'),confirmation_available_at=a.get('available_at'),
        invalidated_at=a.get('invalidated_at'),invalidation_available_at=a.get('invalidation_available_at'),
        target_touch=a.get('target_touch'),structure_status=ctx.get('status'),structure_direction=ctx.get('direction'),
        structure_roles=ctx.get('roles',{}),target_status=h.get('status'),
        structural_target=h.get('structural_target'),nearest_obstacle=h.get('nearest_obstacle'),
        historical_context_gate=a.get('historical_context_gate'),conditional_scenarios=result.get('conditional_scenarios',[]),
        rule_ids=result.get('rule_ids',[]),source_refs=result.get('source_refs',[]))
def inspect_initial(payload,horizon,mode,clock):
    if mode not in ['historical_replay','prospective_observation']:raise ValueError('invalid_mode')
    if type(horizon) is not int or not 1<=horizon<=60:raise ValueError('invalid_horizon')
    asof=J.instant(payload['as_of']);main=payload['series'];tf=main['timeframe']
    if tf not in SECONDS:raise ValueError('unsupported_entry_timeframe')
    if 'discovery' in payload.get('level_analysis',{}):raise ValueError('explicit_fixed_levels_required')
    if mode=='prospective_observation' and not asof<=clock or mode=='historical_replay' and asof!=clock:raise ValueError('invalid_decision_clock')
    streams=series_map(payload)
    for s in streams.values():
        if not s.get('bars'):raise ValueError('initial_history_required')
        if mode=='prospective_observation' and s.get('availability_basis')!='observed_receipt':raise ValueError('actual_receipt_basis_required')
        for b in s['bars']:
            if b.get('is_closed') is not True or not J.instant(b['open_time'])<J.instant(b['close_time'])<=J.instant(b['available_at'])<=asof:raise ValueError('future_or_open_initial_bar')
    for l in payload.get('levels',[]):
        for key in ['origin_at','available_at']:
            if key in l and J.instant(l[key])>asof:raise ValueError('future_level_in_frozen_input')
    for item in [payload.get('assessment',{})]+payload.get('level_analysis',{}).get('evidence',[]):
        if item.get('context_available_at') and J.instant(item['context_available_at'])>asof:raise ValueError('future_context_in_frozen_input')
    step=timedelta(seconds=SECONDS[tf]);start=J.instant(main['bars'][-1]['close_time'])
    if not start<=asof<start+step:raise ValueError('stale_entry_reference')
    if mode=='prospective_observation' and not clock<start+step:raise ValueError('first_future_close_already_occurred')
    candidate=payload.get('assessment',{}).get('candidate_bar_id')
    candidate_present=candidate in {b['id'] for b in main['bars']}
    return start,start+step*horizon,candidate_present
def freeze(payload,horizon,mode='historical_replay',_clock=None):
    clock=_clock or (J.instant(payload['as_of']) if mode=='historical_replay' else J.now())
    start,end,candidate_present=inspect_initial(payload,horizon,mode,clock)
    result=A.analyze(payload)
    if result['status']=='invalid_input':raise ValueError('invalid_analysis_input')
    if mode=='prospective_observation':
        clock=_clock or J.now();inspect_initial(payload,horizon,mode,clock)
    initial=snapshot(payload,result,clock)
    exclusions=[]
    if not candidate_present:exclusions.append('explicit_closed_candidate_missing_at_record')
    if initial['ready']:exclusions.append('already_ready_at_record_not_a_new_activation')
    if initial['invalidated_at']:exclusions.append('already_invalidated_at_record')
    if initial['target_touch']:exclusions.append('target_already_consumed_at_record')
    return dict(schema_version=1,profile=PROFILE,mode=mode,recorded_at=J.stamp(clock),
        input=copy.deepcopy(payload),input_sha256=digest(payload),initial_analysis=result,initial_snapshot=initial,
        engine_sha256=engines(),horizon_bars=horizon,window_open=J.stamp(start),window_close=J.stamp(end),
        publication_deadline=J.stamp(start+timedelta(seconds=SECONDS[payload['series']['timeframe']])),
        excludes_new_activation=exclusions,scope='explicit_engine_profile_not_full_trex_or_trade_performance',**LIMITS)
def validate_decision(d):
    fields(d,'schema_version profile mode recorded_at input input_sha256 initial_analysis initial_snapshot engine_sha256 horizon_bars window_open window_close publication_deadline excludes_new_activation scope entry_permission order fill pnl profitability_claim')
    if type(d['schema_version']) is not int or d['schema_version']!=1 or d['profile']!=PROFILE:raise ValueError('unsupported_decision')
    if any(d[k] is not v for k,v in LIMITS.items()):raise ValueError('unexpected_trade_claim')
    if d['engine_sha256']!=engines():raise ValueError('verifier_engine_changed_use_original_version')
    expected=freeze(d['input'],d['horizon_bars'],d['mode'],J.instant(d['recorded_at']))
    if d!=expected:raise ValueError('decision_content_mismatch')
def record(directory,value,_clock=None):
    fields(value,'analysis_input horizon_bars')
    d=freeze(value['analysis_input'],value['horizon_bars'],'prospective_observation',_clock)
    path=Path(directory)/'decision.json';J.write_new(path,d)
    at=_clock or J.now()
    p=dict(decision_sha256=J.sha(path),persisted_at=J.stamp(at),before_first_future_close=at<J.instant(d['publication_deadline']))
    J.write_new(Path(directory)/'publication.json',p)
    if not p['before_first_future_close']:raise ValueError('publication_missed_future_close')
    return dict(path=str(path),sha256=J.sha(path),window_close=d['window_close'],partial_first_future_bar_may_already_be_open=True)
def prepare_updates(d,value):
    fields(value,'series','received_at source_receipts')
    if not isinstance(value['series'],list):raise ValueError('updates_must_be_series_list')
    base=series_map(d['input']);updates={};end=J.instant(d['window_close']);now=J.now()
    for s in value['series']:
        tf=s['timeframe']
        if tf in updates or tf not in base or metadata(s)!=metadata(base[tf]):raise ValueError('changed_or_duplicate_series_identity')
        if not isinstance(s['bars'],list) or len(s['bars'])>1000:raise ValueError('invalid_update_size')
        ids={b['id'] for b in base[tf]['bars']};last=J.instant(base[tf]['bars'][-1]['close_time'])
        seen=set();previous=last
        for b in s['bars']:
            fields(b,'id open_time close_time available_at is_closed open high low close')
            close=J.instant(b['close_time']);opened=J.instant(b['open_time']);available=J.instant(b['available_at'])
            if b['id'] in ids or b['id'] in seen or b['is_closed'] is not True or not previous<=opened<close<=end or available<close or available<=J.instant(d['recorded_at']):raise ValueError('rewritten_out_of_order_or_unavailable_future_bar')
            values=[J.number(b[k]) for k in ['open','high','low','close']]
            o,h,l,c=values
            if not l<=min(o,c)<=max(o,c)<=h:raise ValueError('invalid_future_ohlc')
            # Validate each new row separately, so an older archive gap cannot
            # hide a malformed duration, calendar alignment or off-grid price.
            try:A.parse_series({**metadata(s),'bars':[b]},available)
            except A.InputIssue as exc:raise ValueError('invalid_future_bar:'+exc.code) from exc
            if d['mode']=='prospective_observation' and not available<=J.instant(value['received_at'])<=now:raise ValueError('future_receipt_clock')
            seen.add(b['id']);previous=close
        updates[tf]=s['bars']
    if d['mode']=='prospective_observation':
        receipts=value.get('source_receipts')
        if not isinstance(receipts,list) or not receipts:raise ValueError('source_receipts_required')
        receipt_times=set()
        for r in receipts:
            fields(r,'received_at source_sha256')
            at=J.instant(r['received_at'])
            if at>J.instant(value['received_at']) or not isinstance(r['source_sha256'],str) or len(r['source_sha256'])!=64 or any(c not in '0123456789abcdef' for c in r['source_sha256']):raise ValueError('invalid_source_receipt')
            receipt_times.add(at)
        if any(J.instant(b['available_at']) not in receipt_times for rows in updates.values() for b in rows):raise ValueError('bar_without_observed_receipt')
    return updates
def payload_at(d,updates,at):
    p=copy.deepcopy(d['input']);p['as_of']=J.stamp(at)
    for tf,s in series_map(p).items():
        # Include the entire closed prefix, even if a row is delayed. The core
        # parser blocks at it; filtering it out would compress time incorrectly.
        s['bars'] += [copy.deepcopy(b) for b in updates.get(tf,[]) if J.instant(b['close_time'])<=at]
    req=p.get('level_analysis',{}).get('target_request')
    if req is not None:
        usable=[]
        for b in p['series']['bars']:
            if J.instant(b['available_at'])>at:break
            usable.append(b)
        if usable:req['selection_bar_id']=usable[-1]['id']
    return p
def evaluate(d,value):
    validate_decision(d);updates=prepare_updates(d,value)
    at0=J.instant(d['recorded_at']);end=J.instant(d['window_close']);main=d['input']['series']['timeframe'];step=timedelta(seconds=SECONDS[main])
    rows=updates.get(main,[]);expected=[J.instant(d['window_open'])+step*n for n in range(d['horizon_bars'])]
    opens={J.instant(b['open_time']) for b in rows};missing=[J.stamp(t) for t in expected if t not in opens]
    times=sorted({J.instant(b['available_at']) for values in updates.values() for b in values if J.instant(b['available_at'])>at0})
    snaps=[];events=[];active=None;state='not_eligible_at_record' if d['excludes_new_activation'] else 'waiting';terminal=bool(d['excludes_new_activation']);seen_events=set()
    if d['initial_snapshot']['invalidated_at']:seen_events.add(('invalidation',d['initial_snapshot']['invalidated_at']))
    if d['initial_snapshot']['target_touch']:seen_events.add(('target_touch',d['initial_snapshot']['target_touch']['bar_id']))
    for at in times:
        p=payload_at(d,updates,at);r=A.analyze(p);snap=snapshot(p,r,at);snaps.append(snap)
        if r['status']=='invalid_input':raise ValueError('invalid_future_analysis')
        if snap['ready'] and not active and not terminal and not d['excludes_new_activation'] and at<=end:
            active=dict(confirmation_occurred_at=snap['confirmed_at'],usable_at=J.stamp(at))
            events.append(dict(kind='engine_scenario_ready',**active,entry_permission=False));state='active'
        a=r.get('assessment',{})
        ordered=[('invalidation','invalidated_at'),('target_touch','target_touch')]
        touch=a.get('target_touch');invalidated=a.get('invalidated_at')
        # A target range ending before (or at) the invalidating CLOSE precedes
        # that invalidation, even when both bars arrive in one receipt group.
        # This says nothing about a hypothetical intrabar stop order/fill.
        if touch and invalidated and J.instant(touch['bar_close_at'])<=J.instant(invalidated):ordered.reverse()
        for kind,key in ordered:
            val=a.get(key)
            if not val:continue
            identity=(kind,val if isinstance(val,str) else val['bar_id'])
            if identity in seen_events:continue
            seen_events.add(identity)
            event=dict(kind=kind,observed_at=J.stamp(at),active_before_observation=active is not None and not terminal)
            if terminal:
                event.update(kind='post_termination_price_observation',observation_kind=kind,observation=val,
                    eligible_for_scenario=False,terminal_state=state)
                events.append(event)
                continue
            if kind=='invalidation':
                event.update(occurred_at=val,available_at=a.get('invalidation_available_at'))
                state='invalidated';terminal=True
            else:
                event.update(touch=val,after_usable_activation=bool(active and J.instant(val['bar_open_at'])>=J.instant(active['usable_at'])))
                if state!='invalidated':state='target_observed_after_activation' if event['after_usable_activation'] else 'target_consumed_without_usable_activation'
                terminal=True
            events.append(event)
        if active and not terminal and not snap['ready']:
            state='context_no_longer_ready';events.append(dict(kind='context_no_longer_ready',observed_at=J.stamp(at),unmet_gates=snap['unmet_gates']));terminal=True
    if missing:state='incomplete_future_window'
    elif state=='waiting':state='data_unusable' if any(s['issues'] for s in snaps) else 'expired_without_engine_activation'
    elif state=='active':state='active_at_horizon'
    return dict(schema_version=1,profile=PROFILE,mode=d['mode'],decision_sha256=digest(d),state=state,
        activation=active,snapshots=snaps,events=events,missing_entry_opens=missing,
        evaluation_clock_basis='declared_historical_availability' if d['mode']=='historical_replay' else 'observed_receipt_groups',
        source_authenticity_verified=False,external_timestamp_authority=False,**LIMITS)
def verify(directory):
    directory=Path(directory);d=J.load(directory/'decision.json');validate_decision(d)
    receipt=J.load(directory/'publication.json');fields(receipt,'decision_sha256 persisted_at before_first_future_close')
    if receipt['decision_sha256']!=J.sha(directory/'decision.json') or receipt['before_first_future_close'] is not True or not J.instant(d['recorded_at'])<=J.instant(receipt['persisted_at'])<J.instant(d['publication_deadline']):raise ValueError('late_or_uncommitted_decision')
    path=directory/'outcome.json'
    if path.exists():
        out=J.load(path);fields(out,'decision_file_sha256 updates result')
        if out['decision_file_sha256']!=receipt['decision_sha256'] or out['result']!=evaluate(d,out['updates']):raise ValueError('outcome_content_mismatch')
    return dict(status='verified',resolved=path.exists(),decision_sha256=receipt['decision_sha256'],mode=d['mode'],trade_performance_verified=False)
def settle(directory,value):
    directory=Path(directory);verify(directory);d=J.load(directory/'decision.json')
    if J.now()<J.instant(d['window_close']):raise ValueError('window_not_closed')
    result=evaluate(d,value)
    if result['missing_entry_opens']:raise ValueError('future_window_incomplete_no_outcome_written')
    J.write_new(directory/'outcome.json',dict(decision_file_sha256=J.sha(directory/'decision.json'),updates=value,result=result))
    return dict(state=result['state'],events=len(result['events']),activation=result['activation'])
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['record','settle','verify','replay']);p.add_argument('--input',type=Path);p.add_argument('--updates',type=Path);p.add_argument('--directory',type=Path);p.add_argument('--horizon',type=int,default=6);a=p.parse_args()
    if a.action=='replay':
        if not a.input or not a.updates:p.error('--input and --updates required')
        d=freeze(J.load(a.input),a.horizon);result=evaluate(d,J.load(a.updates))
    else:
        if not a.directory:p.error('--directory required')
        if a.action=='verify':result=verify(a.directory)
        else:
            if not a.input:p.error('--input required')
            result=(record if a.action=='record' else settle)(a.directory,J.load(a.input))
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
if __name__=='__main__':main()
