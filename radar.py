import os
import json
import logging
from datetime import datetime, timezone

import requests
from openai import OpenAI

from market_scanner import MarketScanner
from catalyst_scanner import find_catalyst_for_asset
from scoring import score_setup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

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


def get_ai_verdict(candidates, asset_type="stock"):
    """Send market candidates to ChatGPT for trade analysis."""
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set, skipping AI analysis")
        return None

    client = OpenAI(api_key=OPENAI_API_KEY)

    # Format candidates into a readable prompt
    candidates_text = "\n".join([
        f"• {item.get('ticker' if asset_type == 'stock' else 'symbol')}: "
        f"Change: {item.get('change' if asset_type == 'stock' else 'change_24h'):.1f}%, "
        f"Volume spike: {item.get('volume_spike', 'N/A')}, "
        f"Score: {item.get('score', {}).get('score', 0)}/10, "
        f"Catalyst: {item.get('catalyst', {}).get('summary', 'No data')}"
        for item in candidates[:3]  # Top 3 only for brevity
    ])

    prompt = f"""You are a professional trading analyst. Review these {asset_type} market opportunities and provide a structured verdict.

{candidates_text}

For the TOP opportunity, return ONLY valid JSON in this exact format (no markdown, no extra text):
{{
  "asset": "ticker or symbol",
  "verdict": "BUY" or "WAIT" or "SKIP",
  "entry_price": 123.45,
  "stop_loss": 122.00,
  "take_profit_1": 125.00,
  "take_profit_2": 127.50,
  "confidence": 0.75,
  "reason": "Short explanation of the trade setup"
}}

Be realistic. If there's no clear setup, return {{"verdict": "SKIP", "reason": "No clear risk/reward"}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a disciplined trading analyst. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500,
        )

        content = response.choices[0].message.content.strip()
        verdict = json.loads(content)
        return verdict

    except json.JSONDecodeError:
        logger.error("ChatGPT returned invalid JSON: %s", content)
        return None
    except Exception as exc:
        logger.exception("ChatGPT API failed: %s", exc)
        return None


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
        
        # Add AI verdict for top stock
        stock_verdict = get_ai_verdict(stocks, asset_type="stock")
        if stock_verdict and stock_verdict.get("verdict") != "SKIP":
            lines.extend([
                "",
                "🤖 AI VERDICT (Stocks):",
                f"Asset: {stock_verdict.get('asset')}",
                f"Verdict: {stock_verdict.get('verdict')} (Confidence: {stock_verdict.get('confidence', 0):.0%})",
                f"Entry: ${stock_verdict.get('entry_price')}",
                f"SL: ${stock_verdict.get('stop_loss')} | TP1: ${stock_verdict.get('take_profit_1')} | TP2: ${stock_verdict.get('take_profit_2')}",
                f"Reason: {stock_verdict.get('reason')}",
            ])
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
        
        # Add AI verdict for top crypto
        crypto_verdict = get_ai_verdict(crypto, asset_type="crypto")
        if crypto_verdict and crypto_verdict.get("verdict") != "SKIP":
            lines.extend([
                "",
                "🤖 AI VERDICT (Crypto):",
                f"Asset: {crypto_verdict.get('asset')}",
                f"Verdict: {crypto_verdict.get('verdict')} (Confidence: {crypto_verdict.get('confidence', 0):.0%})",
                f"Entry: ${crypto_verdict.get('entry_price')}",
                f"SL: ${crypto_verdict.get('stop_loss')} | TP1: ${crypto_verdict.get('take_profit_1')} | TP2: ${crypto_verdict.get('take_profit_2')}",
                f"Reason: {crypto_verdict.get('reason')}",
            ])
    else:
        lines.append("🪙 Crypto: no strong setups")

    lines.extend([
        "",
        "⚠️ RADAR + AI ANALYSIS — not financial advice.",
        "Use for research only. Verify setups independently.",
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
