"""Read-only public broker catalogues. Listing evidence is not account permission."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests
from catalyst_scanner import company_name

T212_URL = 'https://www.trading212.com/trading-instruments/invest'
RH_URL = 'https://robinhood.com/eu/en/support/articles/about-robinhood-crypto/'


class CatalogueHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.next_data = ''
        self.in_next = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('id') == '__NEXT_DATA__':
            self.in_next = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_next = False

    def handle_data(self, data):
        if self.in_next:
            self.next_data += data
        else:
            self.text.append(data)


def parse_t212(html):
    parser = CatalogueHTML(); parser.feed(html)
    props = json.loads(parser.next_data)['props']['pageProps']
    rows = props['instruments']
    if props.get('tradingType') != 'invest' or len(rows) < 1000:
        raise ValueError('Incomplete or wrong account-type catalogue')
    return rows


def parse_robinhood(html):
    parser = CatalogueHTML(); parser.feed(html)
    # Use the first HTML list following the exact introduction, not the entire page.
    raw_start = html.index('You can buy, sell, and hold the following crypto:')
    list_start = html.index('<ul', raw_start)
    list_end = html.index('</ul>', list_start)
    listing = CatalogueHTML(); listing.feed(html[list_start:list_end])
    rows = dict((symbol, name.strip()) for name, symbol in
                re.findall(r'([^\n]+?)\s*\(([A-Z0-9]+)\)', '\n'.join(listing.text)))
    if len(rows) < 20 or not {'BTC', 'ETH', 'SOL'} <= rows.keys():
        raise ValueError('Incomplete Robinhood Europe trading list')
    return rows


def identity(name):
    return re.sub(r'[^a-z0-9]', '', company_name(name).lower().replace('(the)', ''))


class BrokerAvailability:
    def __init__(self, now=None):
        self.now = now or datetime.now(timezone.utc)
        self.catalogues = {}
        self.health = {}

    def load(self):
        def fetch(key, url, parser):
            try:
                response = requests.get(url, timeout=(5, 25))
                response.raise_for_status()
                rows = parser(response.text)
                return key, rows, {'status': 'OK', 'records': len(rows), 'source': url,
                                   'checked_at': self.now.isoformat()}
            except Exception as exc:
                return key, None, {'status': 'UNVERIFIED', 'source': url,
                                   'checked_at': self.now.isoformat(), 'error': type(exc).__name__}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(fetch, 'stock', T212_URL, parse_t212),
                       pool.submit(fetch, 'crypto', RH_URL, parse_robinhood)]
            for future in futures:
                key, rows, health = future.result()
                self.catalogues[key], self.health[key] = rows, health
        return self

    def check(self, asset):
        kind = asset['asset_type']
        rows = self.catalogues.get(kind)
        result = {'broker': 'Trading 212 Invest' if kind == 'stock' else 'Robinhood Europe crypto',
                  'status': 'UNVERIFIED', 'checked_at': self.now.isoformat(),
                  'source': T212_URL if kind == 'stock' else RH_URL,
                  'account_verified': False,
                  'note': 'Public listing only; verify Irish account access, buy restrictions and live spread before entry.'}
        if rows is None:
            result['reason'] = 'Broker catalogue unavailable; availability is unknown.'
            return result
        symbol = asset.get('ticker', asset.get('symbol', '')).upper()
        if kind == 'stock':
            matches = [r for r in rows if r.get('shortName', '').replace('.', '-') == symbol.replace('.', '-')
                       and r.get('type') == 'STOCK' and r.get('currency') == 'USD']
            if not matches:
                return {**result, 'status': 'NOT_LISTED', 'reason': 'No matching USD stock in the public Invest catalogue.'}
            matches = [r for r in matches if identity(asset.get('name', '')) in
                       {identity(r.get('fullName', '')), identity(r.get('description', ''))}]
            if len(matches) != 1:
                return {**result, 'reason': 'Ticker/company identity is ambiguous; manual verification required.'}
            row = matches[0]
            result.update(broker_instrument_id=row.get('ticker'), isin=row.get('isin'),
                          dealer_exclusions=row.get('dealerExclusions', []))
            if row.get('dealerExclusions'):
                return {**result, 'reason': 'Broker entity restrictions exist; Irish account eligibility is unverified.'}
            if row.get('maxOpenLong', 0) <= 0 or row.get('maxTrade', 0) <= 0:
                return {**result, 'status': 'RESTRICTED', 'reason': 'Public catalogue does not permit opening a long position.'}
        else:
            if symbol not in rows:
                return {**result, 'status': 'NOT_LISTED', 'reason': 'Not listed on the official Robinhood Europe spot-crypto support page.'}
            if identity(rows[symbol]) != identity(asset.get('name', '')):
                return {**result, 'reason': 'Crypto symbol matches but name differs; identity needs verification.'}
        return {**result, 'status': 'LISTED_PUBLIC', 'reason': 'Asset identity matched the official public broker catalogue.'}
