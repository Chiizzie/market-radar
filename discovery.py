"""Broad US exchange-listed discovery, with explicit provider coverage accounting."""
import csv
import io
import math
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
import yfinance as yf
from catalyst_scanner import fetch_news, classify_catalyst, company_name

DIRECTORIES = ['https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
               'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt']
STOCK_NEWS_QUERIES = [
    '(earnings OR guidance OR "revenue forecast") (NASDAQ OR NYSE) when:1d',
    '(acquisition OR merger OR takeover) (NASDAQ OR NYSE) when:1d',
    '("FDA approval" OR "clinical trial" OR "phase 3") (stock OR shares) when:1d',
    '(contract OR partnership OR "product launch") (NASDAQ OR NYSE) when:1d',
    '(offering OR bankruptcy OR dilution) (NASDAQ OR NYSE) when:1d',
]
CRYPTO_NEWS_QUERIES = ['(mainnet OR upgrade OR partnership OR launch) (crypto OR token) when:1d',
                       '("token burn" OR "token unlock" OR "ETF approval" OR exploit) crypto when:1d']


def finite(value, default=0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def parse_directory(text):
    rows = csv.DictReader(io.StringIO(text), delimiter='|')
    if not rows.fieldnames or 'Security Name' not in rows.fieldnames:
        raise ValueError('Invalid symbol directory')
    result = {}
    for row in rows:
        symbol = row.get('Symbol') or row.get('ACT Symbol') or ''
        name = row.get('Security Name') or ''
        if row.get('Test Issue') != 'N' or row.get('ETF') != 'N':
            continue
        # Common/ordinary shares and ADRs; exclude derivative and preferred issues.
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', symbol) or re.search(r'\b(warrants?|units?|preferred|rights|notes|debentures)\b', name, re.I):
            continue
        result[symbol.replace('.', '-')] = company_name(name)
    return result


def listed_universe():
    universe, errors = {}, []
    def read(url):
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return parse_directory(r.text)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for url, future in zip(DIRECTORIES, [pool.submit(read, u) for u in DIRECTORIES]):
            try:
                universe.update(future.result())
            except Exception as exc:
                errors.append({'source': url, 'error': type(exc).__name__})
    return universe, errors


def yahoo_quotes(max_pages=48, max_seconds=150):
    """Read every page, rather than predefined gainers' hidden market-cap restrictions."""
    query = yf.EquityQuery('is-in', ['exchange', 'NMS', 'NGM', 'NCM', 'NYQ', 'ASE', 'PCX', 'BTS', 'NAE'])
    quotes, seen, errors = [], set(), []
    total, offset, complete = None, 0, False
    start = time.monotonic()
    for page in range(max_pages):
        if time.monotonic() - start > max_seconds:
            errors.append('Snapshot discovery time budget reached')
            break
        try:
            result = yf.screen(query, offset=offset, size=250, sortField='ticker', sortAsc=True)
            if not isinstance(result, dict) or 'quotes' not in result:
                raise ValueError('Invalid screener response')
            rows = result['quotes']
            total = result.get('total', total)
            if not isinstance(total, int) or total < 0:
                raise ValueError('Missing provider total')
            fresh = [x for x in rows if x.get('symbol') and x['symbol'] not in seen]
            if not fresh and offset < total:
                raise ValueError('Pagination stopped before provider total')
            quotes.extend(fresh)
            seen.update(x['symbol'] for x in fresh)
            offset += len(rows)
            if offset >= total:
                complete = True
                break
        except Exception as exc:
            errors.append('Quote page failed at offset ' + str(offset) + ': ' + type(exc).__name__)
            break
    if not complete and not errors:
        errors.append('Snapshot page limit reached')
    return quotes, {'provider_total': total, 'snapshot_records': len(quotes), 'pagination_complete': complete, 'errors': errors}


def broad_quotes():
    """Nasdaq's full screener download; Yahoo pagination is a degraded fallback."""
    try:
        response = requests.get('https://api.nasdaq.com/api/screener/stocks',
                                params={'tableonly': 'false', 'limit': 0, 'download': 'true'}, timeout=25)
        response.raise_for_status()
        data = response.json().get('data') or {}
        rows = data.get('rows')
        if not isinstance(rows, list) or not rows:
            raise ValueError('Empty Nasdaq screener response')
        def number(value):
            return finite(re.sub(r'[$%,]', '', str(value)), None)
        quotes = []
        for row in rows:
            symbol = row.get('symbol', '').replace('.', '-')
            price = number(row.get('lastsale'))
            if not symbol or price is None:
                continue
            quotes.append({'symbol': symbol, 'regularMarketPrice': price,
                           'regularMarketChangePercent': number(row.get('pctchange')),
                           'regularMarketVolume': number(row.get('volume')), 'marketCap': number(row.get('marketCap')),
                           'snapshot_source': 'Nasdaq screener', 'snapshot_time_unverified': True})
        # Nasdaq does not supply per-symbol timestamps here. These values only discover candidates.
        return quotes, {'source': 'Nasdaq full screener download', 'provider_total': None,
                        'snapshot_records': len(quotes), 'pagination_complete': True,
                        'price_timestamps': 'Not supplied; candidate chart timestamps must be verified separately', 'errors': []}
    except Exception as exc:
        quotes, health = yahoo_quotes()
        health['source'] = 'Yahoo paginated fallback'
        health['errors'].insert(0, 'Nasdaq screener failed: ' + type(exc).__name__)
        return quotes, health


def discover_catalysts(universe, asset_type='stock', now=None):
    """Match broad event headlines to an independently obtained asset directory."""
    now = now or datetime.now(timezone.utc)
    queries = STOCK_NEWS_QUERIES if asset_type == 'stock' else CRYPTO_NEWS_QUERIES
    articles, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for query, items in zip(queries, pool.map(lambda q: fetch_news(q, limit=30), queries)):
            if items and items[0].get('retrieval_error'):
                errors.append(query + ': ' + items[0]['retrieval_error'])
            else:
                articles.extend(items)
    unique = {x.get('link') or x.get('title'): x for x in articles}
    alias_map = defaultdict(set)
    for symbol, name in universe.items():
        alias = re.sub(r'[^a-z0-9]+', ' ', company_name(name).lower()).strip()
        # Avoid matching a common one-word security name to unrelated news.
        if len(alias) >= 6 and (len(alias.split()) > 1 or len(alias) >= 8):
            alias_map[alias].add(symbol)
    hits, mapped = {}, 0
    for article in unique.values():
        title = article.get('title', '')
        normalized = ' ' + re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip() + ' '
        symbols = {s.replace('.', '-') for s in re.findall(r'(?:NASDAQ|NYSE|AMEX)\s*:\s*([A-Z][A-Z0-9.\-]{0,9})\b', title)}
        for alias, candidates in alias_map.items():
            if len(candidates) == 1 and ' ' + alias + ' ' in normalized:
                symbols.update(candidates)
        for symbol in symbols.intersection(universe):
            catalyst = classify_catalyst([article], symbol, universe[symbol], asset_type, now)
            if not catalyst.get('headlines'):
                continue
            mapped += 1
            catalyst.update(retrieval_status='OK', discovery_route='broad_event_news')
            old = hits.get(symbol)
            if old is None or catalyst.get('published_at', '') > old.get('published_at', ''):
                hits[symbol] = catalyst
    return hits, {'queries': len(queries), 'headlines_received': len(unique), 'matched_events': mapped,
                  'matched_assets': len(hits), 'failed_queries': len(errors), 'errors': errors,
                  'scope': 'Bounded broad event-news searches; not an exhaustive newswire'}


def stock_candidate_priority(quote):
    price = finite(quote.get('regularMarketPrice'))
    volume = finite(quote.get('regularMarketVolume'))
    average = finite(quote.get('averageDailyVolume3Month') or quote.get('averageDailyVolume10Day'))
    change = finite(quote.get('regularMarketChangePercent'))
    relative = volume / average if average > 0 else 0
    notional = price * volume
    # These are discovery thresholds only. Intraday validation follows separately.
    eligible = notional >= 500_000 and (abs(change) >= 1 or relative >= .5)
    return eligible, min(abs(change), 15) + min(relative * 5, 15)


def select_stock_candidates(quotes, universe, catalysts, regular, now, limit=30):
    records = {q['symbol']: q for q in quotes if q.get('symbol') in universe}
    news_order = sorted(catalysts, key=lambda s: catalysts[s].get('published_at', ''), reverse=True)
    movers = []
    if regular:
        for symbol, q in records.items():
            stamp = finite(q.get('regularMarketTime'))
            if not q.get('snapshot_time_unverified') and not 0 <= now.timestamp() - stamp <= 900:
                continue
            eligible, rank = stock_candidate_priority(q)
            if eligible:
                movers.append((rank, symbol))
    movers.sort(reverse=True)
    # Reserve room for both routes so one cannot crowd the other out.
    order = []
    for i in range(max(len(news_order), len(movers))):
        for symbol in ([news_order[i]] if i < len(news_order) else []) + ([movers[i][1]] if i < len(movers) else []):
            if symbol not in order:
                order.append(symbol)
    selected = [{'ticker': s, 'name': universe[s], 'quote': records.get(s, {}),
                 'catalyst': catalysts.get(s), 'discovery_route': 'catalyst_first' if s in catalysts else 'market_snapshot'} for s in order[:limit]]
    return selected, {'listed_snapshot_records': len(records), 'discovered_candidates': len(order),
                      'selected_candidates': len(selected), 'deferred_candidates': max(0, len(order)-limit),
                      'unquoted_listings': len(set(universe)-set(records))}
