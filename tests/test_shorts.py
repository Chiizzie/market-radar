import copy
import json
import unittest
from datetime import timedelta
import pandas as pd
from test_chart_analysis import NOW, NEWS, pattern_asset, retest_frame
from chart_analysis import analyse_chart
from trade_filters import classify_setup
from broker_availability import BrokerAvailability, parse_t212_cfd
from discovery import stock_candidate_priority
from publish_alert import material_change
from run_radar import build_potential_signals
from performance import observe_plan
from radar import build_market_snapshot
from outcome_tracker import update_outcomes


def short_asset(verified=True):
    a=pattern_asset()
    a['chart_analysis']['higher_timeframe']={'trend':'FALLING','observed_at':NOW.isoformat()}
    a['chart_analysis']['setups'][0].update(direction='SHORT',pattern='RANGE_BREAKDOWN_RETEST',
        support=101,resistance_1=97,resistance_2=95,momentum_pct=-.5)
    a['short_broker_availability']={'status':'LISTED_PUBLIC' if verified else 'UNVERIFIED',
        'checked_at':NOW.isoformat(),'max_open_short':100,'capabilities':{'product':'CFD','short':verified}}
    return a


def reflect(f, anchor=200):
    out=f.copy()
    out['Open'],out['Close']=anchor-f.Open,anchor-f.Close
    out['High'],out['Low']=anchor-f.Low,anchor-f.High
    return out


class ShortPatterns(unittest.TestCase):
    def test_breakdown_retest_has_downside_projection(self):
        r=analyse_chart(reflect(retest_frame()),'crypto',NOW)
        p=next(x for x in r['setups'] if x['pattern']=='RANGE_BREAKDOWN_RETEST')
        self.assertEqual(p['direction'],'SHORT')
        self.assertEqual(p['entry'],99.5)
        self.assertEqual(p['support'],100)
        self.assertEqual(p['resistance_1'],98)
        self.assertEqual(p['resistance_2'],96)
        self.assertEqual(r['higher_timeframe']['trend'],'FALLING')

    def test_stale_future_gap_invalid_do_not_emit_short(self):
        for mutation in ['stale','future','gap','invalid']:
            f=reflect(retest_frame());now=NOW
            if mutation=='stale':now+=timedelta(minutes=16)
            if mutation=='future':now-=timedelta(minutes=1)
            if mutation=='gap':f=f.drop(f.index[-2])
            if mutation=='invalid':f.iloc[-1,f.columns.get_loc('High')]=1
            self.assertFalse(analyse_chart(f,'crypto',now)['setups'])

    def test_no_future_pivot_used_for_support(self):
        from swing_zones import support_zones
        f=reflect(retest_frame())
        for end in range(30,len(f)+1):
            for z in support_zones(f.iloc[:end],110):
                self.assertLessEqual(pd.Timestamp(z['confirmed_at']),f.index[end-1])

    def test_bearish_counterparts_of_new_patterns(self):
        from test_overhaul import DetectionTests
        frame=DetectionTests().frame
        cases={
            'EARLY_RANGE_BREAKDOWN': [[98,100,96,98.5,100]]*39+[[99.8,100.5,99.7,100.3,200]],
            'RESISTANCE_REJECTION': [[98,102,96,98,100]]*38+[[96.1,96.3,96,96.1,100],[96.1,96.7,96,96.6,200]],
            'SESSION_VWAP_REJECTION': [[100,102,98,100,100]]*38+[[99.5,99.8,99,99.4,100],[99.5,100.5,99.4,100.3,200]],
            'BEARISH_MOMENTUM_CONTINUATION': [[95,96,94,95,100]]*22+[[95+i*.4,95.4+i*.4,94.9+i*.4,95.3+i*.4,100] for i in range(13)]+[[99.9,100.2,99.7,100,100]]*4+[[100,100.5,99.9,100.4,200]]}
        for expected,rows in cases.items():
            with self.subTest(pattern=expected):
                self.assertIn(expected,[p['pattern'] for p in analyse_chart(reflect(frame(rows)),'crypto',NOW)['setups']])

    def test_failed_breakout_requires_prior_completed_break(self):
        f=retest_frame();f.iloc[16:37]=[98,100,96,99,100];f.iloc[-3]=[100,102,99.9,101.2,200]
        f.iloc[-2]=[101,102,99.9,101.1,200];f.iloc[-1]=[100,100.2,98,98.5,200]
        r=analyse_chart(f,'crypto',NOW)
        self.assertIn('FAILED_BREAKOUT_REVERSAL',[p['pattern'] for p in r['setups']])
        f.iloc[-3]=[99,102,98.8,99,200];f.iloc[-2]=[99,102,98.8,99,200]
        self.assertNotIn('FAILED_BREAKOUT_REVERSAL',[p['pattern'] for p in analyse_chart(f,'crypto',NOW)['setups']])


class ShortPlans(unittest.TestCase):
    def test_verified_short_plan_uses_downside_net_rr_and_risk(self):
        q=classify_setup(short_asset(),NEWS,NOW);p=q['plan']
        self.assertEqual(q['status'],'ACTIONABLE NOW')
        self.assertEqual(p['direction'],'SHORT');self.assertEqual(p['product'],'CFD')
        self.assertGreater(p['stop_loss'],p['entry']);self.assertGreater(p['entry'],p['tp1']);self.assertGreater(p['tp1'],p['tp2'])
        self.assertLessEqual(p['planned_loss_eur'],15);self.assertLessEqual(p['position_eur'],500)
        self.assertAlmostEqual(p['planned_loss_eur'],p['units']*(p['stop_loss']-p['entry'])+p['cost_reserve_eur'],places=2)
        self.assertGreaterEqual(p['rr1'],1.5);self.assertGreaterEqual(p['rr2'],2)
        self.assertLess(p['rr1'],p['gross_rr1']);self.assertIn('22:00',p['exit_by'])

    def test_unknown_permission_is_watch_never_plan(self):
        a=short_asset(False);q=classify_setup(a,NEWS,NOW)
        self.assertEqual(q['status'],'BEARISH WATCH');self.assertIsNone(q['plan'])
        a['qualification']=q;self.assertEqual(build_potential_signals([a],[]),[a])

    def test_invest_or_crypto_cannot_authorize_short(self):
        for changes in [{'asset_type':'crypto'}, {'short_broker_availability':{'status':'LISTED_API','capabilities':{'short':True,'product':'INVEST'}}}]:
            a=short_asset();a.update(changes);self.assertIsNone(classify_setup(a,NEWS,NOW)['plan'])

    def test_short_rejects_positive_news_and_failed_retrieval(self):
        for news in [{**NEWS,'eligible_for_points':True},{**NEWS,'retrieval_status':'FAILED'}]:
            self.assertIsNone(classify_setup(short_asset(),news,NOW)['plan'])
        self.assertEqual(classify_setup(short_asset(),{**NEWS,'adverse':True},NOW)['status'],'ACTIONABLE NOW')

    def test_cutoff_both_dst_seasons_and_closed_session(self):
        for now in [NOW.replace(hour=20,minute=30),NOW.replace(month=12,hour=21,minute=30)]:
            a=short_asset();a['observed_at']=now.isoformat();a['short_broker_availability']['checked_at']=now.isoformat()
            a['chart_analysis']['setups'][0]['observed_at']=now.isoformat();a['chart_analysis']['higher_timeframe']['observed_at']=now.isoformat()
            self.assertIsNone(classify_setup(a,NEWS,now)['plan'])
        a=short_asset();a['session']='CLOSED';self.assertIsNone(classify_setup(a,NEWS,NOW)['plan'])

    def test_invalid_order_momentum_context_liquidity_stale_limits(self):
        for change in ['order','momentum','context','liquidity','stale','limit','cost','extended','quote']:
            a=short_asset();p=a['chart_analysis']['setups'][0]
            if change=='order':p['resistance_1']=103
            if change=='momentum':p['momentum_pct']=1
            if change=='context':a['chart_analysis']['higher_timeframe']['trend']='RISING'
            if change=='liquidity':a['dollar_volume']=1
            if change=='stale':a['short_broker_availability']['checked_at']=(NOW-timedelta(minutes=16)).isoformat()
            if change=='limit':a['short_broker_availability']['max_open_short']=1
            if change=='cost':a['cost_reserve_bps']=500
            if change=='extended':a['price']=98
            if change=='quote':a['observed_at']=(NOW-timedelta(minutes=10)).isoformat()
            with self.subTest(change=change):self.assertIsNone(classify_setup(a,NEWS,NOW)['plan'])

    def test_developing_not_actionable_and_wait_above_trigger(self):
        a=short_asset();a['chart_analysis']['setups'][0]['stage']='DEVELOPING'
        self.assertEqual(classify_setup(a,NEWS,NOW)['status'],'TECHNICAL WATCH')
        a=short_asset();a['price']=100.2
        self.assertEqual(classify_setup(a,NEWS,NOW)['status'],'WATCH FOR ENTRY')

    def test_snapshot_direction_and_dedupe(self):
        a=short_asset();a['qualification']=classify_setup(a,NEWS,NOW);a['catalyst']=NEWS
        self.assertIn('TEST SHORT',build_market_snapshot([a],[],now=NOW))
        b=copy.deepcopy(a);b['qualification']['direction']='LONG'
        self.assertTrue(material_change(a,b));self.assertFalse(material_change(a,copy.deepcopy(a)))

    def test_short_outcomes_reverse_barriers(self):
        plan=classify_setup(short_asset(),NEWS,NOW)['plan']
        r={'actionable_plan':plan,'first_actionable_at':NOW.isoformat()}
        q={'price':96,'currency':'EUR','observed_at':(NOW+timedelta(minutes=5)).isoformat()}
        observe_plan(r,q,NOW+timedelta(minutes=5))
        self.assertIn('tp1',r['sampled_plan_events']);self.assertNotIn('stop',r['sampled_plan_events']);self.assertGreater(r['sampled_r_multiple'],0)
        q.update(price=102,observed_at=(NOW+timedelta(minutes=10)).isoformat());observe_plan(r,q,NOW+timedelta(minutes=10))
        self.assertIn('stop',r['sampled_plan_events']);self.assertLess(r['sampled_r_multiple'],0)

    def test_direction_flip_has_separate_outcome_record(self):
        a=short_asset();a['qualification']=classify_setup(a,NEWS,NOW)
        payload={'stocks':[a],'crypto':[],'generated_at':NOW.isoformat()}
        first=update_outcomes({},payload)
        a=pattern_asset();a['qualification']=classify_setup(a,NEWS,NOW)
        payload.update(stocks=[a],generated_at=(NOW+timedelta(minutes=5)).isoformat())
        second=update_outcomes(first,payload)
        self.assertEqual(len(second['records']),2)
        self.assertEqual({r['direction'] for r in second['records'].values()},{'LONG','SHORT'})


class CFDDiscovery(unittest.TestCase):
    def setUp(self):
        self.b=BrokerAvailability(NOW)
        self.row={'shortName':'TEST','fullName':'Test','ticker':'TEST','currency':'USD','type':'STOCK','overnightInterestShort':-.0002}
        self.b.catalogues={'cfd':[self.row],'stock':[]}
        self.a=short_asset()

    def test_real_public_schema_missing_short_limit_fails_closed(self):
        q=self.b.check_short(self.a)
        self.assertEqual(q['listing_status'],'LISTED_PUBLIC');self.assertEqual(q['status'],'UNVERIFIED')
        self.assertFalse(q['capabilities']['short'])
        self.assertNotIn(self.b.check_discovery(self.a)['status'],{'NOT_LISTED','RESTRICTED'})

    def test_explicit_limit_restrictions_identity(self):
        self.row['maxOpenShort']=100
        self.assertTrue(self.b.check_short(self.a)['capabilities']['short'])
        self.row['maxOpenShort']=0;self.assertEqual(self.b.check_short(self.a)['status'],'RESTRICTED')
        self.row['maxOpenShort']=100;self.row['dealerExclusions']=['EU'];self.assertFalse(self.b.check_short(self.a)['capabilities']['short'])
        self.row['dealerExclusions']=[];self.row['fullName']='Different';self.assertEqual(self.b.check_short(self.a)['status'],'UNVERIFIED')

    def test_wrong_product_or_partial_catalogue_rejected(self):
        for product,rows in [('invest',[self.row]*1000),('cfd',[self.row])]:
            html='<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'tradingType':product,'instruments':rows}}})+'</script>'
            with self.assertRaises(ValueError):parse_t212_cfd(html)

    def test_ranking_is_direction_neutral(self):
        q={'regularMarketPrice':100,'regularMarketVolume':100000,'regularMarketChangePercent':2}
        self.assertEqual(stock_candidate_priority(q),stock_candidate_priority({**q,'regularMarketChangePercent':-2}))
