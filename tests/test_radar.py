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

    def test_closed_stocks_make_no_data_requests(self):
        with patch('market_scanner.yf.Ticker') as ticker:
            s = MarketScanner(datetime(2026, 10, 7, 7, tzinfo=timezone.utc))
            self.assertEqual(s.scan_stocks(), [])
            ticker.assert_not_called()

    def test_no_fabricated_targets(self):
        frame = pd.DataFrame({'High': [100] * 40, 'Low': [99] * 40, 'Close': [100] * 40, 'Volume': [1000] * 40})
        self.assertEqual(structure(frame), {})


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


if __name__ == '__main__':
    unittest.main()
