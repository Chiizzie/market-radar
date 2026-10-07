"""Entity-bound, dated headline evidence. RSS evidence is not fact verification."""
import hashlib
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import requests
from event_timing import event_timing

logger = logging.getLogger(__name__)
GOOGLE_NEWS_URL = 'https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en'
EVENTS = {
    'earnings': r'\b(earnings|quarterly results|reports? (?:its )?(?:financial|fiscal|quarterly) results|raises? (?:its )?(?:guidance|revenue|forecast)|revenue target|cuts? (?:its )?guidance)\b',
    'merger': r'\b(acquires?|acquisition|merger|takeover|buyout)\b',
    'product_launch': r'\b(launches?|unveils?|rolls? out|releases? new)\b',
    'regulatory': r'\b(approves?|approval|clinical trial|phase 3|phase iii|lawsuit|sues|sec charges|fda)\b',
    'partnership': r'\b(partnership|partners? with|collaboration|contract|agreement)\b',
    'crypto_event': r'\b(mainnet|upgrade|hard fork|token burn|token unlock|etf approval)\b',
    'financing': r'\b(offering|dilution|convertible notes|at.the.market|bankruptcy)\b',
}
NEGATIVE = r'\b(misses|cuts (?:its )?guidance|lawsuit|sues|charges|hack|exploit|stock offering|share offering|public offering|bond offering|rights offering|million offering|dilution|bankruptcy|token unlock|trial failure|phase (?:3|iii) failure)\b'
COMMENTARY = r'\b(price prediction|should you buy|stocks? to buy|buy before|looks? undervalued|earnings outlook|earnings season|growth runway|investment narrative|change.{0,30}narrative|highs and lows|technical analysis|could (?:rise|surge)|may (?:rise|surge)|rumou?r|speculation|possible|potential|total assets of)\b'
EVENT_ASSERTION = r'\b(reports?|announces?|raises?|cuts?|beats?|miss(?:es)?|results|transcript|presentation|acquires?|acquisition|merger|takeover|approv(?:es?|al)|launch(?:es)?|unveils?|partners? with|partnership|signs?|wins?|awarded|contract|agreement|upgrade|mainnet|burn|unlock|offering|bankruptcy|dilution|trial|clinical)\b'

ASSERTIONS = {
    'earnings': r'\b(reports?|announces?|posts?|results|earnings call|raises?|cuts?|beats?|misses)\b',
    'merger': r'\b(acquires?|agrees?|announces?|completes?|signs?|approved|approves?|acquisition proposal|merger vote|buyout offer)\b',
    'product_launch': r'\b(launches?|unveils?|rolls? out|releases? new)\b',
    'regulatory': r'\b(announces?|reports?|approval|approved|approves?|sues|lawsuit|trial success|trial failure|phase (?:3|iii) (?:success|failure)|FDA aligns)\b',
    'partnership': r'\b(announces?|signs?|wins?|awarded|partners? with|expands?|enters?|secures?|launches?|extends?)\b',
    'crypto_event': r'\b(announces?|launches?|upgrade|mainnet|token burn|token unlock|ETF approval)\b',
    'financing': r'\b(announces?|offering results|launches?|prices?|files?|proposes?|bankruptcy|dilution)\b',
}



def utcnow():
    return datetime.now(timezone.utc)


def fetch_news(query, limit=12):
    try:
        response = requests.get(GOOGLE_NEWS_URL.format(query=quote_plus(query)), timeout=12)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            raise ValueError('Invalid news feed')
        items = []
        for e in feed.entries[:limit]:
            published = None
            try:
                published = parsedate_to_datetime(e.get('published', '')).astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError):
                pass
            items.append({'title': e.get('title', ''), 'link': e.get('link', ''),
                          'source': e.get('source', {}).get('title', ''), 'published_at': published})
        return items
    except Exception as exc:
        logger.warning('News retrieval unavailable for %s (%s)', query, type(exc).__name__)
        return [{'retrieval_error': type(exc).__name__}]


def company_name(name):
    name = re.split(r' - |\b(?:Class [A-Z] |Common Stock|Common Shares|Ordinary Shares|American Depositary|American Depository)\b', name or '', maxsplit=1, flags=re.I)[0]
    return re.sub(r'\b(incorporated|corporation|inc|corp|limited|ltd|plc)\b\.?', '', name, flags=re.I).strip(' .,')


def entity_matches(title, symbol, name, asset_type):
    title = title.lower()
    # Company names must be distinctive; bare stock tickers require financial context.
    clean = company_name(name)
    name_match = bool(clean and re.search(r'(?<!\w)' + re.escape(clean.lower()) + r'(?!\w)', title))
    ticker_match = bool(re.search(r'(?<!\w)' + re.escape(symbol.lower()) + r'(?!\w)', title))
    if asset_type == 'crypto':
        context = bool(re.search(r'\b(crypto|token|blockchain|defi|mainnet|dex|coin|protocol)\b', title))
        # ORCA and other common words cannot qualify on their symbol alone.
        return (name_match or ticker_match) and context
    context = bool(re.search(r'\b(stock|shares|nasdaq|nyse|earnings|revenue|guidance)\b', title))
    return name_match or (ticker_match and context)


def classify_catalyst(headlines, symbol='', name='', asset_type='stock', now=None):
    now = now or utcnow()
    evidence, seen = [], set()
    for item in headlines:
        title = item.get('title', '')
        key = hashlib.sha256(re.sub(r'\s+', ' ', title.lower()).encode()).hexdigest()[:20]
        if key in seen or not entity_matches(title, symbol, name, asset_type):
            continue
        seen.add(key)
        try:
            published = datetime.fromisoformat((item.get('published_at') or '').replace('Z', '+00:00'))
            if published.tzinfo is None:
                continue
            age = (now - published).total_seconds() / 3600
        except (KeyError, ValueError, TypeError):
            continue
        if age < -0.1 or age > 24 or not item.get('source') or not item.get('link'):
            continue
        if '?' in title or re.search(COMMENTARY, title, re.I) or not re.search(EVENT_ASSERTION, title, re.I):
            continue
        category = next((k for k, pattern in EVENTS.items() if re.search(pattern, title, re.I)), None)
        if not category or not re.search(ASSERTIONS[category], title, re.I):
            continue
        if category == 'regulatory' and re.search(r'phase (3|iii)', title, re.I) and not re.search(r'FDA|clinical|trial|drug', title, re.I):
            continue
        evidence.append({**item, 'id': key, 'type': category, 'age_hours': round(age, 2),
                         'confidence': 'headline_evidence', 'adverse': bool(re.search(NEGATIVE, title, re.I)),
                         **event_timing(item, now)})
    evidence.sort(key=lambda x: x['published_at'], reverse=True)
    if not evidence:
        return {'type': 'unverified', 'summary': 'No fresh, entity-matched event found.',
                'eligible_for_points': False, 'headlines': [], 'freshness': 'STALE/UNVERIFIED'}
    best = evidence[0]
    return {**best, 'summary': best['title'], 'eligible_for_points': not best['adverse'] and best['event_time_status'] == 'REPORTED_RECENT',
            'headlines': evidence[:3], 'verification': 'Headline and asset identity matched; read source before trading.'}


def find_catalyst_for_asset(query, asset_type='stock', name='', now=None):
    identity = name or query
    search = f'"{identity}" {"crypto token" if asset_type == "crypto" else "stock"} when:1d'
    items = fetch_news(search)
    if items and items[0].get('retrieval_error'):
        return {'type': 'unverified', 'summary': 'News provider unavailable: ' + items[0]['retrieval_error'],
                'eligible_for_points': False, 'headlines': [], 'freshness': 'UNAVAILABLE', 'retrieval_status': 'FAILED'}
    return {**classify_catalyst(items, query, name, asset_type, now), 'retrieval_status': 'OK', 'headlines_checked': len(items)}
