"""Save every scan for diagnosis; only qualified leads enter the event bridge."""
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from radar import MarketScanner, collect_stock_candidates, collect_crypto_candidates, build_market_snapshot, telegram


def build_potential_signals(stocks, crypto):
    return [item for item in stocks + crypto if item.get('qualification', {}).get('status') in {'ACTIONABLE NOW', 'WATCH FOR ENTRY'}]


def main():
    logging.basicConfig(level=logging.INFO)
    scanner = MarketScanner()
    stocks, crypto = collect_stock_candidates(scanner), collect_crypto_candidates(scanner)
    scanner.health['news'] = {'checked': len(stocks + crypto),
                              'failed': sum(x['catalyst'].get('retrieval_status') == 'FAILED' for x in stocks + crypto)}
    payload = {'schema_version': 2, 'generated_at': datetime.now(timezone.utc).isoformat(),
               'scan_interval_minutes': 15, 'stocks': stocks, 'crypto': crypto,
               'health': scanner.health, 'potential_signals': build_potential_signals(stocks, crypto),
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
