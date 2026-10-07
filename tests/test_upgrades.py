import copy
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pandas as pd
from crypto_candles import validate_candles, CandleProvider
from outcome_tracker import update_outcomes
from radar import irish_time, build_market_snapshot
from primary_news import parse_feed
from discovery import discover_catalysts

NOW = datetime(2026,10,7,15,tzinfo=timezone.utc)
COLS = ['timestamp','Open','High','Low','Close','Volume']


def rows():
    return [[int((NOW-timedelta(minutes=5*(40-i))).timestamp()),100,102,99,101,10] for i in range(40)]


def candidate(status='CATALYST WATCH'):
    return {'asset_type':'crypto','coin_id':'test','symbol':'TEST','name':'Test Coin','price':100,'currency':'EUR',
            'observed_at':NOW.isoformat(), 'catalyst':{'id':'event','eligible_for_points':False},
            'qualification':{'status':status,'plan':None,'reasons':['research']}}


def payload(now=NOW, price=100):
    a=candidate(); a.update(price=price,observed_at=now.isoformat())
    return {'generated_at':now.isoformat(),'stocks':[],'crypto':[a],'potential_signals':[a],'tracking_prices':[a]}


class CandleTests(unittest.TestCase):
    def test_incomplete_bar_dropped_and_sorted(self):
        r=rows()+[[int(NOW.timestamp()),101,200,1,199,1]]
        f=validate_candles(list(reversed(r)),COLS,NOW)
        self.assertEqual(len(f),40)
        self.assertEqual(f.Close.iloc[-1],101)

    def test_bad_stale_and_gapped_rejected(self):
        bad=rows();bad[-1][2]=50
        gaps=rows();del gaps[-10:-7]
        for r,n in [(bad,NOW),(rows(),NOW+timedelta(hours=1)),(gaps,NOW)]:
            with self.assertRaises(ValueError):validate_candles(r,COLS,n)

    def test_fallback_uses_real_coinbase_schema(self):
        p=CandleProvider(NOW);p.pairs={'Kraken':{'TEST':'TESTEUR'},'Coinbase':{'TEST':'TEST-EUR'}}
        cb=[[r[0],r[3],r[2],r[1],r[4],r[5]] for r in rows()]
        with patch.object(p,'get',side_effect=[ValueError('primary down'),cb]):
            f,e=p.candles({'symbol':'TEST','price':101})
        self.assertTrue(e['fallback_used']);self.assertEqual(e['currency'],'EUR')
        self.assertEqual(f.Volume.iloc[-1],10)

    def test_no_eur_pair_never_invents_conversion(self):
        with self.assertRaises(ValueError):CandleProvider(NOW).candles({'symbol':'TEST','price':101})

    def test_cross_provider_price_mismatch_rejected(self):
        p=CandleProvider(NOW);p.pairs={'Kraken':{},'Coinbase':{'TEST':'TEST-EUR'}}
        cb=[[r[0],r[3],r[2],r[1],r[4],r[5]] for r in rows()]
        with patch.object(p,'get',return_value=cb),self.assertRaises(ValueError):p.candles({'symbol':'TEST','price':1})


class NotificationTests(unittest.TestCase):
    def test_dublin_summer_and_winter(self):
        self.assertIn('16:00',irish_time(NOW))
        self.assertIn('15:00',irish_time(datetime(2026,12,1,15,tzinfo=timezone.utc)))
        self.assertEqual(irish_time('2026-10-07'),'2026-10-07 (date only)')

    def test_action_first_and_watch_not_action(self):
        action=candidate('ACTIONABLE NOW');watch=candidate('WATCH FOR ENTRY');watch['symbol']='WAIT'
        msg=build_market_snapshot([], [watch,action], now=NOW)
        self.assertTrue(msg.startswith('🚨 CHECK NOW — TEST'))
        self.assertLess(msg.index('TEST (Test Coin)'),msg.index('WAIT (Test Coin)'))
        self.assertNotIn('UTC',msg)
        self.assertTrue(build_market_snapshot([],[watch],now=NOW).startswith('👀 WATCH FOR ENTRY'))
        self.assertTrue(build_market_snapshot([],[],now=NOW).startswith('NO ACTIONABLE SETUP'))


class PrimaryFeedTests(unittest.TestCase):
    def test_direct_feed_discovery_without_google(self):
        rss=b'<rss version="2.0"><channel><title>Issuer releases</title><item><title>New Company announces acquisition on October 7, 2026</title><link>https://issuer.example/release</link><pubDate>Wed, 07 Oct 2026 14:00:00 GMT</pubDate></item></channel></rss>'
        articles=parse_feed(rss,'Issuer releases','https://issuer.example/feed')
        with patch('discovery.fetch_news',return_value=[]),patch('primary_news.fetch_primary',return_value=(articles,{'feeds_received':1,'failures':[]})):
            hits,h=discover_catalysts({'NEW':'New Company'},now=NOW)
        self.assertIn('NEW',hits);self.assertEqual(hits['NEW']['source_kind'],'direct_announcement_feed')
        self.assertEqual(h['primary_sources']['feeds_received'],1)

    def test_invalid_html_is_failure_not_empty_feed(self):
        with self.assertRaises(ValueError):parse_feed(b'<html>Access denied</html>','Issuer','https://example.com')

    def test_regulatory_filing_does_not_invent_underlying_event_date(self):
        rss=b'<feed xmlns="http://www.w3.org/2005/Atom"><title>SEC</title><entry><title>8-K - New Company</title><link href="https://www.sec.gov/example"/><updated>2026-10-07T14:00:00Z</updated></entry></feed>'
        articles=parse_feed(rss,'SEC EDGAR 8-K filings','https://www.sec.gov/feed')
        with patch('discovery.fetch_news',return_value=[]),patch('primary_news.fetch_primary',return_value=(articles,{})):
            hits,_=discover_catalysts({'NEW':'New Company'},now=NOW)
        self.assertEqual(hits['NEW']['event_time_status'],'UNVERIFIED')


class OutcomeTests(unittest.TestCase):
    def test_deduped_sampled_return_and_horizon(self):
        p=payload();first=update_outcomes({},p)
        self.assertEqual(first,update_outcomes(first,p))
        later=update_outcomes(first,payload(NOW+timedelta(hours=1),105))
        r=next(iter(later['records'].values()))
        self.assertEqual(r['observations'],2);self.assertEqual(r['horizon_returns']['1']['return_pct'],5)
        self.assertFalse(r['executed_trade'])

    def test_stale_quotes_dont_create_entry_or_loss(self):
        p=payload();p['generated_at']=(NOW+timedelta(hours=1)).isoformat()
        result=update_outcomes({},p);r=next(iter(result['records'].values()))
        self.assertNotIn('baseline_price',r);self.assertEqual(r['tracking_status'],'QUOTE_UNAVAILABLE')

    def test_removed_candidate_still_followed_from_broad_quotes(self):
        first=update_outcomes({},payload())
        p=payload(NOW+timedelta(minutes=15),102);p['crypto']=[];p['potential_signals']=[]
        result=update_outcomes(first,p);r=next(iter(result['records'].values()))
        self.assertEqual(r['last_return_pct'],2);self.assertEqual(r['last_status'],'CATALYST WATCH')

    def test_missing_quote_is_not_invalidation_and_status_transition_recorded(self):
        first=update_outcomes({},payload())
        p=payload(NOW+timedelta(minutes=15));p['crypto'][0]['qualification']['status']='REJECTED';p['potential_signals']=[]
        result=update_outcomes(first,p);r=next(iter(result['records'].values()))
        self.assertEqual(r['status_changes'][0]['to'],'REJECTED')
        p={'generated_at':(NOW+timedelta(minutes=30)).isoformat()}
        r=next(iter(update_outcomes(result,p)['records'].values()))
        self.assertEqual(r['tracking_status'],'QUOTE_UNAVAILABLE');self.assertEqual(r['last_status'],'REJECTED')
