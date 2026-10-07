import copy
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pandas as pd
from catalyst_scanner import classify_catalyst
from market_scanner import stock_session, structure, MarketScanner
from scoring import score_setup
from trade_filters import classify_setup, ACTIONABLE, WATCH, EXTENDED, REJECTED
from publish_alert import prepare_alert

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)


def news(title, age=1):
    return {'title': title, 'source': 'Example News', 'link': 'https://example.com/event',
            'published_at': (NOW - timedelta(hours=age)).isoformat()}


def asset():
    return {'ticker': 'MRVL', 'asset_type': 'stock', 'price': 100, 'entry': 100, 'support': 99,
            'resistance_1': 103, 'resistance_2': 105, 'momentum_pct': .5, 'extension_pct': 1,
            'dollar_volume': 20_000_000, 'volume_spike': 2, 'session': 'REGULAR',
            'observed_at': NOW.isoformat(), 'currency': 'EUR'}


class CatalystTests(unittest.TestCase):
    def test_specific_event_and_details(self):
        c = classify_catalyst([news('Marvell raises revenue target to $20 billion')], 'MRVL', 'Marvell', now=NOW)
        self.assertTrue(c['eligible_for_points'])
        self.assertIn('$20 billion', c['summary'])
        self.assertEqual(c['source'], 'Example News')
        self.assertTrue(c['published_at'])

    def test_orca_collision(self):
        c = classify_catalyst([news('TD SYNNEX announces ORCA product launch')], 'ORCA', 'Orca', 'crypto', NOW)
        self.assertFalse(c['eligible_for_points'])
        self.assertEqual(score_setup({}, c)['score'], 0)

    def test_sec_substring_not_event(self):
        c = classify_catalyst([news('Marvell second generation chip demand')], 'MRVL', 'Marvell', now=NOW)
        self.assertFalse(c['eligible_for_points'])

    def test_stale_undated_future_speculative(self):
        for item in [news('Marvell earnings beat', 25), news('Marvell earnings beat', -2),
                     {**news('Marvell earnings beat'), 'published_at': None},
                     news('Marvell possible earnings beat'), news('Marvell price prediction after earnings')]:
            self.assertFalse(classify_catalyst([item], 'MRVL', 'Marvell', now=NOW)['eligible_for_points'])

    def test_adverse_and_duplicate(self):
        n = news('Marvell announces stock offering')
        c = classify_catalyst([n, n], 'MRVL', 'Marvell', now=NOW)
        self.assertFalse(c['eligible_for_points'])
        self.assertEqual(len(c['headlines']), 1)


class SessionTests(unittest.TestCase):
    def test_closed_weekend_holiday_early_close_dst(self):
        for date in ['2026-10-07T07:00', '2026-10-10T15:00', '2026-12-25T15:00', '2026-11-27T19:00']:
            self.assertEqual(stock_session(datetime.fromisoformat(date).replace(tzinfo=timezone.utc)), 'CLOSED')
        self.assertEqual(stock_session(NOW), 'REGULAR')
        self.assertEqual(stock_session(datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)), 'CLOSED')

    def test_closed_stocks_discover_news_without_fetching_charts(self):
        catalyst = {'headlines': [{'title': 'Penguin Solutions reports earnings'}], 'id': 'event1', 'eligible_for_points': True}
        with patch('discovery.listed_universe', return_value=({'PENG': 'Penguin Solutions'}, [])), \
             patch('discovery.broad_quotes', return_value=([], {'pagination_complete': True, 'errors': []})), \
             patch('discovery.discover_catalysts', return_value=({'PENG': catalyst}, {'failed_queries': 0})), \
             patch('market_scanner.yf.Ticker') as ticker:
            scanner = MarketScanner(datetime(2026, 10, 7, 7, tzinfo=timezone.utc))
            result = scanner.scan_stocks()
            self.assertEqual(result[0]['ticker'], 'PENG')
            self.assertEqual(result[0]['discovery_route'], 'catalyst_first')
            ticker.assert_not_called()

    def test_no_fabricated_targets(self):
        frame = pd.DataFrame({'High': [100] * 40, 'Low': [99] * 40, 'Close': [100] * 40, 'Volume': [1000] * 40})
        result = structure(frame)
        self.assertEqual(result['structure_status'], 'INCOMPLETE')
        self.assertEqual(result['entry'], 100)
        self.assertEqual(result['support'], 99)
        self.assertIn('momentum_pct', result)
        self.assertNotIn('resistance_1', result)
        self.assertIn('No historical resistance', result['structure_issues'][0])


class QualificationTests(unittest.TestCase):
    def test_valid_and_risk_in_eur(self):
        q = classify_setup(asset(), {'eligible_for_points': True}, NOW)
        self.assertEqual(q['status'], ACTIONABLE)
        self.assertLessEqual(q['plan']['planned_loss_eur'], 5)
        self.assertLessEqual(q['plan']['position_eur'], 500)

    def test_watch_extended_rejected(self):
        self.assertEqual(classify_setup({**asset(), 'price': 99.8}, {'eligible_for_points': True}, NOW)['status'], WATCH)
        self.assertEqual(classify_setup({**asset(), 'extension_pct': 4}, {'eligible_for_points': True}, NOW)['status'], EXTENDED)
        self.assertEqual(classify_setup(asset(), {'eligible_for_points': False}, NOW)['status'], REJECTED)

    def test_data_momentum_rr_liquidity_and_fx(self):
        mutations = [{'observed_at': (NOW - timedelta(minutes=16)).isoformat()}, {'momentum_pct': -.1},
                     {'resistance_1': 100.2}, {'support': 101}, {'dollar_volume': 1},
                     {'price': float('nan')}, {'currency': 'USD'}, {'session': 'CLOSED'}]
        for change in mutations:
            with self.subTest(change=change):
                self.assertEqual(classify_setup({**asset(), **change}, {'eligible_for_points': True}, NOW)['status'], REJECTED)

    def test_dublin_cutoff_summer_and_winter(self):
        for now in [datetime(2026, 10, 7, 20, 40, tzinfo=timezone.utc), datetime(2026, 11, 2, 21, 40, tzinfo=timezone.utc)]:
            self.assertEqual(classify_setup({**asset(), 'observed_at': now.isoformat()}, {'eligible_for_points': True}, now)['status'], REJECTED)


class DedupeTests(unittest.TestCase):
    def payload(self):
        a = asset()
        a['catalyst'] = {'id': 'news1', 'eligible_for_points': True}
        a['qualification'] = classify_setup(a, a['catalyst'], NOW)
        return {'generated_at': NOW.isoformat(), 'potential_signals': [a]}

    def test_timestamp_changes_and_minor_drift_suppressed(self):
        p = self.payload()
        previous = prepare_alert(p, {})
        p['generated_at'] = (NOW + timedelta(minutes=15)).isoformat()
        p['potential_signals'][0]['price'] *= 1.002
        self.assertIsNone(prepare_alert(p, previous))

    def test_material_price_catalyst_and_invalidation(self):
        previous = prepare_alert(self.payload(), {})
        p = self.payload()
        p['potential_signals'][0]['price'] *= 1.02
        self.assertIsNotNone(prepare_alert(p, previous))
        p = self.payload()
        p['potential_signals'][0]['catalyst']['id'] = 'news2'
        self.assertIsNotNone(prepare_alert(p, previous))
        clear = prepare_alert({'generated_at': NOW.isoformat(), 'potential_signals': []}, previous)
        self.assertEqual(clear['invalidated_signals'], ['stock:MRVL'])
        self.assertIsNone(prepare_alert({'generated_at': NOW.isoformat(), 'potential_signals': []}, clear))


class DataReportingTests(unittest.TestCase):
    def test_missing_chart_is_not_a_market_rejection(self):
        for state, expected in [('FAILED', 'DATA_UNAVAILABLE'), ('STALE', 'DATA_UNAVAILABLE'), ('NOT_REQUESTED', 'NOT_EVALUATED')]:
            item = {**asset(), 'history_status': state, 'data_issue': 'specific chart issue'}
            item.pop('entry')
            q = classify_setup(item, {'eligible_for_points': False}, NOW)
            self.assertEqual(q['status'], expected)
            self.assertIn('specific chart issue', q['reasons'])
            self.assertIn('entry', q['missing_fields'])

    def test_mixed_chart_results_and_report(self):
        from unittest.mock import Mock
        import requests
        from radar import build_market_snapshot
        coins = [{'id': f'coin-{i}', 'symbol': f'C{i}', 'name': f'Coin {i}', 'current_price': 100,
                  'total_volume': 60_000_000 - i * 1_000_000, 'price_change_percentage_24h': 3,
                  'last_updated': NOW.isoformat(), 'price_change_percentage_1h_in_currency': 1} for i in range(6)]
        prices = [[int((NOW - timedelta(hours=48-i)).timestamp()*1000), 100] for i in range(48)]
        market = Mock(); market.json.return_value = coins
        chart = Mock(); chart.json.return_value = {'prices': prices}
        failed = Mock(); failed.status_code = 429
        failed.raise_for_status.side_effect = requests.HTTPError(response=failed)
        with patch('discovery.discover_catalysts', return_value=({}, {})), patch('market_scanner.requests.get', side_effect=[market, chart, chart, chart, chart, failed]) as get:
            scanner = MarketScanner(NOW)
            items = scanner.scan_crypto()
            self.assertEqual(get.call_count, 6)
        h = scanner.health['crypto']
        self.assertEqual(h['status'], 'PARTIAL')
        self.assertEqual((h['price_records'], h['history_received'], h['chart_failures'], h['history_not_requested']), (6, 4, 1, 1))
        self.assertEqual(items[4]['data_issue'], 'Chart request failed: HTTP 429')
        self.assertEqual(items[5]['history_status'], 'NOT_REQUESTED')
        for item in items:
            item['catalyst'] = {'eligible_for_points': False, 'retrieval_status': 'OK', 'headlines_checked': 3}
            item['qualification'] = classify_setup(item, item['catalyst'], NOW)
        message = build_market_snapshot([], items, scanner.health)
        for value in ['6 price records', 'charts 4/5 received', '1 failed', '1 not requested', 'Coin 0', 'DATA_UNAVAILABLE', 'NOT_EVALUATED']:
            self.assertIn(value, message)
        self.assertNotIn('Source: none', message)
        self.assertNotIn('Insufficient intraday structure', message)

    def test_failed_news_is_visible(self):
        q = classify_setup(asset(), {'eligible_for_points': False, 'retrieval_status': 'FAILED'}, NOW)
        self.assertEqual(q['status'], 'DATA_UNAVAILABLE')
        self.assertIn('News retrieval failed; catalyst could not be assessed.', q['reasons'])


if __name__ == '__main__':
    unittest.main()
