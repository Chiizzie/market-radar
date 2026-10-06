import logging
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import feedparser

logger = logging.getLogger(__name__)

GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

KEYWORD_CATEGORIES = {
    "earnings": ["earnings", "guidance", "quarterly results", "results beat", "revenue beat"],
    "merger": ["merger", "acquisition", "takeover", "buyout", "deal"],
    "product_launch": ["product launch", "launch", "release", "rollout", "new feature"],
    "regulatory": ["regulation", "approval", "fda", "sec", "warning"],
    "partnership": ["partnership", "alliance", "collaboration", "deal with", "investment"],
    "supply_chain": ["supply chain", "chip shortage", "shipment", "factory", "production"],
    "macro": ["fed", "rate cut", "inflation", "recession", "growth", "jobs report"],
    "crypto_event": ["etf", "upgrade", "fork", "tokenomics", "partnership", "launch", "mainnet"],
}


def fetch_news(query: str, limit: int = 5) -> List[Dict]:
    target = GOOGLE_NEWS_URL.format(query=quote_plus(query))
    try:
        feed = feedparser.parse(target)
        items = []
        for entry in feed.entries[:limit]:
            title = getattr(entry, "title", "") or ""
            link = getattr(entry, "link", "") or ""
            summary = getattr(entry, "summary", "") or ""
            items.append({"title": title, "link": link, "summary": summary})
        return items
    except Exception as exc:
        logger.debug("Failed to fetch news for %s: %s", query, exc)
        return []


def classify_catalyst(headlines: List[Dict]) -> Optional[Dict]:
    if not headlines:
        return None

    text_blob = " ".join(
        (item.get("title", "") + " " + item.get("summary", ""))
        for item in headlines
    ).lower()

    for category, keywords in KEYWORD_CATEGORIES.items():
        for keyword in keywords:
            if keyword in text_blob:
                return {
                    "type": category,
                    "summary": f"Possible {category.replace('_', ' ')} catalyst in market narrative.",
                    "headlines": headlines[:3],
                }

    return {
        "type": "market_momentum",
        "summary": "Price action and market rotation appear to be driving current move.",
        "headlines": headlines[:3],
    }


def find_catalyst_for_asset(query: str, asset_type: str = "stock") -> Dict:
    base_query = query.strip()
    if asset_type == "crypto":
        search_terms = [
            f"{base_query} token",
            f"{base_query} ETF",
            f"{base_query} partnership",
            f"{base_query} upgrade",
            f"{base_query} regulation",
            f"{base_query} mainnet",
        ]
    else:
        search_terms = [
            f"{base_query} earnings",
            f"{base_query} product launch",
            f"{base_query} merger",
            f"{base_query} acquisition",
            f"{base_query} earnings guidance",
            f"{base_query} analyst upgrade",
            f"{base_query} revenue",
        ]

    headlines = []
    for term in search_terms:
        headlines.extend(fetch_news(term, limit=2))

    catalyst = classify_catalyst(headlines)
    if catalyst:
        return catalyst

    return {
        "type": "market_momentum",
        "summary": "No clear external catalyst detected; move may be driven by momentum or macro flow.",
        "headlines": [],
    }
