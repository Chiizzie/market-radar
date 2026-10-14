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
    from primary_news import fetch_primary
    direct_articles, primary_health = fetch_primary(asset_type)
    articles.extend(direct_articles)
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
                  'primary_sources': primary_health,
                  'scope': 'Broad news search plus direct announcement feeds; not an exhaustive newswire'}


def stock_candidate_priority(quote):
    """Cheap discovery score, never a substitute for completed intraday bars.

    Missing average volume/acceleration is unknown, not evidence of a surge.
    Daily change is only a modest early-move signal and an extension penalty.
    """
    price = finite(quote.get('regularMarketPrice'))
    volume = finite(quote.get('regularMarketVolume'))
    average = finite(quote.get('averageDailyVolume3Month') or quote.get('averageDailyVolume10Day'))
    change = finite(quote.get('regularMarketChangePercent'))
    relative = volume / average if average > 0 else None
    notional = price * volume
    eligible = notional >= 500_000 and (abs(change) >= 1 or (relative is not None and relative >= .5))
    liquidity = min(math.log10(max(notional / 500_000, 1)) * 3, 9)
    volume_score = min(relative * 4, 12) if relative is not None else 0
    early_move = min(max(change, 0), 3)
    extension_penalty = min(max(change - 5, 0) * 1.5, 30)
    downside_penalty = min(max(-change, 0) * 2, 20)
    return eligible, liquidity + volume_score + early_move - extension_penalty - downside_penalty


def select_stock_candidates(quotes, universe, catalysts, regular, now, limit=60,
                            shortlist_limit=150, catalyst_slots=20, broker_check=None, rotation_state=None):
    """Broad screen -> broker-aware ranked shortlist -> bounded chart checks.

    Reserve a third of deep checks for fresh, non-adverse news; unused slots
    flow back to the ranking. Catalogue exclusions never consume chart slots.
    Publication freshness is research evidence, not verified event timing.
    """
    records = {q['symbol']: q for q in quotes if q.get('symbol') in universe}
    ranked, excluded = [], []
    for symbol in sorted(set(records) | (set(catalysts) & set(universe))):
        q, catalyst = records.get(symbol, {}), catalysts.get(symbol)
        fresh_news = False
        if catalyst:
            try:
                published = datetime.fromisoformat(catalyst.get('published_at', '').replace('Z', '+00:00'))
                fresh_news = 0 <= (now - published).total_seconds() <= 86400
            except (ValueError, TypeError):
                pass
        stamp = finite(q.get('regularMarketTime'))
        quote_usable = q.get('snapshot_time_unverified') or 0 <= now.timestamp() - stamp <= 900
        eligible, score = stock_candidate_priority(q)
        prior = (rotation_state or {}).get('entries', {}).get('stock:' + symbol, {}).get('snapshot')
        acceleration = None
        if prior and stamp and 0 < stamp-prior.get('timestamp', stamp) <= 1800 and prior.get('price', 0) > 0:
            acceleration = (finite(q.get('regularMarketPrice')) / prior['price'] - 1) * 100
            if acceleration > 0:
                eligible = eligible or finite(q.get('regularMarketPrice')) * finite(q.get('regularMarketVolume')) >= 5_000_000
                score += min(acceleration * 3, 9)
        if not fresh_news and not (regular and quote_usable and eligible):
            continue
        broker = broker_check({'ticker': symbol, 'name': universe[symbol], 'asset_type': 'stock'}) if broker_check else {'status': 'UNVERIFIED'}
        adverse = bool(catalyst and catalyst.get('adverse'))
        candidate = {'ticker': symbol, 'name': universe[symbol], 'quote': q,
                     'catalyst': catalyst, 'broker_availability': broker,
                     'discovery_route': 'catalyst_first' if fresh_news else 'market_snapshot',
                     'selection_evidence': {'fresh_publication': fresh_news, 'adverse': adverse,
                         'broker_status': broker['status'], 'snapshot_score': round(score, 3),
                         'relative_volume_available': finite(q.get('averageDailyVolume3Month') or q.get('averageDailyVolume10Day')) > 0,
                         'snapshot_change_pct': acceleration,
                         'acceleration_status': 'OBSERVED_BETWEEN_SNAPSHOTS' if acceleration is not None else 'UNAVAILABLE_FROM_SINGLE_SNAPSHOT'}}
        if broker['status'] in {'NOT_LISTED', 'RESTRICTED'}:
            excluded.append(symbol)
            continue
        # Adverse events remain research candidates, but rank behind long-side leads.
        priority = (not adverse, fresh_news, broker['status'] in {'LISTED_PUBLIC', 'LISTED_API'}, score)
        ranked.append((priority, candidate))
    ranked.sort(key=lambda row: row[0], reverse=True)
    shortlist = [row[1] for row in ranked[:max(limit, shortlist_limit)]]
    reserved = [x for x in shortlist if x['selection_evidence']['fresh_publication'] and not x['selection_evidence']['adverse']][:min(limit, catalyst_slots)]
    reserved_symbols = {x['ticker'] for x in reserved}
    order = reserved + [row[1] for row in ranked if row[1]['ticker'] not in reserved_symbols]
    from scan_state import rotate_candidates
    selected, rotation = rotate_candidates(order, rotation_state if rotation_state is not None else {'entries':{}}, now, 'stock', limit, min(60, limit//3) if regular else 0)
    # Rotating candidates outside the top-ranked shortlist replace its tail.
    selected_symbols = {x['ticker'] for x in selected}
    shortlist = (selected + [x for x in shortlist if x['ticker'] not in selected_symbols])[:max(limit,shortlist_limit)]
    if rotation_state is not None:
        for _, candidate in ranked:
            q = candidate['quote']; stamp = finite(q.get('regularMarketTime'))
            if stamp and 0 <= now.timestamp()-stamp <= 900:
                rotation_state['entries'].setdefault('stock:'+candidate['ticker'], {})['snapshot'] = {'timestamp': stamp, 'price': finite(q.get('regularMarketPrice'))}
    return selected, {'candidate_manifest': [{'symbol': x['ticker'], 'state': 'SELECTED' if x['ticker'] in selected_symbols else 'DEFERRED', 'discovery_route': x['discovery_route'], 'rank': i+1, 'snapshot_price': finite(x['quote'].get('regularMarketPrice'), None), 'snapshot_at': finite(x['quote'].get('regularMarketTime'), None)} for i, (_, x) in enumerate(ranked)],
                      'listed_snapshot_records': len(records), 'rotation': rotation,
                      'discovered_candidates': len(ranked) + len(excluded),
                      'broker_excluded_candidates': len(excluded),
                      'shortlisted_candidates': len(shortlist), 'shortlist_limit': max(limit, shortlist_limit),
                      'deep_check_limit': limit, 'catalyst_reserved_slots': min(limit, catalyst_slots),
                      'catalyst_selected': sum(x['selection_evidence']['fresh_publication'] for x in selected),
                      'selected_candidates': len(selected), 'deferred_candidates': max(0, len(ranked)-len(selected)),
                      'outside_shortlist': max(0, len(ranked)-len(shortlist)),
                      'selection_policy': 'fresh non-adverse catalyst, broker listing, volume/liquidity, early move; extension/adverse penalties',
                      'acceleration_status': 'Timestamped snapshot changes used when available; completed charts validate momentum.',
                      'unquoted_listings': len(set(universe)-set(records))}

