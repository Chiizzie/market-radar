"""Causal structural replay, not a historical broker execution backtest."""
import json
import time
from collections import Counter
from pathlib import Path
import pandas as pd
from chart_analysis import analyse_chart


def structural_metrics(pattern, price):
    entry=max(price,pattern['entry']);stop=pattern['support']*.999
    risk=entry-stop
    if risk <= 0:
        return {'passes':False,'reason':'invalid_stop'}
    rr1=(pattern['resistance_1']-entry)/risk;rr2=(pattern['resistance_2']-entry)/risk
    width=risk/entry
    return {'passes':.002 <= width <= .05 and rr1 >= 1.5 and rr2 >= 2,
            'stop_width_pct':round(width*100,4),'rr1':round(rr1,4),'rr2':round(rr2,4)}


def replay(charts, bars_per_chart=36, max_seconds=45):
    start=time.monotonic();events=[];counts=Counter();processed=0;partial=False
    for chart in charts:
        rows=chart['bars']
        f=pd.DataFrame([r[1:] for r in rows],columns=['Open','High','Low','Close','Volume'],
                       index=pd.to_datetime([r[0] for r in rows],utc=True))
        if f.empty:
            continue
        # Only the last recorded regular session for stocks, bounded recent bars for crypto.
        first=max(30,len(f)-bars_per_chart+1)
        if chart['asset_type']=='stock':
            dates=f.index.tz_convert('America/New_York').date
            positions=[i for i,d in enumerate(dates) if d==dates[-1]]
            first=max(first,positions[0]+1)
        for end in range(first,len(f)+1):
            if time.monotonic()-start > max_seconds:
                partial=True;break
            prefix=f.iloc[:end]
            now=prefix.index[-1]+pd.Timedelta(minutes=5)
            # Each detector sees ONLY bars completed at this simulated decision time.
            old=analyse_chart(prefix,chart['asset_type'],now,resistance_mode='candle_highs')
            new=analyse_chart(prefix,chart['asset_type'],now,resistance_mode='swing_zones')
            processed+=1
            versions={}
            for label,analysis in [('old_candle_highs',old),('confirmed_swing_zones',new)]:
                versions[label]={p['pattern']:structural_metrics(p,float(prefix.Close.iloc[-1])) for p in analysis['setups']}
                counts[label+'_patterns']+=len(analysis['setups'])
                counts[label+'_structural_passes']+=sum(x['passes'] for x in versions[label].values())
            if versions['old_candle_highs'] or versions['confirmed_swing_zones']:
                events.append({'symbol':chart['symbol'],'asset_type':chart['asset_type'],
                               'decision_at':now.isoformat(), **versions})
        if partial:
            break
    return {'schema_version':1,'status':'PARTIAL_TIME_BUDGET' if partial else 'COMPLETE',
            'charts_supplied':len(charts),'decision_windows':processed,'counts':dict(counts),'events':events,
            'notice':'Prefix-only replay of recorded OHLCV, comparing target/stop geometry. Selected-asset sample with overlapping windows; not independent trades. Historical broker access, spread, fees, news and fills are unknown. Structural passes are not actionable trades, profit or a win rate.'}


def main():
    data=json.loads(Path('chart_samples.json').read_text())
    result=replay(data['charts'])
    result['captured_at']=data['captured_at']
    Path('replay_report.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print('Replay summary:',json.dumps({k:v for k,v in result.items() if k!='events'}))


if __name__=='__main__':
    main()
