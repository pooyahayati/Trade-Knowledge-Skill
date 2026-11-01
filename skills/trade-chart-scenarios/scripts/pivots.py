"""Bounded Trex evidence review; context assertions are never independent labels.

The caller supplies already filtered closed/available OHLC and Decimal helpers.
This module has no I/O and never changes a supplied level or selects a trade.
"""
from decimal import Decimal


def shadow_master(row):
    size=Decimal(row['range'])
    return bool(row['standard_dominant_candidate'] and size and
                max(Decimal(row['upper_wick']),Decimal(row['lower_wick']))/size>=Decimal('.8')-Decimal('.000000001'))


def media_master_review(row):
    """Flag the two-sided shadow branch shown in the teacher's video.

    The existing 80% engineering comparison is diagnostic, not a newly verified
    inclusive source boundary. It cannot automatically qualify or replace a master.
    """
    size=Decimal(row['range'])
    upper,lower=Decimal(row['upper_wick']),Decimal(row['lower_wick'])
    combined=(upper+lower)/size if size else None
    split=bool(size and row['length_class']=='standard' and upper>0 and lower>0 and
        combined>=Decimal('.8')-Decimal('.000000001') and not row['standard_dominant_candidate'])
    return dict(profile='media_master_review_v1',split_shadow_candidate=split,
        total_wick_fraction=format(combined,'f') if combined is not None else None,
        selected_profile_match=row['standard_dominant_candidate'],
        status='needs_source_variant_review' if split else 'no_additional_media_shape_flag',
        source_refs=['MED16-VIDEO-MASTER'],source_threshold_inclusivity_verified=False,
        automatic_master_selection_allowed=False,profile_failure_rejects_whole_method=False)


def exception_shapes(bar, row, support):
    """Shared descriptive screens, never automatic exception qualification.

    Ordinary-reference review must not silently skip a shape already supported
    by explicit exception review. Long-shadow screening has no body-colour gate.
    Keep the established workbook/design thresholds unchanged.
    """
    size=bar['high']-bar['low'];body=abs(bar['close']-bar['open'])
    tail=min(bar['open'],bar['close'])-bar['low'] if support else bar['high']-max(bar['open'],bar['close'])
    opposite=bar['high']-max(bar['open'],bar['close']) if support else min(bar['open'],bar['close'])-bar['low']
    reverse=bar['close']>bar['open'] if support else bar['close']<bar['open']
    atr=Decimal(row['baseline_atr']) if row['baseline_atr'] is not None else None
    usable=atr is not None and atr>0
    return dict(reverse_body=reverse,long_shadow=tail>=atr if usable else None,
        workbook_pinbar=row['pinbar_candidate'] is True and tail>opposite if usable else None,
        self_cover_screen=bool(size and reverse and Decimal('.4')<=body/size<=Decimal(2)/3
            and tail/size>=Decimal(1)/3 and opposite/size<=Decimal('.1')))


def wave(e, bars, rows, end, support, as_of, A, shadow_endpoint=False, event_shadow=False):
    path = 'level_analysis.evidence'
    p = A.obj(e['preleg'], ['route'], ['bar_ids','start_bar_id','end_bar_id','atr_bar_id','threshold_atr'], path+'.preleg')
    ids = {b['id']: i for i,b in enumerate(bars)}
    issues = []
    toward = lambda b: b['close'] < b['open'] if support else b['close'] > b['open']
    known = bars[end]['available_at']
    start = None
    measurement = None
    if p['route'] == 'three_standard_bars':
        A.obj(p, ['route','bar_ids'], [], path+'.preleg')
        bids = p['bar_ids']
        if not isinstance(bids,list) or not 3 <= len(bids) <= 100:
            A.fail('at_least_three_preleg_bar_ids_required',path+'.preleg.bar_ids')
        for bid in bids:A.string(bid,path+'.preleg.bar_ids',100)
        if any(b not in ids for b in bids):
            return dict(status='needs_review',issues=['preleg_bars_not_available']), known
        selected = [ids[b] for b in bids]
        if selected != sorted(set(selected)) or selected[-1] > end:
            return dict(status='needs_review',issues=['preleg_order_or_end_invalid']), known
        start = selected[0]
        if not all(toward(bars[i]) and rows[i]['length_class']=='standard' for i in selected):
            issues.append('preleg_does_not_meet_selected_route')
    elif p['route'] == 'atr_displacement':
        A.obj(p,['route','start_bar_id','end_bar_id','atr_bar_id','threshold_atr'],[],path+'.preleg')
        for k in ['start_bar_id','end_bar_id','atr_bar_id']:A.string(p[k],path+'.preleg.'+k,100)
        threshold=A.number(p['threshold_atr'],path+'.preleg.threshold_atr')
        if any(p[k] not in ids for k in ['start_bar_id','end_bar_id','atr_bar_id']):
            return dict(status='needs_review',issues=['preleg_bars_not_available']), known
        start, last, ai = [ids[p[k]] for k in ['start_bar_id','end_bar_id','atr_bar_id']]
        # Trex 256 permits the self-cover event's shadow in the ATR route.
        # Directional/noise accounting still stops before that reverse-body event.
        limit = end+1 if event_shadow else end
        if not start <= last <= limit or start > end or ai > last:
            return dict(status='needs_review',issues=['preleg_order_or_end_invalid']), known
        distance=bars[start]['open']-bars[last]['low'] if support else bars[last]['high']-bars[start]['open']
        baseline=rows[ai]['baseline_atr']
        if baseline is None:
            issues.append('preleg_atr_unavailable')
        elif Decimal(baseline) <= 0:
            issues.append('preleg_atr_not_positive')
        elif distance <= 0 or distance < threshold*Decimal(baseline):
            issues.append('preleg_does_not_meet_selected_route')
        if threshold != 3:
            issues.append('custom_atr_threshold_requires_method_review')
        if last > end:
            b=bars[last]
            tail=min(b['open'],b['close'])-b['low'] if support else b['high']-max(b['open'],b['close'])
            if tail <= 0:issues.append('event_shadow_requires_incoming_tail')
            known=max(known,A.instant(rows[last]['available_at'],'wave.measurement.available_at'))
            measurement=dict(start_bar_id=bars[start]['id'],end_bar_id=b['id'],
                endpoint_component='incoming_shadow_tip',distance=format(distance,'f'),
                atr_bar_id=bars[ai]['id'],baseline_atr=baseline,threshold_atr=format(threshold,'f'),
                available_at=A.stamp(known),source_ref='TREX-256',counts_as_directional_body=False)
    else:
        A.fail('unsupported_preleg_route',path+'.preleg.route','unsupported')

    annotations=e.get('wave_review',{'noise':[]})
    A.obj(annotations,['noise'],[],path+'.wave_review')
    if not isinstance(annotations['noise'],list) or len(annotations['noise'])>100:
        A.fail('invalid_noise_review_list',path+'.wave_review.noise')
    reviews={}
    for n in annotations['noise']:
        A.obj(n,['bar_id','classification','source_ref','available_at'],['bypass_bar_id'],path+'.wave_review.noise')
        for k in ['bar_id','source_ref']:A.string(n[k],path+'.wave_review.noise.'+k)
        if n['bar_id'] in reviews:A.fail('duplicate_noise_review',path+'.wave_review.noise')
        if n['classification'] not in ['no_pivot','settlement_bypassed']:
            A.fail('unsupported_noise_classification',path+'.wave_review.noise')
        if n['classification']=='settlement_bypassed':
            A.string(n.get('bypass_bar_id'),path+'.wave_review.noise.bypass_bar_id')
        elif 'bypass_bar_id' in n:A.fail('bypass_only_for_settlement',path+'.wave_review.noise')
        t=A.instant(n['available_at'],path+'.wave_review.noise.available_at')
        if n['bar_id'] not in ids or not start <= ids[n['bar_id']] <= end or toward(bars[ids[n['bar_id']]]):
            issues.append('noise_annotation_outside_counter_bar_span')
            continue
        bi=ids[n['bar_id']]
        if t < bars[bi]['available_at']:
            issues.append('noise_review_predates_bar')
        if t > as_of:
            issues.append('noise_review_not_yet_available')
            continue
        reviews[n['bar_id']]=n
        known=max(known,t)

    counter=[]; accepted=[]; last_master=None; last_split=None
    for i in range(start,end+1):
        b=bars[i]
        if shadow_endpoint and i==end:
            continue  # Reference shadow can end the wave without supplying a third directional body.
        if toward(b):
            if rows[i]['standard_dominant_candidate']:last_master=i
            if media_master_review(rows[i])['split_shadow_candidate']:last_split=i
            continue
        counter.append(b['id']);n=reviews.get(b['id'])
        if n is None:
            issues.append('unreviewed_counter_bar:'+b['id']);continue
        if n['classification']=='no_pivot':
            if last_split is not None and (b['close']>bars[last_split]['high'] if support else b['close']<bars[last_split]['low']):
                issues.append('no_pivot_claim_requires_split_shadow_review:'+b['id']);continue
            # A strict cover of an earlier geometric master contradicts this narrow assertion.
            if last_master is not None and (b['close']>bars[last_master]['high'] if support else b['close']<bars[last_master]['low']):
                issues.append('no_pivot_claim_conflicts_with_master_cover:'+b['id']);continue
        else:
            j=ids.get(n['bypass_bar_id'],-1)
            if not i < j <= end:
                issues.append('settlement_bypass_must_be_later_and_in_preleg:'+b['id']);continue
            passed=bars[j]['close']<b['low'] if support else bars[j]['close']>b['high']
            t=A.instant(n['available_at'],'noise.available_at')
            if not passed or t < bars[j]['available_at']:
                issues.append('settlement_bypass_not_proven_by_closed_price:'+b['id']);continue
            known=max(known,bars[j]['available_at'])
        accepted.append(b['id'])
    known=max(known,A.instant(rows[end]['available_at'],'wave.available_at'))
    result=dict(status='needs_review' if issues else 'consistent_with_supplied_context',
        route=p['route'],start_bar_id=bars[start]['id'],end_bar_id=bars[end]['id'],
        inspected_bar_ids=[b['id'] for b in bars[start:end+1]],counter_bar_ids=counter,
        accepted_noise_bar_ids=accepted,issues=issues,shadow_endpoint_bar_id=bars[end]['id'] if shadow_endpoint else None,
        context_classifications_independently_verified=False)
    if measurement is not None:result['measurement']=measurement
    return result,known


def ordinary(e, level, bars, rows, as_of, base, A):
    ids={b['id']:i for i,b in enumerate(bars)}
    out=dict(base)
    # Missing own timeframe or unknown inputs cannot be qualified by geometry.
    if 'master_bar_id' not in e or e['master_bar_id'] not in ids or base['status']=='needs_context':
        return out
    m=ids[e['master_bar_id']];support=level['side']=='support'
    media=media_master_review(rows[m])
    w,known=wave(e,bars,rows,m,support,as_of,A,shadow_endpoint=shadow_master(rows[m]))
    reasons=list(w['issues'])
    geometric=rows[m]['standard_dominant_candidate'] is True
    if not geometric:reasons.append('master_outside_strict_standard_dominant_profile')
    if media['split_shadow_candidate']:reasons.append('split_shadow_master_requires_source_variant_review')
    start=ids.get(w.get('start_bar_id'),m)
    stop=next((i for i in range(m+1,len(bars)) if
        (bars[i]['close']>bars[m]['high'] if support else bars[i]['close']<bars[m]['low'])),len(bars))
    toward=lambda b:b['close']<b['open'] if support else b['close']>b['open']
    candidates=[i for i in range(start,stop) if rows[i]['standard_dominant_candidate'] and (toward(bars[i]) or shadow_master(rows[i]))]
    latest=candidates[-1] if candidates else None
    if latest is not None and latest>m:reasons.append('later_master_candidate_requires_reselection')
    split_ids=[bars[i]['id'] for i in range(m+1,stop) if media_master_review(rows[i])['split_shadow_candidate']]
    if split_ids:reasons.append('intervening_split_shadow_master_requires_review')
    # Every supported intervening exception screen requires review. In
    # particular a self-cover can be neither standard-dominant nor a pinbar.
    # Exclude first cover and later bars: they cannot revise this event.
    exception_candidates=[]
    for i,b in enumerate(bars[m+1:stop],m+1):
        shape=exception_shapes(b,rows[i],support)
        families=[family for family,key in [('pinbar','workbook_pinbar'),('long_shadow','long_shadow'),('self_cover','self_cover_screen')]
                  if shape[key] is True]
        if not toward(b) and rows[i]['standard_dominant_candidate']:families.append('opposite_standard_dominant')
        if not toward(b) and rows[i]['pinbar_candidate'] and 'pinbar' not in families:families.append('opposite_body_pinbar')
        if families:exception_candidates.append(dict(bar_id=b['id'],families=families))
    exceptions=[c['bar_id'] for c in exception_candidates]
    if exceptions:reasons.append('intervening_exception_requires_reference_review')
    prior=base.get('effective_available_at',A.stamp(level['available_at']))
    effective=max(known,A.instant(prior,'pivot.effective_available_at'))
    if level['available_at'] < known:reasons.append('level_availability_predates_wave_review')
    q=dict(status='needs_review' if reasons else 'consistent_with_supplied_context',
        geometric_master_candidate=geometric,master_geometry='dominant_shadow' if shadow_master(rows[m]) else 'body_or_unqualified',selected_master_bar_id=e['master_bar_id'],
        latest_candidate_bar_id=bars[latest]['id'] if latest is not None else None,
        candidate_bar_ids=[bars[i]['id'] for i in candidates],exception_candidate_ids=exceptions,
        intervening_exception_candidates=exception_candidates,
        media_shape_review=media,split_shadow_candidate_ids=split_ids,
        selection_scope='declared_wave_start_until_first_cover; frozen_after_cover',
        wave=w,issues=reasons,independent_method_validation=False)
    out.update(pivot_qualification=q,arithmetic_status=base['status'],
        obstacle_available_at=prior,effective_available_at=A.stamp(effective))
    if base['status']=='ordinary_contract_consistent' and reasons:
        out['status']='needs_pivot_review'
        out['issues']=list(base['issues'])+reasons
    elif media['split_shadow_candidate'] and base.get('issues')==['ordinary_master_direction_conflicts_with_preleg']:
        # A known alternative shape cannot be rejected solely for its body colour.
        # Preserve the old arithmetic result while requiring review, never confirmation.
        out['status']='needs_pivot_review'
        out['issues']=list(base['issues'])+reasons
    return out


def gate(status, reasons=(), sources=()):
    return dict(status=status,reason_codes=list(reasons),source_refs=list(sources))


def exception_diagnostics(gates, conditions=None, measurements=None):
    """Triage only. A passed descriptive screen cannot confer method authority."""
    statuses=[g['status'] for g in gates.values()]
    decision=('selected_profile_not_met' if 'fail' in statuses else
              'insufficient_evidence' if 'insufficient_data' in statuses else
              'needs_context_review' if 'needs_review' in statuses else
              'needs_method_confirmation')
    out=dict(profile='exception_review_v1',decision=decision,gates=gates,
        failed_conditions=[k for k,v in (conditions or {}).items() if v is False],
        unknown_conditions=[k for k,v in (conditions or {}).items() if v is None],
        unresolved_gates=[k for k,v in gates.items() if v['status']!='pass'],
        automatic_confirmation_allowed=False,independent_method_validation=False,
        intrabar_path_known=False)
    if conditions is not None:out['conditions']=conditions
    if measurements is not None:out['measurements']=measurements
    return out


def review_gates():
    return dict(data=gate('insufficient_data',['closed_own_timeframe_event_required']),
        shape=gate('insufficient_data',['event_geometry_not_evaluated']),
        incoming_wave=gate('insufficient_data',['incoming_wave_not_evaluated']),
        level_construction=gate('insufficient_data',['construction_not_evaluated']),
        availability=gate('insufficient_data',['availability_not_evaluated']),
        level_kind=gate('insufficient_data',['level_kind_not_evaluated']),
        method_confirmation=gate('needs_method_review',[
            'exception_subtype_evidence_required','subtype_confirmation_rule_required',
            'structural_timeframe_context_requires_review'],['TREX-135','TREX-166','PIV-TREX-246']))


def wave_gate(w):
    issues=w['issues']
    failures=('preleg_order_or_end_invalid','preleg_does_not_meet_selected_route',
        'event_shadow_requires_incoming_tail','no_pivot_claim_conflicts_with_master_cover',
        'settlement_bypass_must_be_later_and_in_preleg','settlement_bypass_not_proven_by_closed_price',
        'noise_review_predates_bar','noise_annotation_outside_counter_bar_span')
    missing=('preleg_bars_not_available','preleg_atr_unavailable','preleg_atr_not_positive')
    codes=[s.split(':',1)[0] for s in issues]
    status=('fail' if any(s in failures for s in codes) else
            'insufficient_data' if any(s in missing for s in codes) else
            'needs_review' if issues else 'pass')
    return gate(status,issues,['PIV-TREX-178','PIV-TREX-222','PIV-TREX-246','TREX-256','EXR-DESIGN-01'])


def source_scope_review(family, tail=None, atr=None):
    """Source interpretation, never an alternative permission-producing detector.

    The teacher's exemption principle is known. Proving that an arbitrary bar
    belongs to that source-defined subtype is a different, unresolved question.
    The 0.6 comparison describes a textual variant, not an inclusive new rule.
    """
    ratio=tail/atr if tail is not None and atr is not None and atr>0 else None
    out=dict(profile='source_scope_review_v1',
        selected_geometry_profile={'long_shadow':'workbook_B7_shadow_ge_1_atr',
            'pinbar':'workbook_B8_extreme_fifth','self_cover':'engineering_self_cover_screen'}[family],
        exhaustive_trex_classifier=False,profile_failure_rejects_whole_method=False,
        media_family_review=dict(source_refs=['MED16-AUDIO-165'],
            body_condition='ignored_in_shadow_length_review' if family=='long_shadow' else 'separate_body_condition_required',
            pinbar_body_ratio_denominator='whole_candle_range',
            shadow_length_comparison_denominator='prior_atr_selected_profile',
            numeric_source_branch_thresholds_verified=False,
            exact_self_cover_ratio_verified=False,automatic_confirmation_allowed=False),
        exemption_principle=dict(source_supported=True,
            applies_to='correctly_identified_source_exception_only',
            candidate_membership_verified=False,entry_permission=False,
            source_refs=['SR15-TREX-150','SR15-TREX-197','SR15-TREX-370']),
        contextual_exclusion=dict(status='not_evaluated',
            requires='pinbar_after_long_or_spike_breakout_of_compression',
            applies_to='trend_reversal_extended_third_target_expectation',
            blanket_post_spike_ban=False,delete_obstacle=False,
            source_refs=['SR15-TREX-229','SR15-TREX-370']))
    if family=='long_shadow':
        out['textual_variant']=dict(source_ref='SR15-TREX-225',shadow_atr_ratio=format(ratio,'f') if ratio is not None else None,
            relation_to_0_6_atr=None if ratio is None else 'below' if ratio<Decimal('.6') else 'above' if ratio>Decimal('.6') else 'equal',
            threshold_inclusivity_verified=False,baseline_equivalence_verified=False,
            diagnostic_only=True,automatic_confirmation_allowed=False)
    return out


def pinbar_details(b, r, support, A):
    """Explain the frozen candle screen; do not infer a confirmation rule.

    Zero noise is an exact price fact, not a rounded ratio. The existing
    dimensionless comparison tolerance still applies at 0.1, 0.2, 0.8 and 1.2.
    """
    detail=dict(profile='pinbar_confirmation_review_v1',classification='insufficient_data',
        subtype=None,noise_state='not_computable',geometry_only=True,
        confirmation_requirement='unresolved',confirmation_rule_verified=False,
        confirmation_exemption_granted=False,trigger_entry_verified=False,
        intrabar_path_known=False,
        next_checks=['source_defined_subtype_confirmation_rule','structural_timeframe_and_wave_context',
                     'closed_trigger_timeframe_entry_setup','entry_zone_reached_without_assumed_intrabar_order'],
        source_refs=['X-CANDLE-B8','PBC-TREX-135','PBC-TREX-147','PBC-TREX-160','PBC-DESIGN-01'])
    if b is None:return detail,{}
    o,h,l,c=[b[k] for k in ['open','high','low','close']]
    size=h-l
    tail=min(o,c)-l if support else h-max(o,c)
    opposite=h-max(o,c) if support else min(o,c)-l
    baseline=Decimal(r['baseline_atr']) if r['baseline_atr'] is not None else None
    usable=baseline is not None and baseline>0
    fifth=(A.snap((min(o,c)-l)/size,['.8'])[0]>=Decimal('.8') if support else
           A.snap((max(o,c)-l)/size,['.2'])[0]<=Decimal('.2')) if size else None
    noise_limit=A.snap(opposite/size,['.1'])[0]<=Decimal('.1') if size else None
    conditions=dict(positive_range=size>0,positive_prior_atr=True if usable else None,
        length_at_least_1_2_prior_atr=(Decimal(r['comparison_ratio'])>=Decimal('1.2') if size else False) if usable else None,
        open_close_in_side_extreme_fifth=fifth,opposite_shadow_at_most_tenth=noise_limit,
        incoming_shadow_dominates=tail>opposite)
    state=('selected_profile_not_met' if any(v is False for v in conditions.values()) else
           'insufficient_data' if any(v is None for v in conditions.values()) else 'matched_geometry')
    noise=('not_computable' if not size else 'zero' if opposite==0 else
           'present_within_limit' if noise_limit else 'over_limit')
    detail.update(classification=state,noise_state=noise,
        subtype=('noise_free_pinbar' if opposite==0 else 'pinbar_with_noise') if state=='matched_geometry' else None,
        failed_conditions=[k for k,v in conditions.items() if v is False],
        unknown_conditions=[k for k,v in conditions.items() if v is None])
    return detail,conditions


def exceptional(e,level,bars,rows,timeframe,as_of,A):
    path='level_analysis.evidence'
    A.obj(e,['level_id','profile','event_bar_id','family','preleg','context_source_ref','context_available_at'],['wave_review'],path)
    for k in ['level_id','event_bar_id','context_source_ref']:A.string(e[k],path+'.'+k)
    if e['family'] not in ['pinbar','self_cover','long_shadow']:
        A.fail('unsupported_exception_family',path+'.family','unsupported')
    context=A.instant(e['context_available_at'],path+'.context_available_at')
    out=dict(profile=e['profile'],status='needs_exception_review',construction_verified=False,
             structural_context_verified=False,issues=[],confirmed_at=None,
             exception_family=e['family'],confirmation_exemption_granted=False,
             context_source_ref=e['context_source_ref'])
    out['source_scope_review']=source_scope_review(e['family'])
    if e['family']=='pinbar':out['pinbar_review']=pinbar_details(None,None,None,A)[0]
    gates=review_gates()
    ids={b['id']:i for i,b in enumerate(bars)}
    if level['timeframe']!=timeframe or context>as_of or e['event_bar_id'] not in ids:
        out['issues']=['closed_own_timeframe_exception_and_context_required']
        gates['data']=gate('insufficient_data',out['issues'])
        out['exception_review']=exception_diagnostics(gates)
        return out
    i=ids[e['event_bar_id']];b=bars[i];r=rows[i];support=level['side']=='support'
    gates['data']=gate('pass')
    if i==0:
        out['issues']=['incoming_wave_history_required']
        gates['incoming_wave']=gate('insufficient_data',out['issues'])
        out['exception_review']=exception_diagnostics(gates)
        return out
    # The exception candle is not counted as a third directional body.
    w,known=wave(e,bars,rows,i-1,support,as_of,A,event_shadow=e['family']=='self_cover')
    size=b['high']-b['low'];body=abs(b['close']-b['open'])
    tail=min(b['open'],b['close'])-b['low'] if support else b['high']-max(b['open'],b['close'])
    opposite=b['high']-max(b['open'],b['close']) if support else min(b['open'],b['close'])-b['low']
    reverse=b['close']>b['open'] if support else b['close']<b['open']
    atr=Decimal(r['baseline_atr']) if r['baseline_atr'] is not None else None
    usable_atr=atr is not None and atr>0
    out['source_scope_review']=source_scope_review(e['family'],tail,atr)
    shapes=exception_shapes(b,r,support)
    match=shapes[{'pinbar':'workbook_pinbar','self_cover':'self_cover_screen','long_shadow':'long_shadow'}[e['family']]]
    bounds=A.zone_bounds(b,r,level['side'],level['construction_method'])
    reasons=list(w['issues'])
    if match is False:reasons.append('requested_exception_shape_not_matched')
    if match is None:reasons.append('exception_shape_atr_not_computable')
    if bounds is None or (level['lower'],level['upper'])!=bounds:reasons.append('exception_zone_bounds_disagree')
    if level['origin_at']!=b['open_time']:reasons.append('exception_origin_must_identify_event_bar')
    effective=max(context,known,A.instant(r['available_at'],'exception.available_at'))
    if level['available_at']<effective:reasons.append('level_availability_predates_exception_or_review')
    if level['kind']=='order_node':reasons.append('exception_shape_does_not_validate_order_node')
    reasons.append('exception_subtype_and_confirmation_exemption_require_method_evidence')
    # The arithmetic band is a design screen, distinct from necessary source conditions.
    conditions=dict(positive_range=size>0)
    shape_sources=['EXR-DESIGN-01']
    if e['family']=='self_cover':
        conditions.update(reverse_body=reverse,incoming_shadow_present=tail>0,
            body_fraction_min_screen=body/size>=Decimal('.4') if size else None,
            body_fraction_max_screen=body/size<=Decimal(2)/3 if size else None,
            incoming_shadow_fraction_min_screen=tail/size>=Decimal(1)/3 if size else None,
            opposite_shadow_fraction_max_screen=opposite/size<=Decimal('.1') if size else None)
        shape_sources+=['TREX-269','TREX-412','TREX-179','PIV-DESIGN-01']
    else:
        conditions['positive_prior_atr']=True if usable_atr else None
        if e['family']=='long_shadow':
            conditions['incoming_shadow_at_least_prior_atr']=shapes['long_shadow']
            shape_sources+=['X-CANDLE-B7','TREX-166','PIV-DESIGN-01']
        else:
            conditions.update(workbook_pinbar_geometry=r['pinbar_candidate'] if usable_atr else None,
                incoming_shadow_dominates=tail>opposite)
            detail,pin_conditions=pinbar_details(b,r,support,A)
            out['pinbar_review']=detail
            conditions.update(pin_conditions)
            shape_sources+=['X-CANDLE-B8','PBC-TREX-147']
            gates['method_confirmation']=gate('needs_method_review',[
                'noise_free_pinbar_needs_rule_review' if detail['subtype']=='noise_free_pinbar' else
                'nonzero_noise_does_not_prove_confirmation_exemption' if detail['subtype']=='pinbar_with_noise' else
                'pinbar_subtype_not_established',
                'source_defined_confirmation_rule_missing','closed_trigger_timeframe_entry_setup_required'],
                ['X-CANDLE-B8','PBC-TREX-135','PBC-TREX-160','PBC-DESIGN-01'])
    shape_status=('fail' if any(v is False for v in conditions.values()) else
                  'insufficient_data' if any(v is None for v in conditions.values()) else 'pass')
    gates['data']=gate('pass')
    gates['shape']=gate(shape_status,[k for k,v in conditions.items() if v is not True],shape_sources)
    gates['incoming_wave']=wave_gate(w)
    construction_issues=[s for s in reasons if s in ['exception_zone_bounds_disagree','exception_origin_must_identify_event_bar']]
    gates['level_construction']=gate('insufficient_data' if bounds is None and level['construction_method']=='half_atr' else
        'needs_review' if bounds is None else 'fail' if construction_issues else 'pass',construction_issues)
    timing_issues=[s for s in reasons if s=='level_availability_predates_exception_or_review']
    gates['availability']=gate('fail' if timing_issues else 'pass',timing_issues)
    gates['level_kind']=gate('needs_review',['exception_shape_does_not_validate_order_node']) if level['kind']=='order_node' else gate('pass')
    fmt=lambda v:format(v,'f') if v is not None else None
    measurements=dict(range=fmt(size),body=fmt(body),incoming_shadow=fmt(tail),opposite_shadow=fmt(opposite),
        baseline_atr=fmt(atr),body_fraction=fmt(body/size) if size else None,
        incoming_shadow_fraction=fmt(tail/size) if size else None,
        opposite_shadow_fraction=fmt(opposite/size) if size else None,
        incoming_shadow_atr=fmt(tail/atr) if usable_atr else None,
        expected_bounds=[fmt(v) for v in bounds] if bounds is not None else None)
    out.update(issues=reasons,event_bar_id=b['id'],shape_checks=shapes,wave=w,
        construction_verified=bounds is not None and (level['lower'],level['upper'])==bounds,
        shape_available_at=r['available_at'],effective_available_at=A.stamp(max(effective,level['available_at'])),
        obstacle_available_at=A.stamp(max(context,A.instant(r['available_at'],'shape.available_at'),level['available_at'])),
        screening_profile='descriptive_shapes_v1; not an exhaustive exception detector',
        no_noise_wick=opposite==0,noise_free_pin_requires_confirmation_review=e['family']=='pinbar' and match is True and opposite==0,
        exception_review=exception_diagnostics(gates,conditions,measurements))
    return out
