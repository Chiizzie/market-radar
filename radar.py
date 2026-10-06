import os
import logging
from datetime import datetime, timezone

import requests

from market_scanner import MarketScanner
from catalyst_scanner import find_catalyst_for_asset
from scoring import score_setup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

MAX_ALERTS = 8


def telegram(message: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        logger.error("Telegram bot credentials are missing.")
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": TG_CHAT,
                "text": message,
                "disable_web_page_preview": True,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.exception("Telegram send failed: %s", exc)


def build_market_snapshot(stocks, crypto):
    timestamp = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [
        "🚨 MARKET RADAR",
        f"Scan: {timestamp}",
        "",
    ]

    if stocks:
        lines.append("📈 STOCK WATCHLIST")
        for item in stocks:
            score = item.get("score", {}).get("score", 0)
            catalyst = item.get("catalyst", {})
            catalyst_text = catalyst.get("summary", "Macro flow / rotation")
            lines.append(
                f"• {item['ticker']} | {item['change']:.1f}% | Vol {item['volume']:,} | "
                f"score {score}/10 | {catalyst_text}"
            )
    else:
        lines.append("📈 Stocks: no strong setups")

    lines.append("")

    if crypto:
        lines.append("🪙 CRYPTO WATCHLIST")
        for item in crypto:
            score = item.get("score", {}).get("score", 0)
            catalyst = item.get("catalyst", {})
            catalyst_text = catalyst.get("summary", "Momentum / macro rotation")
            lines.append(
                f"• {item['symbol']} | {item['change_24h']:.1f}% | Vol ${item['volume']:,.0f} | "
                f"score {score}/10 | {catalyst_text}"
            )
    else:
        lines.append("🪙 Crypto: no strong setups")

    lines.extend([
        "",
        "⚠️ RADAR ONLY — not financial advice.",
        "Use this for catalyst-aware monitoring, not as a trade recommendation.",
    ])
    return "\n".join(lines)


def collect_stock_candidates(scanner: MarketScanner):
    candidates = []
    for item in scanner.scan_stocks()[:MAX_ALERTS]:
        ticker = item["ticker"]
        fundamentals = scanner.get_stock_fundamentals(ticker)
        earnings = scanner.get_stock_earnings_date(ticker)
        catalyst = find_catalyst_for_asset(ticker, asset_type="stock")
        score = score_setup(
            item,
            catalyst,
            fundamentals=fundamentals,
            near_term_event=earnings,
        )

        item["fundamentals"] = fundamentals
        item["earnings"] = earnings
        item["catalyst"] = catalyst
        item["score"] = score
        candidates.append(item)

    candidates.sort(key=lambda x: x["score"]["score"], reverse=True)
    return candidates[:MAX_ALERTS]


def collect_crypto_candidates(scanner: MarketScanner):
    candidates = []
    for item in scanner.scan_crypto()[:MAX_ALERTS]:
        symbol = item["symbol"]
        catalyst = find_catalyst_for_asset(symbol, asset_type="crypto")
        score = score_setup(item, catalyst, fundamentals=None)
        item["catalyst"] = catalyst
        item["score"] = score
        candidates.append(item)

    candidates.sort(key=lambda x: x["score"]["score"], reverse=True)
    return candidates[:MAX_ALERTS]


def main():
    scanner = MarketScanner()
    stocks = collect_stock_candidates(scanner)
    crypto = collect_crypto_candidates(scanner)
    message = build_market_snapshot(stocks, crypto)
    telegram(message)


if __name__ == "__main__":
    main()
