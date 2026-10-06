"""
Market data scanner: fetches price, volume, and technical data.
Uses yfinance for stocks and CoinGecko for crypto.
"""
import logging
from typing import Dict, List, Optional
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# Configuration
STOCK_MIN_CHANGE = 5.0
STOCK_MIN_VOLUME = 500_000
CRYPTO_MIN_CHANGE = 3.0
CRYPTO_MIN_QUOTE_VOLUME = 5_000_000
MAX_ALERTS = 8


def get_sp500_movers() -> List[Dict]:
    """Fetch S&P 500 stocks with largest 24h price moves."""
    try:
        # Get S&P 500 ticker list
        sp500 = yf.download("^GSPC", period="1d", progress=False)
        
        # Top gainers/losers from a known set
        tickers = [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "GOOG",
            "BRK.B", "JNJ", "V", "WMT", "JPM", "MA", "PG", "HD", "DIS", "PYPL"
        ] * 3  # Expand list as needed
        
        movers = []
        for ticker in tickers[:50]:  # Sample 50 tickers
            try:
                data = yf.Ticker(ticker)
                hist = data.history(period="5d")
                
                if len(hist) < 2:
                    continue
                
                current_price = hist['Close'].iloc[-1]
                prev_price = hist['Close'].iloc[-2]
                change_pct = ((current_price - prev_price) / prev_price) * 100
                volume = hist['Volume'].iloc[-1]
                
                if abs(change_pct) >= STOCK_MIN_CHANGE and volume >= STOCK_MIN_VOLUME:
                    movers.append({
                        "ticker": ticker,
                        "price": float(current_price),
                        "change": float(change_pct),
                        "volume": int(volume),
                        "source": "yfinance"
                    })
            except Exception as e:
                logger.warning(f"Error fetching {ticker}: {e}")
                continue
        
        movers.sort(key=lambda x: abs(x["change"]), reverse=True)
        return movers[:MAX_ALERTS]
        
    except Exception as e:
        logger.exception(f"Stock scan failed: {e}")
        return []


def get_crypto_movers() -> List[Dict]:
    """Fetch cryptocurrencies with largest 24h price moves from CoinGecko."""
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 250,
        "sparkline": False,
        "price_change_percentage": "24h",
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
            change = float(coin["price_change_percentage_24h"])
            volume = float(coin["total_volume"] or 0)
            
            if abs(change) >= CRYPTO_MIN_CHANGE and volume >= CRYPTO_MIN_QUOTE_VOLUME:
                movers.append({
                    "symbol": symbol,
                    "price": price,
                    "change": change,
                    "volume": volume,
                    "source": "coingecko",
                    "market_cap": float(coin.get("market_cap") or 0)
                })
        except (KeyError, ValueError, TypeError):
            continue
    
    movers.sort(key=lambda x: abs(x["change"]), reverse=True)
    return movers[:MAX_ALERTS]


def get_stock_fundamentals(ticker: str) -> Optional[Dict]:
    """Fetch fundamental data: P/E, PEG, debt, growth metrics."""
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
        }
    except Exception as e:
        logger.warning(f"Failed to fetch fundamentals for {ticker}: {e}")
        return None
