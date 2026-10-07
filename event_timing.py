"""Keep article publication and event occurrence separate; never date an event by RSS."""
import re
from datetime import datetime, timezone

RECAP = r'\b(transcript|earnings call presentation|recap|retrospective|since (?:January|February|March|April|May|June|July|August|September|October|November|December))\b'
MONTHS = r'January|February|March|April|May|June|July|August|September|October|November|December'


def event_timing(item, now):
    title = item.get('title', '')
    result = {'event_at': None, 'event_date_source': None, 'event_date_precision': None,
              'event_time_status': 'UNVERIFIED', 'freshness': 'EVENT DATE UNVERIFIED',
              'timing_reason': 'Recent article publication does not establish when the underlying event occurred.'}
    if re.search(RECAP, title, re.I):
        return {**result, 'event_time_status': 'RECAP_UNVERIFIED', 'freshness': 'RECAP — EVENT DATE UNVERIFIED',
                'timing_reason': 'Transcript, presentation or recap; publication date is not the event date.'}
    # A date must be tied to the event with "on". A trailing article date or fiscal quarter is not evidence.
    match = re.search(r'\bon\s+((?:' + MONTHS + r')\s+\d{1,2},?\s+20\d{2}|20\d{2}-\d{2}-\d{2})\b', title, re.I)
    if not match:
        return result
    raw = match[1]
    try:
        day = datetime.strptime(raw, '%Y-%m-%d') if re.match(r'20\d{2}-', raw) else datetime.strptime(raw.replace(',', ''), '%B %d %Y')
        day = day.replace(tzinfo=timezone.utc)
    except ValueError:
        return result
    delta = (now - day).total_seconds() / 3600
    status = 'REPORTED_UPCOMING' if day.date() > now.date() else 'REPORTED_RECENT' if 0 <= delta <= 24 else 'REPORTED_OLDER'
    return {**result, 'event_at': day.date().isoformat(), 'event_date_source': item.get('link'),
            'event_date_precision': 'day', 'event_time_status': status,
            'freshness': status.replace('_', ' '),
            'timing_reason': 'Headline explicitly reports an event date; verify the source and exact event time before entry.'}
