"""
Market scanner: Detects price movers, unusual volume, and technical signals.
Uses yfinance for stocks and CoinGecko for crypto.
Proactively identifies assets with high conviction moves.
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# Thresholds
STOCK_MIN_VOLUME_SPIKE = 1.5  # 1.5x average volume
STOCK_MIN_CHANGE = 3.0  # 3% move (lower than reactive threshold)
CRYPTO_MIN_CHANGE = 2.0  # 2% move
CRYPTO_MIN_VOLUME = 1_000_000  # minimum USD volume
MAX_CANDIDATES = 20


class MarketScanner:
    """Scans for unusual price/volume activity that often precedes major moves."""

    def __init__(self):
        self.sp500_tickers = self._get_sp500_tickers()

    def _get_sp500_tickers(self) -> List[str]:
        """Fetch S&P 500 constituents."""
        try:
            url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            tables = __import__("pandas").read_html(url)
            df = tables[0]
            return df["Symbol"].tolist()
        except Exception as e:
            logger.warning(f"Failed to fetch S&P 500 list: {e}")
            # Fallback list of mega-cap + active stocks
            return [
                "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "GOOG",
                "BRK.B", "JNJ", "V", "WMT", "JPM", "MA", "PG", "HD", "DIS", "PYPL",
                "ADBE", "NFLX", "CRM", "AMD", "INTC", "IBM", "CSCO", "ORCL", "SAP",
                "SMCI", "MSTR", "MRVL", "AVGO", "QUALM", "ARM"
            ]

    def scan_stocks(self) -> List[Dict]:
        """
        Scan for stocks with:
        - Price move >= STOCK_MIN_CHANGE%
        - Volume spike (1.5x average)
        - Technical strength (RSI, moving averages)
        """
        movers = []

        for ticker in self.sp500_tickers[:100]:  # Scan top 100
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="60d")

                if len(hist) < 20:
                    continue

                # Calculate metrics
                current_price = hist["Close"].iloc[-1]
                prev_close = hist["Close"].iloc[-2]
                change_pct = ((current_price - prev_close) / prev_close) * 100
                current_volume = hist["Volume"].iloc[-1]
                avg_volume_20d = hist["Volume"].iloc[-20:].mean()
                volume_spike = current_volume / avg_volume_20d if avg_volume_20d > 0 else 0

                # Simple RSI calculation (14-period)
                delta = hist["Close"].diff()
                gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss if loss.iloc[-1] != 0 else 0
                rsi = 100 - (100 / (1 + rs))

                # Detect unusual activity
                if abs(change_pct) >= STOCK_MIN_CHANGE or volume_spike >= STOCK_MIN_VOLUME_SPIKE:
                    movers.append({
                        "ticker": ticker,
                        "price": float(current_price),
                        "change": float(change_pct),
                        "volume": int(current_volume),
                        "avg_volume_20d": int(avg_volume_20d),
                        "volume_spike": float(volume_spike),
                        "rsi": float(rsi.iloc[-1]),
                        "source": "yfinance",
                        "activity_type": "volume_spike" if volume_spike >= STOCK_MIN_VOLUME_SPIKE else "price_move",
                    })
            except Exception as e:
                logger.debug(f"Error scanning {ticker}: {e}")
                continue

        # Sort by conviction (volume spike + price move combination)
        movers.sort(
            key=lambda x: (abs(x["change"]) * x["volume_spike"]),
            reverse=True
        )
        return movers[:MAX_CANDIDATES]

    def scan_crypto(self) -> List[Dict]:
        """
        Scan for cryptocurrencies with:
        - Price move >= CRYPTO_MIN_CHANGE%
        - Significant USD volume
        - Market momentum
        """
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 250,
            "sparkline": False,
            "price_change_percentage": "24h,7d",
        }

        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.RequestException as e:
            logger.exception(f"CoinGecko request failed: {e}")
            return []

        movers = []

        for coin in data:
            try:
                symbol = coin["symbol"].upper()
                price = float(coin["current_price"])
                change_24h = float(coin.get("price_change_percentage_24h") or 0)
                change_7d = float(coin.get("price_change_percentage_7d") or 0)
                volume = float(coin.get("total_volume") or 0)
                market_cap = float(coin.get("market_cap") or 0)

                # Filter by volume and movement
                if volume < CRYPTO_MIN_VOLUME:
                    continue

                # Detect divergence (7d move different from 24h = potential reversal/acceleration)
                momentum_divergence = abs(change_7d - change_24h)

                if abs(change_24h) >= CRYPTO_MIN_CHANGE:
                    movers.append({
                        "symbol": symbol,
                        "price": float(price),
                        "change_24h": float(change_24h),
                        "change_7d": float(change_7d),
                        "volume": float(volume),
                        "market_cap": float(market_cap),
                        "momentum_divergence": float(momentum_divergence),
                        "source": "coingecko",
                    })
            except (KeyError, ValueError, TypeError):
                continue

        movers.sort(key=lambda x: abs(x["change_24h"]), reverse=True)
        return movers[:MAX_CANDIDATES]

    def get_stock_fundamentals(self, ticker: str) -> Optional[Dict]:
        """Fetch fundamental data for scoring."""
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
        except Exception as e:
            logger.debug(f"Failed to fetch fundamentals for {ticker}: {e}")
            return None

    def get_stock_earnings_date(self, ticker: str) -> Optional[str]:
        """Check if earnings are upcoming (within 5 days)."""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            earnings_date = info.get("earningsDate")
            
            if earnings_date:
                # earnings_date is typically a list [start, end] as Unix timestamps
                if isinstance(earnings_date, (list, tuple)) and len(earnings_date) > 0:
                    earnings_ts = earnings_date[0]
                    earnings_dt = datetime.fromtimestamp(earnings_ts)
                    days_until = (earnings_dt - datetime.now()).days
                    
                    if 0 <= days_until <= 5:
                        return earnings_dt.strftime("%Y-%m-%d")
            
            return None
        except Exception as e:
            logger.debug(f"Failed to fetch earnings date for {ticker}: {e}")
            return None
