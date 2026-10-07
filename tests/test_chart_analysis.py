import copy
import unittest
from datetime import datetime, timezone, timedelta
import pandas as pd
from chart_analysis import analyse_chart
from trade_filters import classify_setup
from publish_alert import material_change
from run_radar import build_potential_signals

NOW = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)
NEWS = {'retrieval_status': 'OK', 'eligible_for_points': False, 'headlines': []}


def pattern_asset():
    p = {'pattern': 'RANGE_BREAKOUT_RETEST', 'entry': 100, 'support': 99,
         'resistance_1': 103, 'resistance_2': 105, 'momentum_pct': .5, 'extension_pct': 1,
         'observed_at': NOW.isoformat(), 'target_method': 'Measured range projections capped at observed overhead highs; not confirmed resistance'}
    return {'ticker':'TEST','name':'Test','asset_type':'stock','price':100,'currency':'EUR',
            'history_status':'OK','observed_at':NOW.isoformat(),'session':'REGULAR',
            'dollar_volume':20_000_000,'volume_spike':2,'broker_availability':{'status':'LISTED_API'},
            'chart_analysis':{'setups':[p], 'recent_volume_ratio':2}}


def retest_frame():
    # 16 earlier bars, 18-bar range, three breakout bars, three retest bars.
    f = pd.DataFrame({'Open':[97.5]*40,'High':[98.0]*40,'Low':[97.0]*40,'Close':[97.5]*40,'Volume':[100]*40},
                     index=pd.date_range(end=NOW-timedelta(minutes=5),periods=40,freq='5min'))
    f.iloc[16:34] = [98,100,96,99,100]
    f.iloc[34:37] = [100,102,99.9,101,200]
    f.iloc[37] = [100.3,100.5,100,100.2,200]
    f.iloc[38] = [100.2,100.4,100.05,100.3,200]
    f.iloc[39] = [100.3,100.8,100.2,100.7,200]
    return f


class PatternTests(unittest.TestCase):
    def test_range_breakout_retest_detected_with_traceable_projection(self):
        r=analyse_chart(retest_frame(),'crypto',NOW)
        p=next(x for x in r['setups'] if x['pattern']=='RANGE_BREAKOUT_RETEST')
        self.assertEqual(p['entry'],100.5)
        self.assertEqual(p['support'],100)
        self.assertEqual(p['target_basis']['measured_height'],4)
        self.assertEqual(p['resistance_1'],102)
        self.assertEqual(p['resistance_2'],104)
        self.assertIn('not confirmed resistance',p['target_method'])

    def test_failed_retest_stale_unfinished_invalid_or_gapped_bars_do_not_qualify(self):
        for mutation in ['failed','stale','unfinished','gap','bad_ohlc']:
            f=retest_frame(); now=NOW
            if mutation=='failed': f.iloc[-2,f.columns.get_loc('Low')]=98
            if mutation=='stale': now += timedelta(minutes=16)
            if mutation=='unfinished': now -= timedelta(minutes=1)
            if mutation=='gap': f=f.drop(f.index[-2])
            if mutation=='bad_ohlc': f.iloc[-1,f.columns.get_loc('High')]=1
            with self.subTest(mutation=mutation):
                self.assertFalse(analyse_chart(f,'crypto',now)['setups'])

    def test_opening_range_requires_real_ny_open_and_retest(self):
        f=retest_frame()
        # Previous session + today's 12 bars, with the first six opening range bars.
        f.index = pd.date_range('2026-10-06T17:00Z',periods=28,freq='5min').append(pd.date_range('2026-10-07T13:30Z',periods=12,freq='5min'))
        f.iloc[28:34]=[98,100,96,99,100]
        r=analyse_chart(f,'stock',datetime(2026,10,7,14,30,tzinfo=timezone.utc))
        self.assertIn('OPENING_RANGE_RETEST',[p['pattern'] for p in r['setups']])
        f.index=f.index+pd.Timedelta(minutes=5)
        self.assertNotIn('OPENING_RANGE_RETEST',[p['pattern'] for p in analyse_chart(f,'stock',datetime(2026,10,7,14,35,tzinfo=timezone.utc))['setups']])

    def test_trend_pullback_reclaim_detected(self):
        closes = [90+i*.25 for i in range(28)] + [97+i*.75 for i in range(9)] + [101,100.7,101.5]
        f = pd.DataFrame({'Open':[x-.1 for x in closes], 'High':[x+.2 for x in closes],
                          'Low':[x-.3 for x in closes], 'Close':closes, 'Volume':[100.0]*40},
                         index=pd.date_range(end=NOW-timedelta(minutes=5),periods=40,freq='5min'))
        r=analyse_chart(f,'crypto',NOW)
        self.assertIn('TREND_PULLBACK_RECLAIM',[p['pattern'] for p in r['setups']])

    def test_no_pattern_on_flat_chart(self):
        f=retest_frame(); f.loc[:,'Open']=100.;f.loc[:,'Close']=100.;f.loc[:,'High']=101.;f.loc[:,'Low']=99.
        self.assertFalse(analyse_chart(f,'crypto',NOW)['setups'])


class ChartQualificationTests(unittest.TestCase):
    def test_chart_route_can_pass_without_news_and_keeps_eur_risk_caps(self):
        q=classify_setup(pattern_asset(),NEWS,NOW)
        self.assertEqual(q['status'],'ACTIONABLE NOW')
        self.assertEqual(q['route'],'CHART_LED')
        self.assertLessEqual(q['plan']['planned_loss_eur'],5)
        self.assertLessEqual(q['plan']['position_eur'],500)
        self.assertIn('target_method',q['plan'])
        self.assertEqual(q['assessments'][0]['status'],'REJECTED')

    def test_no_pattern_cannot_use_chart_route_to_bypass_news(self):
        a=pattern_asset(); a['chart_analysis']['setups']=[]
        self.assertEqual(classify_setup(a,NEWS,NOW)['status'],'REJECTED')

    def test_adverse_and_failed_news_block_chart_route(self):
        for news in [{**NEWS,'adverse':True},{**NEWS,'retrieval_status':'FAILED'}]:
            q=classify_setup(pattern_asset(),news,NOW)
            self.assertNotIn(q['status'],['ACTIONABLE NOW','WATCH FOR ENTRY','TECHNICAL WATCH'])

    def test_safety_gates_cannot_become_near_misses(self):
        changes=[{'history_status':'STALE'},{'session':'CLOSED'},{'dollar_volume':100},
                 {'observed_at':(NOW-timedelta(minutes=16)).isoformat()},
                 {'broker_availability':{'status':'UNVERIFIED'}},{'currency':'USD','eur_per_unit':None}]
        for change in changes:
            q=classify_setup({**pattern_asset(),**change},NEWS,NOW)
            with self.subTest(change=change):
                self.assertNotIn(q['status'],['ACTIONABLE NOW','WATCH FOR ENTRY','TECHNICAL WATCH'])

    def test_volume_near_miss_has_no_plan_and_enters_review_bridge(self):
        a={**pattern_asset(),'volume_spike':1.2}
        a['qualification']=classify_setup(a,NEWS,NOW)
        self.assertEqual(a['qualification']['status'],'TECHNICAL WATCH')
        self.assertIsNone(a['qualification']['plan'])
        self.assertIn('1.5x',a['qualification']['blockers'][0])
        self.assertEqual(build_potential_signals([a],[]),[a])
        self.assertFalse(material_change(a,copy.deepcopy(a)))
        b=copy.deepcopy(a);b['qualification']['blockers']=['different blocker']
        self.assertTrue(material_change(a,b))

    def test_crypto_requires_volume_confirmation(self):
        a=pattern_asset();a['asset_type']='crypto';a['session']='24/7';a['chart_analysis']['recent_volume_ratio']=1
        self.assertEqual(classify_setup(a,NEWS,NOW)['status'],'TECHNICAL WATCH')

    def test_extended_chart_and_cutoff_still_block(self):
        a=pattern_asset();a['chart_analysis']['setups'][0]['extension_pct']=4
        self.assertEqual(classify_setup(a,NEWS,NOW)['status'],'EXTENDED—DO NOT CHASE')
        now=NOW.replace(hour=21);a=pattern_asset();a['observed_at']=now.isoformat()
        self.assertNotIn(classify_setup(a,NEWS,now)['status'],['ACTIONABLE NOW','TECHNICAL WATCH'])


class EvidenceTests(unittest.TestCase):
    def test_old_pattern_cannot_pass_with_new_quote(self):
        a=pattern_asset();a['chart_analysis']['setups'][0]['observed_at']=(NOW-timedelta(hours=1)).isoformat()
        self.assertNotIn(classify_setup(a,NEWS,NOW)['status'],['ACTIONABLE NOW','WATCH FOR ENTRY','TECHNICAL WATCH'])

    def test_all_technical_blockers_are_reported_together(self):
        a=pattern_asset();a['volume_spike']=1
        a['chart_analysis']['setups'][0]['resistance_1']=100.5
        q=classify_setup(a,NEWS,NOW)
        self.assertEqual(q['status'],'TECHNICAL WATCH')
        self.assertEqual(len(q['blockers']),2)
