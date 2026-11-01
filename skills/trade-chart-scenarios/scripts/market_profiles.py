"""Explicit research-only instrument/cost contract. No broker access or orders."""
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path

_spec = importlib.util.spec_from_file_location('profile_analysis', Path(__file__).with_name('analyze.py'))
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)

PROFILE_ID = 'linear_quote_research_v1'


def bounded(value, path, low, high):
    x = A.number(value, path, positive=False)
    if not Decimal(low) <= x <= Decimal(high):
        A.fail('number_outside_research_bounds', path)
    return x


def validate(raw, series, start, end):
    """Validate identity and coverage, never infer missing costs or contract type."""
    p = A.obj(raw, ['schema_version', 'profile_id', 'symbol', 'market', 'venue',
        'feed_id', 'price_basis', 'contract_type', 'base_currency', 'quote_currency',
        'settlement_currency', 'valid_from', 'valid_until', 'data_tick_size',
        'order_tick_evidence', 'costs'], ['order_tick_size'], 'instrument')
    if 'order_tick_size' not in p:
        A.fail('required_field_missing', 'instrument.order_tick_size', 'insufficient_data')
    if type(p['schema_version']) is not int or p['schema_version'] != 1 or p['profile_id'] != PROFILE_ID:
        A.fail('unsupported_instrument_profile', 'instrument', 'unsupported')
    for k in ['symbol', 'market', 'feed_id', 'price_basis']:
        if p[k] != series[k]:
            A.fail('instrument_series_identity_mismatch', 'instrument.' + k)
    for k in ['venue', 'base_currency', 'quote_currency', 'settlement_currency', 'order_tick_evidence']:
        A.string(p[k], 'instrument.' + k)
    if p['base_currency'] == p['quote_currency'] or p['settlement_currency'] != p['quote_currency']:
        A.fail('only_linear_quote_settlement_supported', 'instrument', 'unsupported')
    allowed = {'forex': ['forex_otc_linear'], 'crypto': ['crypto_spot', 'crypto_perpetual_linear']}
    if p['contract_type'] not in allowed.get(p['market'], []):
        A.fail('contract_market_mismatch_or_unsupported', 'instrument.contract_type', 'unsupported')
    data_tick = A.number(p['data_tick_size'], 'instrument.data_tick_size')
    # Unknown historical order grid is honest input, not a fabricated precision.
    # This profile never creates or rounds orders; any future order path must
    # independently require a verified grid and account-specific constraints.
    order_tick = None if p['order_tick_size'] is None else A.number(p['order_tick_size'], 'instrument.order_tick_size')
    if data_tick != A.number(series['tick_size'], 'series.tick_size'):
        A.fail('data_grid_mismatch', 'instrument.data_tick_size')
    # Different grids are deliberate. Neither is inferred from decimal precision.
    first, last = [A.instant(p[k], 'instrument.' + k) for k in ['valid_from', 'valid_until']]
    if not first <= start < end <= last:
        A.fail('instrument_validity_does_not_cover_data', 'instrument')
    c = A.obj(p['costs'], ['evidence_status', 'source_ref', 'observed_at',
        'valid_from', 'valid_until', 'spread_price', 'slippage_price', 'fee_bps_per_side',
        'carry_model', 'carry_events', 'carry_schedule_complete'], [], 'instrument.costs')
    if c['evidence_status'] not in ['hypothetical', 'documented_unverified']:
        A.fail('research_cost_status_unsupported', 'instrument.costs.evidence_status')
    # A supplied URL is not verification. This helper cannot certify actual fills.
    A.string(c['source_ref'], 'costs.source_ref')
    A.instant(c['observed_at'], 'costs.observed_at')
    cfirst, clast = [A.instant(c[k], 'costs.' + k) for k in ['valid_from', 'valid_until']]
    if not cfirst <= start < end <= clast:
        A.fail('cost_coverage_incomplete', 'instrument.costs')
    spread = bounded(c['spread_price'], 'costs.spread_price', '0', '1000000000')
    slip = bounded(c['slippage_price'], 'costs.slippage_price', '0', '1000000000')
    fee = bounded(c['fee_bps_per_side'], 'costs.fee_bps_per_side', '0', '1000')
    if c['carry_model'] not in ['none_spot', 'explicit_signed_entry_notional_bps']:
        A.fail('carry_model_required', 'costs.carry_model')
    spot = p['contract_type'] == 'crypto_spot'
    if (c['carry_model'] == 'none_spot') != spot:
        A.fail('carry_contract_mismatch', 'costs.carry_model')
    if c['carry_schedule_complete'] is not True:
        A.fail('explicit_complete_carry_schedule_required', 'costs.carry_schedule_complete', 'insufficient_data')
    events = c['carry_events']
    if not isinstance(events, list) or len(events) > 10000 or (spot and events):
        A.fail('invalid_carry_events', 'costs.carry_events')
    parsed, previous = [], None
    for i, e in enumerate(events):
        path = f'costs.carry_events[{i}]'
        A.obj(e, ['at', 'long_bps', 'short_bps'], [], path)
        at = A.instant(e['at'], path + '.at')
        if not cfirst <= at <= clast or (previous is not None and at <= previous):
            A.fail('unordered_duplicate_or_uncovered_carry', path)
        parsed.append(dict(at=at, **{k: bounded(e[k], path + '.' + k, '-1000', '1000')
                                    for k in ['long_bps', 'short_bps']}))
        previous = at
    if not spot and not events:
        A.fail('explicit_carry_schedule_required', 'costs.carry_events', 'insufficient_data')
    return dict(raw=p, spread=spread, slippage=slip, fee_bps=fee, carry=parsed,
                order_tick=order_tick, spot=spot, price_basis=p['price_basis'])


def execution_price(chart_price, buy, profile):
    """Synthetic market fill with an explicit constant spread and adverse slippage."""
    half = profile['spread'] / 2
    mid = chart_price + {'bid': half, 'ask': -half, 'mid': Decimal(0), 'last': Decimal(0)}[profile['price_basis']]
    fill = mid + (half + profile['slippage']) * (1 if buy else -1)
    if fill <= 0:
        A.fail('cost_assumptions_produce_nonpositive_fill', 'instrument.costs')
    return fill


def pnl(entry, exit_price, long, profile, entered_at, exit_open, exit_close, exact_exit):
    """Quote currency per one base unit; signed carry: positive is a cost.

    For a barrier touch with unknown time, carry events inside the exit bar are
    optional in the outcome interval. Carry at the entry instant is excluded;
    at an exact exit instant it is included. No funding interval is inferred.
    """
    gross = (exit_price - entry) * (1 if long else -1)
    fees = (entry + exit_price) * profile['fee_bps'] / Decimal(10000)
    certain = Decimal(0)
    optional = []
    for e in profile['carry']:
        cost = entry * e['long_bps' if long else 'short_bps'] / Decimal(10000)
        if e['at'] <= entered_at:
            continue
        if exact_exit:
            if e['at'] <= exit_close:
                certain += cost
        elif e['at'] <= exit_open:
            certain += cost
        elif e['at'] <= exit_close:
            optional.append(cost)
    maximum = certain + sum((max(Decimal(0), x) for x in optional), Decimal(0))
    minimum = certain + sum((min(Decimal(0), x) for x in optional), Decimal(0))
    return dict(gross=gross, fees=fees, carry_min=minimum, carry_max=maximum,
                net_lower=gross-fees-maximum, net_upper=gross-fees-minimum,
                carry_time_ambiguous=any(x != 0 for x in optional))
