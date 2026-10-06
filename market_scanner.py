import logging
from datetime import datetime
from typing import Dict, List, Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

STOCK_MIN_VOLUME_SPIKE = 1.5
STOCK_MIN_CHANGE = 3.0
CRYPTO_MIN_CHANGE = 2.0
CRYPTO_MIN_VOLUME = 1_000_000
MAX_CANDIDATES = 20


class MarketScanner:
    """Scan the market for unusual movement and momentum signals."""

    def __init__(self):
        self.sp500_tickers = self._get_sp500_tickers()

    def _get_sp500_tickers(self) -> List[str]:
        fallback = [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "GOOG",
            "BRK.B", "JNJ", "V", "WMT", "JPM", "MA", "PG", "HD", "DIS", "PYPL",
            "ADBE", "NFLX", "CRM", "AMD", "INTC", "IBM", "CSCO", "ORCL", "SAP",
            "SMCI", "MSTR", "MRVL", "AVGO", "QCOM", "ARM", "AMGN", "NKE", "COST",
            "ABBV", "XOM", "COP", "CVX", "PFE", "LLY", "UNH", "RTX", "CAT", "HON",
        ]
        try:
            import pandas as pd
            try:
                tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
                if tables:
                    df = tables[0]
                    if "Symbol" in df.columns:
                        return df["Symbol"].dropna().astype(str).tolist()
            except Exception:
                pass
        except Exception:
            pass

        return fallback

    def scan_stocks(self) -> List[Dict]:
        movers = []

        for ticker in self.sp500_tickers[:100]:
            try:
                hist = yf.Ticker(ticker).history(period="60d")
                if hist.empty or len(hist) < 20:
                    continue

                current_price = float(hist["Close"].iloc[-1])
                prev_close = float(hist["Close"].iloc[-2])
                change_pct = ((current_price - prev_close) / prev_close) * 100
                current_volume = int(hist["Volume"].iloc[-1])
                avg_volume_20d = float(hist["Volume"].iloc[-20:].mean())
                volume_spike = current_volume / avg_volume_20d if avg_volume_20d > 0 else 0.0

                delta = hist["Close"].diff().dropna()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss if loss.iloc[-1] != 0 else 0
                rsi = 100 - (100 / (1 + rs))

                if abs(change_pct) >= STOCK_MIN_CHANGE or volume_spike >= STOCK_MIN_VOLUME_SPIKE:
                    movers.append(
                        {
                            "ticker": ticker,
                            "price": current_price,
                            "change": float(change_pct),
                            "volume": current_volume,
                            "avg_volume_20d": int(avg_volume_20d),
                            "volume_spike": float(volume_spike),
                            "rsi": float(rsi.iloc[-1]),
                            "source": "yfinance",
                            "activity_type": "volume_spike" if volume_spike >= STOCK_MIN_VOLUME_SPIKE else "price_move",
                        }
                    )
            except Exception as exc:
                logger.debug("Failed to scan %s: %s", ticker, exc)
                continue

        movers.sort(key=lambda x: abs(x["change"]) * x["volume_spike"], reverse=True)
        return movers[:MAX_CANDIDATES]

    def scan_crypto(self) -> List[Dict]:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "sparkline": False,
            "price_change_percentage": "24h,7d",
        }

        try:
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as exc:
            logger.exception("CoinGecko request failed: %s", exc)
            return []

        movers = []

        for coin in data:
            try:
                symbol = str(coin["symbol"]).upper()
                price = float(coin["current_price"])
                change_24h = float(coin.get("price_change_percentage_24h") or 0)
                change_7d = float(coin.get("price_change_percentage_7d") or 0)
                volume = float(coin.get("total_volume") or 0)
                market_cap = float(coin.get("market_cap") or 0)

                if volume < CRYPTO_MIN_VOLUME:
                    continue

                if abs(change_24h) >= CRYPTO_MIN_CHANGE:
                    movers.append(
                        {
                            "symbol": symbol,
                            "price": price,
                            "change_24h": float(change_24h),
                            "change_7d": float(change_7d),
                            "volume": float(volume),
                            "market_cap": float(market_cap),
                            "momentum_divergence": abs(change_7d - change_24h),
                            "source": "coingecko",
                        }
                    )
            except (KeyError, ValueError, TypeError):
                continue

        movers.sort(key=lambda x: abs(x["change_24h"]), reverse=True)
        return movers[:MAX_CANDIDATES]

    def get_stock_fundamentals(self, ticker: str) -> Optional[Dict]:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            return {
                "ticker": ticker,
                "pe_ratio": float(info.get("trailingPE") or 0),
                "peg_ratio": float(info.get("pegRatio") or 0),
                "debt_to_equity": float(info.get("debtToEquity") or 0),
                "free_cash_flow": float(info.get("freeCashflow") or 0),
                "revenue_growth": float(info.get("revenueGrowth") or 0),
                "profit_margin": float(info.get("profitMargins") or 0),
                "short_ratio": float(info.get("shortRatio") or 0),
                "beta": float(info.get("beta") or 1.0),
            }
        except Exception as exc:
            logger.debug("Failed to fetch fundamentals for %s: %s", ticker, exc)
            return None

    def get_stock_earnings_date(self, ticker: str) -> Optional[str]:
        try:
            info = yf.Ticker(ticker).info
            earnings_date = info.get("earningsDate")
            if not earnings_date:
                return None

            if isinstance(earnings_date, (list, tuple)) and earnings_date:
                earnings_ts = earnings_date[0]
                earnings_dt = datetime.fromtimestamp(earnings_ts)
                days_until = (earnings_dt - datetime.now()).days
                if 0 <= days_until <= 5:
                    return earnings_dt.strftime("%Y-%m-%d")
            return None
        except Exception as exc:
            logger.debug("Failed to fetch earnings date for %s: %s", ticker, exc)
            return None
