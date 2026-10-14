"""Causal resistance zones from confirmed, prominent swing highs."""
import pandas as pd


def resistance_zones(frame, entry, wing=2):
    if len(frame) < 2*wing+1:
        return []
    f = frame
    previous = f.Close.shift(1)
    tr = pd.concat([f.High-f.Low, (f.High-previous).abs(), (f.Low-previous).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=1).mean()
    highs=f.High.to_numpy(dtype=float);lows=f.Low.to_numpy(dtype=float)
    atrs=atr.to_numpy(dtype=float)
    times=f.index
    gaps=(times[1:]-times[:-1]) != pd.Timedelta(minutes=5) if isinstance(times,pd.DatetimeIndex) else None
    pivots=[]
    for i in range(wing,len(f)-wing):
        left=highs[i-wing:i];right=highs[i+1:i+wing+1];high=float(highs[i])
        if high < left.max() or high < right.max() or high <= left.min() or high <= right.min():
            continue
        if gaps is not None and gaps[i-wing:i+wing].any():
            continue
        prominence=min(high-lows[i-wing:i].min(),high-lows[i+1:i+wing+1].min())
        if prominence >= max(high*.0015,float(atrs[i+wing])*.5):
            pivots.append({'price':high,'at':str(times[i]),'confirmed_at':str(times[i+wing]),
                           'index':i,'tolerance':max(high*.0015,float(atrs[i+wing])*.25)})
    zones = []
    for p in sorted(pivots, key=lambda x:x['price']):
        if zones and p['price']-zones[-1]['lower'] <= min(p['tolerance'], zones[-1]['tolerance']):
            z=zones[-1]; z['upper']=p['price']; z['pivots'].append(p)
        else:
            zones.append({'lower':p['price'], 'upper':p['price'], 'tolerance':p['tolerance'], 'pivots':[p]})
    # Confirmed zones below the proposed entry are no longer overhead resistance.
    return [{'lower':z['lower'], 'upper':z['upper'], 'touches':len(z['pivots']),
             'confirmed_at':max(p['confirmed_at'] for p in z['pivots']),
             'pivot_times':[p['at'] for p in z['pivots']]}
            for z in zones if z['upper'] > entry*1.001]



def support_zones(frame, entry, wing=2):
    if len(frame) < 2*wing+1:
        return []
    f = frame
    previous = f.Close.shift(1)
    tr = pd.concat([f.High-f.Low, (f.High-previous).abs(), (f.Low-previous).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=1).mean()
    highs=f.High.to_numpy(dtype=float);lows=f.Low.to_numpy(dtype=float)
    atrs=atr.to_numpy(dtype=float)
    times=f.index
    gaps=(times[1:]-times[:-1]) != pd.Timedelta(minutes=5) if isinstance(times,pd.DatetimeIndex) else None
    pivots=[]
    for i in range(wing,len(f)-wing):
        left=lows[i-wing:i];right=lows[i+1:i+wing+1];high=float(lows[i])
        if high > left.min() or high > right.min() or high >= left.max() or high >= right.max():
            continue
        if gaps is not None and gaps[i-wing:i+wing].any():
            continue
        prominence=min(highs[i-wing:i].max()-high,highs[i+1:i+wing+1].max()-high)
        if prominence >= max(high*.0015,float(atrs[i+wing])*.5):
            pivots.append({'price':high,'at':str(times[i]),'confirmed_at':str(times[i+wing]),
                           'index':i,'tolerance':max(high*.0015,float(atrs[i+wing])*.25)})
    zones = []
    for p in sorted(pivots, key=lambda x:x['price']):
        if zones and p['price']-zones[-1]['lower'] <= min(p['tolerance'], zones[-1]['tolerance']):
            z=zones[-1]; z['upper']=p['price']; z['pivots'].append(p)
        else:
            zones.append({'lower':p['price'], 'upper':p['price'], 'tolerance':p['tolerance'], 'pivots':[p]})
    # Confirmed zones above entry cannot cap a downside projection.
    return [{'lower':z['lower'], 'upper':z['upper'], 'touches':len(z['pivots']),
             'confirmed_at':max(p['confirmed_at'] for p in z['pivots']),
             'pivot_times':[p['at'] for p in z['pivots']]}
            for z in zones if z['lower'] < entry*.999]


