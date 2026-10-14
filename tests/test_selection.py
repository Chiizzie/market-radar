import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from discovery import select_stock_candidates, stock_candidate_priority
from market_scanner import MarketScanner
from radar import build_market_snapshot

NOW = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)


def quote(symbol, change=2, **extra):
    return dict(symbol=symbol, regularMarketPrice=10, regularMarketVolume=1_000_000,
                regularMarketChangePercent=change, regularMarketTime=NOW.timestamp(), **extra)


class SelectionTests(unittest.TestCase):
    def test_funnel_expands_to_150_shortlist_and_60_checks(self):
        rows = [quote(f'S{i}') for i in range(713)]
        selected, health = select_stock_candidates(rows, {q['symbol']: q['symbol'] for q in rows}, {}, True, NOW)
        self.assertEqual(len(selected), 60)
        self.assertEqual(health['shortlisted_candidates'], 150)
        self.assertEqual(health['deferred_candidates'], 653)
        self.assertEqual(health['outside_shortlist'], 563)

    def test_broker_exclusions_do_not_consume_slots(self):
        rows = [quote(f'S{i}') for i in range(200)]
        def check(asset):
            return {'status': 'NOT_LISTED' if int(asset['ticker'][1:]) < 100 else 'LISTED_API'}
        selected, h = select_stock_candidates(rows, {q['symbol']: q['symbol'] for q in rows}, {}, True, NOW, broker_check=check)
        self.assertEqual(len(selected), 60)
        self.assertTrue(all(int(x['ticker'][1:]) >= 100 for x in selected))
        self.assertEqual(h['broker_excluded_candidates'], 100)
        self.assertEqual(h['deferred_candidates'], 40)

    def test_flat_fresh_catalysts_survive_crowded_movers(self):
        rows = [quote(f'S{i}') for i in range(200)] + [quote(f'N{i}', 0) for i in range(20)]
        news = {f'N{i}': {'published_at': NOW.isoformat()} for i in range(20)}
        selected, h = select_stock_candidates(rows, {q['symbol']: q['symbol'] for q in rows}, news, True, NOW)
        self.assertEqual(h['catalyst_selected'], 20)
        self.assertEqual(len(selected), 60)
        self.assertEqual(len({x['ticker'] for x in selected}), 60)

    def test_no_average_volume_does_not_reward_extended_or_falling_moves(self):
        early = stock_candidate_priority(quote('EARLY', 2))[1]
        self.assertGreater(early, stock_candidate_priority(quote('LATE', 40))[1])
        self.assertGreater(early, stock_candidate_priority(quote('FALL', -40))[1])
        self.assertGreater(stock_candidate_priority(quote('VOL', 2, averageDailyVolume3Month=250000))[1], early)

    def test_adverse_catalyst_not_penalized_and_known_broker_preferred(self):
        rows = [quote(s) for s in ['ADVERSE', 'UNKNOWN', 'SUPPORTED']]
        news = {s: {'published_at': NOW.isoformat(), 'adverse': s == 'ADVERSE'} for s in ['ADVERSE', 'UNKNOWN', 'SUPPORTED']}
        def check(asset):
            return {'status': 'UNVERIFIED' if asset['ticker'] == 'UNKNOWN' else 'LISTED_API'}
        selected, _ = select_stock_candidates(rows, {q['symbol']: q['symbol'] for q in rows}, news, True, NOW, broker_check=check)
        self.assertEqual([x['ticker'] for x in selected], ['ADVERSE', 'SUPPORTED', 'UNKNOWN'])

    def test_stale_news_and_stale_quotes_cannot_fill_shortlist(self):
        stale = quote('STALE'); stale['regularMarketTime'] = (NOW-timedelta(hours=1)).timestamp()
        news = {'NEWS': {'published_at': (NOW-timedelta(days=2)).isoformat()}}
        selected, _ = select_stock_candidates([stale], {'STALE':'Stale', 'NEWS':'News'}, news, True, NOW)
        self.assertEqual(selected, [])

    def test_closed_market_reports_zero_chart_checks(self):
        with patch('discovery.listed_universe', return_value=({'NEWS':'News Company'}, [])), \
             patch('discovery.broad_quotes', return_value=([], {'pagination_complete': True, 'errors': []})), \
             patch('discovery.discover_catalysts', return_value=({'NEWS': {'published_at': NOW.isoformat()}}, {'failed_queries': 0})), \
             patch('market_scanner.stock_session', return_value='CLOSED'), \
             patch('market_scanner.yf.Ticker') as ticker:
            scanner = MarketScanner(NOW)
            items = scanner.scan_stocks()
            self.assertEqual(len(items), 1)
            self.assertEqual(scanner.health['stocks']['history_requested'], 0)
            self.assertEqual(scanner.health['stocks']['history_not_requested'], 1)
            ticker.assert_not_called()

    def test_report_scopes_no_setup_to_checked_candidates(self):
        text = build_market_snapshot([], [], {'stocks': {'listed_assets': 5000, 'selected_candidates': 60,
                   'shortlisted_candidates':150, 'history_requested':60, 'history_received':58,
                   'deferred_candidates':653}, 'crypto': {}}, NOW)
        self.assertTrue(text.startswith('NO ACTIONABLE SETUP AMONG CHECKED CANDIDATES'))
        self.assertIn('Stock charts: 58/60 received', text)
        self.assertIn('deferred: 653', text)


if __name__ == '__main__':
    unittest.main()

