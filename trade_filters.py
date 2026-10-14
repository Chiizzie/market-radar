"""Conservative direction-aware gates. ACTIONABLE NOW always needs live broker review."""
import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

ACTIONABLE = 'ACTIONABLE NOW'
WATCH = 'WATCH FOR ENTRY'
EXTENDED = 'EXTENDED—DO NOT CHASE'
REJECTED = 'REJECTED'


def _classify_setup(asset, catalyst, now=None, chart=False):
    now = now or datetime.now(timezone.utc)
    reasons = []
    direction = asset.get('direction', 'LONG')
    short = direction == 'SHORT'
    sign = -1 if short else 1
    if direction not in {'LONG', 'SHORT'}:
        return {'status': REJECTED, 'reasons': ['Unknown trade direction.'], 'plan': None}
    if short:
        broker = asset.get('short_broker_availability', {})
        from outcome_tracker import stamp as parse_stamp
        checked = parse_stamp(broker.get('checked_at'))
        if (asset.get('asset_type') != 'stock' or broker.get('status') != 'LISTED_PUBLIC'
                or broker.get('capabilities', {}).get('product') != 'CFD'
                or broker.get('capabilities', {}).get('short') is not True
                or not checked or not 0 <= (now-checked).total_seconds() <= 900):
            return {'status': 'BROKER_UNAVAILABLE', 'reasons': ['Fresh instrument-specific CFD short eligibility is unverified; bearish research only.'], 'plan': None}

    result = {'status': REJECTED, 'reasons': reasons, 'plan': None}
    local = now.astimezone(ZoneInfo('Europe/Dublin'))
    if local.hour >= 21 and (local.hour > 21 or local.minute >= 30):
        reasons.append('Fewer than 30 minutes remain before the 22:00 Dublin intraday cutoff.')
    if asset.get('asset_type') == 'stock' and asset.get('session') != 'REGULAR':
        reasons.append('US regular market session is closed.')
    age = None
    try:
        stamped = datetime.fromisoformat((asset.get('observed_at') or '').replace('Z', '+00:00'))
        age = (now - stamped).total_seconds()
        if not 0 <= age <= 900:
            reasons.append('Price data is stale or future-dated.')
    except (KeyError, TypeError, ValueError):
        reasons.append('Price timestamp missing.')
    history_status = asset.get('history_status')
    if chart and history_status != 'OK':
        reasons.append('Chart-led assessment requires fresh completed OHLCV.')
    if history_status in {'FAILED', 'STALE', 'NOT_REQUESTED'}:
        result['status'] = 'NOT_EVALUATED' if history_status == 'NOT_REQUESTED' else 'DATA_UNAVAILABLE'
        reasons.append(asset.get('data_issue', 'Historical data unavailable.'))
    if catalyst.get('retrieval_status') == 'FAILED':
        result['status'] = 'DATA_UNAVAILABLE'
        reasons.append('News retrieval failed; catalyst could not be assessed.')
    elif catalyst.get('adverse') and not short:
        reasons.append('Matched headline contains an adverse event; no bullish catalyst credit.')
    elif short and catalyst.get('eligible_for_points') and not catalyst.get('adverse'):
        reasons.append('Positive catalyst conflicts with a short setup; review squeeze risk.')
    elif short and not chart and not catalyst.get('adverse'):
        reasons.append('No matched adverse catalyst for the bearish news route.')
    elif not chart and catalyst.get('headlines') and catalyst.get('event_time_status') != 'REPORTED_RECENT':
        reasons.append(catalyst.get('timing_reason', 'Underlying event date is unverified.'))
    elif not chart and not (catalyst.get('eligible_for_points') or (short and catalyst.get('adverse'))):
        reasons.append('No qualifying fresh catalyst found; this does not mean the asset identity is missing.')
    elif not chart and catalyst.get('event_time_status') != 'REPORTED_RECENT':
        reasons.append('Underlying event date is unverified.')
    required = ['price', 'entry', 'support', 'resistance_1', 'resistance_2', 'momentum_pct', 'extension_pct', 'dollar_volume']
    missing = [k for k in required if not isinstance(asset.get(k), (int, float)) or not math.isfinite(asset[k])]
    if missing:
        result['missing_fields'] = missing
        reasons.extend(asset.get('structure_issues', []))
        if not asset.get('structure_issues') and history_status not in {'FAILED', 'STALE', 'NOT_REQUESTED'}:
            reasons.append('Missing or invalid fields: ' + ', '.join(missing) + '.')
        return result
    price, entry, support = asset['price'], asset['entry'], asset['support']
    tp1, tp2 = asset['resistance_1'], asset['resistance_2']
    if min(price, entry, support, tp1, tp2) <= 0:
        reasons.append('Invalid price or structural levels.')
        return result
    if asset['dollar_volume'] < (5_000_000 if asset['asset_type'] == 'stock' else 10_000_000):
        reasons.append('Insufficient traded notional liquidity.')
    if sign * asset['momentum_pct'] <= 0:
        reasons.append('No negative recent momentum; a daily decline alone is insufficient.' if short else 'No positive recent momentum; a daily gain alone is insufficient.')
    if asset['asset_type'] == 'stock' and asset.get('volume_spike', 0) < 1.5:
        reasons.append('Recent same-time relative volume is below 1.5x.')
    if chart and asset['asset_type'] == 'crypto' and (asset.get('chart_analysis', {}).get('recent_volume_ratio') or 0) < 1.5:
        reasons.append('Recent crypto bar volume is below 1.5x its preceding baseline.')
    if not (tp2 < tp1 < entry < support if short else support < entry < tp1 < tp2):
        reasons.append('No ordered resistance, trigger and two downside targets.' if short else 'No ordered support, trigger and two historical resistance targets.')
    if reasons and not chart:
        return result
    broker = asset.get('short_broker_availability' if short else 'broker_availability', {})
    if broker.get('status') not in {'LISTED_PUBLIC', 'LISTED_API'}:
        return {**result, 'status': 'BROKER_UNVERIFIED' if broker.get('status') not in {'NOT_LISTED', 'RESTRICTED'} else 'BROKER_UNAVAILABLE',
                'reasons': reasons + [broker.get('reason', 'Broker eligibility is unverified.')]}
    if asset['extension_pct'] > 3 or sign * (price / entry - 1) * 100 > 0.75:
        return {**result, 'status': EXTENDED, 'reasons': reasons + ['Price is extended in the trade direction; do not chase.']}
    # Stop below observed support; targets come from history, never invented multiples.
    stop = support * (1.001 if short else .999)
    executable_entry = min(price, entry) if short else max(price, entry)
    risk = sign * (executable_entry - stop)
    if risk <= 0:
        reasons.append('Stop must be above the short entry.' if short else 'Stop must be below the executable entry.')
        return result
    rr1, rr2 = sign * (tp1 - executable_entry) / risk, sign * (tp2 - executable_entry) / risk
    result['metrics'] = {'stop_width_pct':round(risk/executable_entry*100,4),
                         'rr1':round(rr1,4), 'rr2':round(rr2,4),
                         'minimum_rr1':1.5, 'minimum_rr2':2.0}
    if risk / executable_entry < .002:
        reasons.append('Stop width is below 0.2%.')
    if risk / executable_entry > .05:
        reasons.append('Stop width exceeds 5%.')
    if rr1 < 1.5:
        reasons.append('TP1 reward/risk is below 1.5R.')
    if rr2 < 2:
        reasons.append('TP2 reward/risk is below 2R.')
    currency = asset.get('currency')
    fx = asset.get('eur_per_unit', 1 if currency == 'EUR' else None)
    if not fx or not math.isfinite(fx) or fx <= 0:
        reasons.append('Fresh EUR conversion is unavailable.')
        return result
    status = ACTIONABLE if sign * (price-entry) >= 0 else WATCH
    if sign * (price/entry-1) < -.01:
        reasons.append('Price is more than 1% above the short trigger; wait for structure to improve.' if short else 'Price is more than 1% below the trigger; wait for structure to improve.')
        return result
    if age is None or age >= 600:
        reasons.append('Entry evidence is at least 10 minutes old; refresh before entry.')
    if reasons:
        return result
    risk_eur = risk * fx
    from risk_policy import position_size
    try:
        sizing = position_size(asset, executable_entry * fx, risk_eur, now)
    except ValueError as exc:
        return {**result, 'reasons': reasons + [str(exc)]}
    units = sizing['units']
    per_unit_cost = executable_entry * asset.get('cost_reserve_bps', 20) / 10000
    net_rr1 = (sign*(tp1-executable_entry)-per_unit_cost)/(risk+per_unit_cost)
    net_rr2 = (sign*(tp2-executable_entry)-per_unit_cost)/(risk+per_unit_cost)
    if net_rr1 < 1.5 or net_rr2 < 2:
        return {**result, 'reasons': reasons + ['Reward/risk after the planning cost reserve fails 1.5R/2R.']}
    result['metrics'].update(net_rr1=round(net_rr1,4), net_rr2=round(net_rr2,4))
    plan = {'currency': 'EUR', 'entry': executable_entry * fx, 'entry_trigger': entry * fx,
            'stop_loss': stop * fx, 'tp1': tp1 * fx, 'tp2': tp2 * fx,
            'rr1': round(net_rr1, 2), 'rr2': round(net_rr2, 2), 'gross_rr1':round(rr1,2), 'gross_rr2':round(rr2,2), 'units': units,
            'quote_currency': currency, 'eur_per_quote_unit': fx,
            'position_eur': round(units * executable_entry * fx, 2),
            'planned_loss_eur': sizing['planned_loss_eur'], 'risk_ceiling_eur': 20,
            'risk_budget_eur': sizing['risk_budget_eur'], 'cost_reserve_eur': sizing['cost_reserve_eur'],
            'cost_assumption': 'Planning reserve only; verify actual spread, fees and slippage. Stops do not guarantee maximum loss.',
            'generated_at': now.isoformat(), 'price_observed_at': asset['observed_at'],
            'expires_at': min(stamped + timedelta(minutes=10), now + timedelta(minutes=5)).isoformat(),
            'direction': direction, 'product': 'CFD' if short else broker.get('capabilities', {}).get('product'), 'horizon': 'INTRADAY',
            'exit_by': local.replace(hour=22, minute=0, second=0, microsecond=0).isoformat(),
            'invalidation': ('Resistance/stop breaks, positive catalyst emerges, or downward momentum reverses.' if short else 'Pattern/support fails, adverse news emerges, or momentum reverses.' if chart else 'Support/stop fails, catalyst is disproved, or momentum reverses.'),
            'review_required': 'Verify broker availability, bid/ask spread, source article, fees and fresh price.'}
    if short:
        limit = broker.get('max_open_short')
        if not isinstance(limit, (int, float)) or not math.isfinite(limit) or units > limit:
            return {**result, 'reasons': reasons + ['CFD quantity exceeds verified short limit or limit is missing.']}
        plan.update(broker='Trading 212 CFD', financing='Intraday only; verify current overnight rate and broker rollover time before entry.',
                    review_required='Verify CFD sell permission, executable bid/ask, quantity limits, margin, spread, fees, financing and fresh chart. Underlying equity chart is not an executable CFD quote.')
    return {'status': status, 'reasons': [('Bearish pattern/catalyst, negative momentum, volume and net R:R pass; verify CFD execution.' if short else 'Confirmed chart pattern, positive momentum, volume and projected/observed R:R pass.' if chart else 'Observed trigger, positive momentum, fresh headline evidence and structural R:R pass.')], 'plan': plan, 'metrics': result['metrics']}


SOFT_BLOCKERS = {
    'No negative recent momentum; a daily decline alone is insufficient.',
    'Price is more than 1% above the short trigger; wait for structure to improve.',
    'Recent same-time relative volume is below 1.5x.',
    'Recent crypto bar volume is below 1.5x its preceding baseline.',
    'No positive recent momentum; a daily gain alone is insufficient.',
    'Stop width is below 0.2%.',
    'Stop width exceeds 5%.',
    'TP1 reward/risk is below 1.5R.',
    'TP2 reward/risk is below 2R.',
    'Price is more than 1% below the trigger; wait for structure to improve.',
}


def classify_setup(asset, catalyst, now=None):
    """Assess news and chart routes independently; neither bypasses risk gates."""
    news = {**_classify_setup(asset, catalyst, now), 'route': 'CATALYST_LED'}
    assessments = [news]
    for pattern in asset.get('chart_analysis', {}).get('setups', []):
        short = pattern.get('direction', asset.get('direction', 'LONG')) == 'SHORT'
        candidate = {**asset, 'direction': pattern.get('direction', asset.get('direction', 'LONG')), **{k: pattern[k] for k in ['entry', 'support', 'resistance_1', 'resistance_2', 'momentum_pct', 'extension_pct']}}
        try:
            observed = datetime.fromisoformat(pattern['observed_at'].replace('Z', '+00:00'))
            evidence_fresh = 0 <= ((now or datetime.now(timezone.utc)) - observed).total_seconds() <= 900
        except (KeyError, TypeError, ValueError):
            evidence_fresh = False
        if not evidence_fresh:
            candidate['history_status'] = 'STALE'
            candidate['data_issue'] = 'Pattern evidence timestamp is missing or stale.'
        new_pattern = pattern['pattern'] in {'EARLY_RANGE_BREAKOUT', 'SUPPORT_BOUNCE', 'MOMENTUM_CONTINUATION', 'SESSION_VWAP_RECLAIM'}
        context = asset.get('chart_analysis', {}).get('higher_timeframe', {})
        from outcome_tracker import stamp as parse_stamp
        higher_at = parse_stamp(context.get('observed_at'))
        higher_fresh = higher_at and 0 <= ((now or datetime.now(timezone.utc))-higher_at).total_seconds() <= 900
        if (new_pattern or short) and (context.get('trend') != ('FALLING' if short else 'RISING') or not higher_fresh):
            candidate['history_status'] = 'STALE'
            candidate['data_issue'] = 'Pattern requires fresh falling completed 15-minute context.' if short else 'New pattern requires rising completed 15-minute context.'
        chart = {**_classify_setup(candidate, catalyst, now, chart=True),
                 'route': 'CHART_LED', 'direction': 'SHORT' if short else 'LONG', 'pattern': pattern['pattern'],
                 'target_method': pattern['target_method'], 'pattern_evidence': pattern}
        if short and chart['status'] == 'BROKER_UNAVAILABLE' and evidence_fresh and candidate.get('history_status') == 'OK':
            chart.update(status='BEARISH WATCH', research_only=True, plan=None,
                         blockers=chart['reasons'], reasons=['Bearish chart detected; no approved executable short plan.'] + chart['reasons'])
        if pattern.get('stage') == 'DEVELOPING' and chart['status'] in {ACTIONABLE, WATCH}:
            chart.update(status='TECHNICAL WATCH', research_only=True, plan=None,
                         blockers=['Wait for a completed breakout/confirmation bar.'])
        if chart.get('plan'):
            chart['plan'].update(target_method=pattern['target_method'], pattern=pattern['pattern'])
        # Only technical near-misses become research watches; never data/broker/news faults.
        fx = asset.get('eur_per_unit', 1 if asset.get('currency') == 'EUR' else None)
        safe = (asset.get('short_broker_availability' if short else 'broker_availability', {}).get('status') in {'LISTED_PUBLIC', 'LISTED_API'}
                and isinstance(fx, (int, float)) and math.isfinite(fx) and fx > 0
                and pattern['extension_pct'] <= 3 and (-1 if short else 1)*(asset['price']/pattern['entry']-1) <= .0075)
        blocker_groups = {('reward_risk' if r.startswith(('TP1 reward', 'TP2 reward')) else 'stop_width' if r.startswith('Stop width') else r) for r in chart['reasons']}
        if chart['status'] == REJECTED and safe and 1 <= len(blocker_groups) <= 2 and set(chart['reasons']) <= SOFT_BLOCKERS:
            chart.update(status='TECHNICAL WATCH', research_only=True, plan=None,
                         blockers=list(chart['reasons']))
        assessments.append(chart)
    priority = {ACTIONABLE: 5, WATCH: 4, 'TECHNICAL WATCH': 3, 'BEARISH WATCH': 2.5, EXTENDED: 2}
    best = max(assessments, key=lambda q: (priority.get(q['status'], 0), (q.get('plan') or {}).get('rr1', 0)))
    return {**best, 'assessments': assessments}


