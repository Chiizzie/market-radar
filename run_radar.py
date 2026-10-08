"""Save every scan for diagnosis; only qualified leads enter the event bridge."""
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from broker_availability import BrokerAvailability
from scan_state import load_state, cadence, finish_state, persist_state
from radar import MarketScanner, collect_stock_candidates, collect_crypto_candidates, build_market_snapshot, telegram, irish_time


def build_potential_signals(stocks, crypto):
    return [item for item in stocks + crypto if item.get('qualification', {}).get('status') in {'ACTIONABLE NOW', 'WATCH FOR ENTRY', 'CATALYST WATCH', 'TECHNICAL WATCH'}
            and item.get('broker_availability', {}).get('status') not in {'NOT_LISTED', 'RESTRICTED'}]


def main():
    logging.basicConfig(level=logging.INFO)
    state, state_health, parent = load_state()
    scanner = MarketScanner()
    scanner.selection_state = state
    scanner.health['cadence'] = cadence(state, scanner.now)
    scanner.brokers = BrokerAvailability().load()
    scanner.health['brokers'] = scanner.brokers.health
    stocks, crypto = collect_stock_candidates(scanner), collect_crypto_candidates(scanner)
    finish_state(state, stocks+crypto, scanner.now)
    scanner.health['rotation_state'] = persist_state(state, state_health, parent)
    Path('chart_samples.json').write_text(json.dumps({'captured_at':scanner.now.isoformat(),'charts':scanner.chart_samples},allow_nan=False)+'\n')
    scanner.health['news'] = {'checked': len(stocks + crypto),
                              'failed': sum(x['catalyst'].get('retrieval_status') == 'FAILED' for x in stocks + crypto)}
    payload = {'schema_version': 2, 'generated_at': datetime.now(timezone.utc).isoformat(),
               'scan_interval_minutes': 15, 'stocks': stocks, 'crypto': crypto,
               'assessment_diagnostics': {'routes': dict(Counter(x['qualification'].get('route') for x in stocks + crypto)),
                   'chart_patterns': dict(Counter(p['pattern'] for x in stocks + crypto for p in x.get('chart_analysis', {}).get('setups', []))),
                   'near_misses': [{'symbol': x.get('ticker', x.get('symbol')), 'pattern': x['qualification'].get('pattern'), 'blockers': x['qualification'].get('blockers')} for x in stocks + crypto if x['qualification']['status'] == 'TECHNICAL WATCH']},
               'health': scanner.health, 'potential_signals': build_potential_signals(stocks, crypto),
               'tracking_prices': scanner.tracking_prices + stocks + crypto,
               'display_timezone': 'Europe/Dublin', 'generated_at_display': irish_time(datetime.now(timezone.utc)),
               'classifications': dict(Counter(x['qualification']['status'] for x in stocks + crypto)),
               'notice': 'Scores rank research only. ACTIONABLE NOW is provisional and requires live price, broker availability, spread, fees and source verification.'}
    payload['telegram_delivery'] = telegram(build_market_snapshot(stocks, crypto, scanner.health))
    Path('latest_scan.json').write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
    print('Scan health:', json.dumps(scanner.health))
    print('Classifications:', json.dumps(payload['classifications']))
    summary = __import__('os').environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as f:
            f.write('## Market Radar V2\n\n```json\n' + json.dumps({'health': scanner.health, 'classifications': payload['classifications'], 'telegram_delivery': payload['telegram_delivery']}, indent=2) + '\n```\n')


if __name__ == '__main__':
    main()
