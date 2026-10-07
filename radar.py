"""Research snapshots, with final trading decisions delegated to the event task."""
import logging
import os
from datetime import datetime, timezone
import requests
from market_scanner import MarketScanner
from catalyst_scanner import find_catalyst_for_asset
from scoring import score_setup
from trade_filters import classify_setup

logger = logging.getLogger(__name__)


def telegram(message):
    token, chat = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        return 'NOT_CONFIGURED'
    try:
        # Plain text avoids invalid HTML from headlines; no secret-bearing URL in errors.
        r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                          json={'chat_id': chat, 'text': message[:4000], 'disable_web_page_preview': True}, timeout=15)
        r.raise_for_status()
        if not r.json().get('ok'):
            return 'FAILED'
        return 'API_ACCEPTED'
    except Exception as exc:
        logger.error('Telegram delivery failed (%s)', type(exc).__name__)
        return 'FAILED'


def enrich(items):
    for item in items:
        catalyst = find_catalyst_for_asset(item.get('ticker', item.get('symbol')), item['asset_type'], item['name'])
        item['catalyst'] = catalyst
        item['score'] = score_setup(item, catalyst)
        item['qualification'] = classify_setup(item, catalyst)
    return sorted(items, key=lambda x: x['score']['score'], reverse=True)


def collect_stock_candidates(scanner):
    return enrich(scanner.scan_stocks())


def collect_crypto_candidates(scanner):
    return enrich(scanner.scan_crypto())


def build_market_snapshot(stocks, crypto, health=None):
    lines = ['MARKET RADAR V2 — ' + datetime.now(timezone.utc).strftime('%H:%M UTC')]
    if health:
        lines.append('Stocks: ' + health['stocks'].get('status', 'UNKNOWN') + ' | Crypto: ' + health['crypto'].get('status', 'UNKNOWN'))
    for item in (stocks + crypto)[:8]:
        q, c = item['qualification'], item['catalyst']
        lines.extend(['', f"{item.get('ticker', item.get('symbol'))}: {q['status']}",
                      c['summary'], f"Source: {c.get('source', 'none')} | Published: {c.get('published_at', 'unverified')}",
                      '; '.join(q['reasons'])])
        if c.get('link'):
            lines.append(c['link'])
        if q['plan']:
            p = q['plan']
            lines.append(f"EUR entry {p['entry']:.4f}, SL {p['stop_loss']:.4f}, TP1 {p['tp1']:.4f}, TP2 {p['tp2']:.4f}; R:R {p['rr1']}/{p['rr2']}")
    lines.append('\nScanner classification is provisional. ChatGPT must verify live broker conditions before an alert.')
    return '\n'.join(lines)
