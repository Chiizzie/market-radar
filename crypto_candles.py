"""Public exchange OHLCV, EUR pairs only; no synthetic candles or invented FX."""
from datetime import timedelta
import time
import math
import pandas as pd
import requests


def validate_candles(rows, columns, now):
    frame = pd.DataFrame(rows, columns=columns)
    for col in columns:
        frame[col] = pd.to_numeric(frame[col], errors='coerce')
    frame.index = pd.to_datetime(frame.pop('timestamp'), unit='s', utc=True)
    frame = frame.sort_index().loc[lambda f: ~f.index.duplicated(keep='last')]
    frame = frame.loc[frame.index + pd.Timedelta(minutes=5) <= now]
    frame = frame.dropna()
    if len(frame) < 30:
        raise ValueError('Fewer than 30 completed candles')
    if not 0 <= (now - frame.index[-1].to_pydatetime() - timedelta(minutes=5)).total_seconds() <= 900:
        raise ValueError('Stale completed candles')
    if not all(math.isfinite(x) for row in frame.to_numpy() for x in row):
        raise ValueError('Non-finite candle values')
    if ((frame[['Open', 'High', 'Low', 'Close']] <= 0).any().any() or (frame.Volume < 0).any()
        or (frame.High < frame[['Open', 'Close', 'Low']].max(axis=1)).any()
        or (frame.Low > frame[['Open', 'Close', 'High']].min(axis=1)).any()):
        raise ValueError('Invalid OHLCV values')
    recent = frame.tail(30)
    if recent.index.to_series().diff().dropna().max() > pd.Timedelta(minutes=10):
        raise ValueError('Gaps in recent candles')
    if recent.Volume.sum() <= 0:
        raise ValueError('No recent exchange volume')
    return frame[['Open', 'High', 'Low', 'Close', 'Volume']]


class CandleProvider:
    def __init__(self, now):
        self.now = now
        self.pairs = {'Kraken': {}, 'Coinbase': {}}
        self.errors = []
        self.blocked = set()
        self.last_request = {}

    def get(self, venue, url, params=None):
        # Public requests are paced; rate-limit responses disable that venue for this run.
        wait = 1.05 - (time.monotonic() - self.last_request.get(venue, 0))
        if wait > 0:
            time.sleep(wait)
        self.last_request[venue] = time.monotonic()
        response = requests.get(url, params=params, timeout=(5, 12))
        if response.status_code == 429:
            self.blocked.add(venue)
        response.raise_for_status()
        return response.json()

    def load(self):
        for venue in self.pairs:
            try:
                if venue == 'Kraken':
                    data = self.get(venue, 'https://api.kraken.com/0/public/AssetPairs')
                    if data.get('error'):
                        raise ValueError('Kraken pair error')
                    for key, row in data['result'].items():
                        pair = row.get('wsname', '').split('/')
                        if len(pair) == 2 and pair[1] == 'EUR' and row.get('status', 'online') == 'online' and '.' not in key:
                            self.pairs[venue][{'XBT': 'BTC', 'XDG': 'DOGE'}.get(pair[0], pair[0])] = key
                else:
                    for row in self.get(venue, 'https://api.exchange.coinbase.com/products'):
                        if row.get('quote_currency') == 'EUR' and row.get('status') == 'online' and not row.get('trading_disabled'):
                            self.pairs[venue][row['base_currency']] = row['id']
                if not self.pairs[venue]:
                    raise ValueError('Empty EUR pair catalogue')
            except Exception as exc:
                self.errors.append(venue + ' catalogue: ' + type(exc).__name__)
        return self

    def candles(self, asset):
        errors = []
        for venue, pairs in self.pairs.items():
            pair = pairs.get(asset['symbol'])
            if not pair or venue in self.blocked:
                errors.append(venue + ': no available EUR pair or venue rate-limited')
                continue
            try:
                if venue == 'Kraken':
                    d = self.get(venue, 'https://api.kraken.com/0/public/OHLC', {'pair': pair, 'interval': 5})
                    if d.get('error'):
                        raise ValueError('Kraken candle error')
                    rows = next(v for k, v in d['result'].items() if k != 'last')
                    rows = [[r[0], r[1], r[2], r[3], r[4], r[6]] for r in rows]
                    columns = ['timestamp', 'Open', 'High', 'Low', 'Close', 'Volume']
                else:
                    rows = self.get(venue, f'https://api.exchange.coinbase.com/products/{pair}/candles', {'granularity': 300})
                    columns = ['timestamp', 'Low', 'High', 'Open', 'Close', 'Volume']
                frame = validate_candles(rows, columns, self.now)
                if abs(float(frame.Close.iloc[-1]) / asset['price'] - 1) > .05:
                    raise ValueError('Exchange price differs over 5% from EUR discovery quote')
                return frame, {'provider': venue, 'pair': pair, 'currency': 'EUR', 'interval_minutes': 5,
                               'failed_attempts': errors, 'fallback_used': venue != 'Kraken'}
            except Exception as exc:
                errors.append(venue + ': ' + type(exc).__name__ + (' (' + str(exc) + ')' if isinstance(exc, ValueError) else ''))
        raise ValueError('; '.join(errors))
