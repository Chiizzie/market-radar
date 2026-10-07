import unittest
from datetime import datetime, timezone
from unittest.mock import patch, Mock
from discovery import parse_directory, yahoo_quotes, discover_catalysts, select_stock_candidates, broad_quotes
from catalyst_scanner import classify_catalyst
from radar import enrich, build_market_snapshot
from publish_alert import material_change
from run_radar import build_potential_signals

NOW = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)

class DiscoveryTests(unittest.TestCase):
    def test_universe_accepts_unlisted_watchlist_company_and_filters_derivatives(self):
        text = 'Symbol|Security Name|Test Issue|ETF\nPENG|Penguin Solutions, Inc. - Ordinary Shares|N|N\nNEWX|New Company Common Stock|N|N\nFUND|ETF Fund|N|Y\nZZ|Test Common Stock|Y|N\nNEWXW|New Company Warrants|N|N\n'
        self.assertEqual(parse_directory(text), {'PENG': 'Penguin Solutions', 'NEWX': 'New Company'})

    def test_pagination_includes_later_pages(self):
        first = [{'symbol': f'A{i}'} for i in range(250)]
        with patch('discovery.yf.screen', side_effect=[{'total':251, 'quotes':first}, {'total':251, 'quotes':[{'symbol':'PENG'}]}]):
            rows, h = yahoo_quotes()
        self.assertEqual(len(rows), 251)
        self.assertTrue(h['pagination_complete'])
        self.assertEqual(rows[-1]['symbol'], 'PENG')

    def test_failed_page_is_partial(self):
        with patch('discovery.yf.screen', side_effect=[{'total':2, 'quotes':[{'symbol':'A'}]}, RuntimeError('provider failed')]):
            rows,h = yahoo_quotes()
        self.assertFalse(h['pagination_complete'])
        self.assertEqual(len(rows), 1)
        self.assertTrue(h['errors'])

    def test_flat_price_catalyst_and_small_cap_mover_both_discovered(self):
        quotes = [{'symbol':'NEWX','regularMarketPrice':2,'regularMarketVolume':400000,'regularMarketChangePercent':1.2,'regularMarketTime':NOW.timestamp()},
                  {'symbol':'PENG','regularMarketPrice':20,'regularMarketVolume':100,'regularMarketChangePercent':0,'regularMarketTime':NOW.timestamp()}]
        news = {'PENG': {'published_at':NOW.isoformat()}}
        items,h = select_stock_candidates(quotes, {'PENG':'Penguin Solutions','NEWX':'New Company'}, news, True, NOW)
        self.assertEqual({x['ticker'] for x in items}, {'PENG','NEWX'})
        self.assertEqual(items[0]['discovery_route'], 'catalyst_first')

    def test_news_mapping_without_hardcoded_ticker(self):
        article = {'title':'Penguin Solutions reports quarterly earnings and raises guidance',
                   'published_at':NOW.isoformat(), 'source':'Company release', 'link':'https://example.com/release'}
        with patch('discovery.fetch_news', return_value=[article]):
            hits,h = discover_catalysts({'PENG':'Penguin Solutions','NEWX':'New Company'}, now=NOW)
        self.assertIn('PENG',hits)
        self.assertEqual(h['headlines_received'],1)

    def test_commentary_and_drilling_not_material_regulatory_event(self):
        for title in ['Penguin Solutions: Buy Before Earnings As The Growth Runway Extends',
                      'Penguin Solutions Completes Phase 3 Drilling Program',
                      'Can Penguin Solutions Lead Again This Earnings Season?']:
            c=classify_catalyst([{'title':title,'published_at':NOW.isoformat(),'source':'News','link':'https://example.com'}], 'PENG','Penguin Solutions', now=NOW)
            self.assertFalse(c['eligible_for_points'])

    def test_watch_without_quote_or_plan_is_rendered_and_deduplicated(self):
        item={'ticker':'PENG','name':'Penguin Solutions','asset_type':'stock','session':'CLOSED','price':None,'observed_at':None,
              'history_status':'NOT_REQUESTED','catalyst':{'id':'event1','summary':'Penguin reports earnings','title':'Penguin reports earnings',
              'published_at':NOW.isoformat(),'source':'Company','headlines':[{}],'eligible_for_points':True}}
        output=enrich([item])[0]
        self.assertEqual(output['qualification']['status'],'CATALYST WATCH')
        self.assertIsNone(output['qualification']['plan'])
        self.assertFalse(material_change(output, output))
        self.assertEqual(build_potential_signals([output],[]),[output])
        self.assertIn('Quote unavailable',build_market_snapshot([output],[]))

    def test_nasdaq_snapshot_does_not_invent_quote_timestamp(self):
        response=Mock(); response.json.return_value={'data':{'rows':[{'symbol':'PENG','lastsale':'$20.00','volume':'1000','pctchange':'0.2%'}]}}
        with patch('discovery.requests.get',return_value=response):
            quotes,h=broad_quotes()
        self.assertTrue(quotes[0]['snapshot_time_unverified'])
        self.assertNotIn('regularMarketTime',quotes[0])
        self.assertEqual(quotes[0]['regularMarketPrice'],20)

    def test_crypto_recent_momentum_can_enter_below_two_percent_daily_move(self):
        from market_scanner import MarketScanner
        market = Mock()
        market.json.return_value = [
            {'id':'early','symbol':'EARLY','name':'Early Token','current_price':1,'total_volume':20_000_000,
             'price_change_percentage_24h':.5,'price_change_percentage_1h_in_currency':.8,'last_updated':NOW.isoformat()},
            {'id':'old','symbol':'OLD','name':'Old Token','current_price':1,'total_volume':50_000_000,
             'price_change_percentage_24h':10,'price_change_percentage_1h_in_currency':-.1,'last_updated':NOW.isoformat()}]
        history=Mock(); history.json.return_value={'prices':[[int((NOW.timestamp()-3600*(48-i))*1000),1] for i in range(48)]}
        with patch('discovery.discover_catalysts',return_value=({},{})), patch('market_scanner.requests.get',side_effect=[market,history]):
            items=MarketScanner(NOW).scan_crypto()
        self.assertEqual([x['symbol'] for x in items],['EARLY'])


if __name__ == '__main__':
    unittest.main()
