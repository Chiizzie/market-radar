"""Entity-bound, dated headline evidence. RSS evidence is not fact verification."""
import hashlib
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import requests

logger = logging.getLogger(__name__)
GOOGLE_NEWS_URL = 'https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en'
EVENTS = {
    'earnings': r'\b(earnings|quarterly results|raises? (?:its )?(?:guidance|revenue|forecast)|revenue target|cuts? (?:its )?guidance)\b',
    'merger': r'\b(acquires?|acquisition|merger|takeover|buyout)\b',
    'product_launch': r'\b(launches?|unveils?|rolls? out|releases? new)\b',
    'regulatory': r'\b(approves?|approval|lawsuit|sues|sec charges|fda)\b',
    'partnership': r'\b(partnership|partners? with|collaboration|contract|agreement)\b',
    'crypto_event': r'\b(mainnet|upgrade|hard fork|token burn|token unlock|etf approval)\b',
    'financing': r'\b(offering|dilution|convertible notes|at.the.market|bankruptcy)\b',
}
NEGATIVE = r'\b(misses|cuts|lawsuit|sues|charges|hack|exploit|offering|dilution|bankruptcy|token unlock)\b'
COMMENTARY = r'\b(price prediction|should you buy|stocks? to buy|technical analysis|could (?:rise|surge)|may (?:rise|surge)|rumou?r|possible|potential)\b'


def utcnow():
    return datetime.now(timezone.utc)


def fetch_news(query, limit=12):
    try:
        response = requests.get(GOOGLE_NEWS_URL.format(query=quote_plus(query)), timeout=12)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
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
        return []


def entity_matches(title, symbol, name, asset_type):
    title = title.lower()
    # Company names must be distinctive; bare stock tickers require financial context.
    clean = re.sub(r'\b(incorporated|corporation|inc|corp|limited|ltd|holdings|plc)\b\.?', '', name or '', flags=re.I).strip(' .,')
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
        if re.search(COMMENTARY, title, re.I):
            continue
        category = next((k for k, pattern in EVENTS.items() if re.search(pattern, title, re.I)), None)
        if not category:
            continue
        evidence.append({**item, 'id': key, 'type': category, 'age_hours': round(age, 2),
                         'confidence': 'headline_evidence', 'adverse': bool(re.search(NEGATIVE, title, re.I)),
                         'freshness': 'NEW CATALYST' if age <= 6 else 'ONGOING CATALYST'})
    evidence.sort(key=lambda x: x['published_at'], reverse=True)
    if not evidence:
        return {'type': 'unverified', 'summary': 'No fresh, entity-matched event found.',
                'eligible_for_points': False, 'headlines': [], 'freshness': 'STALE/UNVERIFIED'}
    best = evidence[0]
    return {**best, 'summary': best['title'], 'eligible_for_points': not best['adverse'],
            'headlines': evidence[:3], 'verification': 'Headline and asset identity matched; read source before trading.'}


def find_catalyst_for_asset(query, asset_type='stock', name='', now=None):
    identity = name or query
    search = f'"{identity}" {"crypto token" if asset_type == "crypto" else "stock"} when:1d'
    return classify_catalyst(fetch_news(search), query, name, asset_type, now)
