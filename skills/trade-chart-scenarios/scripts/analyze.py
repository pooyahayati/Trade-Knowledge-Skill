"""Offline reference calculations for an explicitly supplied chart scenario.

No network, account, order, subprocess or file-writing capability. Python stdlib.
The agent supplies structural judgment; optional swing discovery returns candidates only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

VERSION = "0.17.0"
SKILL = Path(__file__).resolve().parents[1]
CONTRACT_BYTES = (SKILL / "references/rule-contract.v1.json").read_bytes()
CONTRACT = json.loads(CONTRACT_BYTES)
LEVEL_CONTRACT_BYTES = (SKILL / "references/levels-targets-contract.v1.json").read_bytes()
LEVEL_CONTRACT = json.loads(LEVEL_CONTRACT_BYTES)
STRUCTURE_CONTRACT_BYTES = (SKILL / 'references/structure-mtf-contract.v1.json').read_bytes()
STRUCTURE_CONTRACT = json.loads(STRUCTURE_CONTRACT_BYTES)
SEMANTIC_CONTRACT_BYTES = (SKILL / 'references/semantic-contract.v1.json').read_bytes()
SEMANTIC_CONTRACT = json.loads(SEMANTIC_CONTRACT_BYTES)
TARGET_CONTRACT_BYTES = (SKILL / 'references/target-hierarchy-contract.v1.json').read_bytes()
TARGET_CONTRACT = json.loads(TARGET_CONTRACT_BYTES)
PIVOT_CONTRACT_BYTES = (SKILL / 'references/pivot-review-contract.v1.json').read_bytes()
PIVOT_CONTRACT = json.loads(PIVOT_CONTRACT_BYTES)
EXCEPTION_SHADOW_BYTES = (SKILL / 'references/exception-shadow-contract.v1.json').read_bytes()
EXCEPTION_SHADOW_CONTRACT = json.loads(EXCEPTION_SHADOW_BYTES)
EXCEPTION_REVIEW_BYTES = (SKILL / 'references/exception-review-contract.v1.json').read_bytes()
EXCEPTION_REVIEW_CONTRACT = json.loads(EXCEPTION_REVIEW_BYTES)
PINBAR_CONFIRMATION_BYTES = (SKILL / 'references/pinbar-confirmation-contract.v1.json').read_bytes()
PINBAR_CONFIRMATION_CONTRACT = json.loads(PINBAR_CONFIRMATION_BYTES)
MEDIA_REVIEW_BYTES = (SKILL / 'references/media-review-contract.v1.json').read_bytes()
MEDIA_REVIEW_CONTRACT = json.loads(MEDIA_REVIEW_BYTES)
CLAUSES = {c["id"]: c for c in CONTRACT["clauses"]}
PERIODS = CLAUSES["VOL-C01"]["behavior"]["periods"]
LADDER = CLAUSES["TF-C01"]["behavior"]["analysis_ladder_high_to_low"]
PROFILE = "excel_periods_wilder_v1"
EPS = Decimal("0.000000001")
MAX_BYTES = 8 * 1024 * 1024
MAX_BARS = 10000
# Shared bounded budget for supplied and discovered obstacle inventories.
# Never truncate a long history: an omitted nearer level changes the scenario.
MAX_LEVELS = 2000
_spec = importlib.util.spec_from_file_location('trade_structure', Path(__file__).with_name('structure.py'))
STRUCTURE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(STRUCTURE)
_pivot_spec = importlib.util.spec_from_file_location('trade_pivots', Path(__file__).with_name('pivots.py'))
PIVOTS = importlib.util.module_from_spec(_pivot_spec)
_pivot_spec.loader.exec_module(PIVOTS)


class InputIssue(ValueError):
    def __init__(self, status, code, path):
        self.status, self.code, self.path = status, code, path
        super().__init__(code)


def fail(code, path, status="invalid_input"):
    raise InputIssue(status, code, path)


def obj(value, required, optional, path):
    if not isinstance(value, dict):
        fail("expected_object", path)
    missing = [k for k in required if k not in value or value[k] is None]
    if missing:
        fail("required_field_missing", path + "." + missing[0], "insufficient_data")
    if set(value) - set(required) - set(optional):
        fail("unknown_fields", path)
    return value


def string(value, path, limit=300):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        fail("invalid_text_field", path)
    return value


def number(value, path, positive=True, fraction_digits=18):
    # Decimal strings avoid binary rounding, nonfinite JSON and exponent bombs.
    pattern = r"-?\d{1,18}(?:\.\d{1," + str(fraction_digits) + r"})?"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        fail("expected_bounded_decimal_string", path)
    result = Decimal(value)
    if positive and result <= 0:
        fail("price_or_unit_must_be_positive", path)
    return result


def instant(value, path):
    string(value, path, 50)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            fail("timezone_required", path)
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        fail("invalid_timestamp", path)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def dec(value):
    return format(value, "f") if value is not None else None


def snap(value, boundaries):
    for boundary in map(Decimal, boundaries):
        if abs(value - boundary) <= EPS:
            return boundary, value != boundary
    return value, False


def candle_geometry(bar, baseline):
    o, h, l, c = (bar[k] for k in ("open", "high", "low", "close"))
    size, body = h-l, abs(c-o)
    upper, lower = h-max(o,c), min(o,c)-l
    out = {"range": dec(size), "body": dec(body), "upper_wick": dec(upper), "lower_wick": dec(lower),
           "baseline_atr": dec(baseline), "range_ratio": None, "comparison_ratio": None,
           "boundary_snapped": False, "body_fraction": None, "close_location": None,
           "length_class": "not_computable", "standard_dominant_candidate": None, "pinbar_candidate": None}
    if size == 0:
        if baseline is not None and baseline > 0:
            out.update(range_ratio="0", comparison_ratio="0")
        out.update(length_class="flat_bar", standard_dominant_candidate=False, pinbar_candidate=False)
        return out
    out.update(body_fraction=dec(body/size), close_location=dec((c-l)/size))
    if baseline is None or baseline <= 0:
        return out
    raw = size/baseline
    compared, snapped = snap(raw, ["0.8", "1.2", "2.5"])
    label = "short" if compared < Decimal(".8") else "standard" if compared <= Decimal("1.2") else "long_range" if compared <= Decimal("2.5") else "very_long_range"
    dominance = snap(max(body,upper,lower)/size,[".8"])[0] >= Decimal(".8")
    at_top = snap((min(o,c)-l)/size,[".8"])[0] >= Decimal(".8") and snap(upper/size,[".1"])[0] <= Decimal(".1")
    at_bottom = snap((max(o,c)-l)/size,[".2"])[0] <= Decimal(".2") and snap(lower/size,[".1"])[0] <= Decimal(".1")
    out.update(range_ratio=dec(raw), comparison_ratio=dec(compared), boundary_snapped=snapped,
               length_class=label, standard_dominant_candidate=label == "standard" and dominance,
               pinbar_candidate=compared >= Decimal("1.2") and (at_top or at_bottom))
    return out


def parse_series(data, as_of):
    s = obj(data, ["symbol","market","feed_id","price_basis","timeframe","chart_type","tick_size",
                   "session_definition","source_timezone","bars"], ["atr_period","pip_size","declared_session_gaps","availability_basis","price_grid_basis"], "series")
    if s.get("availability_basis","provided_unverified") not in ["provided_unverified","observed_receipt","simulated_bar_close"]:
        fail("invalid_availability_basis","series.availability_basis")
    if s.get("price_grid_basis","provided_unverified") not in ["provided_unverified","instrument_tick","archive_precision"]:
        fail("invalid_price_grid_basis","series.price_grid_basis")
    for key in ["symbol","feed_id","session_definition","source_timezone"]:
        string(s[key], "series."+key)
    if s["market"] not in ["forex","crypto"] or s["price_basis"] not in [
            "bid","ask","mid","last","indicative_quote","aggregate_reference"]:
        fail("unsupported_market_or_price_basis", "series")
    if s["chart_type"] != "standard_ohlc":
        fail("transformed_chart_not_raw_prices", "series.chart_type", "unsupported")
    if s["timeframe"] not in LADDER:
        fail("timeframe_outside_reference_helper", "series.timeframe", "unsupported")
    tick = number(s["tick_size"],"series.tick_size")
    if "pip_size" in s:
        number(s["pip_size"],"series.pip_size")
    period = s.get("atr_period", PERIODS.get(s["timeframe"]))
    if period is not None and (type(period) is not int or not 1 <= period <= 5000):
        fail("invalid_atr_period", "series.atr_period")
    if s["timeframe"] in PERIODS and period != PERIODS[s["timeframe"]]:
        fail("period_conflicts_with_selected_profile", "series.atr_period", "unsupported")
    raw = s["bars"]
    if not isinstance(raw,list) or not 1 <= len(raw) <= MAX_BARS:
        fail("bars_must_be_nonempty_bounded_list", "series.bars")
    gaps = s.get("declared_session_gaps",[])
    if not isinstance(gaps,list) or len(gaps) > MAX_BARS:
        fail("invalid_session_gaps", "series.declared_session_gaps")
    allowed_gaps = set()
    for g in gaps:
        obj(g,["from_close","to_open","reason","source_ref"],[],"session_gap")
        start,end = instant(g["from_close"],"gap.from_close"),instant(g["to_open"],"gap.to_open")
        if start >= end or g["reason"] != "scheduled_session_closure":
            fail("invalid_session_gap", "session_gap")
        string(g["source_ref"],"gap.source_ref")
        allowed_gaps.add((start,end))
    bars, ids, previous_end = [], set(), None
    duration = {"M1":60,"M5":300,"M15":900,"H1":3600,"H4":14400}.get(s["timeframe"])
    blocked = False
    for i,b in enumerate(raw):
        path = f"series.bars[{i}]"
        obj(b,["id","open_time","close_time","available_at","is_closed"],["open","high","low","close"],path)
        bid = string(b["id"],path+".id",100)
        if bid in ids:
            fail("duplicate_bar_id",path+".id")
        ids.add(bid)
        start,end,available = [instant(b[k],path+"."+k) for k in ["open_time","close_time","available_at"]]
        if type(b["is_closed"]) is not bool:
            fail("expected_boolean",path+".is_closed")
        if start >= end or available < start or (b["is_closed"] and available < end):
            fail("inconsistent_bar_times",path)
        if previous_end and start < previous_end:
            fail("unordered_or_overlapping_bars",path)
        if duration and (end-start).total_seconds() != duration:
            fail("bar_duration_conflicts_with_timeframe",path)
        previous_end = end
        usable = b["is_closed"] and available <= as_of and end <= as_of
        if blocked or not usable:
            blocked = True  # Do not compress an unavailable middle bar out of a sequence.
            continue
        if bars and bars[-1]["close_time"] != start and (bars[-1]["close_time"],start) not in allowed_gaps:
            fail("unexplained_missing_expected_bars",path,"not_computable")
        prices = {}
        for key in ["open","high","low","close"]:
            if key not in b:
                fail("required_field_missing",path+"."+key,"insufficient_data")
            prices[key] = number(b[key],path+"."+key)
            if prices[key] % tick != 0:
                fail("price_not_tick_aligned",path+"."+key)
        if not prices["low"] <= min(prices["open"],prices["close"]) <= max(prices["open"],prices["close"]) <= prices["high"]:
            fail("inconsistent_ohlc",path)
        bars.append({"id":bid,"open_time":start,"close_time":end,"available_at":available,**prices})
    return s,period,bars,len(raw)-len(bars)


def calculate(bars, period):
    atr, previous, trs, available = None, None, [], None
    seed_weight = None
    rows=[]
    for i,b in enumerate(bars):
        baseline=atr
        baseline_seed_weight=seed_weight
        available=max(available,b["available_at"]) if available else b["available_at"]
        tr=None
        if previous is not None:
            tr=max(b["high"]-b["low"],abs(b["high"]-previous),abs(b["low"]-previous))
            trs.append(tr)
            if period is not None and len(trs) == period:
                atr=sum(trs)/period
                seed_weight=Decimal(1)
            elif period is not None and len(trs) > period:
                atr=((period-1)*atr+tr)/period
                seed_weight=seed_weight*Decimal(period-1)/Decimal(period)
        rows.append({"id":b["id"],"close_time":stamp(b["close_time"]),"available_at":stamp(available),
                     "true_range":dec(tr),"atr_after_bar":dec(atr),
                     "baseline_seed_weight":dec(baseline_seed_weight),
                     "baseline_bar_id":bars[i-1]["id"] if i and baseline is not None else None,
                     **candle_geometry(b,baseline)})
        previous=b["close"]
    return rows


def parse_levels(raw, as_of, timeframe):
    if not isinstance(raw,list) or len(raw) > MAX_LEVELS:
        fail("invalid_level_list","levels")
    levels,ids={},set()
    for i,l in enumerate(raw):
        path=f"levels[{i}]"
        obj(l,["id","kind","side","timeframe","lower","upper","origin_at","available_at","source_refs","construction_method"],[],path)
        lid=string(l["id"],path+".id",100)
        if lid in ids:
            fail("duplicate_level_id",path)
        ids.add(lid)
        origin,available=instant(l["origin_at"],path+".origin_at"),instant(l["available_at"],path+".available_at")
        if origin > available:
            fail("level_available_before_origin",path)
        if available > as_of:
            continue
        lo,hi=number(l["lower"],path+".lower",fraction_digits=50),number(l["upper"],path+".upper",fraction_digits=50)
        if lo > hi or l["kind"] not in ["classical_line","pivot_zone","order_node"] or l["side"] not in ["support","resistance"]:
            fail("invalid_level_geometry_or_type",path)
        if l["kind"] == "classical_line" and lo != hi:
            fail("line_requires_equal_bounds",path)
        if not isinstance(l["source_refs"],list) or not l["source_refs"] or len(l["source_refs"]) > 20:
            fail("level_evidence_required",path+".source_refs")
        for ref in l["source_refs"]:
            string(ref,path+".source_refs")
        string(l["construction_method"],path+".construction_method")
        string(l["timeframe"],path+".timeframe",10)
        levels[lid]={**l,"lower":lo,"upper":hi,"origin_at":origin,"available_at":available}
    return levels


def zone_bounds(bar, row, side, method):
    """Exact construction, independent of tick rounding and subsequent prices."""
    support = side == "support"
    if method == "wick_to_body":
        return (bar["low"], min(bar["open"], bar["close"])) if support else (max(bar["open"], bar["close"]), bar["high"])
    if method == "half_atr":
        atr = row["baseline_atr"]
        if atr is None or Decimal(atr) <= 0:
            return None
        half = Decimal(atr) / 2
        return (bar["low"], bar["low"] + half) if support else (bar["high"] - half, bar["high"])
    return None


def discover_swings(request, bars, rows, timeframe):
    obj(request, ["left_bars", "right_bars", "construction_method"], [], "level_analysis.discovery")
    for name in ["left_bars", "right_bars"]:
        if type(request[name]) is not int or not 1 <= request[name] <= 20:
            fail("swing_window_must_be_1_to_20", "level_analysis.discovery." + name)
    method = request["construction_method"]
    if method not in ["wick_to_body", "half_atr"]:
        fail("unsupported_zone_construction", "level_analysis.discovery.construction_method")
    left, right = request["left_bars"], request["right_bars"]
    levels, reviews, skipped = {}, {}, []
    for i in range(left, len(bars) - right):
        b = bars[i]
        neighbors = bars[i-left:i] + bars[i+1:i+right+1]
        for side, field in [("support", "low"), ("resistance", "high")]:
            # Strict extrema: ties are unresolved plateaus, not arbitrarily selected pivots.
            if not all(b[field] < n[field] if side == "support" else b[field] > n[field] for n in neighbors):
                continue
            lid = "swing:" + side + ":" + b["id"]
            bounds = zone_bounds(b, rows[i], side, method)
            if bounds is None or bounds[0] <= 0:
                skipped.append({"id": lid, "reason": "positive_prior_atr_and_zone_required"})
                continue
            lo, hi = bounds
            available = instant(rows[i+right]["available_at"], "swing.available_at")
            levels[lid] = dict(id=lid, kind="classical_line" if lo == hi else "pivot_zone", side=side,
                timeframe=timeframe, lower=lo, upper=hi, origin_at=b["open_time"], available_at=available,
                source_refs=["DESIGN-SWING-01", "X-PIVOT-B5"], construction_method=method)
            reviews[lid] = dict(status="needs_structure_review", construction_verified=True,
                structural_context_verified=False, origin_bar_id=b["id"], confirmation_bar_id=bars[i+right]["id"],
                confirmed_at=stamp(bars[i+right]["close_time"]), effective_available_at=stamp(available),
                baseline_atr=rows[i]["baseline_atr"], issues=["zero_width_wick_requires_method_review"] if lo == hi else [],
                profile="confirmed_swing_v1", left_bars=left, right_bars=right)
            if len(levels) > MAX_LEVELS:
                fail("too_many_discovered_levels_use_smaller_window", "level_analysis.discovery")
    return levels, reviews, skipped


def validate_pivot_evidence(e, level, bars, rows, timeframe, as_of, pivot_review=False):
    api = SimpleNamespace(**{k:globals()[k] for k in ['obj','string','number','instant','stamp','fail','zone_bounds','snap']})
    if pivot_review and e.get('profile') == 'exception_candidate_v1':
        return PIVOTS.exceptional(e,level,bars,rows,timeframe,as_of,api)
    ordinary = {k:v for k,v in e.items() if k != 'wave_review'}
    if 'wave_review' in e and not pivot_review:
        fail('wave_review_requires_pivot_review_profile','level_analysis.evidence.wave_review')
    base = ordinary_pivot_arithmetic(ordinary,level,bars,rows,timeframe,as_of,pivot_review)
    return PIVOTS.ordinary(e,level,bars,rows,as_of,base,api) if pivot_review else base


def ordinary_pivot_arithmetic(e, level, bars, rows, timeframe, as_of, allow_shadow_colour=False):
    """Check ordinary pivot arithmetic inside an explicitly supplied structural context.

    This does not verify the analyst's master selection or the highest valid timeframe.
    """
    path = "level_analysis.evidence"
    obj(e, ["level_id", "profile", "master_bar_id", "extreme_bar_id", "preleg",
            "context_source_ref", "context_available_at"], [], path)
    for key in ["level_id", "master_bar_id", "extreme_bar_id", "context_source_ref"]:
        string(e[key], path + "." + key)
    if e["profile"] != "ordinary_master_cover_v1":
        fail("unsupported_pivot_evidence_profile", path + ".profile", "unsupported")
    contextual = instant(e["context_available_at"], path + ".context_available_at")
    out = dict(status="needs_context", construction_verified=False, structural_context_verified=False,
               profile=e["profile"], issues=[], context_source_ref=e["context_source_ref"])
    ids = {b["id"]: i for i, b in enumerate(bars)}
    if contextual > as_of or any(e[k] not in ids for k in ["master_bar_id", "extreme_bar_id"]):
        out["issues"] = ["closed_available_master_extreme_and_context_required"]
        return out
    if level["timeframe"] != timeframe:
        out["issues"] = ["pivot_requires_its_own_timeframe_series"]
        return out
    m, x = ids[e["master_bar_id"]], ids[e["extreme_bar_id"]]
    master, extreme = bars[m], bars[x]
    support = level["side"] == "support"
    p = obj(e["preleg"], ["route"], ["bar_ids", "start_bar_id", "end_bar_id", "atr_bar_id", "threshold_atr"], path + ".preleg")
    toward = lambda b: b["close"] < b["open"] if support else b["close"] > b["open"]
    used = [m, x]
    if p["route"] == "three_standard_bars":
        obj(p, ["route", "bar_ids"], [], path + ".preleg")
        bids = p["bar_ids"]
        if not isinstance(bids, list) or not 3 <= len(bids) <= 100:
            fail("at_least_three_preleg_bar_ids_required", path + ".preleg.bar_ids")
        for bid in bids:
            string(bid, path + ".preleg.bar_ids", 100)
        if any(bid not in ids for bid in bids):
            out["issues"] = ["preleg_bars_not_available"]
            return out
        indices = [ids[bid] for bid in bids]
        if indices != sorted(set(indices)) or indices[-1] > m:
            out["issues"] = ["preleg_must_precede_or_end_at_master_in_order"]
            return out
        valid_preleg = all(toward(bars[i]) and rows[i]["length_class"] == "standard" for i in indices)
        start = indices[0]
        used += indices
    elif p["route"] == "atr_displacement":
        obj(p, ["route", "start_bar_id", "end_bar_id", "atr_bar_id", "threshold_atr"], [], path + ".preleg")
        for key in ["start_bar_id", "end_bar_id", "atr_bar_id"]:
            string(p[key], path + ".preleg." + key, 100)
        threshold = number(p["threshold_atr"], path + ".preleg.threshold_atr")
        if any(p[k] not in ids for k in ["start_bar_id", "end_bar_id", "atr_bar_id"]):
            out["issues"] = ["preleg_bars_not_available"]
            return out
        start, end, ai = [ids[p[k]] for k in ["start_bar_id", "end_bar_id", "atr_bar_id"]]
        if not start <= end <= m or ai > end:
            out["issues"] = ["preleg_and_atr_must_be_known_by_master"]
            return out
        # Explicit engineering measurement: start open -> end extreme, baseline ATR of atr_bar_id.
        baseline = rows[ai]["baseline_atr"]
        distance = bars[start]["open"] - bars[end]["low"] if support else bars[end]["high"] - bars[start]["open"]
        valid_preleg = baseline is not None and Decimal(baseline)>0 and distance > 0 and distance >= threshold * Decimal(baseline)
        out["preleg_measurement"] = dict(distance=dec(distance), baseline_atr=baseline, threshold_atr=dec(threshold),
            definition="start_open_to_end_extreme; explicitly supplied baseline and threshold")
        used += [start, end, ai]
    else:
        fail("unsupported_preleg_route", path + ".preleg.route", "unsupported")
    if not valid_preleg:
        out["status"], out["issues"] = "rejected_evidence", ["preleg_does_not_meet_selected_route"]
        return out
    if not toward(master) and not (allow_shadow_colour and PIVOTS.shadow_master(rows[m])):
        out["status"], out["issues"] = "rejected_evidence", ["ordinary_master_direction_conflicts_with_preleg"]
        return out
    cover = next((j for j in range(m+1, len(bars))
                  if (bars[j]["close"] > master["high"] if support else bars[j]["close"] < master["low"])), None)
    if cover is None:
        out["status"], out["issues"] = "pending_cover", ["strict_closed_master_cover_required"]
        return out
    field = "low" if support else "high"
    expected_extreme = (min if support else max)(b[field] for b in bars[start:cover+1])
    if not start <= x <= cover or extreme[field] != expected_extreme:
        out["status"], out["issues"] = "rejected_evidence", ["extreme_not_in_preleg_through_cover_span"]
        return out
    bounds = zone_bounds(extreme, rows[x], level["side"], level["construction_method"])
    if bounds is None:
        out["issues"] = ["supported_construction_and_positive_prior_atr_required"]
        return out
    available = max(contextual, instant(rows[max(used + [cover])]["available_at"], "pivot.available_at"))
    issues = []
    if (level["lower"], level["upper"]) != bounds:
        issues.append("zone_bounds_disagree_with_selected_construction")
    if level["origin_at"] != extreme["open_time"]:
        issues.append("level_origin_must_identify_extreme_bar_open")
    if level["available_at"] < available:
        issues.append("level_availability_predates_confirmation_or_context")
    if level["kind"] == "order_node":
        issues.append("ordinary_pivot_evidence_does_not_validate_order_node")
    if bounds[0] == bounds[1] and level["construction_method"] == "wick_to_body":
        issues.append("zero_width_wick_requires_method_review")
    out.update(status="rejected_evidence" if issues else "ordinary_contract_consistent", issues=issues,
        construction_verified=(level["lower"], level["upper"]) == bounds,
        expected_bounds=[dec(v) for v in bounds], origin_bar_id=extreme["id"], confirmation_bar_id=bars[cover]["id"],
        confirmed_at=stamp(bars[cover]["close_time"]), effective_available_at=stamp(max(available, level["available_at"])),
        cover_timing="timely" if cover-m <= 2 else "late", cover_bar_count=cover-m,
        baseline_atr=rows[x]["baseline_atr"],
        context_caveat="master selection, intervening noise and highest timeframe remain analyst supplied; not an entry signal")
    return out


def level_lifecycle(level, bars, cutoff, timeframe):
    """Close-through terminates the original role; range intersections are descriptive episodes."""
    if level["timeframe"] != timeframe:
        return dict(status="needs_timeframe_series", touch_episodes=None, first_touch_bar_id=None,
                    broken_at=None, break_available_at=None, partial_bar_intersection_ids=[], role_reversal=None,
                    freshness_scope="cannot_validate_level_role_using_another_timeframe")
    if not bars or level['available_at'] < bars[0]['open_time']:
        return dict(status='needs_level_history',touch_episodes=None,first_touch_bar_id=None,
                    broken_at=None,break_available_at=None,partial_bar_intersection_ids=[],role_reversal=None,
                    freshness_scope='history_from_level_availability_required; unknown_is_not_active_or_deleted')
    prefix_known={};known=level['available_at']
    for b in bars:
        known=max(known,b['available_at']);prefix_known[b['id']]=known
    usable = [b for b in bars if b["close_time"] > level["available_at"] and prefix_known[b['id']] <= cutoff]
    episodes, inside, broken, first = 0, False, None, None
    partial = []
    for b in usable:
        touch = b["low"] <= level["upper"] and b["high"] >= level["lower"]
        full = b["open_time"] >= level["available_at"]
        if touch and not full:
            partial.append(b["id"])
        if touch and full and not inside:
            episodes += 1
            if first is None:
                first = b["id"]
        inside = touch and full
        if b["close"] < level["lower"] if level["side"] == "support" else b["close"] > level["upper"]:
            broken = b
            break
    if not broken and any(b['close_time'] <= cutoff and prefix_known[b['id']] > cutoff for b in bars):
        return dict(status='needs_level_history',touch_episodes=None,first_touch_bar_id=None,
                    broken_at=None,break_available_at=None,partial_bar_intersection_ids=partial,role_reversal=None,
                    freshness_scope='closed_prefix_unavailable_at_cutoff; later_receipt_cannot_backdate_role')
    return dict(status="broken_original_role" if broken else "active_original_role", touch_episodes=episodes,
                first_touch_bar_id=first, broken_at=stamp(broken["close_time"]) if broken else None,
                break_available_at=stamp(prefix_known[broken['id']]) if broken else None,
                partial_bar_intersection_ids=partial,
                role_reversal="requires_new_context_and_identity" if broken else None,
                freshness_scope="observable_full_bars_after_level_available_at; formation bars excluded")


def overlap_components(levels):
    """Connected interval components, never new merged zones or changed level identities."""
    components = []
    ordered = sorted(levels.values(), key=lambda l: (l["lower"], l["upper"], l["id"]))
    upper = None
    for level in ordered:
        if upper is None or level["lower"] > upper:
            components.append([level["id"]])
            upper = level["upper"]
        else:
            components[-1].append(level["id"])
            upper = max(upper, level["upper"])
    return components


def own_lifecycle(level, histories, cutoff):
    """Role checks never borrow the trigger's closes or ignore missing history."""
    if level['available_at'] > cutoff:
        return dict(status='not_available_at_decision')
    own = histories.get(level['timeframe'])
    if own is None:
        return dict(status='needs_timeframe_series')
    own_bars = own[0]
    if not own_bars or level['available_at'] < own_bars[0]['open_time']:
        return dict(status='needs_level_history')
    return level_lifecycle(level, own_bars, cutoff, level['timeframe'])


def obstacle_known_at(level, review):
    """A later method review cannot erase an already observable price obstacle."""
    return max(level['available_at'],instant(review.get('obstacle_available_at',
        review.get('effective_available_at',stamp(level['available_at']))),'level.obstacle_available_at'))


def select_target(request, levels, reviews, bars, rows, timeframe, terminated_at=None, histories=None):
    path = "level_analysis.target_request"
    obj(request, ["direction", "selection_bar_id"], ["anchor_level_id", "scenario_start_bar_id"], path)
    if request["direction"] not in ["up", "down"]:
        fail("invalid_direction", path + ".direction")
    for key in ["selection_bar_id", "anchor_level_id", "scenario_start_bar_id"]:
        if key in request:
            string(request[key], path + "." + key)
    ids = {b["id"]: i for i, b in enumerate(bars)}
    out = dict(status="needs_context", selected=None, first_reach=None, exclusions=[], unresolved_obstacles=[],
               order=None, risk_reward=None, further_targets=[], warnings=[
                   "supplied_level_inventory_is_not_exhaustive" if histories is not None else
                   "single_series_does_not_clear_higher_timeframe_obstacles"])
    if request["selection_bar_id"] not in ids:
        return {**out, "reason": "selection_bar_not_closed_and_available"}
    end = ids[request["selection_bar_id"]]
    if end != len(bars)-1:
        return {**out, "reason": "selection_requires_latest_closed_available_bar_set_as_of_or_truncate_series"}
    start_id = request.get("scenario_start_bar_id")
    if start_id is not None and start_id not in ids:
        return {**out, "reason": "scenario_start_not_available"}
    start = ids[start_id] if start_id is not None else end + 1
    if start_id is not None and start > end:
        fail("scenario_start_after_selection", path)
    # Scenario targets must have been known at b0 open; simple maps use selection close.
    selection_at = instant(rows[end]["available_at"], "target.selection_available_at")
    freeze = bars[start]["open_time"] if start_id is not None else selection_at
    known = {}
    for lid, level in levels.items():
        effective = obstacle_known_at(level,reviews[lid])
        if max(effective, level["available_at"]) > freeze:
            out["exclusions"].append({"id": lid, "reason": "not_known_at_target_freeze_time"})
            continue
        known[lid] = level
    bull = request["direction"] == "up"
    side = "resistance" if bull else "support"
    anchor_id = request.get("anchor_level_id")
    anchor = known.get(anchor_id)
    if anchor_id is not None and anchor is None:
        return {**out, "reason": "anchor_not_known_at_target_freeze_time"}
    if anchor is not None and (anchor["side"] != side or anchor["timeframe"] != timeframe):
        return {**out, "reason": "anchor_direction_or_timeframe_requires_review"}
    reference = (anchor["upper"] if bull else anchor["lower"]) if anchor else (bars[start]["open"] if start_id else bars[end]["close"])
    states = {}
    for lid, level in known.items():
        effective_level = {**level, 'available_at': obstacle_known_at(level,reviews[lid])}
        states[lid] = (own_lifecycle(effective_level,histories,freeze) if histories is not None else
                       level_lifecycle(effective_level,bars,freeze,timeframe))
    active = {lid: l for lid, l in known.items() if states[lid]["status"] != "broken_original_role"}
    # Persisted watch decisions compare exact analysis bytes across processes.
    # Set iteration depends on Python's hash seed; preserve a stable audit order.
    for lid in sorted(known.keys() - active.keys()):
        out["exclusions"].append({"id": lid, "reason": "original_role_broken_before_target_freeze"})
    if anchor and anchor_id not in active:
        return {**out, "reason": "anchor_original_role_already_broken"}
    if anchor and states[anchor_id]['status'] != 'active_original_role':
        return {**out, 'reason': 'anchor_own_timeframe_history_required'}
    cluster = next((c for c in overlap_components(active) if anchor_id in c), []) if anchor else []
    anchor_overlap = [lid for lid in cluster if lid != anchor_id and active[lid]["side"] == side
                      and (active[lid]["upper"] > reference if bull else active[lid]["lower"] < reference)]
    candidates = []
    for lid, level in active.items():
        if level["side"] != side or lid == anchor_id:
            continue
        near = level["lower"] if bull else level["upper"]
        far = level["upper"] if bull else level["lower"]
        if lid in cluster:
            out["exclusions"].append({"id": lid, "reason": "overlaps_anchor_component_not_independent_target"})
            continue
        if near > reference if bull else near < reference:
            candidates.append(level)
        elif (far >= reference if bull else far <= reference):
            out["unresolved_obstacles"].append({"id": lid, "reason": "reference_inside_or_touching_opposing_zone"})
    out.update(frozen_at=stamp(freeze), selection_available_at=stamp(selection_at), reference_price=dec(reference),
               observed_close=dec(bars[end]["close"]), anchor_level_id=anchor_id, anchor_overlap_ids=anchor_overlap,
               candidate_count=len(candidates))
    if anchor_overlap or out["unresolved_obstacles"]:
        return {**out, "status": "needs_zone_review", "reason": "nearby_overlapping_obstacle_cannot_be_skipped"}
    candidates.sort(key=lambda l: ((l["lower"] if bull else -l["upper"]), l["id"]))
    if not candidates:
        return {**out, "status": "no_known_target", "reason": "do_not_invent_target_from_rr_or_atr_multiples"}
    target = candidates[0]
    near = target["lower"] if bull else target["upper"]
    same_edge = [l["id"] for l in candidates if (l["lower"] if bull else l["upper"]) == near]
    selected = dict(id=target["id"], near_edge=dec(near), lower=dec(target["lower"]), upper=dec(target["upper"]),
                    timeframe=target["timeframe"], source_refs=target["source_refs"], same_near_edge_ids=same_edge,
                    lifecycle_status=states[target['id']]['status'],
                    validation_status=reviews[target["id"]]["status"], available_at=stamp(target["available_at"]))
    out["selected"] = selected
    out["remaining_distance"] = dec(near-bars[end]["close"] if bull else bars[end]["close"]-near)
    # Never replace a reached nearest target with a farther one in this scenario.
    live_bars=[b for b in bars[start:end+1] if terminated_at is None or b["close_time"] <= terminated_at]
    hit = next((b for b in live_bars if (b["high"] >= near if bull else b["low"] <= near)), None)
    if hit:
        intersection = hit["low"] <= target["upper"] and hit["high"] >= target["lower"]
        out["first_reach"] = dict(bar_id=hit["id"], kind="range_intersection" if intersection else "gap_beyond_zone",
            within_bar=[stamp(hit["open_time"]), stamp(hit["close_time"])], available_at=rows[ids[hit['id']]]['available_at'], exact_time=None,
            execution_fill_proven=False)
        return {**out, "status": "consumed_reassess", "reason": "nearest_target_reached_since_scenario_start"}
    if histories is None and any(active[lid]["timeframe"] != timeframe for lid in same_edge):
        return {**out, "status": "needs_timeframe_review"}
    if any(states[lid]['status'] != 'active_original_role' for lid in same_edge):
        return {**out, 'status': 'needs_timeframe_review', 'reason': 'own_timeframe_role_and_complete_history_required'}
    if any(reviews[lid]["status"] != "ordinary_contract_consistent" or
           instant(reviews[lid].get('effective_available_at',stamp(active[lid]['available_at'])),'target.review_at') > freeze
           for lid in same_edge):
        return {**out, "status": "needs_structure_review", "reason": "nearest_obstacle_not_validated_do_not_skip_to_farther_level"}
    return {**out, "status": "conditional_target", "reason": "nearest_known_independent_level_under_supplied_context_only"}


def analyze_levels(request, supplied, bars, rows, timeframe, as_of, histories=None, pivot_review=False):
    obj(request, ["profile"], ["discovery", "evidence", "target_request"], "level_analysis")
    if request["profile"] != "levels_targets_v1":
        fail("unsupported_level_profile", "level_analysis.profile", "unsupported")
    levels = dict(supplied)
    reviews = {lid: dict(status="unverified_annotation", construction_verified=False,
                         structural_context_verified=False, issues=["origin_and_context_evidence_required"])
               for lid in levels}
    skipped = []
    if "discovery" in request:
        discovered, checks, skipped = discover_swings(request["discovery"], bars, rows, timeframe)
        if set(discovered) & set(levels):
            fail("supplied_and_discovered_level_id_collision", "levels")
        levels.update(discovered)
        reviews.update(checks)
        if len(levels) > MAX_LEVELS:
            fail("too_many_combined_levels", "level_analysis.discovery")
    evidence = request.get("evidence", [])
    if not isinstance(evidence, list) or len(evidence) > MAX_LEVELS:
        fail("invalid_level_evidence_list", "level_analysis.evidence")
    seen = set()
    for e in evidence:
        if not isinstance(e, dict) or not isinstance(e.get("level_id"), str):
            fail("evidence_requires_level_id", "level_analysis.evidence")
        lid = e["level_id"]
        if lid in seen:
            fail("duplicate_level_evidence", "level_analysis.evidence")
        seen.add(lid)
        if lid not in levels:
            fail("evidence_level_not_available", "level_analysis.evidence", "needs_context")
        own_tf = levels[lid]['timeframe']
        if histories is not None and own_tf in histories:
            own_bars, own_rows = histories[own_tf]
            reviews[lid] = validate_pivot_evidence(e, levels[lid], own_bars, own_rows, own_tf, as_of, pivot_review)
        else:
            reviews[lid] = validate_pivot_evidence(e, levels[lid], bars, rows, timeframe, as_of, pivot_review)
    items = []
    for lid, level in levels.items():
        item = {k: dec(v) if isinstance(v, Decimal) else stamp(v) if isinstance(v, datetime) else v for k, v in level.items()}
        item["validation"] = reviews[lid]
        effective=obstacle_known_at(level,reviews[lid])
        effective_level = {**level,"available_at":max(effective,level["available_at"])}
        item["lifecycle"] = (own_lifecycle(effective_level,histories,as_of) if histories is not None else
                             level_lifecycle(effective_level,bars,as_of,timeframe))
        items.append(item)
    out = dict(profile="levels_targets_v1", levels=items, skipped_discovery=skipped,
               overlapping_components=[c for c in overlap_components(levels) if len(c) > 1],
               component_semantics="identity groups only; never merged price boundaries or independent confirmations",
               limits=["engineering_swing_candidates_are_not_Trex_pivots", "structural_master_and_timeframe_judgment_not_automated",
                       "no_touch_count_probability_or_automatic_role_flip", "no_liquidity_or_profitability_inference"])
    if "target_request" in request:
        out["target_selection"] = select_target(request["target_request"], levels, reviews, bars, rows, timeframe, histories=histories)
    return out, levels


def evaluate(request, bars, rows, levels, as_of, timeframe, own_timeframe_targets=False):
    a=obj(request,["model","candidate_bar_id","direction","level_id","context_available_at","context_source_ref"],["target_level_id"],"assessment")
    string(a["model"],"assessment.model",100)
    base={"status":"needs_context","model":a["model"],"confirmed_at":None,"available_at":None,"invalidated_at":None,
          "invalidation_available_at":None,"missing_inputs":[],"conditional_scenarios":[],"events":[],"next_known_level":None,
          "checks":[],"failed_conditions":[]}
    if a["model"] != "strict_three_bar_confirmation_v1":
        return {**base,"status":"unsupported","reason":"model_not_implemented"}
    if a["direction"] not in ["up","down"]:
        fail("invalid_direction","assessment.direction")
    for key in ["candidate_bar_id","level_id","context_source_ref"]:
        string(a[key],"assessment."+key)
    if "target_level_id" in a:
        string(a["target_level_id"],"assessment.target_level_id")
    context=instant(a["context_available_at"],"assessment.context_available_at")
    if context > as_of or a["level_id"] not in levels:
        return {**base,"missing_inputs":["known_level_and_context_as_of_analysis"]}
    level=levels[a["level_id"]]
    if level["timeframe"] != timeframe:
        return {**base,"status":"unsupported","reason":"breakout_must_use_level_timeframe"}
    bull=a["direction"] == "up"
    if level["side"] != ("resistance" if bull else "support"):
        fail("direction_conflicts_with_level_side","assessment.level_id")
    ids=[b["id"] for b in bars]
    if a["candidate_bar_id"] not in ids:
        return {**base,"status":"pending_confirmation","missing_inputs":["closed_available_candidate_bar"]}
    i=ids.index(a["candidate_bar_id"])
    if i == 0:
        return {**base,"missing_inputs":["immediate_previous_closed_bar"]}
    b0,r0,prior=bars[i],rows[i],bars[i-1]
    baseline=Decimal(r0["baseline_atr"]) if r0["baseline_atr"] is not None else None
    if baseline is None or baseline <= 0:
        return {**base,"status":"not_computable","missing_inputs":["positive_previous_bar_atr"]}
    edge=level["upper"] if bull else level["lower"]
    before=lambda v: v <= edge if bull else v >= edge
    beyond=lambda v: v > edge if bull else v < edge
    if before(prior["close"]) and beyond(b0["open"]):
        return {**base,"status":"unsupported","reason":"gap_crossing_requires_separate_model"}
    size=b0["high"]-b0["low"]
    body_fraction=abs(b0["close"]-b0["open"])/size if size else Decimal(0)
    ratio=snap(size/baseline,[".8","1.2","2.5"])[0]
    direction=b0["close"] > b0["open"] if bull else b0["close"] < b0["open"]
    def check(cid, passed, observed, requirement):
        base["checks"].append({"id":cid,"passed":bool(passed),"observed":observed,"requirement":requirement})
        if not passed:
            base["failed_conditions"].append(cid)
    edge_text=dec(edge)
    check("previous_close_on_origin_side",before(prior["close"]),dec(prior["close"]),{"edge":edge_text,"operator":"<=" if bull else ">="})
    check("candidate_open_on_origin_side",before(b0["open"]),dec(b0["open"]),{"edge":edge_text,"operator":"<=" if bull else ">="})
    check("candidate_close_beyond_edge",beyond(b0["close"]),dec(b0["close"]),{"edge":edge_text,"operator":">" if bull else "<"})
    check("directional_body",direction,{"open":dec(b0["open"]),"close":dec(b0["close"])},a["direction"])
    check("candidate_range_in_atr_band",Decimal('.8') <= ratio <= Decimal('2.5'),{"raw":r0["range_ratio"],"compared":dec(ratio)},{"minimum":"0.8","maximum":"2.5","inclusive":True})
    check("candidate_body_at_least_80_percent",snap(body_fraction,[".8"])[0] >= Decimal('.8'),dec(body_fraction),{"minimum":"0.8","inclusive":True})
    candidate=not base["failed_conditions"]
    if not candidate:
        return {**base,"status":"strict_template_not_matched","reason":"candidate_conditions_not_met"}
    dependency=max(level["available_at"],context,instant(r0["available_at"],"calculation.available_at"))
    base.update(status="candidate",origin_at=stamp(b0["open_time"]),available_at=stamp(dependency),
                candidate_bar_id=b0["id"],level_id=level["id"],baseline_atr=dec(baseline),
                close_distance_atr=dec((b0["close"]-edge if bull else edge-b0["close"])/baseline))
    base["events"].append({"type":"candidate","at":stamp(b0["close_time"]),"available_at":stamp(dependency)})
    later=bars[i+1:]
    invalid=lambda b: b["close"] < b0["low"] if bull else b["close"] > b0["high"]
    invalid_bar=next((b for b in later if invalid(b)),None)
    invalid_known=instant(rows[ids.index(invalid_bar['id'])]['available_at'],'invalidation.prefix_available_at') if invalid_bar else None
    confirmation=None
    if later:
        b1=later[0]
        stable=b1["low"] > edge if bull else b1["high"] < edge
        check("first_confirmation_bar_strictly_beyond_level",stable,dec(b1["low"] if bull else b1["high"]),{"edge":edge_text,"operator":">" if bull else "<","bar_id":b1["id"]})
        base["status"]="pending_confirmation" if stable else "strict_template_not_matched"
        if stable and len(later) > 1:
            b2=later[1]
            confirmed=b2["close"] > b0["high"] if bull else b2["close"] < b0["low"]
            check("second_confirmation_close_beyond_candidate_extreme",confirmed,dec(b2["close"]),{"edge":dec(b0["high"] if bull else b0["low"]),"operator":">" if bull else "<","bar_id":b2["id"]})
            base["status"]="confirmed_under_selected_profile" if confirmed else "strict_template_not_matched"
            if confirmed:
                confirmation=max(dependency,b1["available_at"],b2["available_at"])
                # An invalidation already known when confirmation becomes usable wins.
                if not invalid_bar or invalid_known > confirmation:
                    base.update(confirmed_at=stamp(b2["close_time"]),available_at=stamp(confirmation))
                    base["events"].append({"type":"confirmation","at":stamp(b2["close_time"]),"available_at":stamp(confirmation)})
                else:
                    base["reason"]="invalidation_known_before_confirmation_available"
    if invalid_bar:
        available=max(dependency,invalid_known)
        base.update(status="invalidated",invalidated_at=stamp(invalid_bar["close_time"]),invalidation_available_at=stamp(available))
        base["events"].append({"type":"invalidation","at":stamp(invalid_bar["close_time"]),"available_at":stamp(available)})
    target=levels.get(a.get("target_level_id"))
    touched=False
    base["target_touch"]=None
    # A terminated setup cannot acquire new target events from later bars.
    active_bars=[b for b in bars[i:] if not invalid_bar or b["close_time"] <= invalid_bar["close_time"]]
    if target:
        target_edge=target["lower"] if bull else target["upper"]
        if (target["timeframe"] != timeframe and not own_timeframe_targets) or target["side"] != ("resistance" if bull else "support") or not beyond(target_edge):
            fail("target_must_be_a_known_level_ahead_on_same_timeframe","assessment.target_level_id")
        hit=next((b for b in active_bars if (b["high"] >= target_edge if bull else b["low"] <= target_edge)),None)
        touched=hit is not None
        if hit:
            # OHLC locates the touch within a bar; never invent its tick timestamp.
            touch_available=max(dependency,target["available_at"],instant(rows[ids.index(hit['id'])]['available_at'],'touch.prefix_available_at'))
            phase=('without_usable_confirmation' if not base['confirmed_at'] else
                   'by_confirmation' if hit['close_time'] <= confirmation else
                   'confirmation_availability_within_touch_bar' if hit['open_time'] < confirmation else
                   'after_confirmation')
            base["target_touch"]={"bar_id":hit["id"],"bar_open_at":stamp(hit["open_time"]),"bar_close_at":stamp(hit["close_time"]),
                "available_at":stamp(touch_available),"exact_time":None,"phase":phase,"price":dec(target_edge)}
            base["events"].append({"type":"target_touch","at":None,"bar_id":hit["id"],"within_bar":[stamp(hit["open_time"]),stamp(hit["close_time"])],"available_at":stamp(touch_available)})
        base["next_known_level"]={"id":target["id"],"near_edge":dec(target_edge),"available_at":stamp(target["available_at"]),"source_refs":target["source_refs"]}
    else:
        base["missing_inputs"].append("next_known_target_level")
    base["target_touched"]=touched
    base["intrabar_order_unknown_bar_ids"]=[]
    if target:
        stop=b0["low"] if bull else b0["high"]
        base["intrabar_order_unknown_bar_ids"]=[b["id"] for b in active_bars
            if (b["high"] >= target_edge and b["low"] <= stop if bull else b["low"] <= target_edge and b["high"] >= stop)]
    base["scenario_status"]="invalidated" if base["status"] == "invalidated" else "reassess" if touched else base["status"]
    base["events"].sort(key=lambda event:(event["available_at"],event.get("at") or event.get("within_bar",[""])[0]))
    stop=b0["low"] if bull else b0["high"]
    condition={"operator":"closed_close_below" if bull else "closed_close_above","price":dec(stop),"timeframe":timeframe}
    if base["status"] in ["candidate","pending_confirmation","confirmed_under_selected_profile"] and not touched:
        base["conditional_scenarios"].append({"kind":"continuation","condition":"strict_confirmation_available_and_not_invalidated_and_target_not_already_touched",
            "confirmation_timeframe":timeframe,"invalidation_condition":condition,"next_known_level":base["next_known_level"],
            "remaining_uncertainty":["higher_timeframe_context","entry_method","execution_costs_and_risk"],"entry_price":None,"order":None})
    base["conditional_scenarios"].append({"kind":"reassess_or_wait","condition":"invalidation_or_target_touch_or_incomplete_or_unmatched_template",
        "confirmation_timeframe":timeframe,"invalidation_condition":condition,"next_known_level":base["next_known_level"],
        "remaining_uncertainty":["a_separate_opposite_setup_is_required_for_reversal"],"order":None})
    return base


def context_review(request, primary, as_of, levels):
    """Optional causal three-role coordinator. All series belong to one price feed."""
    obj(request, ['profile','structure_timeframe','left_bars','right_bars','max_pivot_age_bars',
                  'max_close_lag_seconds','alignment_source_ref','higher_series'], [], 'structure_analysis')
    if request['profile'] != 'confirmed_swings_mtf_v1':
        fail('unsupported_structure_profile','structure_analysis.profile','unsupported')
    tf = request['structure_timeframe']
    if tf not in LADDER or LADDER.index(tf)+2 >= len(LADDER):
        fail('three_timeframe_roles_unavailable','structure_analysis.structure_timeframe','unsupported')
    role_tfs = dict(zip(['structure','pattern','trigger'], LADDER[LADDER.index(tf):LADDER.index(tf)+3]))
    if primary['timeframe'] != role_tfs['trigger']:
        fail('primary_series_must_be_trigger_role','series.timeframe')
    for key, minimum, maximum in [('left_bars',1,20),('right_bars',1,20),('max_pivot_age_bars',4,500)]:
        if type(request[key]) is not int or not minimum <= request[key] <= maximum:
            fail('invalid_structure_parameter','structure_analysis.'+key)
    limits = obj(request['max_close_lag_seconds'], list(role_tfs.values()), [], 'structure_analysis.max_close_lag_seconds')
    if any(type(v) is not int or not 1 <= v <= 2678400 for v in limits.values()):
        fail('invalid_freshness_budget','structure_analysis.max_close_lag_seconds')
    string(request['alignment_source_ref'],'structure_analysis.alignment_source_ref')
    higher = request['higher_series']
    if not isinstance(higher,list) or len(higher)>2:
        fail('at_most_two_higher_series','structure_analysis.higher_series')
    supplied = {primary['timeframe']:primary}
    identity = ['symbol','market','feed_id','price_basis','chart_type','tick_size','session_definition','source_timezone']
    for i, series in enumerate(higher):
        if not isinstance(series,dict) or series.get('timeframe') not in role_tfs.values():
            fail('series_outside_selected_roles',f'structure_analysis.higher_series[{i}]')
        if series['timeframe'] in supplied:
            fail('duplicate_timeframe_series',f'structure_analysis.higher_series[{i}]')
        if any(series.get(k) != primary.get(k) for k in identity):
            fail('incompatible_series_identity',f'structure_analysis.higher_series[{i}]')
        supplied[series['timeframe']] = series
    _, _, trigger_bars, _ = parse_series(primary,as_of)
    if not trigger_bars:
        return dict(status='missing_trigger_data',direction=None,roles={},scenario_ready=False)
    # Later HTF information must not be attached to an older trigger decision.
    decision = max(b['available_at'] for b in trigger_bars)
    roles, parsed = {}, {}
    for role, timeframe in role_tfs.items():
        if timeframe not in supplied:
            continue
        s, period, bars, excluded = parse_series(supplied[timeframe],decision)
        parsed[timeframe] = bars
        result = STRUCTURE.scan(bars,request['left_bars'],request['right_bars'],request['max_pivot_age_bars'])
        lag = (as_of-bars[-1]['close_time']).total_seconds() if bars else None
        # Only declared closures subtract from the age, never unexplained missing data.
        if lag is not None:
            closures = sorted((instant(g['from_close'],'gap'),instant(g['to_open'],'gap'))
                              for g in s.get('declared_session_gaps',[]))
            merged = []
            for start,end in closures:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0],max(end,merged[-1][1]))
                else:
                    merged.append((start,end))
            for start,end in merged:
                lag -= max(0,(min(as_of,end)-max(bars[-1]['close_time'],start)).total_seconds())
        roles[role] = dict(timeframe=timeframe,usable_closed_bars=len(bars),excluded_tail_bars=excluded,
                           close_lag_seconds=lag,lag_budget_seconds=limits[timeframe],
                           fresh=lag is not None and lag < limits[timeframe],structure=result,
                           availability_basis=s.get('availability_basis','provided_unverified'))
    checks = []
    for high,low in [(role_tfs['structure'],role_tfs['pattern']),(role_tfs['pattern'],role_tfs['trigger'])]:
        if high in parsed and low in parsed:
            check = STRUCTURE.alignment(parsed[high],parsed[low])
            checks.append(dict(higher=high,lower=low,**check))
    status,direction = STRUCTURE.combine(roles)
    if checks and any(c['status'] != 'consistent_overlap' for c in checks):
        status = 'needs_alignment_review'
    level_checks = []
    for level in levels.values():
        own_bars = parsed.get(level['timeframe'])
        if level['available_at']>decision:
            life = {'status':'not_available_at_decision'}
        elif own_bars is None:
            life = {'status':'needs_timeframe_series'}
        elif not own_bars or level['available_at']<own_bars[0]['open_time']:
            life = {'status':'needs_level_history'}
        else:
            life = level_lifecycle(level,own_bars,decision,level['timeframe'])
        level_checks.append(dict(level_id=level['id'],timeframe=level['timeframe'],lifecycle=life,
                                 master_context_verified=False))
    return dict(profile=request['profile'],status=status,direction=direction,decision_at=stamp(decision),
                roles=roles,missing_timeframes=[v for v in role_tfs.values() if v not in supplied],
                alignment_checks=checks,alignment_source_ref=request['alignment_source_ref'],
                level_role_checks=level_checks,scenario_ready=False,
                interpretation='profile_observation_not_full_method_validation',
                continuation_requires=['aligned_roles','matching_direction','independent_reviewed_target','reviewed_anchor','trigger_confirmation'],
                reversal_requires=['structure_timeframe_protected_swing_breach','new_opposite_confirmed_swing_sequence'],
                engineering_parameters={k:request[k] for k in ['left_bars','right_bars','max_pivot_age_bars','max_close_lag_seconds']})


def target_hierarchy_review(request, levels, level_review, histories, bars, rows, timeframe, ctx, assessed=None):
    """Keep a structural destination separate from the first obstacle on its path.

    Selection is over the supplied inventory only. A further destination never
    grants permission to bypass, consume or silently replace a nearer obstacle.
    """
    reviews = {l['id']:l['validation'] for l in level_review['levels']}
    structural_tf = ctx['roles'].get('structure',{}).get('timeframe')
    # Missing structure data must not accidentally select all supplied levels.
    structural_levels = {lid:l for lid,l in levels.items()
                         if structural_tf is not None and l['timeframe']==structural_tf}
    anchor_id = request.get('anchor_level_id')
    if anchor_id in levels:
        structural_levels[anchor_id] = levels[anchor_id]
    same_scenario = assessed is not None and (
        assessed.get('candidate_bar_id')==request.get('scenario_start_bar_id') and
        assessed.get('level_id')==anchor_id)
    terminated = instant(assessed['invalidated_at'],'assessment.invalidated_at') if same_scenario and assessed.get('invalidated_at') else None
    nearest = select_target(request,levels,reviews,bars,rows,timeframe,terminated,histories)
    structural = select_target(request,structural_levels,reviews,bars,rows,timeframe,terminated,histories)
    selected = structural.get('selected')
    path, known_active = [], {}
    if nearest.get('frozen_at'):
        freeze = instant(nearest['frozen_at'],'target.freeze')
        for lid,l in levels.items():
            effective = obstacle_known_at(l,reviews[lid])
            if effective > freeze:
                continue
            state = own_lifecycle({**l,'available_at':effective},histories,freeze)
            if state['status']=='broken_original_role':
                continue
            known_active[lid] = l
            if selected and lid not in [anchor_id, selected['id']]:
                bull = request['direction']=='up'
                edge = l['lower'] if bull else l['upper']
                reference, destination = Decimal(nearest['reference_price']), Decimal(selected['near_edge'])
                if l['side']==('resistance' if bull else 'support') and (reference < edge < destination if bull else destination < edge < reference):
                    path.append(dict(id=lid,timeframe=l['timeframe'],near_edge=dec(edge),
                        validation_status=reviews[lid]['status'],lifecycle_status=state['status']))
        # Filtering to the structure timeframe must not erase a connecting overlap.
        cluster = next((c for c in overlap_components(known_active) if anchor_id in c),[])
        if selected and selected['id'] in cluster:
            structural.update(status='needs_zone_review',reason='structural_target_overlaps_anchor_through_known_levels')
    path.sort(key=lambda x:(Decimal(x['near_edge']) if request['direction']=='up' else -Decimal(x['near_edge']),x['id']))
    data_ready = (len(ctx['roles'])==3 and not ctx.get('missing_timeframes') and
                  all(r['fresh'] for r in ctx['roles'].values()) and len(ctx.get('alignment_checks',[]))==2 and
                  all(c['status']=='consistent_overlap' for c in ctx['alignment_checks']))
    numeric_valid = data_ready and structural['status']=='conditional_target'
    coincident = selected is not None and selected['id'] in (nearest.get('selected') or {}).get('same_near_edge_ids',[])
    direct = numeric_valid and nearest['status']=='conditional_target' and coincident and not path
    status = ('scenario_invalidated' if terminated else
              'needs_timeframe_context' if not data_ready else
              'reassess_nearest_obstacle' if nearest['status']=='consumed_reassess' else
              'reassess_structural_target' if structural['status']=='consumed_reassess' else
              'needs_structural_target_review' if not numeric_valid else
              'needs_nearest_obstacle_review' if nearest['status']!='conditional_target' else
              'conditional_structural_target' if direct else 'structural_target_with_intermediate_obstacles')
    if terminated:
        for selection in [nearest,structural]:
            selection.update(selection_status_before_termination=selection['status'],status='scenario_invalidated',
                             terminated_at=stamp(terminated),remaining_distance=None)
        numeric_valid = direct = False
    return dict(profile='own_timeframe_targets_v1',status=status,
        structural_timeframe=structural_tf,nearest_obstacle=nearest,structural_target=structural,
        intermediate_obstacles=path,direct_path_clear=direct,structural_target_contract_consistent=numeric_valid,
        validation_scope='ordinary_contract_consistency_with_supplied_context',
        level_inventory_complete=False,order=None,risk_reward=None,
        limitations=['supplied_master_context_not_independently_verified','no_automatic_timeframe_promotion',
                    'no_farther_target_rollover','no_intrabar_fill_or_profitability_inference'])


def historical_context_gate(assessed, payload, levels, level_review):
    """Keep retrospective observations, but never backdate a prospective setup."""
    origin = assessed.get('origin_at')
    if origin is None:
        return
    freeze = instant(origin, 'assessment.origin_at')
    evidence = {x['id']: x['validation'] for x in level_review.get('levels', [])}
    dependencies = [('context', instant(payload['assessment']['context_available_at'], 'assessment.context_available_at'))]
    unavailable = []
    for key, role in [('level_id', 'anchor'), ('target_level_id', 'target')]:
        lid = payload['assessment'].get(key)
        if lid is None:
            continue
        if lid not in levels:
            unavailable.append(role)
            continue
        available = levels[lid]['available_at']
        effective = evidence.get(lid, {}).get('effective_available_at')
        if effective is not None:
            available = max(available, instant(effective, 'level.effective_available_at'))
        dependencies.append((role, available))
    late = [dict(role=role, available_at=stamp(at)) for role, at in dependencies if at > freeze]
    assessed['historical_context_gate'] = dict(
        status='retrospective_only' if late else 'dependencies_unavailable' if unavailable else 'known_at_origin', frozen_at=origin,
        prospective_eligible=not late and not unavailable, late_dependencies=late, unavailable_dependencies=unavailable,
        policy='Scenario annotation context must exist at candidate open; later review needs a newly defined scenario')
    if late or unavailable:
        assessed['conditional_scenarios'] = [c for c in assessed['conditional_scenarios'] if c['kind'] != 'continuation']
        if assessed.get('scenario_status') not in ['invalidated', 'reassess']:
            assessed['scenario_status'] = 'retrospective_only' if late else 'needs_context'
        if assessed.get('target_touch'):
            assessed['target_touch']['interpretation'] = 'retrospective_price_observation_not_a_prospective_target_result'


def analyze(payload):
    """Return analytical data only. No mutations and no actions from source text."""
    result={"schema_version":1,"skill_version":VERSION,"contract_id":CONTRACT["contract_id"],
            "contract_sha256":hashlib.sha256(CONTRACT_BYTES).hexdigest(),"profile_id":PROFILE,
            "status":"insufficient_data","missing_inputs":[],"conditional_scenarios":[],"issues":[],
            "order":None,"position_size":None,"confidence_probability":None}
    try:
        with localcontext() as context:
            context.prec=50
            obj(payload,["schema_version","as_of","series"],["levels","assessment","level_analysis","structure_analysis","target_hierarchy","pivot_review"],"input")
            pivot_review = 'pivot_review' in payload
            if pivot_review:
                obj(payload['pivot_review'],['profile'],[],'pivot_review')
                if payload['pivot_review']['profile']!='source_linked_pivots_v1':
                    fail('unsupported_pivot_review_profile','pivot_review.profile','unsupported')
                if 'level_analysis' not in payload:
                    fail('pivot_review_requires_level_analysis','pivot_review')
            if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
                fail("unsupported_schema_version","schema_version","unsupported")
            as_of=instant(payload["as_of"],"as_of")
            result["as_of"]=stamp(as_of)
            s,period,bars,excluded=parse_series(payload["series"],as_of)
            rows=calculate(bars,period)
            result["data_scope"]={k:s[k] for k in ["symbol","market","feed_id","price_basis","timeframe","chart_type","session_definition","source_timezone","tick_size"]}
            result["data_scope"].update(atr_period=period,usable_closed_bars=len(bars),excluded_tail_bars=excluded,
                availability_basis=s.get("availability_basis","provided_unverified"),price_grid_basis=s.get("price_grid_basis","provided_unverified"),
                seed_start_bar_id=bars[1]["id"] if len(bars)>1 else None,
                series_sha256=hashlib.sha256(json.dumps(payload["series"],sort_keys=True,ensure_ascii=False).encode()).hexdigest())
            result['data_scope'].update(execution_quote_verified=False,
                price_basis_scope='non_executable_reference' if s['price_basis'] in
                ['indicative_quote','aggregate_reference'] else 'declared_basis_not_independently_verified')
            result["candles"]=rows
            result["observation"]={"last_closed_bar_id":bars[-1]["id"] if bars else None,"geometry_is_not_structural_master":True}
            result['method_scope'] = dict(
                full_method_validated=False,
                pivot_method='trex_primary_macd_fibonacci_auxiliary',
                pivot_policy_source='USER-PIVOT-20261025',
                target_review_scope='same_timeframe_only',
                required_structural_target_timeframe=None,
                structural_target_validated=False,
                qualification='partial_engineering_profile_not_complete_user_method',
                pivot_review_scope='source_linked_bounded_review' if pivot_review else 'legacy_arithmetic_only',
                pivot_review_active=pivot_review,
                open_method_dependencies=['Trex_master_selection', 'exception_pivots', 'structural_target_review'])
            applied=["DATA-01","DATA-02","VOL-C01","VOL-C02","CND-C01","CND-C02","SCN-C01"]
            levels=parse_levels(payload.get("levels",[]),as_of,s["timeframe"])
            histories, prepared_ctx = None, None
            if 'target_hierarchy' in payload:
                obj(payload['target_hierarchy'],['profile'],[],'target_hierarchy')
                if payload['target_hierarchy']['profile']!='own_timeframe_targets_v1':
                    fail('unsupported_target_hierarchy_profile','target_hierarchy.profile','unsupported')
                if 'structure_analysis' not in payload or not isinstance(payload.get('level_analysis'),dict) or 'target_request' not in payload['level_analysis']:
                    fail('target_hierarchy_requires_structure_and_level_target_request','target_hierarchy')
                prepared_ctx = context_review(payload['structure_analysis'],s,as_of,levels)
                decision = instant(prepared_ctx['decision_at'],'structure.decision') if prepared_ctx.get('decision_at') else as_of
                histories = {}
                for raw in [s]+payload['structure_analysis']['higher_series']:
                    own, own_period, own_bars, _ = parse_series(raw,decision)
                    histories[own['timeframe']] = (own_bars,calculate(own_bars,own_period))
            if "level_analysis" in payload:
                result["level_analysis"],levels=analyze_levels(payload["level_analysis"],levels,bars,rows,s["timeframe"],
                    decision if histories is not None else as_of,histories,pivot_review)
            if payload.get("assessment") is not None:
                assessed=evaluate(payload["assessment"],bars,rows,levels,as_of,s["timeframe"],histories is not None)
                result["assessment"]=assessed
                for key in ["status","missing_inputs","conditional_scenarios"]:
                    result[key]=assessed[key]
                if "level_analysis" in result:
                    review=next((l["validation"] for l in result["level_analysis"]["levels"] if l["id"] == payload["assessment"]["level_id"]),{})
                    target=result["level_analysis"].get("target_selection",{})
                    request=payload["level_analysis"].get("target_request",{})
                    same_scenario=(request.get("anchor_level_id") == payload["assessment"]["level_id"]
                                   and request.get("scenario_start_bar_id") == payload["assessment"]["candidate_bar_id"]
                                   and request.get("direction") == payload["assessment"]["direction"])
                    if same_scenario and assessed.get("invalidated_at"):
                        reviews={l["id"]:l["validation"] for l in result["level_analysis"]["levels"]}
                        target=select_target(request,levels,reviews,bars,rows,s["timeframe"],
                            terminated_at=instant(assessed["invalidated_at"],"assessment.invalidated_at"),histories=histories)
                        target.update(selection_status_before_termination=target["status"],status="scenario_invalidated",
                                      terminated_at=assessed["invalidated_at"],remaining_distance=None)
                        result["level_analysis"]["target_selection"]=target
                    matching=(request.get("anchor_level_id") == payload["assessment"]["level_id"]
                              and request.get("scenario_start_bar_id") == payload["assessment"]["candidate_bar_id"]
                              and request.get("direction") == payload["assessment"]["direction"]
                              and request.get("selection_bar_id") == (bars[-1]["id"] if bars else None)
                              and target.get("selected",{}).get("id") == payload["assessment"].get("target_level_id")) if target.get("selected") else False
                    ready=review.get("status") == "ordinary_contract_consistent" and target.get("status") == "conditional_target" and matching
                    assessed["level_and_target_gate"]="consistent_with_supplied_context" if ready else "needs_level_or_target_review"
                    if not ready:
                        assessed["conditional_scenarios"]=[c for c in assessed["conditional_scenarios"] if c["kind"] != "continuation"]
                        result["conditional_scenarios"]=assessed["conditional_scenarios"]
                        if assessed.get("scenario_status") not in ["invalidated","reassess"]:
                            assessed["scenario_status"]="needs_level_or_target_review"
                applied += ["LVL-C01","BRK-C01","BRK-C02","INV-C01"]
            else:
                result.update(status="needs_context",missing_inputs=["explicit_assessment_context"])
                if "level_analysis" in result:
                    result.update(status="levels_reviewed",missing_inputs=[])
            if period is None:
                result.update(status="insufficient_data",conditional_scenarios=[])
                result["missing_inputs"]=["series.atr_period"]+result["missing_inputs"]
            result["rule_ids"]=applied
            result["source_refs"]=sorted({ref for cid in applied for ref in CLAUSES[cid]["source_refs"]})
            result['semantic_contract_id'] = SEMANTIC_CONTRACT['contract_id']
            result['semantic_contract_sha256'] = hashlib.sha256(SEMANTIC_CONTRACT_BYTES).hexdigest()
            result['semantic_rule_ids'] = ['SEM-01', 'SEM-02', 'SEM-03', 'SEM-04']
            result['source_refs'] = sorted(set(result['source_refs']) | {
                ref for c in SEMANTIC_CONTRACT['clauses'] if c['id'] in result['semantic_rule_ids'] for ref in c['source_refs']})
            if pivot_review:
                result['media_review_contract_id']=MEDIA_REVIEW_CONTRACT['contract_id']
                result['media_review_contract_sha256']=hashlib.sha256(MEDIA_REVIEW_BYTES).hexdigest()
                result['media_review_rule_ids']=[c['id'] for c in MEDIA_REVIEW_CONTRACT['clauses']]
                result['source_refs']=sorted(set(result['source_refs']) | {
                    ref for c in MEDIA_REVIEW_CONTRACT['clauses'] for ref in c['source_refs']})
                result['pivot_contract_id']=PIVOT_CONTRACT['contract_id']
                result['pivot_contract_sha256']=hashlib.sha256(PIVOT_CONTRACT_BYTES).hexdigest()
                result['exception_shadow_contract_id']=EXCEPTION_SHADOW_CONTRACT['contract_id']
                result['exception_shadow_contract_sha256']=hashlib.sha256(EXCEPTION_SHADOW_BYTES).hexdigest()
                result['exception_review_contract_id']=EXCEPTION_REVIEW_CONTRACT['contract_id']
                result['exception_review_contract_sha256']=hashlib.sha256(EXCEPTION_REVIEW_BYTES).hexdigest()
                result['exception_review_rule_ids']=[c['id'] for c in EXCEPTION_REVIEW_CONTRACT['clauses']]
                result['pinbar_confirmation_contract_id']=PINBAR_CONFIRMATION_CONTRACT['contract_id']
                result['pinbar_confirmation_contract_sha256']=hashlib.sha256(PINBAR_CONFIRMATION_BYTES).hexdigest()
                result['pinbar_confirmation_rule_ids']=[c['id'] for c in PINBAR_CONFIRMATION_CONTRACT['clauses']]
                result['pivot_rule_ids']=[c['id'] for c in PIVOT_CONTRACT['clauses']]
                result['source_refs']=sorted(set(result['source_refs']) | {ref for c in PIVOT_CONTRACT['clauses'] for ref in c['source_refs']})
                result['source_refs']=sorted(set(result['source_refs']) | {ref for c in EXCEPTION_REVIEW_CONTRACT['clauses'] for ref in c['source_refs']})
                result['source_refs']=sorted(set(result['source_refs']) | {ref for c in PINBAR_CONFIRMATION_CONTRACT['clauses'] for ref in c['source_refs']})
            result["limitations"]=["single_supplied_series", "levels_and_candidate_are_supplied_not_discovered",
                "no_automatic_pivot_or_node_detector", "reference_design_not_performance_validation", "no_live_market_access",
                "atr_initialization_depends_on_supplied_history_not_only_period",
                "calendar_bar_boundaries_and_session_declarations_are_provider_metadata_not_independently_verified"]
            if "level_analysis" in result:
                result["limitations"].remove("levels_and_candidate_are_supplied_not_discovered")
                result["limitations"].append("optional_swing_candidates_do_not_replace_structural_review")
                result["level_rule_ids"]=["LVL-E01","LVL-E02","LVL-E03","TGT-E01","TGT-E02"]
                result["level_contract_id"]=LEVEL_CONTRACT["contract_id"]
                result["level_contract_sha256"]=hashlib.sha256(LEVEL_CONTRACT_BYTES).hexdigest()
                result["source_refs"]=sorted(set(result["source_refs"]) | {ref for c in LEVEL_CONTRACT["clauses"] for ref in c["source_refs"]})
            if s.get("availability_basis") != "observed_receipt":
                result["limitations"].append("historical_availability_is_assumed_or_unverified_not_execution_evidence")
            if s.get("price_grid_basis") != "instrument_tick":
                result["limitations"].append("provided_price_grid_is_not_verified_historical_order_tick_size")
            if s['price_basis'] in ['indicative_quote','aggregate_reference']:
                result['limitations'].append('indicative_or_aggregate_reference_not_bid_ask_or_executable_trade_price')
            if 'structure_analysis' in payload:
                ctx = prepared_ctx if prepared_ctx is not None else context_review(payload['structure_analysis'],s,as_of,levels)
                result['structural_context'] = ctx
                ctx['scenario_ready_scope'] = 'engineering_profile_only'
                result['method_scope']['required_structural_target_timeframe'] = payload['structure_analysis'].get('structure_timeframe')
                result['limitations'].remove('single_supplied_series')
                result['limitations'].append('strict_local_swings_are_engineering_observations_not_Trex_master_validation')
                result['structure_rule_ids'] = ['STR-E01','STR-E02','MTF-E01','MTF-E02','CTX-E01']
                result['structure_contract_id'] = STRUCTURE_CONTRACT['contract_id']
                result['structure_contract_sha256'] = hashlib.sha256(STRUCTURE_CONTRACT_BYTES).hexdigest()
                result['source_refs'] = sorted(set(result['source_refs']) | {r for c in STRUCTURE_CONTRACT['clauses'] for r in c['source_refs']})
                if histories is not None:
                    hierarchy = target_hierarchy_review(payload['level_analysis']['target_request'],levels,
                        result['level_analysis'],histories,bars,rows,s['timeframe'],ctx,result.get('assessment'))
                    result['target_hierarchy'] = hierarchy
                    result['method_scope'].update(target_review_scope='own_timeframe_supplied_levels',
                        structural_target_contract_consistent=hierarchy['structural_target_contract_consistent'],
                        structural_target_validation_scope=hierarchy['validation_scope'])
                    # SEM-03 requires independent method labels before claiming validation.
                    result['target_contract_id'] = TARGET_CONTRACT['contract_id']
                    result['target_contract_sha256'] = hashlib.sha256(TARGET_CONTRACT_BYTES).hexdigest()
                    result['target_rule_ids'] = [c['id'] for c in TARGET_CONTRACT['clauses']]
                    result['source_refs'] = sorted(set(result['source_refs']) | {r for c in TARGET_CONTRACT['clauses'] for r in c['source_refs']})
                    ctx['level_role_checks'] = [dict(level_id=l['id'],timeframe=l['timeframe'],
                        lifecycle=l['lifecycle'],master_context_verified=False) for l in result['level_analysis']['levels']]
                if 'assessment' in result:
                    assessed = result['assessment']
                    ready = (ctx['status']=='aligned' and ctx['direction']==payload['assessment']['direction']
                             and assessed.get('level_and_target_gate')=='consistent_with_supplied_context')
                    if histories is not None:
                        assessed['target_hierarchy_gate'] = hierarchy['status']
                        ready = ready and hierarchy['direct_path_clear']
                    assessed['structural_context_gate'] = 'consistent_with_profile' if ready else 'needs_context_or_level_review'
                    if not ready:
                        assessed['conditional_scenarios'] = [c for c in assessed['conditional_scenarios'] if c['kind']!='continuation']
                        result['conditional_scenarios'] = assessed['conditional_scenarios']
                        if assessed.get('scenario_status') not in ['invalidated','reassess']:
                            assessed['scenario_status']='needs_context_or_level_review'
                    ctx['scenario_ready'] = (ready and assessed['status']=='confirmed_under_selected_profile'
                                             and assessed.get('scenario_status') not in ['invalidated','reassess'])
                else:
                    result.update(status='structure_reviewed',missing_inputs=[])
            if 'assessment' in result:
                assessed = result['assessment']
                historical_context_gate(assessed, payload, levels, result.get('level_analysis', {}))
                result['conditional_scenarios'] = assessed['conditional_scenarios']
                if assessed.get('historical_context_gate', {}).get('prospective_eligible') is False:
                    if 'structural_context' in result:
                        result['structural_context']['scenario_ready'] = False
    except InputIssue as exc:
        result.update(status=exc.status,conditional_scenarios=[],issues=[{"code":exc.code,"path":exc.path}])
        if exc.status in ["insufficient_data","needs_context"]:
            result["missing_inputs"]=[exc.path]
        result.pop("assessment",None)
    return result


def no_duplicates(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            fail("duplicate_json_key","input")
        result[key]=value
    return result


def reject_constant(value):
    fail("nonfinite_json_number","input")


def reject_float(value):
    fail("decimal_values_must_be_strings","input")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",help="Explicit local JSON file; '-' reads stdin. No paths are read from payload fields.")
    args=parser.parse_args()
    try:
        if args.input == "-":
            raw=sys.stdin.buffer.read(MAX_BYTES+1)
        else:
            with Path(args.input).open("rb") as handle:
                raw=handle.read(MAX_BYTES+1)
        if len(raw) > MAX_BYTES:
            fail("input_too_large","input")
        payload=json.loads(raw.decode("utf-8-sig"),object_pairs_hook=no_duplicates,parse_constant=reject_constant,parse_float=reject_float)
        result=analyze(payload)
    except (OSError,UnicodeError,json.JSONDecodeError,InputIssue,RecursionError,ValueError) as exc:
        result={"schema_version":1,"status":"invalid_input","conditional_scenarios":[],"order":None,
                "issues":[{"code":exc.code if isinstance(exc,InputIssue) else "unreadable_or_invalid_json","path":"input"}]}
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 2 if result["status"] == "invalid_input" else 0


if __name__ == "__main__":
    raise SystemExit(main())
