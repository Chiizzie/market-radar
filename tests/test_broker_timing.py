import json
import unittest
from unittest.mock import patch, Mock
from datetime import datetime, timezone
from broker_availability import BrokerAvailability, parse_t212, parse_robinhood
from catalyst_scanner import classify_catalyst
from event_timing import event_timing
from publish_alert import material_change
from run_radar import build_potential_signals
from trade_filters import classify_setup
from test_radar import asset

NOW = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.b = BrokerAvailability(NOW)
        self.row = {'shortName': 'PENG', 'fullName': 'Penguin Solutions', 'ticker': 'SGH_US_EQ',
                    'type': 'STOCK', 'currency': 'USD', 'isin': 'US7069151055', 'maxOpenLong': 100, 'maxTrade': 100}
        self.b.catalogues = {'stock': [self.row], 'crypto': {'ZRO': 'LayerZero'}}
        self.stock = {'ticker': 'PENG', 'name': 'Penguin Solutions', 'asset_type': 'stock'}

    def test_metadata_api_is_read_only_and_credentials_never_in_result(self):
        response = Mock()
        response.json.return_value = [{'ticker': 'SGH_US_EQ', 'shortName': 'PENG', 'name': 'Penguin Solutions',
                                      'currencyCode': 'USD', 'type': 'STOCK', 'isin': 'US7069151055', 'maxOpenQuantity': 100}]
        with patch.dict('os.environ', {'TRADING212_API_KEY': 'example-key', 'TRADING212_API_SECRET': 'example-secret'}), \
             patch('broker_availability.requests.get', return_value=response) as get:
            b = BrokerAvailability(NOW).load()
        calls = [c for c in get.call_args_list if 'trading212' in c.args[0]]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], 'https://live.trading212.com/api/v0/equity/metadata/instruments')
        result = b.check(self.stock)
        self.assertEqual(result['status'], 'LISTED_API')
        self.assertNotIn('example-secret', json.dumps(b.health) + json.dumps(result))

    def test_renamed_instrument_matches_company_and_symbol(self):
        result = self.b.check(self.stock)
        self.assertEqual(result['status'], 'LISTED_PUBLIC')
        self.assertEqual(result['broker_instrument_id'], 'SGH_US_EQ')
        self.assertFalse(result['account_verified'])

    def test_collision_and_restriction_fail_closed(self):
        self.assertEqual(self.b.check({**self.stock, 'name': 'Wrong Company'})['status'], 'UNVERIFIED')
        self.row['dealerExclusions'] = ['T212CY']
        self.assertEqual(self.b.check(self.stock)['status'], 'UNVERIFIED')
        self.row['dealerExclusions'] = []
        self.row['maxOpenLong'] = 0
        self.assertEqual(self.b.check(self.stock)['status'], 'RESTRICTED')

    def test_missing_catalogue_not_claimed_unavailable(self):
        self.b.catalogues['stock'] = None
        self.assertEqual(self.b.check(self.stock)['status'], 'UNVERIFIED')

    def test_crypto_identity_and_region(self):
        item = {'symbol': 'ZRO', 'name': 'LayerZero', 'asset_type': 'crypto'}
        self.assertEqual(self.b.check(item)['status'], 'LISTED_PUBLIC')
        self.assertEqual(self.b.check({**item, 'name': 'Another Coin'})['status'], 'UNVERIFIED')
        self.assertEqual(self.b.check({**item, 'symbol': 'FAKE'})['status'], 'NOT_LISTED')

    def test_wrong_catalogue_and_partial_page_rejected(self):
        for data in [{'tradingType': 'cfd', 'instruments': [self.row]*1000}, {'tradingType': 'invest', 'instruments': [self.row]}]:
            html = '<script id="__NEXT_DATA__">' + json.dumps({'props': {'pageProps': data}}) + '</script>'
            with self.assertRaises(ValueError): parse_t212(html)
        with self.assertRaises(ValueError): parse_robinhood('<p>Buy BTC here</p>')

    def test_unsupported_not_published_unknown_remains_research(self):
        def item(status):
            return {'qualification': {'status': 'CATALYST WATCH'}, 'broker_availability': {'status': status}}
        rows = [item('NOT_LISTED'), item('RESTRICTED'), item('UNVERIFIED'), item('LISTED_PUBLIC')]
        self.assertEqual(build_potential_signals(rows, []), rows[2:])

    def test_unverified_broker_blocks_actionable(self):
        q = classify_setup({**asset(), 'broker_availability': {'status': 'UNVERIFIED'}},
                           {'eligible_for_points': True, 'event_time_status': 'REPORTED_RECENT'}, NOW)
        self.assertEqual(q['status'], 'BROKER_UNVERIFIED')
        self.assertIsNone(q['plan'])


class EventTimingTests(unittest.TestCase):
    def item(self, title):
        return {'title': title, 'published_at': NOW.isoformat(), 'source': 'News', 'link': 'https://example.com/news'}

    def test_republished_old_transcript_is_not_new_event(self):
        c = classify_catalyst([self.item('Penguin Solutions Q2 2026 earnings call transcript 2026-10-07')],
                             'PENG', 'Penguin Solutions', now=NOW)
        self.assertEqual(c['event_time_status'], 'RECAP_UNVERIFIED')
        self.assertIsNone(c['event_at'])
        self.assertFalse(c['eligible_for_points'])
        self.assertTrue(c['headlines'])

    def test_publication_date_and_fiscal_period_not_event_date(self):
        for title in ['Penguin Solutions reports Q4 2026 earnings', 'Penguin Solutions reports earnings 2026-10-07']:
            c = event_timing(self.item(title), NOW)
            self.assertEqual(c['event_time_status'], 'UNVERIFIED')
            self.assertIsNone(c['event_at'])

    def test_explicit_recent_older_and_upcoming_dates(self):
        for day, status in [('7', 'REPORTED_RECENT'), ('1', 'REPORTED_OLDER'), ('9', 'REPORTED_UPCOMING')]:
            c = event_timing(self.item('Penguin Solutions announces results on October ' + day + ', 2026'), NOW)
            self.assertEqual(c['event_time_status'], status)
            self.assertEqual(c['event_date_precision'], 'day')
        self.assertEqual(event_timing(self.item('Penguin Solutions announces results on 2026-10-07'), NOW)['event_time_status'], 'REPORTED_RECENT')

    def test_missing_event_time_cannot_qualify_even_with_score_credit(self):
        q = classify_setup(asset(), {'eligible_for_points': True}, NOW)
        self.assertNotEqual(q['status'], 'ACTIONABLE NOW')
        self.assertIsNone(q['plan'])

    def test_broker_or_timing_change_wakes_review_without_price_move(self):
        old = {'qualification': {'status': 'CATALYST WATCH', 'plan': None}, 'catalyst': {'id': 'same'}, 'broker_availability': {'status': 'UNVERIFIED'}}
        self.assertTrue(material_change(old, {**old, 'broker_availability': {'status': 'LISTED_PUBLIC'}}))
        self.assertTrue(material_change(old, {**old, 'catalyst': {'id': 'same', 'event_time_status': 'REPORTED_RECENT'}}))
        self.assertFalse(material_change(old, old))
