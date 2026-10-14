"""Causal bearish counterparts using real prices and completed OHLCV only."""
import pandas as pd
from swing_zones import support_zones


def analyse_bearish(f, asset_type, now):
    # Called only after analyse_chart validates OHLCV, timestamps and freshness.
    result = {'setups': []}
    price = float(f.Close.iloc[-1])
    recent = f.iloc[-8:]
    reference = float((((recent.High+recent.Low+recent.Close)/3)*recent.Volume).sum()/recent.Volume.sum())
    end = (f.index[-1]+pd.Timedelta(minutes=5)).isoformat()
    dates = f.index.tz_convert('America/New_York').date
    session = f.loc[dates == dates[-1]] if asset_type == 'stock' else f

    def add(pattern, entry, ceiling, origin, height, evidence, history, stage='CONFIRMED'):
        if not 0 < entry < ceiling or not .004 <= height/origin <= .08:
            return
        zones = support_zones(history, entry)
        floors = sorted([min(entry, z['upper']) for z in zones], reverse=True)
        projected = [origin-height*.5, origin-height]
        t1 = max(projected[0], floors[0]) if floors else projected[0]
        lower = [v for v in floors if v < t1*.997]
        t2 = max(projected[1], lower[0]) if lower else projected[1]
        if not 0 < t2 < t1 < entry:
            return
        result['setups'].append({'direction':'SHORT', 'pattern':pattern, 'entry':entry,
            'support':ceiling, 'resistance_1':t1, 'resistance_2':t2,
            'stop_reference':ceiling, 'target_1':t1, 'target_2':t2,
            'momentum_pct':(price/float(f.Close.iloc[-4])-1)*100,
            'extension_pct':(reference-price)/reference*100,
            'observed_at':end, 'stage':stage, 'evidence':evidence,
            'target_method':'Measured downside projections capped at confirmed swing-low support; projections are not confirmed support',
            'target_basis':{'origin':origin,'measured_height':height,'uncapped_targets':projected,'support_levels':floors[:8]}})

    def retest(base, breakdown, name):
        if base.empty or breakdown.empty:
            return
        high, low = float(base.High.max()), float(base.Low.min())
        ret = session.iloc[-3:]
        ceiling = float(ret.High.max())
        if ((breakdown.Close < low*.999).any() and low*.997 <= ceiling <= low*1.003
                and price < low and price < float(ret.Open.iloc[-1])):
            add(name, min(low,float(ret.Low.iloc[:-1].min())), ceiling, low, high-low,
                {'range_high':high,'range_low':low,'retest_high':ceiling,'confirmation':'Breakdown, retest and bearish completed close'},f.iloc[:-3])

    if len(session) >= 24 and (session.index[-24:].to_series().diff().dropna() == pd.Timedelta(minutes=5)).all():
        retest(session.iloc[-24:-6],session.iloc[-6:-3],'RANGE_BREAKDOWN_RETEST')
        base, last = session.iloc[-21:-1], session.iloc[-1]
        high, low = float(base.High.max()), float(base.Low.min())
        if price < float(session.Close.iloc[-4]) and low*.9925 <= price <= low*1.01:
            add('EARLY_RANGE_BREAKDOWN',low*.999,float(session.High.iloc[-3:].max()),low,high-low,
                {'range_high':high,'range_low':low},f.iloc[:-1], 'CONFIRMED' if price < low*.999 else 'DEVELOPING')
        touches = sum(abs(float(v)/high-1) <= .003 for v in base.High)
        if touches >= 2 and float(last.High) >= high*.996 and price < float(last.Open) and price < float(session.Low.iloc[-2]):
            add('RESISTANCE_REJECTION',float(session.Low.iloc[-2]),max(high,float(last.High)),high,high-low,
                {'resistance_touches':touches},base)
        impulse, pause = session.iloc[-18:-5],session.iloc[-5:-1]
        height = float(impulse.High.max()-impulse.Low.min())
        ph, pl = float(pause.High.max()),float(pause.Low.min())
        if (float(impulse.Close.iloc[-1]) < float(impulse.Close.iloc[0])*.995 and ph-pl <= height*.35
                and price < pl and ph <= float(impulse.High.max())-height*.6):
            add('BEARISH_MOMENTUM_CONTINUATION',pl,ph,pl,height,{'pause_high':ph,'pause_low':pl},f.iloc[:-5])
        # A prior completed breakout above resistance followed by a close back inside.
        base = session.iloc[-24:-3]; high,low=float(base.High.max()),float(base.Low.min())
        breakout = session.iloc[-3:-1]
        if (breakout.Close > high*1.001).any() and price < high*.999 and price < float(last.Open):
            add('FAILED_BREAKOUT_REVERSAL',high*.999,float(session.High.iloc[-3:].max()),high,high-low,
                {'failed_resistance':high,'confirmation':'Prior completed breakout failed back inside range'},base)
        ema9,ema21 = session.Close.ewm(span=9,adjust=False).mean(),session.Close.ewm(span=21,adjust=False).mean()
        impulse,pullback=session.iloc[-12:-3],session.iloc[-3:]
        peak,trough=float(impulse.High.max()),float(impulse.Low.min()); ceiling=float(pullback.High.max())
        if (ema9.iloc[-1] < ema21.iloc[-1] < ema21.iloc[-4] and impulse.Close.iloc[-1] < impulse.Close.iloc[0]
                and trough < ceiling <= peak-(peak-trough)*.5 and pullback.High.iloc[:-1].max() >= ema9.iloc[-2]*.997
                and price < ema9.iloc[-1] and price < pullback.Low.iloc[:-1].min() and price < pullback.Open.iloc[-1]):
            add('TREND_RALLY_REJECTION',float(pullback.Low.iloc[:-1].min()),ceiling,ceiling,peak-trough,
                {'ema9':float(ema9.iloc[-1]),'ema21':float(ema21.iloc[-1])},f.iloc[:-3])
    if asset_type == 'stock' and len(session) >= 10:
        opening=session.iloc[:6]; times=opening.index.tz_convert('America/New_York')
        if times[0].hour == 9 and times[0].minute == 30 and (times.to_series().diff().dropna() == pd.Timedelta(minutes=5)).all():
            retest(opening,session.iloc[6:-3],'OPENING_RANGE_BREAKDOWN_RETEST')
    vs = session if asset_type == 'stock' else f.loc[f.index.date == f.index[-1].date()]
    if len(vs) >= 12:
        vwap=(((vs.High+vs.Low+vs.Close)/3)*vs.Volume).cumsum()/vs.Volume.cumsum().replace(0,float('nan'))
        if vs.Close.iloc[-2] >= vwap.iloc[-2] and price < vwap.iloc[-1]*.999 and price < vs.Open.iloc[-1]:
            entry=min(float(vwap.iloc[-1]),float(vs.Low.iloc[-2]))
            add('SESSION_VWAP_REJECTION',entry,float(vs.High.iloc[-3:].max()),entry,float(vs.High.max()-vs.Low.min()),
                {'session_vwap':float(vwap.iloc[-1]),'confirmation':'Completed bearish close loses session VWAP'},f.iloc[:-1])
    return result
