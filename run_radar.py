"""Run the scanner, save a machine-readable snapshot, and keep Telegram as backup."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from radar import (
    MarketScanner,
    build_market_snapshot,
    collect_crypto_candidates,
    collect_stock_candidates,
    telegram,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_potential_signals(stocks, crypto):
    """Broad pre-filter only; ChatGPT makes the final trade decision."""
    signals = []

    for item in stocks:
        score = item.get("score", {}).get("score", 0)
        if (
            score >= 5
            and item.get("change", 0) > 0
            and item.get("volume_spike", 0) >= 1.5
        ):
            signals.append({"asset_type": "stock", **item})

    for item in crypto:
        score = item.get("score", {}).get("score", 0)
        if (
            score >= 3
            and item.get("change_24h", 0) >= 3
            and item.get("volume", 0) >= 5_000_000
        ):
            signals.append({"asset_type": "crypto", **item})

    return signals


def main():
    scanner = MarketScanner()
    stocks = collect_stock_candidates(scanner)
    crypto = collect_crypto_candidates(scanner)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan_interval_minutes": 15,
        "stocks": stocks,
        "crypto": crypto,
        "potential_signals": build_potential_signals(stocks, crypto),
        "notice": (
            "Scanner candidates are leads only. ChatGPT must verify live prices, "
            "news, liquidity, risk/reward, and the user's trading rules."
        ),
    }

    Path("latest_scan.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    logger.info("Saved latest_scan.json with %d potential signals", len(payload["potential_signals"]))

    # The OpenAI API key is deliberately not passed by the workflow. ChatGPT's
    # event task performs the final review; Telegram remains a scan-only backup.
    telegram(build_market_snapshot(stocks, crypto))


if __name__ == "__main__":
    main()
