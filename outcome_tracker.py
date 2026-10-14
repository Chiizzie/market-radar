"""Persist sampled research outcomes. This is not a trade-fill or P&L ledger."""
import copy
import hashlib
import json
import math
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

BRANCH = 'radar-history'
FILE = 'radar_outcomes.json'


def stamp(value):
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return d if d.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def key(item):
    return item['asset_type'] + ':' + (item.get('coin_id') or item.get('ticker') or item.get('symbol'))


def usable_quote(item, now):
    t = stamp(item.get('observed_at'))
    p = item.get('price')
    return bool(t and 0 <= (now-t).total_seconds() <= 900 and isinstance(p, (int,float)) and math.isfinite(p) and p > 0)


def update_outcomes(previous, payload):
    now = stamp(payload['generated_at'])
    if now is None:
        raise ValueError('Invalid scan timestamp')
    if previous.get('last_scan') == payload['generated_at']:
        return previous
    records = copy.deepcopy(previous.get('records', {}))
    # Retain 30 days. These are sampled research observations, never assumed executions.
    records = {k:r for k,r in records.items() if stamp(r['first_seen']) >= now-timedelta(days=30)}
    quotes = {}
    for item in payload.get('tracking_prices', []) + payload.get('stocks', []) + payload.get('crypto', []):
        if usable_quote(item, now):
            k = key(item)
            if k not in quotes or stamp(item['observed_at']) > stamp(quotes[k]['observed_at']):
                quotes[k] = item
    current = {key(x):x for x in payload.get('stocks', [])+payload.get('crypto', [])}
    cohort = {key(x): x for x in payload.get('stocks', [])+payload.get('crypto', [])+payload.get('potential_signals', [])}
    for candidate in payload.get('health', {}).get('stocks', {}).get('candidate_manifest', []):
        if candidate['state'] == 'DEFERRED':
            symbol = candidate['symbol']
            cohort.setdefault('stock:'+symbol, {'asset_type':'stock','ticker':symbol,'qualification':{'status':'DEFERRED'}})
    for item in cohort.values():
        asset_key = key(item)
        event = item.get('catalyst', {}).get('id') or 'no-event'
        direction = item.get('qualification', {}).get('direction', (item.get('qualification', {}).get('plan') or {}).get('direction', 'LONG'))
        if direction == 'SHORT':
            event += ':SHORT'
        record_key = hashlib.sha256((asset_key + ':' + event + ':' + now.date().isoformat()).encode()).hexdigest()[:24]
        if record_key not in records:
            records[record_key] = {'asset': asset_key, 'symbol': item.get('ticker', item.get('symbol')),
                                  'event_id': event, 'direction': direction, 'first_seen': payload['generated_at'], 'last_seen': payload['generated_at'],
                                  'initial_status': item['qualification']['status'], 'last_status': item['qualification']['status'],
                                  'initial_plan': item['qualification'].get('plan'), 'observations': 0, 'missing_scans': 0,
                                  'status_changes': [], 'horizon_returns': {}, 'executed_trade': False}
    for record in records.values():
        age = (now-stamp(record['first_seen'])).total_seconds()
        if age > 26*3600:
            record['tracking_status'] = 'OBSERVATION_WINDOW_ENDED'
            continue
        if (record['asset'] in current and record.get('direction', 'LONG') == current[record['asset']].get('qualification', {}).get('direction', (current[record['asset']].get('qualification', {}).get('plan') or {}).get('direction', 'LONG'))):
            item = current[record['asset']]
            status = item['qualification']['status']
            if status == 'ACTIONABLE NOW' and item['qualification'].get('plan') and not record.get('first_actionable_at'):
                record['first_actionable_at'] = payload['generated_at']
                record['actionable_plan'] = copy.deepcopy(item['qualification']['plan'])
                record['watch_to_actionable_seconds'] = max(0, (now-stamp(record['first_seen'])).total_seconds())
                evidence = stamp(item.get('observed_at'))
                record['evidence_latency_seconds'] = max(0, (now-evidence).total_seconds()) if evidence else None
            record['last_seen'] = payload['generated_at']
            if status != record['last_status']:
                record['status_changes'].append({'at':payload['generated_at'], 'from':record['last_status'], 'to':status})
                record['last_status'] = status
        quote = quotes.get(record['asset'])
        if not quote:
            record['missing_scans'] += 1
            record['tracking_status'] = 'QUOTE_UNAVAILABLE'
            continue
        if record.get('last_observed_at') and stamp(quote['observed_at']) <= stamp(record['last_observed_at']):
            continue
        if 'baseline_price' not in record:
            record.update(baseline_price=quote['price'], baseline_at=quote['observed_at'], currency=quote['currency'],
                          max_observed_return_pct=0, min_observed_return_pct=0)
        if record['currency'] != quote['currency']:
            record['tracking_status'] = 'CURRENCY_MISMATCH'
            continue
        change = (quote['price']/record['baseline_price']-1)*100
        record.update(last_observed_at=quote['observed_at'], last_price=quote['price'], last_return_pct=round(change,4),
                      max_observed_return_pct=max(record['max_observed_return_pct'],change),
                      min_observed_return_pct=min(record['min_observed_return_pct'],change), tracking_status='OBSERVING')
        record['observations'] += 1
        from performance import observe_plan
        observe_plan(record, quote, now)
        elapsed = (stamp(quote['observed_at'])-stamp(record['baseline_at'])).total_seconds()/3600
        for hours in [1,4,24]:
            if hours <= elapsed <= hours+1 and str(hours) not in record['horizon_returns']:
                record['horizon_returns'][str(hours)] = {'return_pct': round(change,4), 'observed_at':quote['observed_at'], 'elapsed_hours':round(elapsed,3)}
    return {'schema_version':1, 'last_scan':payload['generated_at'], 'records':records,
            'notice':'30-day retained research log; up to 26h sampled follow-up. Returns start at first fresh observed quote, not an assumed entry. No fills, fees, stop/target ordering or realised P&L inferred. Missing quotes are coverage gaps, not losses or invalidations.'}


def git(args, input=None, check=True):
    return subprocess.run(['git']+args, input=input, text=True, capture_output=True, check=check)


def main():
    payload = json.loads(Path('latest_scan.json').read_text())
    exists = git(['ls-remote','--exit-code','--heads','origin',BRANCH], check=False)
    if exists.returncode not in {0,2}:
        raise RuntimeError('Cannot read history branch; refusing to reset outcomes')
    parent, previous = None, {}
    if exists.returncode == 0:
        git(['fetch','origin',BRANCH])
        parent = git(['rev-parse','FETCH_HEAD']).stdout.strip()
        previous = json.loads(git(['show',parent+':'+FILE]).stdout)
    result = update_outcomes(previous, payload)
    Path(FILE).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    if result != previous:
        blob = git(['hash-object','-w','--stdin'], input=json.dumps(result,allow_nan=False)+'\n').stdout.strip()
        tree = git(['mktree'], input='100644 blob '+blob+'\t'+FILE+'\n').stdout.strip()
        git(['config','user.name','github-actions[bot]'])
        git(['config','user.email','41898282+github-actions[bot]@users.noreply.github.com'])
        commit = git(['commit-tree',tree]+(['-p',parent] if parent else [])+['-m','Record sampled radar research outcomes']).stdout.strip()
        git(['push','origin',commit+':refs/heads/'+BRANCH])
    from performance import summarize
    summary = {**summarize(result), 'records':len(result['records']), 'with_fresh_baseline':sum('baseline_price' in r for r in result['records'].values()),
               'with_1h_followup':sum('1' in r['horizon_returns'] for r in result['records'].values()),
               'status':'PERSISTED', 'notice':result['notice']}
    Path('outcome_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write('\n## Research outcome tracking\n\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()


