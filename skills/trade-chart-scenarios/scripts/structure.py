"""Pure causal swing-sequence observations; not a Trex master-candle detector."""


def iso(t):
    return t.isoformat().replace('+00:00', 'Z') if t else None


def public(p):
    if p is None:
        return None
    return {k: (iso(v) if k.endswith('_at') else str(v) if k == 'price' else v)
            for k, v in p.items() if k not in ('index', 'confirmation_index')}


def describe(sequence, bars, i):
    """Four alternating observations, then the actual predecessor of the driver."""
    if len(sequence) < 4:
        return dict(state='insufficient_swings', direction=None, protected=None, driver=None,
                    break_bar_id=None, break_at=None, range_bounds=None)
    recent = sequence[-4:]
    highs = [p for p in recent if p['side'] == 'high']
    lows = [p for p in recent if p['side'] == 'low']
    h1, h2 = highs; l1, l2 = lows
    direction = ('up' if h2['price'] > h1['price'] and l2['price'] > l1['price'] else
                 'down' if h2['price'] < h1['price'] and l2['price'] < l1['price'] else None)
    out = dict(state='transition', direction=direction, protected=None, driver=None,
               break_bar_id=None, break_at=None, range_bounds=None)
    if direction:
        driver = h2 if direction == 'up' else l2
        # A new pullback pivot does not protect a trend until it produces a new extreme.
        guard = next(p for p in reversed(sequence) if p['index'] < driver['index'] and p['side'] != driver['side'])
        breach = next((b for b in bars[guard['index']+1:i+1]
                       if (b['close'] < guard['price'] if direction == 'up' else b['close'] > guard['price'])), None)
        out.update(state='uptrend' if direction == 'up' else 'downtrend',
                   protected=public(guard), driver=public(driver))
        if breach:
            out.update(state='transition', direction=None, break_bar_id=breach['id'], break_at=iso(breach['close_time']))
    elif h2['price'] <= h1['price'] and l2['price'] >= l1['price']:
        lo, hi = l1['price'], h1['price']
        if lo < hi:
            out['range_bounds'] = dict(lower=str(lo), upper=str(hi))
            breach = next((b for b in bars[recent[-1]['index']+1:i+1] if not lo <= b['close'] <= hi), None)
            out.update(state='range_candidate' if breach is None else 'transition',
                       break_bar_id=breach['id'] if breach else None, break_at=iso(breach['close_time']) if breach else None)
    return out


def scan(bars, left, right, max_age):
    """Discover only after right-hand bars close. History is replayed, not relabeled."""
    sequence, pivots, events, ambiguities = [], [], [], []
    available, last_signature, last_accepted = None, None, None
    state = dict(state='insufficient_swings', direction=None, protected=None, driver=None,
                 break_bar_id=None, break_at=None, range_bounds=None)
    last_changed = None
    ambiguous_since = None
    active = None
    broken = None
    reset_index = -1
    for i, bar in enumerate(bars):
        available = max(available, bar['available_at']) if available else bar['available_at']
        center = i-right
        if center >= left:
            candidate = bars[center]
            neighbors = bars[center-left:center]+bars[center+1:i+1]
            hi = all(candidate['high'] > b['high'] for b in neighbors)
            lo = all(candidate['low'] < b['low'] for b in neighbors)
            if hi and lo:
                # OHLC cannot reveal the intrabar order of the two extremes.
                ambiguities.append(dict(bar_id=candidate['id'], available_at=iso(available),
                                        reason='outside_bar_extreme_order_unknown'))
                sequence = []
                last_accepted = i
                ambiguous_since = candidate['id']
                reset_index = center
            elif hi or lo:
                side = 'high' if hi else 'low'
                p = dict(id=side+':'+candidate['id'], bar_id=candidate['id'], side=side,
                         price=candidate[side], index=center, confirmation_index=i,
                         origin_at=candidate['open_time'], confirmed_at=bar['close_time'], available_at=available)
                action = 'append'
                if center <= reset_index:
                    action = 'ignore_before_structure_reset'
                elif sequence and sequence[-1]['side'] == side:
                    old = sequence[-1]
                    stronger = p['price'] > old['price'] if hi else p['price'] < old['price']
                    action = 'replace_same_side' if stronger else 'ignore_same_side'
                    if stronger:
                        sequence[-1] = p
                else:
                    sequence.append(p)
                pivots.append({**public(p), 'sequence_action': action})
                if action in ('append','replace_same_side'):
                    last_accepted = i
                if len(sequence) >= 4:
                    ambiguous_since = None
        pattern = describe(sequence, bars, i)
        if active:
            direction, guard, driver = active
            breach = bar['close'] < guard['price'] if direction == 'up' else bar['close'] > guard['price']
            if breach:
                broken = dict(state='transition', direction=None, protected=public(guard), driver=public(driver),
                              break_bar_id=bar['id'], break_at=iso(bar['close_time']), range_bounds=None)
                active = None
                sequence = []
                reset_index = i
            else:
                candidates = [p for p in sequence if p['side'] == driver['side'] and p['index'] > driver['index']]
                if candidates:
                    new_driver = candidates[-1]
                    extends = new_driver['price'] > driver['price'] if direction == 'up' else new_driver['price'] < driver['price']
                    if extends:
                        new_guard = next((p for p in reversed(sequence) if p['index'] < new_driver['index'] and p['side'] != new_driver['side']),None)
                        if new_guard:
                            improves = new_guard['price'] > guard['price'] if direction == 'up' else new_guard['price'] < guard['price']
                            intact = all(b['close'] >= new_guard['price'] if direction == 'up' else b['close'] <= new_guard['price']
                                         for b in bars[new_guard['index']+1:i+1])
                            if improves and intact:
                                guard = new_guard
                        driver = new_driver
                        active = direction,guard,driver
        elif pattern['direction'] and not ambiguous_since:
            direction = pattern['direction']
            guard = next(p for p in sequence if p['id']==pattern['protected']['id'])
            driver = next(p for p in sequence if p['id']==pattern['driver']['id'])
            active = direction,guard,driver
            broken = None
        state = dict(broken) if broken else pattern
        if broken and pattern['state']=='range_candidate':
            state = dict(pattern,prior_structure_break=dict(broken))
        if active:
            direction,guard,driver = active
            state = dict(state='uptrend' if direction=='up' else 'downtrend',direction=direction,
                         protected=public(guard),driver=public(driver),break_bar_id=None,break_at=None,range_bounds=None)
            state['internal_swing_pattern'] = pattern['state']
            state['phase'] = ('extension' if (bar['close'] > driver['price'] if direction=='up' else bar['close'] < driver['price'])
                              else 'pullback_or_pause')
        if ambiguous_since and not active:
            state.update(state='ambiguous', direction=None)
        state['swing_order_review_pending'] = ambiguous_since is not None
        age_anchor = active[2]['confirmation_index'] if active else last_accepted
        age = i-age_anchor if age_anchor is not None else None
        structural_state = state['state']
        if age is not None and age > max_age:
            state.update(state='stale_structure', direction=None)
        state.update(underlying_state=structural_state, pivot_age_bars=age)
        signature = (state['state'], state['direction'], (state.get('protected') or {}).get('id'),
                     (state.get('driver') or {}).get('id'), state['break_bar_id'],state['swing_order_review_pending'],
                     tuple((state.get('range_bounds') or {}).values()))
        if signature != last_signature:
            last_changed = available
            events.append(dict(observed_on_bar_id=bar['id'], observed_at=iso(bar['close_time']),
                               available_at=iso(available), **state))
            last_signature = signature
    return dict(**state, last_changed_at=iso(last_changed), available_at=iso(available),
                last_closed_bar_id=bars[-1]['id'] if bars else None,
                last_close_at=iso(bars[-1]['close_time']) if bars else None,
                last_close=str(bars[-1]['close']) if bars else None,
                recent_sequence=[public(p) for p in sequence[-6:]], pivots=pivots,
                ambiguities=ambiguities, events=events,
                trex_master_context_verified=False)


def alignment(higher, lower):
    """Compare completed overlapping aggregates; never consume unavailable candles."""
    checked, incomplete = 0, 0
    cursor = 0
    for high in higher:
        while cursor < len(lower) and lower[cursor]['close_time'] <= high['open_time']:
            cursor += 1
        members = []
        j = cursor
        while j < len(lower) and lower[j]['open_time'] < high['close_time']:
            b = lower[j]
            if b['open_time'] < high['open_time'] or b['close_time'] > high['close_time']:
                return dict(status='misaligned_boundaries', higher_bar_id=high['id'], lower_bar_id=b['id'])
            members.append(b); j += 1
        if (not members or members[0]['open_time'] != high['open_time'] or
                members[-1]['close_time'] != high['close_time'] or
                any(a['close_time'] != b['open_time'] for a,b in zip(members,members[1:]))):
            incomplete += 1
            continue
        expected = dict(open=members[0]['open'], high=max(b['high'] for b in members),
                        low=min(b['low'] for b in members), close=members[-1]['close'])
        if any(high[k] != expected[k] for k in expected):
            return dict(status='inconsistent_aggregate', higher_bar_id=high['id'])
        checked += 1
    return dict(status='consistent_overlap' if checked else 'no_complete_overlap',
                checked_bars=checked, uncovered_bars=incomplete)


def combine(roles):
    if set(roles) != {'structure', 'pattern', 'trigger'}:
        return 'missing_timeframes', None
    if any(not r['fresh'] for r in roles.values()):
        return 'stale_data', None
    a,b,c = [roles[k]['structure'] for k in ['structure','pattern','trigger']]
    if a['state'] in ('insufficient_swings','ambiguous','stale_structure'):
        return 'unresolved_structure', None
    if a['state'] == 'range_candidate':
        return 'structure_range_candidate', None
    if not a['direction']:
        return 'structure_transition', None
    direction = a['direction']
    if b['direction'] and b['direction'] != direction:
        return 'higher_timeframe_pullback', direction
    if not b['direction']:
        return 'pattern_unresolved', direction
    if c['direction'] and c['direction'] != direction:
        return 'trigger_countermove', direction
    if not c['direction']:
        return 'trigger_unresolved', direction
    if any(s.get('swing_order_review_pending') for s in (a,b,c)):
        return 'aligned_with_swing_ambiguity', direction
    return 'aligned', direction
