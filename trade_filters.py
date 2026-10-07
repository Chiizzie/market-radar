"""Conservative long-only gates. ACTIONABLE NOW always needs live broker review."""
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ACTIONABLE = 'ACTIONABLE NOW'
WATCH = 'WATCH FOR ENTRY'
EXTENDED = 'EXTENDED—DO NOT CHASE'
REJECTED = 'REJECTED'


def classify_setup(asset, catalyst, now=None):
    now = now or datetime.now(timezone.utc)
    reasons = []
    result = {'status': REJECTED, 'reasons': reasons, 'plan': None}
    local = now.astimezone(ZoneInfo('Europe/Dublin'))
    if local.hour >= 21 and (local.hour > 21 or local.minute >= 30):
        reasons.append('Fewer than 30 minutes remain before the 22:00 Dublin intraday cutoff.')
    if asset.get('asset_type') == 'stock' and asset.get('session') != 'REGULAR':
        reasons.append('US regular market session is closed.')
    try:
        stamped = datetime.fromisoformat((asset.get('observed_at') or '').replace('Z', '+00:00'))
        age = (now - stamped).total_seconds()
        if not 0 <= age <= 900:
            reasons.append('Price data is stale or future-dated.')
    except (KeyError, TypeError, ValueError):
        reasons.append('Price timestamp missing.')
    history_status = asset.get('history_status')
    if history_status in {'FAILED', 'STALE', 'NOT_REQUESTED'}:
        result['status'] = 'NOT_EVALUATED' if history_status == 'NOT_REQUESTED' else 'DATA_UNAVAILABLE'
        reasons.append(asset.get('data_issue', 'Historical data unavailable.'))
    if catalyst.get('retrieval_status') == 'FAILED':
        result['status'] = 'DATA_UNAVAILABLE'
        reasons.append('News retrieval failed; catalyst could not be assessed.')
    elif catalyst.get('adverse'):
        reasons.append('Matched headline contains an adverse event; no bullish catalyst credit.')
    elif catalyst.get('headlines') and catalyst.get('event_time_status') != 'REPORTED_RECENT':
        reasons.append(catalyst.get('timing_reason', 'Underlying event date is unverified.'))
    elif not catalyst.get('eligible_for_points'):
        reasons.append('No qualifying fresh catalyst found; this does not mean the asset identity is missing.')
    elif catalyst.get('event_time_status') != 'REPORTED_RECENT':
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
    if asset['momentum_pct'] <= 0:
        reasons.append('No positive recent momentum; a daily gain alone is insufficient.')
    if asset['asset_type'] == 'stock' and asset.get('volume_spike', 0) < 1.5:
        reasons.append('Recent same-time relative volume is below 1.5x.')
    if not support < entry < tp1 < tp2:
        reasons.append('No ordered support, trigger and two historical resistance targets.')
    if reasons:
        return result
    broker = asset.get('broker_availability', {})
    if broker.get('status') not in {'LISTED_PUBLIC', 'LISTED_API'}:
        return {**result, 'status': 'BROKER_UNVERIFIED' if broker.get('status') not in {'NOT_LISTED', 'RESTRICTED'} else 'BROKER_UNAVAILABLE',
                'reasons': [broker.get('reason', 'Broker eligibility is unverified.')]}
    if asset['extension_pct'] > 3 or (price / entry - 1) * 100 > 0.75:
        return {**result, 'status': EXTENDED, 'reasons': ['Price is extended above the recent average or the entry trigger.']}
    # Stop below observed support; targets come from history, never invented multiples.
    stop = support * 0.999
    executable_entry = max(price, entry)
    risk = executable_entry - stop
    rr1, rr2 = (tp1 - executable_entry) / risk, (tp2 - executable_entry) / risk
    if not 0.002 <= risk / executable_entry <= 0.05 or rr1 < 1.5 or rr2 < 2:
        reasons.append('Stop width or achievable reward/risk fails the entry filter.')
        return result
    currency = asset.get('currency')
    fx = asset.get('eur_per_unit', 1 if currency == 'EUR' else None)
    if not fx or not math.isfinite(fx) or fx <= 0:
        reasons.append('Fresh EUR conversion is unavailable.')
        return result
    status = ACTIONABLE if price >= entry else WATCH
    if price < entry * 0.99:
        reasons.append('Price is more than 1% below the trigger; wait for structure to improve.')
        return result
    risk_eur = risk * fx
    units = min(5 / risk_eur, 500 / (executable_entry * fx))
    plan = {'currency': 'EUR', 'entry': executable_entry * fx, 'entry_trigger': entry * fx,
            'stop_loss': stop * fx, 'tp1': tp1 * fx, 'tp2': tp2 * fx,
            'rr1': round(rr1, 2), 'rr2': round(rr2, 2), 'units': units,
            'position_eur': round(units * executable_entry * fx, 2),
            'planned_loss_eur': round(units * risk_eur, 2), 'risk_ceiling_eur': 20,
            'exit_by': local.replace(hour=22, minute=0, second=0, microsecond=0).isoformat(),
            'invalidation': 'Support/stop fails, catalyst is disproved, or momentum reverses.',
            'review_required': 'Verify broker availability, bid/ask spread, source article, fees and fresh price.'}
    return {'status': status, 'reasons': ['Observed trigger, positive momentum, fresh headline evidence and structural R:R pass.'], 'plan': plan}
