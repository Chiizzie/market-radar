"""Direct issuer-distribution, regulatory and project RSS. No publication/event conflation."""
import calendar
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import feedparser
import requests
from reliability import http_get

STOCK_FEEDS = [
    ('PR Newswire issuer releases', 'https://www.prnewswire.com/rss/general-business-latest-news/general-business-latest-news-list.rss'),
    ('GlobeNewswire issuer releases', 'https://www.globenewswire.com/RssFeed/subjectcode/01-Business%20and%20Economy/feedTitle/GlobeNewswire%20-%20Business%20and%20Economy'),
    ('SEC EDGAR 8-K filings', 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=100&output=atom'),
]
CRYPTO_FEEDS = [
    ('Ethereum Foundation', 'https://blog.ethereum.org/feed.xml'),
    ('Chainlink', 'https://blog.chain.link/feed/'),
    ('Kraken announcements', 'https://blog.kraken.com/feed'),
]


def parse_feed(content, source, url):
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries or not feed.version:
        raise ValueError('Invalid announcement feed')
    articles = []
    for entry in feed.entries[:100]:
        stamp = entry.get('published_parsed') or entry.get('updated_parsed')
        published = datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc).isoformat() if stamp else None
        title = entry.get('title', '')
        if source.startswith('SEC'):
            title = re.sub(r'^8-K(?:/A)?\s*-\s*', '', title) + ' files an 8-K regulatory filing'
        articles.append({'title': title, 'link': entry.get('link', ''), 'published_at': published,
                         'source': source, 'source_kind': 'regulatory_filing' if source.startswith('SEC') else 'direct_announcement_feed',
                         'feed_url': url})
    return articles


def fetch_primary(asset_type):
    feeds = STOCK_FEEDS if asset_type == 'stock' else CRYPTO_FEEDS
    articles, failures = [], []
    def fetch(pair):
        source, url = pair
        try:
            r = http_get(url, headers={'User-Agent': 'MarketRadar research contact https://github.com/Chiizzie/market-radar'}, timeout=(5, 12))
            r.raise_for_status()
            return parse_feed(r.content, source, url), None
        except Exception as exc:
            return [], {'source': source, 'error': type(exc).__name__}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for rows, error in pool.map(fetch, feeds):
            articles.extend(rows)
            if error:
                failures.append(error)
    return articles, {'feeds_requested': len(feeds), 'feeds_received': len(feeds)-len(failures),
                      'headlines_received': len(articles), 'failures': failures,
                      'scope': 'Bounded issuer-distribution, regulatory and selected project feeds; not every issuer/project.'}

