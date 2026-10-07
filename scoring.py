"""Ranking only; a score is never an execution signal."""
def score_setup(asset, catalyst, fundamentals=None, near_term_event=None):
    score, reasons = 0, []
    if asset.get('momentum_pct', 0) > 0:
        score += 2
        reasons.append('positive recent momentum')
    if asset.get('volume_spike', 0) >= 1.5:
        score += 2
        reasons.append('same-time relative volume')
    if asset.get('dollar_volume', 0) >= 10_000_000:
        score += 1
        reasons.append('notional liquidity')
    if catalyst and catalyst.get('eligible_for_points'):
        score += 2
        reasons.append('dated entity-matched headline evidence; source review required')
    if 0 <= asset.get('extension_pct', 999) <= 3:
        score += 1
        reasons.append('not extended above recent VWAP')
    return {'score': min(score, 10), 'label': 'Research rank only', 'reasons': reasons}
