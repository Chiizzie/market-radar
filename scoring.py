from typing import Optional, Dict


def score_setup(asset: Dict, catalyst: Optional[Dict], fundamentals: Optional[Dict] = None, near_term_event: Optional[str] = None) -> Dict:
    score = 0
    reasons = []

    move = abs(float(asset.get("change", asset.get("change_24h", 0))))
    if move >= 5.0:
        score += 2
        reasons.append("large move")
    elif move >= 3.0:
        score += 1
        reasons.append("moderate move")

    volume_spike = float(asset.get("volume_spike", 1.0))
    if volume_spike >= 1.5:
        score += 2
        reasons.append("volume spike")

    asset_volume = float(asset.get("volume", 0))
    if asset_volume >= 1_000_000:
        score += 1
        reasons.append("meaningful volume")

    if catalyst:
        catalyst_type = catalyst.get("type", "market_momentum")
        if catalyst_type in {"earnings", "merger", "product_launch", "partnership", "regulatory", "crypto_event"}:
            score += 3
            reasons.append("clear catalyst")
        elif catalyst_type == "market_momentum":
            score += 1
            reasons.append("momentum-led move")

    if near_term_event:
        score += 1
        reasons.append("near-term event")

    if fundamentals:
        pe = float(fundamentals.get("pe_ratio") or 0)
        debt = float(fundamentals.get("debt_to_equity") or 0)
        revenue_growth = float(fundamentals.get("revenue_growth") or 0)

        if 0 < pe < 30:
            score += 1
            reasons.append("reasonable valuation")
        if debt > 0 and debt < 2:
            score += 1
            reasons.append("healthy balance sheet")
        if revenue_growth > 0.10:
            score += 1
            reasons.append("growth supports move")

    if asset.get("rsi") is not None:
        rsi = float(asset["rsi"])
        if rsi > 70:
            score += 1
            reasons.append("overbought but strong")
        elif rsi < 30:
            score -= 1
            reasons.append("oversold")

    if score >= 8:
        label = "High conviction"
    elif score >= 6:
        label = "Bullish setup"
    elif score >= 4:
        label = "Watchlist"
    else:
        label = "Low conviction"

    return {
        "score": int(score),
        "label": label,
        "reasons": reasons,
        "catalyst": catalyst,
    }
