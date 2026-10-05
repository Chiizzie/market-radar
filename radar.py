import os
import requests
from datetime import datetime, timezone

AV_KEY = os.environ["ALPHA_VANTAGE_API_KEY"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

# Initial filters — we'll tune these after observing real alerts.
STOCK_MIN_CHANGE = 5.0
STOCK_MIN_VOLUME = 500_000

CRYPTO_MIN_CHANGE = 3.0
CRYPTO_MIN_QUOTE_VOLUME = 5_000_000

MAX_ALERTS = 8


def telegram(message):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": TG_CHAT,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    r.raise_for_status()


def stock_scan():
    """
    Alpha Vantage TOP_GAINERS_LOSERS gives us broad discovery
    without requesting hundreds of individual tickers.
    """
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TOP_GAINERS_LOSERS",
        "apikey": AV_KEY,
    }

    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    candidates = []

    for stock in data.get("top_gainers", []):
        try:
            ticker = stock["ticker"]
            price = float(stock["price"])
            change = float(stock["change_percentage"].replace("%", ""))
            volume = int(stock["volume"])

            if (
                change >= STOCK_MIN_CHANGE
                and volume >= STOCK_MIN_VOLUME
            ):
                candidates.append(
                    {
                        "ticker": ticker,
                        "price": price,
                        "change": change,
                        "volume": volume,
                    }
                )
        except (KeyError, ValueError, TypeError):
            continue

    return candidates[:MAX_ALERTS]


def crypto_scan():
    """
    Binance public 24h ticker feed.
    No Binance API key required.
    """
    url = "https://api.binance.com/api/v3/ticker/24hr"

    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()

    candidates = []

    # Stick primarily to liquid USDT pairs.
    excluded = (
        "UPUSDT",
        "DOWNUSDT",
        "BULLUSDT",
        "BEARUSDT",
    )

    for coin in data:
        try:
            symbol = coin["symbol"]

            if not symbol.endswith("USDT"):
                continue

            if any(symbol.endswith(x) for x in excluded):
                continue

            change = float(coin["priceChangePercent"])
            quote_volume = float(coin["quoteVolume"])
            price = float(coin["lastPrice"])

            if (
                abs(change) >= CRYPTO_MIN_CHANGE
                and quote_volume >= CRYPTO_MIN_QUOTE_VOLUME
            ):
                candidates.append(
                    {
                        "symbol": symbol,
                        "price": price,
                        "change": change,
                        "volume": quote_volume,
                    }
                )
        except (KeyError, ValueError, TypeError):
            continue

    candidates.sort(
        key=lambda x: abs(x["change"]),
        reverse=True
    )

    return candidates[:MAX_ALERTS]


def build_message(stocks, crypto):
    timestamp = datetime.now(timezone.utc).strftime("%H:%M UTC")

    lines = [
        "🚨 MARKET RADAR",
        f"Scan: {timestamp}",
        "",
    ]

    if stocks:
        lines.append("📈 STOCK MOVERS")
        for x in stocks:
            lines.append(
                f"{x['ticker']} | "
                f"${x['price']:.4f} | "
                f"+{x['change']:.1f}% | "
                f"Vol {x['volume']:,}"
            )
    else:
        lines.append("📈 Stocks: no qualifying candidates")

    lines.append("")

    if crypto:
        lines.append("🪙 CRYPTO MOVERS")
        for x in crypto:
            sign = "+" if x["change"] >= 0 else ""
            lines.append(
                f"{x['symbol']} | "
                f"{x['price']:.6g} | "
                f"{sign}{x['change']:.1f}% | "
                f"${x['volume']:,.0f} 24h volume"
            )
    else:
        lines.append("🪙 Crypto: no qualifying candidates")

    lines.extend([
        "",
        "⚠️ RADAR ONLY — not a trade signal.",
        "Send interesting ticker(s) to ChatGPT for catalyst, dilution, live-price and R:R analysis."
    ])

    return "\n".join(lines)


def main():
    stocks = stock_scan()
    crypto = crypto_scan()

    # For initial testing we send every run.
    # Once confirmed working we'll change this to candidate-only alerts.
    telegram(build_message(stocks, crypto))


if __name__ == "__main__":
    main()
