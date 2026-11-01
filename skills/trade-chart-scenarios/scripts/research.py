"""Bounded offline research, NOT a Trex trading system or execution engine.

Rolling channel discovery and R targets are engineering hypotheses. Only the
strict three-bar candle confirmation reuses the frozen reference contract.
No I/O except bounded local JSON input and stdout. No orders, network or equity.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path

_spec = importlib.util.spec_from_file_location('research_profiles', Path(__file__).with_name('market_profiles.py'))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)
A = M.A
MODEL = 'channel_strict_breakout_research_v1'


def integer(v, path, lo, hi):
    if type(v) is not int or not lo <= v <= hi:
        A.fail('invalid_bounded_integer', path)
    return v


def digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def config(raw):
    p = A.obj(raw, ['model', 'lookback', 'target_r', 'max_holding_bars', 'directions'], [], 'setup')
    if p['model'] != MODEL:
        A.fail('unsupported_research_setup', 'setup.model', 'unsupported')
    n = integer(p['lookback'], 'setup.lookback', 3, 500)
    h = integer(p['max_holding_bars'], 'setup.max_holding_bars', 1, 200)
    r = M.bounded(p['target_r'], 'setup.target_r', '0.25', '10')
    if p['directions'] not in [['long'], ['short'], ['long', 'short']]:
        A.fail('invalid_directions', 'setup.directions')
    return n, r, h


def barriers(bar, stop, target, long):
    """Return all possible first-hit exits. Open gaps have known priority."""
    o, h, l = (bar[k] for k in ['open', 'high', 'low'])
    if (o <= stop if long else o >= stop):
        return [(o, 'gap_stop', True, bar['open_time'])]
    if (o >= target if long else o <= target):
        return [(o, 'gap_target', True, bar['open_time'])]
    stop_hit = l <= stop if long else h >= stop
    target_hit = h >= target if long else l <= target
    return ([(stop, 'stop', False, bar['close_time'])] if stop_hit else []) + (
        [(target, 'target', False, bar['close_time'])] if target_hit else [])


def simulate(bars, entry_index, stop, target, long, holding, profile):
    first = bars[entry_index]
    reference_entry = first['open']
    risk = abs(reference_entry-stop)
    if risk == 0 or not (stop < reference_entry < target if long else target < reference_entry < stop):
        return dict(status='skipped', reason='entry_open_outside_frozen_stop_target')
    entry = M.execution_price(reference_entry, long, profile)
    last_index = min(len(bars)-1, entry_index+holding-1)
    for j in range(entry_index, last_index+1):
        bar = bars[j]
        exits = barriers(bar, stop, target, long)
        if not exits and j == entry_index+holding-1:
            exits = [(bar['close'], 'time_exit', True, bar['close_time'])]
        if not exits:
            continue
        outcomes = []
        for price, reason, exact, at in exits:
            fill = M.execution_price(price, not long, profile)
            amount = M.pnl(entry, fill, long, profile, first['open_time'], bar['open_time'], at, exact)
            outcomes.append(dict(reason=reason, chart_price=A.dec(price), fill=A.dec(fill),
                exact_exit_time=A.stamp(at) if exact else None,
                **{k: A.dec(v) if isinstance(v, Decimal) else v for k, v in amount.items()}))
        lower = min(Decimal(x['net_lower']) for x in outcomes)
        upper = max(Decimal(x['net_upper']) for x in outcomes)
        return dict(status='closed', entry_index=entry_index, exit_index=j,
            entry_at=A.stamp(first['open_time']), exit_bar_open=A.stamp(bar['open_time']),
            exit_bar_close=A.stamp(bar['close_time']), outcome_available_at=A.stamp(max(b['available_at'] for b in bars[entry_index:j+1])),
            direction='long' if long else 'short', entry_chart=A.dec(reference_entry), entry_fill=A.dec(entry),
            stop=A.dec(stop), target=A.dec(target), initial_chart_risk=A.dec(risk),
            net_quote_per_base_lower=A.dec(lower), net_quote_per_base_upper=A.dec(upper),
            net_r_lower=A.dec(lower/risk), net_r_upper=A.dec(upper/risk),
            ambiguous=len(exits)>1 or any(x['carry_time_ambiguous'] for x in outcomes), outcomes=outcomes)
    return dict(status='censored', entry_index=entry_index, entry_at=A.stamp(first['open_time']),
        direction='long' if long else 'short', entry_chart=A.dec(reference_entry), entry_fill=A.dec(entry),
        stop=A.dec(stop), target=A.dec(target), reason='future_exit_not_observed',
        last_observed_at=A.stamp(bars[last_index]['close_time']), net_r_lower=None, net_r_upper=None)


def stats(trades):
    with localcontext() as context:
        context.prec = 50
        return _stats(trades)


def _stats(trades):
    closed = [t for t in trades if t['status'] == 'closed']
    lows = [Decimal(t['net_r_lower']) for t in closed]
    highs = [Decimal(t['net_r_upper']) for t in closed]
    wealth = peak = dd = Decimal(0)
    for x in lows:
        wealth += x
        peak = max(peak, wealth)
        dd = max(dd, peak-wealth)
    losses = -sum((x for x in lows if x < 0), Decimal(0))
    gains = sum((x for x in lows if x > 0), Decimal(0))
    return dict(closed=len(closed), censored=sum(t['status']=='censored' for t in trades),
        ambiguous=sum(t.get('ambiguous', False) for t in closed),
        net_r_lower=A.dec(sum(lows, Decimal(0))), net_r_upper=A.dec(sum(highs, Decimal(0))),
        mean_r_lower=A.dec(sum(lows)/len(lows)) if lows else None,
        win_fraction_lower=A.dec(Decimal(sum(x>0 for x in lows))/len(lows)) if lows else None,
        win_fraction_upper=A.dec(Decimal(sum(x>0 for x in highs))/len(highs)) if highs else None,
        profit_factor_lower=A.dec(gains/losses) if losses else None,
        closed_trade_drawdown_r=A.dec(dd),
        qualification='descriptive_not_confidence_or_account_return; drawdown_excludes_intratrade_path')


def run(payload):
    result = dict(schema_version=1, model=MODEL, status='invalid_input', issues=[],
        order=None, position_size=None, profitability_validated=False)
    try:
        with localcontext() as ctx:
            ctx.prec = 50
            A.obj(payload, ['schema_version', 'as_of', 'series', 'instrument', 'setup'], [], 'research')
            if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
                A.fail('unsupported_schema_version', 'schema_version', 'unsupported')
            as_of = A.instant(payload['as_of'], 'as_of')
            series, period, bars, excluded = A.parse_series(payload['series'], as_of)
            if len(bars) < 2:
                A.fail('not_enough_available_bars', 'series', 'insufficient_data')
            if series['timeframe'] not in ['H1', 'H4']:
                A.fail('research_timeframe_not_supported', 'series.timeframe', 'unsupported')
            profile = M.validate(payload['instrument'], series, bars[0]['open_time'], bars[-1]['close_time'])
            n, multiple, holding = config(payload['setup'])
            if profile['spot'] and 'short' in payload['setup']['directions']:
                A.fail('spot_short_requires_separate_borrow_model', 'setup.directions', 'unsupported')
            rows = A.calculate(bars, period)
            trades, rejected = [], Counter()
            busy_until = -1
            for i in range(max(n, period+1), len(bars)-3):
                if i <= busy_until:
                    continue
                window = bars[i-n:i]
                # A later published prior candle cannot become pre-existing context.
                known = max(A.instant(rows[i-1]['available_at'], 'atr.available'), *(b['available_at'] for b in window))
                if known > bars[i]['open_time']:
                    rejected['late_level_or_atr'] += 1
                    continue
                if any(bars[j]['close_time'] != bars[j+1]['open_time'] for j in range(i-1, i+3)):
                    rejected['gap_in_signal_or_entry_sequence'] += 1
                    continue
                for direction in payload['setup']['directions']:
                    long = direction == 'long'
                    edge = max(b['high'] for b in window) if long else min(b['low'] for b in window)
                    level = dict(id='channel', side='resistance' if long else 'support',
                        timeframe=series['timeframe'], lower=edge, upper=edge,
                        available_at=known, source_refs=['ENGINEERING-CHANNEL-NOT-TREX'])
                    request = dict(model='strict_three_bar_confirmation_v1', candidate_bar_id=bars[i]['id'],
                        direction='up' if long else 'down', level_id='channel',
                        context_available_at=A.stamp(known), context_source_ref='ENGINEERING-CHANNEL-NOT-TREX')
                    evaluated = A.evaluate(request, bars[i-1:i+3], rows[i-1:i+3], {'channel': level},
                                           as_of, series['timeframe'])
                    if evaluated['status'] != 'confirmed_under_selected_profile':
                        continue
                    if A.instant(evaluated['available_at'], 'signal.available_at') > bars[i+3]['open_time']:
                        rejected['confirmation_unavailable_at_entry'] += 1
                        continue
                    stop = bars[i]['low'] if long else bars[i]['high']
                    reference = bars[i+2]['close']
                    target = reference + (reference-stop)*multiple
                    if target <= 0:
                        rejected['nonpositive_frozen_target'] += 1
                        continue
                    # A fixed research barrier, not a discovered structural target.
                    trade = simulate(bars, i+3, stop, target, long, holding, profile)
                    if trade['status'] == 'skipped':
                        rejected[trade['reason']] += 1
                        continue
                    trade.update(candidate_bar_id=bars[i]['id'], confirmed_at=evaluated['confirmed_at'],
                        signal_available_at=evaluated['available_at'], channel_edge=A.dec(edge),
                        target_kind='engineering_R_multiple_not_structural_level',
                        utc_entry_block=f'{(bars[i+3]["open_time"].hour//6)*6:02d}-{(bars[i+3]["open_time"].hour//6+1)*6:02d}',
                        prior_range_atr_ratio=A.dec((max(b['high'] for b in window)-min(b['low'] for b in window))/Decimal(rows[i]['baseline_atr'])))
                    trades.append(trade)
                    busy_until = trade.get('exit_index', len(bars))
                    break
            result.update(status='research_only', input_sha256=digest(payload), as_of=A.stamp(as_of),
                instrument=payload['instrument'], setup=payload['setup'], usable_bars=len(bars),
                excluded_tail_bars=excluded, trades=trades, summary=stats(trades), rejected=dict(rejected),
                limits=['engineering_channel_and_R_target_not_Trex_strategy',
                    'constant_spread_slippage_and_explicit_carry_are_assumptions',
                    'last_price_treated_as_synthetic_mid_not_observed_quote',
                    'one_base_unit_quote_PnL_no_rounding_or_order_size_validation',
                    'no_liquidity_borrow_margin_liquidation_or_capacity_model',
                    'closed_available_bars_only; no_intrabar_path_inference',
                    'one_position_per_symbol_per_candidate; no_portfolio_equity',
                    'historical_replay_is_not_live_forward_paper_validation']+
                    (['historical_order_grid_unknown_no_executable_order_validation'] if profile['order_tick'] is None else []))
    except A.InputIssue as exc:
        result.update(status=exc.status, issues=[dict(code=exc.code, path=exc.path)])
    return result


def chronological(trades, start, end):
    """Complete outcomes only when their signal, entry and outcome lie in a fold."""
    start, end = A.instant(start, 'fold.start'), A.instant(end, 'fold.end')
    if start >= end:
        A.fail('invalid_fold', 'fold')
    included, boundary, pending = [], [], []
    for t in trades:
        entry = A.instant(t['entry_at'], 'entry_at')
        if not start <= entry < end:
            continue
        if t['status'] != 'closed':
            pending.append(t)
        elif A.instant(t['signal_available_at'], 'signal_available_at') < start or A.instant(t['outcome_available_at'], 'outcome_available_at') >= end:
            boundary.append(t)
        else:
            included.append(t)
    return dict(summary=stats(included), included=included, purged_boundary=len(boundary), censored=len(pending))


def select_on_validation(candidates, development, validation, minimum_development=5, minimum_validation=3):
    """Choose one shared candidate for explicitly supplied symbols, never holdout.

    candidates is an ordered list of {symbol: trades}. All instruments use the
    same two calendar folds. Missing/no-trade symbols cannot disappear from the
    equal-weight score. Returned eligibility is research eligibility only.
    """
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 24:
        A.fail('invalid_candidate_count', 'candidates')
    integer(minimum_development, 'minimum_development', 1, 10000)
    integer(minimum_validation, 'minimum_validation', 1, 10000)
    for fold in [development, validation]:
        A.obj(fold, ['start', 'end'], [], 'fold')
        if A.instant(fold['start'], 'fold.start') >= A.instant(fold['end'], 'fold.end'):
            A.fail('invalid_fold', 'fold')
    if A.instant(development['end'], 'development.end') > A.instant(validation['start'], 'validation.start'):
        A.fail('overlapping_selection_folds', 'fold')
    symbols = set(candidates[0]) if isinstance(candidates[0], dict) else set()
    if not 1 <= len(symbols) <= 32 or any(not isinstance(c, dict) or set(c)!=symbols for c in candidates):
        A.fail('inconsistent_candidate_instrument_set', 'candidates')
    ranked=[]
    with localcontext() as context:
        context.prec=50
        for i, c in enumerate(candidates):
            scores=[];counts={};eligible=True
            for symbol in sorted(symbols):
                dev=chronological(c[symbol], **development)['summary']
                val=chronological(c[symbol], **validation)['summary']
                counts[symbol]=dict(development=dev['closed'],validation=val['closed'])
                if dev['closed']<minimum_development or val['closed']<minimum_validation:
                    eligible=False
                elif val['mean_r_lower'] is not None:
                    scores.append(Decimal(val['mean_r_lower']))
            ranked.append(dict(candidate_index=i,eligible=eligible,counts=counts,
                               score=A.dec(sum(scores)/len(symbols)) if eligible else None))
        eligible=[x for x in ranked if x['eligible']]
        winner=max(eligible,key=lambda x:(Decimal(x['score']),-x['candidate_index'])) if eligible else None
    return dict(status='selected_for_research_only' if winner else 'insufficient_selection_sample',
        selected_index=winner['candidate_index'] if winner else None, candidates=ranked,
        score_kind='equal_weight_instrument_validation_mean_lower_R', holdout_used=False,
        profitability_validated=False)


def analyze_with_instrument(payload):
    """Explicit bridge for a mixed price/profile document; old analyzer is unchanged."""
    result=dict(schema_version=1,mode='profiled_analysis',status='invalid_input',issues=[],order=None,
                entry_permission=False,profitability_validated=False)
    try:
        optional=['setup','levels','assessment','level_analysis','structure_analysis','target_hierarchy','pivot_review']
        A.obj(payload,['schema_version','as_of','series','instrument'],optional,'profiled_analysis')
        # Only these explicitly known research fields are separated. Unknown keys
        # still fail at the boundary; a proposed setup is never executed here.
        if 'setup' in payload:
            config(payload['setup'])
        with localcontext() as context:
            context.prec=50
            s,_,bars,_=A.parse_series(payload['series'],A.instant(payload['as_of'],'as_of'))
            if len(bars)<2:
                A.fail('not_enough_available_bars','series','insufficient_data')
            profile=M.validate(payload['instrument'],s,bars[0]['open_time'],bars[-1]['close_time'])
            analysis=A.analyze({k:v for k,v in payload.items() if k not in ['instrument','setup']})
        result.update(status='analysis_with_limits' if not analysis['issues'] else analysis['status'],
            issues=analysis['issues'],analysis=analysis,instrument=payload['instrument'],
            setup_executed=False,order_tick_known=profile['order_tick'] is not None,
            review_required=['Read nested level/pivot/target validation and method_scope before any scenario conclusion.',
                'A summary status or empty top-level missing_inputs does not establish structural or entry readiness.'])
    except A.InputIssue as exc:
        result.update(status=exc.status,issues=[dict(code=exc.code,path=exc.path)])
    return result


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--mode',choices=['research','analysis'],default='research')
    args = parser.parse_args()
    try:
        with args.input.open('rb') as f:
            raw = f.read(A.MAX_BYTES+1)
        if len(raw)>A.MAX_BYTES:
            A.fail('input_too_large', 'input')
        value=json.loads(raw.decode('utf-8-sig'), object_pairs_hook=A.no_duplicates,
                         parse_constant=A.reject_constant, parse_float=A.reject_float)
        output=run(value) if args.mode=='research' else analyze_with_instrument(value)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        output=dict(status='invalid_input', issues=[dict(code=getattr(exc,'code','unreadable_json'),path=getattr(exc,'path','input'))])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output['status'] in ['research_only','analysis_with_limits'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
