"""Deterministic long-side patterns on completed 5-minute OHLCV.

Measured moves are projections from observed ranges, never claimed resistance
or calibrated probabilities. Qualification and live broker review are separate.
"""
import math
import pandas as pd
from swing_zones import resistance_zones


def analyse_chart(frame, asset_type, now, resistance_mode="swing_zones"):
    result = {'setups': [], 'issues': [], 'interval': '5m'}
    columns = ['Open', 'High', 'Low', 'Close', 'Volume']
    if len(frame) < 30 or any(c not in frame for c in columns):
        result['issues'] = ['At least 30 complete OHLCV bars are required.']
        return result
    f = frame[columns].copy()
    if not isinstance(f.index, pd.DatetimeIndex) or f.index.tz is None or not f.index.is_monotonic_increasing or f.index.has_duplicates:
        result['issues'] = ['Chart timestamps must be unique, ordered and timezone-aware.']
        return result
    if not all(math.isfinite(float(x)) for x in f.to_numpy().ravel()) or (f[columns[:4]] <= 0).any().any() or (f.Volume < 0).any():
        result['issues'] = ['Invalid OHLCV values.']
        return result
    if ((f.High < f[['Open', 'Close', 'Low']].max(axis=1)) | (f.Low > f[['Open', 'Close', 'High']].min(axis=1))).any():
        result['issues'] = ['Inconsistent OHLC bars.']
        return result
    end = f.index[-1] + pd.Timedelta(minutes=5)
    if not 0 <= (now-end).total_seconds() <= 900:
        result['issues'] = ['Chart is stale or contains an unfinished bar.']
        return result
    if not (f.index[-6:].to_series().diff().dropna() == pd.Timedelta(minutes=5)).all():
        result['issues'] = ['Recent chart bars have gaps.']
        return result
    price = float(f.Close.iloc[-1])
    recent = f.iloc[-8:]
    reference = float((((recent.High+recent.Low+recent.Close)/3)*recent.Volume).sum()/recent.Volume.sum()) if recent.Volume.sum() > 0 else 0
    if reference <= 0:
        result['issues'] = ['Recent volume is unavailable.']
        return result
    baseline = float(f.Volume.iloc[-23:-3].mean())
    result['recent_volume_ratio'] = float(f.Volume.iloc[-3:].mean()/baseline) if baseline > 0 else None
    result['observed_at'] = end.isoformat()

    def add(pattern, entry, support, projection_origin, height, evidence, resistance_frame):
        if not 0 < support < entry or not .004 <= height / projection_origin <= .08:
            return
        # Cap projections at observed overhead levels, never ignore nearer resistance.
        projected = [projection_origin + height*.5, projection_origin + height]
        zones = resistance_zones(resistance_frame, entry) if resistance_mode == 'swing_zones' else []
        overhead = ([max(entry, z['lower']) for z in zones] if resistance_mode == 'swing_zones' else sorted(set(float(x) for x in resistance_frame.High if x > entry*1.001)))
        t1 = min(projected[0], overhead[0]) if overhead else projected[0]
        higher = [x for x in overhead if x > t1*1.003]
        t2 = min(projected[1], higher[0]) if higher else projected[1]
        if not entry < t1 < t2:
            return
        result['setups'].append({'pattern': pattern, 'entry': entry, 'support': support,
            'resistance_1': t1, 'resistance_2': t2,
            'momentum_pct': (price/float(f.Close.iloc[-4])-1)*100,
            'extension_pct': (price/reference-1)*100,
            'target_method': 'Measured range projections capped at confirmed swing zones; projected targets are not confirmed resistance',
            'target_basis': {'origin': projection_origin, 'measured_height': height, 'fractions': [.5, 1.0],
                             'uncapped_targets': projected, 'overhead_levels': overhead[:4], 'resistance_zones': zones[:8], 'resistance_mode': resistance_mode},
            'observed_at': end.isoformat(), 'evidence': evidence})

    def range_retest(base, breakout, pattern, resistance_frame):
        if base.empty or breakout.empty:
            return
        high, low = float(base.High.max()), float(base.Low.min())
        retest = f.iloc[-3:]
        if (breakout.Close > high*1.001).any() and float(retest.Low.min()) >= high*.997 and float(retest.Low.min()) <= high*1.003 and price > high and price > float(f.Open.iloc[-1]):
            entry = max(high, float(retest.High.iloc[:-1].max()))
            add(pattern, entry, float(retest.Low.min()), high, high-low,
                {'range_start': base.index[0].isoformat(), 'range_end': base.index[-1].isoformat(),
                 'range_high': high, 'range_low': low, 'retest_low': float(retest.Low.min()),
                 'breakout_close': float(breakout.Close.max()), 'confirmation': 'Break above range, hold/retest, bullish latest bar'}, resistance_frame)

    # Stock patterns cannot bridge an overnight gap; crypto ranges are continuous.
    local_dates = f.index.tz_convert('America/New_York').date
    session = f.loc[local_dates == local_dates[-1]] if asset_type == 'stock' else f
    if len(session) >= 24 and (session.index[-24:].to_series().diff().dropna() == pd.Timedelta(minutes=5)).all():
        range_retest(session.iloc[-24:-6], session.iloc[-6:-3], 'RANGE_BREAKOUT_RETEST', f.iloc[:-3])
    if asset_type == 'stock' and len(session) >= 10:
        opening = session.iloc[:6]
        times = opening.index.tz_convert('America/New_York')
        if times[0].hour == 9 and times[0].minute == 30 and (times.to_series().diff().dropna() == pd.Timedelta(minutes=5)).all():
            range_retest(opening, session.iloc[6:-3], 'OPENING_RANGE_RETEST', f.iloc[:-3])
    # An established EMA trend, an observed impulse, then a shallow pullback/reclaim.
    if len(session) >= 24:
        ema9 = session.Close.ewm(span=9, adjust=False).mean()
        ema21 = session.Close.ewm(span=21, adjust=False).mean()
        impulse, pullback = session.iloc[-12:-3], session.iloc[-3:]
        peak, trough = float(impulse.High.max()), float(impulse.Low.min())
        support = float(pullback.Low.min())
        if (float(ema9.iloc[-1]) > float(ema21.iloc[-1]) and float(ema21.iloc[-1]) > float(ema21.iloc[-4])
            and float(impulse.Close.iloc[-1]) > float(impulse.Close.iloc[0])
            and trough + (peak-trough)*.5 <= support < peak
            and float(pullback.Low.iloc[:-1].min()) <= float(ema9.iloc[-2])*1.003
            and price > float(ema9.iloc[-1]) and price > float(pullback.High.iloc[:-1].max())
            and price > float(pullback.Open.iloc[-1])):
            entry = float(pullback.High.iloc[:-1].max())
            # Half/full impulse extensions from pullback support, capped by old peak.
            add('TREND_PULLBACK_RECLAIM', entry, support, support, peak-trough,
                {'impulse_high': peak, 'impulse_low': trough, 'pullback_low': support,
                 'ema9': float(ema9.iloc[-1]), 'ema21': float(ema21.iloc[-1]),
                 'confirmation': 'Rising EMA trend, shallow pullback, close above pullback trigger'}, f.iloc[:-3])
    if not result['setups']:
        result['issues'] = ['No confirmed range retest, opening-range retest or trend pullback/reclaim pattern.']
    return result
