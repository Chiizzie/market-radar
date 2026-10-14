"""Sampled research proxies, never inferred fills or a trading win rate."""
import math
from outcome_tracker import stamp


def observe_plan(record, quote, now):
    plan = record.get('actionable_plan')
    if not plan:
        return
    observed = stamp(quote.get('observed_at'))
    first = stamp(record.get('first_actionable_at'))
    if not observed or not first or observed <= first:
        return
    exit_by = stamp(plan.get('exit_by'))
    if exit_by and observed > exit_by:
        record['plan_tracking_status'] = 'INTRADAY_WINDOW_ENDED'
        return
    fx = quote.get('eur_per_unit', 1 if quote.get('currency') == 'EUR' else None)
    if not isinstance(fx, (int, float)) or not math.isfinite(fx) or fx <= 0:
        record['plan_tracking_status'] = 'EUR_CONVERSION_UNAVAILABLE'
        return
    price = quote['price']*fx
    events = record.setdefault('sampled_plan_events', {})
    for name, hit in [('stop', price <= plan['stop_loss']), ('tp1', price >= plan['tp1']), ('tp2', price >= plan['tp2'])]:
        if hit:
            events.setdefault(name, {'observed_at': quote['observed_at'], 'price_eur': price})
    risk = plan['entry']-plan['stop_loss']
    if risk > 0:
        record['sampled_r_multiple'] = round((price-plan['entry'])/risk, 4)
    record['plan_tracking_status'] = 'SAMPLED_ONLY_NO_FILL_ASSUMED'


def summarize(result):
    records = list(result['records'].values())
    actionable = [r for r in records if r.get('first_actionable_at')]
    sampled = [r for r in actionable if 'sampled_r_multiple' in r]
    missed = [r for r in records if r['initial_status'] in {'REJECTED','DEFERRED','NOT_EVALUATED'}
              and r.get('max_observed_return_pct', 0) >= 3]
    latencies = sorted(r['evidence_latency_seconds'] for r in actionable if r.get('evidence_latency_seconds') is not None)
    return {'actionable_records': len(actionable), 'actionable_with_followup': len(sampled),
            'sampled_stop_observations': sum('stop' in r.get('sampled_plan_events', {}) for r in sampled),
            'sampled_tp1_observations': sum('tp1' in r.get('sampled_plan_events', {}) for r in sampled),
            'missed_move_proxy_count': len(missed),
            'missed_move_proxy_symbols': [r['symbol'] for r in missed[:30]],
            'median_evidence_latency_seconds': latencies[len(latencies)//2] if latencies else None,
            'measurement_limitations': 'Missed-move proxy: rejected/deferred cohort later observed +3%, not proof of a valid missed entry. Stop observations are false-positive proxies, not losses. Sparse quotes cannot resolve barrier order, fills, slippage or realised P&L; unquoted candidates are unmeasured.'}
