"""Material-change Telegram notifications plus hourly operational summaries."""
from datetime import datetime, timezone
from outcome_tracker import stamp
from publish_alert import prepare_alert
from radar import telegram


def deliver_snapshot(payload, state, message):
    now = stamp(payload['generated_at']) or datetime.now(timezone.utc)
    previous = state.get('telegram', {})
    change = prepare_alert(payload, previous.get('payload', {}))
    last = stamp(previous.get('accepted_at'))
    heartbeat = not last or (now-last).total_seconds() >= 3600
    if not change and not heartbeat:
        return 'SUPPRESSED_NO_MATERIAL_CHANGE'
    result = telegram(message)
    # Never mark failed/ambiguous sends as delivered; do not retry a POST here.
    # A later scan may retry; Telegram has no idempotency key, so exactly-once
    # delivery cannot be guaranteed after a lost acknowledgement.
    if result == 'API_ACCEPTED':
        state['telegram'] = {'accepted_at': now.isoformat(), 'payload': change or previous.get('payload', {})}
    return result
