"""Fresh intraday stock data and EUR crypto snapshots with history-based levels."""
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd
import pandas_market_calendars as mcal
import requests
import yfinance as yf

logger = logging.getLogger(__name__)
# Explicit liquid universe; avoid silently scanning only the first 100 alphabetic names.
STOCKS = ['AAPL','MSFT','GOOGL','AMZN','NVDA','TSLA','META','AMD','AVGO','MRVL','ARM','SMCI',
          'MSTR','COIN','PLTR','MP','MU','QCOM','INTC','ORCL','NFLX','CRM','ADBE','PYPL',
          'JPM','V','MA','WMT','COST','LLY','UNH','XOM','CVX','CAT','GE','BA','RDDT','HOOD',
          'SOFI','RKLB','ASTS','IONQ','RGTI','QBTS','HIMS','CRWD','NET','SNOW']
MAX_CANDIDATES = 12


def stock_session(now=None):
    now = now or datetime.now(timezone.utc)
    schedule = mcal.get_calendar('NYSE').schedule(start_date=now.date(), end_date=now.date())
    if schedule.empty:
        return 'CLOSED'
    return 'REGULAR' if schedule.iloc[0]['market_open'] <= now < schedule.iloc[0]['market_close'] else 'CLOSED'


def structure(frame):
    """Use completed bars: trigger from prior 8, stop from recent lows, targets from older highs."""
    if len(frame) < 30:
        return {}
    recent, older = frame.iloc[-8:], frame.iloc[:-8]
    entry = float(recent['High'].iloc[:-1].max())
    support = float(recent['Low'].min())
    price = float(frame['Close'].iloc[-1])
    resistance = sorted(set(float(x) for x in older['High'] if float(x) > max(entry, price) * 1.001))
    if len(resistance) < 2:
        return {}
    tp1 = resistance[0]
    # Require a meaningfully separate second resistance.
    tp2 = next((x for x in resistance[1:] if x > tp1 * 1.003), None)
    if tp2 is None:
        return {}
    volume = recent['Volume'].clip(lower=0)
    vwap = float((((recent['High'] + recent['Low'] + recent['Close']) / 3) * volume).sum() / volume.sum()) if volume.sum() > 0 else 0
    if not vwap:
        return {}
    return {'entry': entry, 'support': support, 'resistance_1': tp1, 'resistance_2': tp2,
            'momentum_pct': (price / float(frame['Close'].iloc[-4]) - 1) * 100,
            'extension_pct': (price / vwap - 1) * 100, 'vwap': vwap}


class MarketScanner:
    def __init__(self, now=None):
        self.now = now or datetime.now(timezone.utc)
        self.sp500_tickers = STOCKS
        self.health = {'stocks': {}, 'crypto': {}, 'errors': []}

    def scan_stocks(self):
        session = stock_session(self.now)
        self.health['stocks']['session'] = session
        if session != 'REGULAR':
            self.health['stocks']['status'] = 'SKIPPED_MARKET_CLOSED'
            return []
        movers = []
        try:
            fx = yf.Ticker('USDEUR=X').history(period='1d', interval='5m')
            fx_age = (self.now - fx.index[-1].to_pydatetime()).total_seconds()
            eur_per_usd = float(fx['Close'].iloc[-1]) if 0 <= fx_age <= 1800 else None
        except Exception:
            eur_per_usd = None
        failed, observed = 0, 0
        for symbol in self.sp500_tickers:
            try:
                history = yf.Ticker(symbol).history(period='5d', interval='5m', prepost=False, auto_adjust=True)
                if history.empty:
                    raise ValueError('empty data')
                history = history.loc[history.index + pd.Timedelta(minutes=5) <= self.now].dropna()
                if len(history) < 30:
                    continue
                stamp = history.index[-1].to_pydatetime() + timedelta(minutes=5)
                if not 0 <= (self.now - stamp).total_seconds() <= 900:
                    continue
                observed += 1
                price = float(history['Close'].iloc[-1])
                current_day = history.index[-1].date()
                today = history.loc[[t.date() == current_day for t in history.index]]
                prior = history.loc[[t.date() < current_day for t in history.index]]
                if prior.empty or len(today) < 4:
                    continue
                change = (price / float(prior['Close'].iloc[-1]) - 1) * 100
                # Compare the latest 15-minute volume with that exact slot on prior sessions.
                last = today.iloc[-3:]
                slots = {t.time() for t in last.index}
                matching = prior.loc[[t.time() in slots for t in prior.index]]
                baseline = matching.groupby(matching.index.date)['Volume'].sum().mean()
                relative = float(last['Volume'].sum() / baseline) if baseline > 0 else 0
                if change < 2 and relative < 1.5:
                    continue
                daily_vol = int(today['Volume'].sum())
                info = yf.Ticker(symbol).info
                movers.append({'ticker': symbol, 'name': info.get('shortName', symbol), 'asset_type': 'stock',
                               'price': price, 'currency': 'USD', 'eur_per_unit': eur_per_usd,
                               'change': change, 'volume': daily_vol, 'dollar_volume': float((today['Close'] * today['Volume']).sum()),
                               'volume_spike': relative, 'session': session, 'observed_at': stamp.isoformat(),
                               'source': 'yfinance completed 5-minute bars', **structure(history)})
            except Exception as exc:
                failed += 1
                logger.warning('Stock data failed for %s (%s)', symbol, type(exc).__name__)
        self.health['stocks'].update(status='OK' if observed else 'DATA_UNAVAILABLE', fresh_assets=observed, failed_assets=failed)
        movers.sort(key=lambda x: x['volume_spike'], reverse=True)
        return movers[:MAX_CANDIDATES]

    def scan_crypto(self):
        try:
            response = requests.get('https://api.coingecko.com/api/v3/coins/markets', params={
                'vs_currency': 'eur', 'order': 'market_cap_desc', 'per_page': 100,
                'sparkline': 'false', 'price_change_percentage': '24h,7d'}, timeout=20)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError('invalid response')
        except Exception as exc:
            self.health['crypto']['status'] = 'DATA_UNAVAILABLE'
            self.health['errors'].append('Crypto markets retrieval failed: ' + type(exc).__name__)
            return []
        movers = []
        for coin in data:
            try:
                volume = float(coin.get('total_volume') or 0)
                change = float(coin.get('price_change_percentage_24h') or 0)
                if volume < 10_000_000 or change < 2:
                    continue
                price = float(coin['current_price'])
                item = {'symbol': coin['symbol'].upper(), 'name': coin['name'], 'coin_id': coin['id'],
                        'asset_type': 'crypto', 'price': price, 'currency': 'EUR', 'eur_per_unit': 1,
                        'change_24h': change, 'change_7d': coin.get('price_change_percentage_7d_in_currency'),
                        'volume': volume, 'dollar_volume': volume, 'market_cap': coin.get('market_cap'),
                        'session': '24/7', 'observed_at': coin.get('last_updated'), 'source': 'coingecko EUR'}
                movers.append(item)
            except (ValueError, KeyError, TypeError):
                continue
        movers.sort(key=lambda x: x['volume'], reverse=True)
        # Keep free API request budget bounded. Missing history must never become actionable.
        chart_failures = 0
        for item in movers[:5]:
            try:
                response = requests.get(f"https://api.coingecko.com/api/v3/coins/{item['coin_id']}/market_chart",
                                        params={'vs_currency': 'eur', 'days': 2}, timeout=15)
                response.raise_for_status()
                prices = response.json()['prices']
                frame = pd.DataFrame(prices, columns=['timestamp', 'Close'])
                frame.index = pd.to_datetime(frame['timestamp'], unit='ms', utc=True)
                # Hourly snapshots are not OHLC candles. Use observed prices only and label it.
                frame = frame.loc[frame.index <= self.now - timedelta(minutes=5)]
                frame['High'] = frame['Low'] = frame['Close']
                frame['Volume'] = 1
                item.update(structure(frame))
                item['structure_source'] = 'CoinGecko historical price samples; broker chart confirmation required'
                item['structure_observed_at'] = frame.index[-1].isoformat()
                if (self.now - frame.index[-1].to_pydatetime()).total_seconds() > 7200:
                    for key in ['entry','support','resistance_1','resistance_2','momentum_pct','extension_pct']:
                        item.pop(key, None)
            except Exception as exc:
                chart_failures += 1
                item['data_issue'] = 'Historical structure unavailable: ' + type(exc).__name__
        self.health['crypto'].update(status='OK' if data else 'DATA_UNAVAILABLE', chart_failures=chart_failures,
                                     universe='Top 100 by market cap; history enriched for 5 liquid movers')
        return movers[:MAX_CANDIDATES]
