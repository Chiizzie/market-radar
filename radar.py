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
    from collections import Counter
    lines = ['MARKET RADAR V2 — ' + datetime.now(timezone.utc).strftime('%H:%M UTC')]
    if health:
        h = health['crypto']
        lines.append('Stocks: ' + health['stocks'].get('status', 'UNKNOWN') + ' | Crypto data: ' + h.get('status', 'UNKNOWN'))
        lines.append(f"Crypto: {h.get('price_records', len(crypto))} price records; charts {h.get('history_received', 0)}/{h.get('history_requested', 0)} received, "
                     f"{h.get('chart_failures', 0)} failed, {h.get('history_not_requested', 0)} not requested.")
    counts = Counter(x['qualification']['status'] for x in stocks + crypto)
    lines.append('Results: ' + (', '.join(f'{v} {k}' for k, v in counts.items()) or 'No screened movers'))
    lines.append('Movers enter the screen on price/volume; news and chart checks follow.')
    blocks = []
    for item in stocks + crypto:
        q, c = item['qualification'], item['catalyst']
        symbol = item.get('ticker', item.get('symbol'))
        change = item.get('change', item.get('change_24h', 0))
        block = [f"{symbol} ({item.get('name', symbol)}): {q['status']}",
                 f"{item.get('currency', '')} {item['price']:.6g} | move {change:+.2f}% | observed {item.get('observed_at', 'unknown')}"]
        if c.get('title'):
            block.extend([c['summary'], f"{c['source']} | {c['published_at']}" + (' | ADVERSE EVENT' if c.get('adverse') else '')])
            if c.get('link'):
                block.append(c['link'])
        elif c.get('retrieval_status') == 'FAILED':
            block.append(c['summary'])
        else:
            block.append(f"News: no qualifying event in last 24h ({c.get('headlines_checked', '?')} headlines checked).")
        block.append('; '.join(q['reasons']))
        if q['plan']:
            p = q['plan']
            block.append(f"EUR entry {p['entry']:.4f}, SL {p['stop_loss']:.4f}, TP1 {p['tp1']:.4f}, TP2 {p['tp2']:.4f}; R:R {p['rr1']}/{p['rr2']}")
        blocks.append('\n'.join(block))
    # Preserve full asset blocks instead of cutting a headline or link mid-string.
    for i, block in enumerate(blocks):
        if len('\n'.join(lines)) + len(block) > 3650:
            lines.append(f'Additional candidates: {len(blocks) - i}. Full details in the saved scan artifact.')
            break
        lines.extend(['', block])
    lines.append('\nTrade plans require live broker/source verification. Data failures are not trade rejections.')
    return '\n'.join(lines)
