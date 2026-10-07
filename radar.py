"""Research snapshots, with final trading decisions delegated to the event task."""
import logging
import os
from datetime import datetime, timezone
import requests
from zoneinfo import ZoneInfo
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


def enrich(items, brokers=None):
    for item in items:
        catalyst = item.get('catalyst') or find_catalyst_for_asset(item.get('ticker', item.get('symbol')), item['asset_type'], item['name'])
        item['broker_availability'] = brokers.check(item) if brokers else {'status': 'UNVERIFIED', 'reason': 'Broker catalogue not loaded.'}
        item['catalyst'] = catalyst
        item['score'] = score_setup(item, catalyst)
        item['qualification'] = classify_setup(item, catalyst)
        q = item['qualification']
        if catalyst.get('headlines') and q['status'] not in {'ACTIONABLE NOW', 'WATCH FOR ENTRY', 'EXTENDED—DO NOT CHASE'}:
            item['qualification'] = {**q, 'status': 'CATALYST WATCH', 'trade_assessment_status': q['status'],
                                     'research_only': True, 'plan': None,
                                     'reasons': ['News lead for research; event timing and entry require verification.'] + q['reasons']}
    return sorted(items, key=lambda x: x['score']['score'], reverse=True)


def collect_stock_candidates(scanner):
    return enrich(scanner.scan_stocks(), getattr(scanner, 'brokers', None))


def collect_crypto_candidates(scanner):
    return enrich(scanner.scan_crypto(), getattr(scanner, 'brokers', None))


def irish_time(value):
    if not value:
        return 'unverified'
    if isinstance(value, str) and len(value) == 10:
        return value + ' (date only)'
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        if d.tzinfo is None:
            return 'unverified'
        return d.astimezone(ZoneInfo('Europe/Dublin')).strftime('%d %b %H:%M') + ' Irish time'
    except (TypeError, ValueError):
        return 'unverified'


def build_market_snapshot(stocks, crypto, health=None, now=None):
    from collections import Counter
    actions = [x for x in stocks + crypto if x['qualification']['status'] == 'ACTIONABLE NOW']
    watches = [x for x in stocks + crypto if x['qualification']['status'] == 'WATCH FOR ENTRY']
    symbols = lambda items: ', '.join(x.get('ticker', x.get('symbol')) for x in items)
    headline = ('🚨 CHECK NOW — ' + symbols(actions) + ' | provisional setup') if actions else ('👀 WATCH FOR ENTRY — ' + symbols(watches)) if watches else 'NO ACTIONABLE SETUP'
    lines = [headline, 'MARKET RADAR — ' + irish_time(now or datetime.now(timezone.utc))]
    for item in actions + watches:
        p = item['qualification'].get('plan')
        if p:
            lines.append(f"{item.get('ticker', item.get('symbol'))}: EUR entry {p['entry']:.4f} | SL {p['stop_loss']:.4f} | TP1 {p['tp1']:.4f} | TP2 {p['tp2']:.4f}")
    if actions:
        lines.append('Check live broker price, spread and source before acting.')
    if health:
        h = health['crypto']
        stock = health['stocks']
        lines.append('Stocks: ' + stock.get('status', 'UNKNOWN') + ' | session ' + stock.get('session', 'UNKNOWN') + ' | Crypto data: ' + h.get('status', 'UNKNOWN'))
        if 'listed_assets' in stock:
            lines.append(f"US listed universe: {stock['listed_assets']}; quote coverage: {stock.get('listed_snapshot_records', 0)}; news leads: {stock.get('catalyst_discovery', {}).get('matched_assets', 0)}; candidates: {stock.get('selected_candidates', 0)} checked / {stock.get('deferred_candidates', 0)} deferred.")
        lines.append(f"Crypto: {h.get('price_records', len(crypto))} price records; charts {h.get('history_received', 0)}/{h.get('history_requested', 0)} received, "
                     f"{h.get('chart_failures', 0)} failed, {h.get('history_not_requested', 0)} not requested.")
    counts = Counter(x['qualification']['status'] for x in stocks + crypto)
    lines.append('Results: ' + (', '.join(f'{v} {k}' for k, v in counts.items()) or 'No screened movers'))
    if health and health.get('brokers'):
        lines.append('Broker catalogues: ' + ', '.join(k + ' ' + v['status'] for k, v in health['brokers'].items()))
    lines.append('Discovery: broad stock snapshots + fresh events; crypto recent momentum + events.')
    blocks = []
    ordered = sorted(stocks + crypto, key=lambda x: ({'ACTIONABLE NOW': 3, 'WATCH FOR ENTRY': 2, 'CATALYST WATCH': 1}.get(x['qualification']['status'], 0), x['qualification']['status'] == 'CATALYST WATCH', x.get('catalyst', {}).get('published_at', ''), x.get('score', {}).get('score', 0)), reverse=True)
    watch_symbols = [x.get('ticker', x.get('symbol')) for x in ordered if x['qualification']['status'] == 'CATALYST WATCH']
    if watch_symbols:
        lines.append('Catalyst watch: ' + ', '.join(watch_symbols))
    for item in ordered:
        q, c = item['qualification'], item['catalyst']
        symbol = item.get('ticker', item.get('symbol'))
        change = item.get('change', item.get('change_24h', 0))
        quote_text = f"{item.get('currency', '')} {item['price']:.6g}" if item.get('price') is not None else 'Quote unavailable'
        block = [f"{symbol} ({item.get('name', symbol)}): {q['status']}",
                 f"{quote_text} | move {change:+.2f}% | observed {irish_time(item.get('observed_at'))}"]
        broker = item.get('broker_availability', {})
        block.append('Broker: ' + broker.get('broker', 'unverified') + ' | ' + broker.get('status', 'UNVERIFIED'))
        if c.get('title'):
            block.extend([c['summary'], f"{c['source']} | Published {irish_time(c['published_at'])} | Event: {irish_time(c.get('event_at'))}" + (' | ADVERSE EVENT' if c.get('adverse') else '')])
            # Full source URLs remain in the structured scan; long RSS redirects would hide other leads.
        elif c.get('retrieval_status') == 'FAILED':
            block.append(c['summary'])
        else:
            block.append(f"News: no qualifying event in last 24h ({c.get('headlines_checked', '?')} headlines checked).")
        if q['status'] == 'CATALYST WATCH':
            block.append('Research watch; no approved entry. ' + ('Market closed; assess at the regular open.' if item.get('session') == 'CLOSED' else '; '.join(q['reasons'][1:2])))
        else:
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
    lines.append('\nTrade plans require live broker/source verification. Full source links and details are in the scan artifact.')
    if os.environ.get('GITHUB_RUN_ID'):
        lines.append('https://github.com/' + os.environ.get('GITHUB_REPOSITORY', 'Chiizzie/market-radar') + '/actions/runs/' + os.environ['GITHUB_RUN_ID'])
    return '\n'.join(lines)
