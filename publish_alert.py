"""Publish material changes to the existing open PR; dedupe survives runner restarts."""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ALERT_BRANCH = 'radar-alert-live'


def run(args, capture=False, check=True):
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def signal_key(item):
    return item['asset_type'] + ':' + item.get('coin_id', item.get('ticker', item.get('symbol')))


def material_change(previous, current):
    if not previous:
        return True
    if previous.get('qualification', {}).get('status') != current['qualification']['status']:
        return True
    if previous.get('catalyst', {}).get('id') != current.get('catalyst', {}).get('id'):
        return True
    for field in ['status']:
        if previous.get('broker_availability', {}).get(field) != current.get('broker_availability', {}).get(field):
            return True
    for field in ['event_time_status', 'event_at']:
        if previous.get('catalyst', {}).get(field) != current.get('catalyst', {}).get(field):
            return True
    old, new = previous.get('qualification', {}).get('plan') or {}, current['qualification'].get('plan') or {}
    if not old and not new:
        # News watches have no executable plan; stable news must not wake the task every scan.
        return False
    if bool(old) != bool(new):
        return True
    for field in ['entry_trigger', 'stop_loss', 'tp1', 'tp2']:
        if old.get(field) is None or new.get(field) is None:
            return True
        if old[field] <= 0 or abs(new[field] / old[field] - 1) >= .01:
            return True
    return abs(current['price'] / previous['price'] - 1) >= .01


def prepare_alert(payload, previous):
    signals = payload.get('potential_signals', [])
    old = {signal_key(x): x for x in previous.get('potential_signals', [])}
    current = {signal_key(x): x for x in signals}
    changes = [x for key, x in current.items() if material_change(old.get(key), x)]
    removed = [key for key in old if key not in current]
    if not changes and not removed:
        return None
    # Keep unchanged items at their last published levels for comparison against cumulative changes.
    published = [item if material_change(old.get(key), item) else old[key] for key, item in current.items()]
    return {'schema_version': 2, 'kind': 'scan_update', 'generated_at': payload['generated_at'],
            'potential_signals': published, 'changed_signals': [signal_key(x) for x in changes],
            'invalidated_signals': removed, 'health': payload.get('health'),
            'notice': payload.get('notice'), 'delivery_status': 'GitHub publication only; not a ChatGPT delivery receipt.'}


def main():
    payload = json.loads(Path('latest_scan.json').read_text())
    # Verify the monitored PR exists without relying on pull-request read permission.
    run(['git', 'fetch', 'origin', ALERT_BRANCH], capture=True)
    prior = run(['git', 'show', f'origin/{ALERT_BRANCH}:radar_alert.json'], capture=True, check=False)
    if prior.returncode and 'does not exist' not in prior.stderr and 'exists on disk' not in prior.stderr:
        raise RuntimeError('Cannot read the previous alert payload; refusing to reset deduplication state.')
    previous = json.loads(prior.stdout) if prior.returncode == 0 else {}
    alert = prepare_alert(payload, previous)
    diagnostic = {'generated_at': payload['generated_at'], 'qualified_count': len(payload.get('potential_signals', [])),
                  'status': 'PUBLISH_PENDING' if alert else 'SUPPRESSED_NO_MATERIAL_CHANGE',
                  'chatgpt_delivery': 'NOT_OBSERVABLE_FROM_GITHUB', 'monitored_pr': 1}
    if alert:
        run(['git', 'config', 'user.name', 'github-actions[bot]'])
        run(['git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com'])
        run(['git', 'checkout', '--force', '-B', ALERT_BRANCH, f'origin/{ALERT_BRANCH}'])
        Path('radar_alert.json').write_text(json.dumps(alert, indent=2, allow_nan=False) + '\n')
        run(['git', 'add', 'radar_alert.json'])
        run(['git', 'commit', '-m', 'Publish material radar setup change'])
        run(['git', 'push', 'origin', ALERT_BRANCH])
        diagnostic['status'] = 'GITHUB_PUBLISHED'
    Path('bridge_status.json').write_text(json.dumps(diagnostic, indent=2) + '\n')
    print(json.dumps(diagnostic))
    if summary := os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a') as f:
            f.write('\n## ChatGPT event bridge\n\n```json\n' + json.dumps(diagnostic, indent=2) + '\n```\n')


if __name__ == '__main__':
    main()
