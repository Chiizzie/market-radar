"""Fresh intraday stock data and EUR crypto snapshots with history-based levels."""
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd
import pandas_market_calendars as mcal
import requests
import yfinance as yf

logger = logging.getLogger(__name__)
MAX_CANDIDATES = 12


def stock_session(now=None):
    now = now or datetime.now(timezone.utc)
    schedule = mcal.get_calendar('NYSE').schedule(start_date=now.date(), end_date=now.date())
    if schedule.empty:
        return 'CLOSED'
    return 'REGULAR' if schedule.iloc[0]['market_open'] <= now < schedule.iloc[0]['market_close'] else 'CLOSED'


def structure(frame):
    """Preserve observed fields even when history cannot supply two targets."""
    result = {'history_samples': len(frame), 'structure_status': 'INCOMPLETE', 'structure_issues': []}
    if len(frame) < 30:
        result['structure_issues'].append(f'Only {len(frame)} history samples; at least 30 required.')
        return result
    recent, older = frame.iloc[-8:], frame.iloc[:-8]
    entry = float(recent['High'].iloc[:-1].max())
    price = float(frame['Close'].iloc[-1])
    result.update(entry=entry, support=float(recent['Low'].min()),
                  momentum_pct=(price / float(frame['Close'].iloc[-4]) - 1) * 100)
    volume = recent['Volume'].clip(lower=0)
    reference = float((((recent['High'] + recent['Low'] + recent['Close']) / 3) * volume).sum() / volume.sum()) if volume.sum() > 0 else 0
    if reference > 0:
        result.update(reference_price=reference, extension_pct=(price / reference - 1) * 100)
    else:
        result['structure_issues'].append('Recent volume does not support an average-price calculation.')
    resistance = sorted(set(float(x) for x in older['High'] if float(x) > max(entry, price) * 1.001))
    if resistance:
        result['resistance_1'] = resistance[0]
        tp2 = next((x for x in resistance[1:] if x > resistance[0] * 1.003), None)
        if tp2 is not None:
            result['resistance_2'] = tp2
        else:
            result['structure_issues'].append('No second distinct historical resistance target in the lookback.')
    else:
        result['structure_issues'].append('No historical resistance above the trigger/current history price in the lookback.')
    if not result['structure_issues']:
        result['structure_status'] = 'COMPLETE'
    return result


class MarketScanner:
    def __init__(self, now=None):
        self.now = now or datetime.now(timezone.utc)
        self.health = {'stocks': {}, 'crypto': {}, 'errors': []}
        self.tracking_prices = []

    def scan_stocks(self):
        from discovery import listed_universe, broad_quotes, discover_catalysts, select_stock_candidates, finite
        from concurrent.futures import ThreadPoolExecutor
        session = stock_session(self.now)
        universe, directory_errors = listed_universe()
        # Discovery keeps running while the exchange is closed; stale prices cannot be entries.
        with ThreadPoolExecutor(max_workers=2) as pool:
            quote_future = pool.submit(broad_quotes)
            news_future = pool.submit(discover_catalysts, universe, 'stock', self.now)
            quotes, quote_health = quote_future.result()
            catalysts, news_health = news_future.result()
        self.tracking_prices.extend({'asset_type': 'stock', 'ticker': q.get('symbol'), 'price': q.get('regularMarketPrice'), 'currency': 'USD',
                                     'observed_at': datetime.fromtimestamp(q['regularMarketTime'], timezone.utc).isoformat()}
                                    for q in quotes if q.get('regularMarketTime') and q.get('symbol') in universe)
        selected, selection_health = select_stock_candidates(quotes, universe, catalysts, session == 'REGULAR', self.now)
        complete = not directory_errors and quote_health['pagination_complete'] and not quote_health['errors'] and not news_health['failed_queries'] and not selection_health['unquoted_listings'] and not news_health.get('primary_sources', {}).get('failures')
        self.health['stocks'] = {'session': session, 'status': 'OK' if complete else 'PARTIAL',
                                 'universe': 'US exchange-listed common/ordinary shares and ADRs; excludes ETFs, OTC and non-US listings',
                                 'listed_assets': len(universe), 'directory_errors': directory_errors,
                                 'snapshots': quote_health, 'catalyst_discovery': news_health, **selection_health}
        if not universe:
            self.health['stocks']['status'] = 'DATA_UNAVAILABLE'
            return []
        eur_per_usd = None
        if session == 'REGULAR' and selected:
            try:
                fx = yf.Ticker('USDEUR=X').history(period='1d', interval='5m')
                fx_age = (self.now - fx.index[-1].to_pydatetime()).total_seconds()
                eur_per_usd = float(fx['Close'].iloc[-1]) if 0 <= fx_age <= 1800 else None
            except Exception:
                pass
        def analyse(candidate):
            symbol, q = candidate['ticker'], candidate['quote']
            stamp = finite(q.get('regularMarketTime'))
            item = {'ticker': symbol, 'name': candidate['name'], 'asset_type': 'stock',
                    'price': finite(q.get('regularMarketPrice'), None), 'currency': 'USD', 'eur_per_unit': eur_per_usd,
                    'change': finite(q.get('regularMarketChangePercent')), 'volume': finite(q.get('regularMarketVolume')),
                    'dollar_volume': finite(q.get('regularMarketPrice')) * finite(q.get('regularMarketVolume')),
                    'session': session, 'observed_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else None,
                    'source': q.get('snapshot_source', 'Yahoo broad exchange snapshot'), 'discovery_route': candidate['discovery_route'],
                    'history_status': 'NOT_REQUESTED', 'data_issue': 'Regular-session chart checks wait for the market to open.'}
            if candidate['catalyst']:
                item['catalyst'] = candidate['catalyst']
            if getattr(self, 'brokers', None):
                item['broker_availability'] = self.brokers.check(item)
                if item['broker_availability']['status'] in {'NOT_LISTED', 'RESTRICTED'}:
                    item['data_issue'] = 'Chart skipped: no supported broker listing for this instrument.'
                    return item
            if session != 'REGULAR':
                return item
            try:
                history = yf.Ticker(symbol).history(period='5d', interval='5m', prepost=False, auto_adjust=True)
                if history.empty:
                    raise ValueError('empty history')
                history = history.loc[history.index + pd.Timedelta(minutes=5) <= self.now].dropna()
                if history.empty:
                    raise ValueError('no completed bars')
                stamp = history.index[-1].to_pydatetime() + timedelta(minutes=5)
                if not 0 <= (self.now - stamp).total_seconds() <= 900:
                    item.update(history_status='STALE', data_issue='No fresh completed regular-session bar.')
                    return item
                day = history.index[-1].date()
                today = history.loc[[t.date() == day for t in history.index]]
                prior = history.loc[[t.date() < day for t in history.index]]
                last = today.iloc[-3:]
                slots = {t.time() for t in last.index}
                matching = prior.loc[[t.time() in slots for t in prior.index]]
                baseline = matching.groupby(matching.index.date)['Volume'].sum().mean()
                item.update(price=float(history['Close'].iloc[-1]), observed_at=stamp.isoformat(),
                            volume=int(today['Volume'].sum()), dollar_volume=float((today['Close']*today['Volume']).sum()),
                            volume_spike=float(last['Volume'].sum()/baseline) if baseline > 0 else 0,
                            history_status='OK', source='yfinance completed 5-minute bars', **structure(history))
                item.pop('data_issue', None)
            except Exception as exc:
                item.update(history_status='FAILED', data_issue='Stock chart failed: ' + type(exc).__name__)
            return item
        with ThreadPoolExecutor(max_workers=3) as pool:
            candidates = list(pool.map(analyse, selected))
        self.health['stocks']['chart_failures'] = sum(x['history_status'] in {'FAILED', 'STALE'} for x in candidates)
        if self.health['stocks']['chart_failures'] or selection_health['deferred_candidates']:
            self.health['stocks']['status'] = 'PARTIAL'
        return candidates

    def scan_crypto(self):
        try:
            response = requests.get('https://api.coingecko.com/api/v3/coins/markets', params={
                'vs_currency': 'eur', 'order': 'market_cap_desc', 'per_page': 250,
                'sparkline': 'false', 'price_change_percentage': '1h,24h,7d'}, timeout=20)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError('invalid response')
        except Exception as exc:
            self.health['crypto']['status'] = 'DATA_UNAVAILABLE'
            self.health['errors'].append('Crypto markets retrieval failed: ' + type(exc).__name__)
            return []
        from discovery import discover_catalysts, finite
        self.tracking_prices.extend({'asset_type': 'crypto', 'coin_id': x.get('id'), 'symbol': x.get('symbol', '').upper(),
                                     'price': x.get('current_price'), 'currency': 'EUR', 'observed_at': x.get('last_updated')} for x in data)
        liquid = [x for x in data if finite(x.get('total_volume')) >= 10_000_000]
        identities = {x['symbol'].upper(): x['name'] for x in liquid}
        # Ambiguous duplicate symbols are not eligible for name-based news matching.
        duplicates = {s for s in identities if sum(x['symbol'].upper() == s for x in liquid) > 1}
        for symbol in duplicates:
            identities.pop(symbol)
        news_hits, news_health = discover_catalysts(identities, 'crypto', self.now)
        movers = []
        for coin in liquid:
            try:
                volume = float(coin.get('total_volume') or 0)
                change = float(coin.get('price_change_percentage_24h') or 0)
                hour_change = finite(coin.get('price_change_percentage_1h_in_currency'))
                catalyst = news_hits.get(coin['symbol'].upper())
                acceleration = hour_change - change / 24
                if not catalyst and not (hour_change >= .3 and acceleration >= .15):
                    continue
                price = float(coin['current_price'])
                item = {'symbol': coin['symbol'].upper(), 'name': coin['name'], 'coin_id': coin['id'],
                        'asset_type': 'crypto', 'price': price, 'currency': 'EUR', 'eur_per_unit': 1,
                        'change_24h': change, 'change_1h': hour_change, 'acceleration': acceleration, 'discovery_route': 'catalyst_first' if catalyst else 'recent_momentum', 'change_7d': coin.get('price_change_percentage_7d_in_currency'),
                        'volume': volume, 'dollar_volume': volume, 'market_cap': coin.get('market_cap'),
                        'session': '24/7', 'observed_at': coin.get('last_updated'), 'source': 'coingecko EUR',
                        'history_status': 'NOT_REQUESTED', 'data_issue': 'Chart not requested: candidate limit or broker restriction.'}
                if catalyst:
                    item['catalyst'] = catalyst
                movers.append(item)
            except (ValueError, KeyError, TypeError):
                continue
        if getattr(self, 'brokers', None):
            for item in movers:
                item['broker_availability'] = self.brokers.check(item)
        movers.sort(key=lambda x: (x.get('broker_availability', {}).get('status') in {'LISTED_PUBLIC', 'LISTED_API'}, bool(x.get('catalyst')), x['acceleration'], x['volume']), reverse=True)
        chart_candidates = [x for x in movers[:MAX_CANDIDATES] if x.get('broker_availability', {}).get('status') not in {'NOT_LISTED', 'RESTRICTED'} and x['symbol'] not in duplicates]
        for item in movers:
            if item.get('broker_availability', {}).get('status') in {'NOT_LISTED', 'RESTRICTED'}:
                item['data_issue'] = 'Chart skipped: no supported broker listing for this instrument.'
        from crypto_candles import CandleProvider
        provider = CandleProvider(self.now).load() if chart_candidates else None
        chart_failures = 0
        history_successes = 0
        for item in chart_candidates:
            try:
                frame, evidence = provider.candles(item)
                item.update(structure(frame))
                item.update(history_status='OK', price=float(frame.Close.iloc[-1]),
                            observed_at=(frame.index[-1] + pd.Timedelta(minutes=5)).isoformat(),
                            structure_observed_at=(frame.index[-1] + pd.Timedelta(minutes=5)).isoformat(),
                            structure_source=evidence['provider'] + ' completed 5-minute EUR OHLCV',
                            candle_evidence=evidence, source=evidence['provider'] + ' EUR candles')
                item.pop('data_issue', None)
                history_successes += 1
            except Exception as exc:
                chart_failures += 1
                item.update(history_status='FAILED', data_issue='Candle retrieval failed: ' + str(exc))
        self.health['crypto']['candle_providers'] = {'pairs': {k: len(v) for k,v in provider.pairs.items()}, 'errors': provider.errors} if provider else {}
        returned = movers[:MAX_CANDIDATES]
        not_requested = sum(x['history_status'] == 'NOT_REQUESTED' for x in returned)
        stale = sum(x['history_status'] == 'STALE' for x in returned)
        self.health['crypto'].update(status=('DATA_UNAVAILABLE' if not data else 'PARTIAL' if chart_failures or not_requested or stale else 'OK'),
                                     screened_assets=len(data), price_records=len(returned), history_requested=len(chart_candidates),
                                     history_received=history_successes, chart_failures=chart_failures, history_stale=stale, history_not_requested=not_requested,
                                     catalyst_discovery=news_health, universe='Top 250 by market cap; up to 12 candidates; every broker-eligible selected candidate gets an EUR candle attempt')
        return movers[:MAX_CANDIDATES]
