"""Read-only public broker catalogues. Listing evidence is not account permission."""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests
from catalyst_scanner import company_name

T212_URL = 'https://www.trading212.com/trading-instruments/invest'
T212_API_URL = 'https://live.trading212.com/api/v0/equity/metadata/instruments'
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
                if key == 'stock' and (os.environ.get('TRADING212_API_KEY') or os.environ.get('TRADING212_API_SECRET')):
                    url = T212_API_URL
                    api_key, api_secret = os.environ.get('TRADING212_API_KEY'), os.environ.get('TRADING212_API_SECRET')
                    if not api_key or not api_secret:
                        raise ValueError('Both metadata credentials are required')
                    response = requests.get(T212_API_URL, auth=(api_key, api_secret), timeout=(5, 25))
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, list) or not data:
                        raise ValueError('Invalid instrument metadata')
                    rows = [{'shortName': r.get('shortName'), 'fullName': r.get('name'), 'description': r.get('name'),
                             'ticker': r.get('ticker'), 'type': r.get('type'), 'currency': r.get('currencyCode'),
                             'isin': r.get('isin'), 'maxOpenLong': r.get('maxOpenQuantity', 0),
                             'maxTrade': r.get('maxOpenQuantity', 0)} for r in data]
                    return key, rows, {'status': 'OK', 'records': len(rows), 'source': T212_API_URL,
                                       'checked_at': self.now.isoformat(), 'evidence': 'authenticated_metadata'}
                urls = [url, 'https://www.trading212.com/en/trading-instruments/invest'] if key == 'stock' else [url]
                failure = None
                for candidate_url in urls:
                    try:
                        response = requests.get(candidate_url, headers={'User-Agent': 'MarketRadar/2 (public instrument catalogue research)', 'Accept': 'text/html'}, timeout=(5, 25))
                        response.raise_for_status()
                        rows = parser(response.text)
                        url = candidate_url
                        break
                    except (requests.RequestException, ValueError, KeyError) as exc:
                        failure = exc
                else:
                    raise failure
                return key, rows, {'status': 'OK', 'records': len(rows), 'source': url,
                                   'checked_at': self.now.isoformat()}
            except Exception as exc:
                return key, None, {'status': 'UNVERIFIED', 'source': url,
                                   'checked_at': self.now.isoformat(), 'error': type(exc).__name__,
                                   'api_credentials_configured': bool(os.environ.get('TRADING212_API_KEY') and os.environ.get('TRADING212_API_SECRET')),
                                   'http_status': getattr(getattr(exc, 'response', None), 'status_code', None)}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(fetch, 'stock', T212_URL, parse_t212),
                       pool.submit(fetch, 'crypto', RH_URL, parse_robinhood),
                       pool.submit(fetch, 'cfd', T212_CFD_URL, parse_t212_cfd)]
            for future in futures:
                key, rows, health = future.result()
                self.catalogues[key], self.health[key] = rows, health
        return self

    def check_discovery(self, asset):
        invest = self.check(asset)
        if asset['asset_type'] != 'stock' or invest['status'] not in {'NOT_LISTED', 'RESTRICTED'}:
            return invest
        cfd = self.check_short(asset)
        if cfd['status'] not in {'NOT_LISTED', 'RESTRICTED'}:
            return {**cfd, 'reason': 'Retained for bearish research; Invest availability checked separately.'}
        return {**cfd, 'status': 'UNVERIFIED', 'execution_status': cfd['status'],
                'reason': 'Retained for directional bearish research despite broker exclusion; no execution permission implied.'}

    def check(self, asset):
        kind = asset['asset_type']
        rows = self.catalogues.get(kind)
        result = {'broker': 'Trading 212 Invest' if kind == 'stock' else 'Robinhood Europe crypto',
                  'status': 'UNVERIFIED', 'checked_at': self.now.isoformat(),
                  'source': T212_URL if kind == 'stock' else RH_URL,
                  'account_verified': False, 'capabilities': {'long': True, 'short': False, 'product': 'INVEST' if kind == 'stock' else 'SPOT'},
                  'note': 'Public listing only; verify Irish account access, buy restrictions and live spread before entry.'}
        if rows is None:
            result['reason'] = 'Broker catalogue unavailable; availability is unknown.'
            return result
        if self.health.get(kind, {}).get('evidence') == 'authenticated_metadata':
            result['source'] = T212_API_URL
            result['note'] = 'Authenticated instrument catalogue; confirm current buy permissions and live spread before entry.'
        symbol = asset.get('ticker', asset.get('symbol', '')).upper()
        if kind == 'stock':
            matches = [r for r in rows if (r.get('shortName') or '').replace('.', '-') == symbol.replace('.', '-')
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
        return {**result, 'status': 'LISTED_API' if result['source'] == T212_API_URL else 'LISTED_PUBLIC',
                'reason': 'Asset identity matched the official broker instrument catalogue.'}



    def check_short(self, asset):
        import math
        result = {'broker':'Trading 212 CFD' if asset['asset_type']=='stock' else 'Robinhood Europe crypto',
                  'status':'UNVERIFIED', 'source':T212_CFD_URL, 'checked_at':self.now.isoformat(),
                  'account_verified':False, 'capabilities':{'product':'CFD' if asset['asset_type']=='stock' else 'SPOT','short':False},
                  'reason':'CFD short-opening permission is unverified; listing alone is insufficient.'}
        if asset['asset_type'] != 'stock':
            return {**result,'status':'UNSUPPORTED','source':RH_URL,'reason':'Configured Robinhood Europe spot account does not establish short capability.'}
        rows=self.catalogues.get('cfd')
        if rows is None:
            return result
        symbol=asset.get('ticker','').upper().replace('.','-')
        matches=[r for r in rows if (r.get('shortName') or '').upper().replace('.','-')==symbol
                 and r.get('type')=='STOCK' and r.get('currency')=='USD']
        if not matches:
            return {**result,'status':'NOT_LISTED','reason':'No matching USD stock CFD in the official catalogue.'}
        matches=[r for r in matches if identity(asset.get('name','')) in {identity(r.get('fullName','')),identity(r.get('description',''))}]
        if len(matches)!=1:
            return {**result,'reason':'CFD symbol/company identity is ambiguous.'}
        row=matches[0]
        result.update(listing_status='LISTED_PUBLIC',broker_instrument_id=row.get('ticker'),isin=row.get('isin'),
                      leverage=row.get('leverage'),overnight_interest_short=row.get('overnightInterestShort'),
                      note='Underlying stock candles are research evidence; verify executable CFD quote, sell permission, size limits, margin and costs.')
        if row.get('dealerExclusions') or row.get('isInActiveIpoState'):
            return {**result,'reason':'Entity restrictions or active IPO require account-level review.'}
        limit=row.get('maxOpenShort')
        if isinstance(limit,bool) or not isinstance(limit,(int,float)) or not math.isfinite(limit):
            return result
        if limit<=0:
            return {**result,'status':'RESTRICTED','reason':'Catalogue explicitly blocks new short positions.'}
        return {**result,'status':'LISTED_PUBLIC','max_open_short':limit,
                'capabilities':{'product':'CFD','short':True},'reason':'Official CFD identity and positive short-opening limit verified; live account review still required.'}


T212_CFD_URL = 'https://www.trading212.com/trading-instruments/cfd'


def parse_t212_cfd(html):
    parser = CatalogueHTML(); parser.feed(html)
    props = json.loads(parser.next_data)['props']['pageProps']
    rows = props['instruments']
    if props.get('tradingType') != 'cfd' or not isinstance(rows, list) or len(rows) < 1000:
        raise ValueError('Incomplete or wrong account-type CFD catalogue')
    return rows

