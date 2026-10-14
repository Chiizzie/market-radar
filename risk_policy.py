"""Research sizing only. An approval must be explicit, scoped and unexpired."""
import math
from outcome_tracker import stamp


def position_size(asset, entry_eur, stop_risk_eur, now):
    budget = asset.get('risk_budget_eur', 15)
    capital = asset.get('capital_limit_eur', 500)
    reserve_bps = asset.get('cost_reserve_bps', 20)
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
           for x in [budget, capital, reserve_bps, entry_eur, stop_risk_eur]):
        raise ValueError('Risk inputs must be finite numbers.')
    if not 0 < budget <= 20 or capital <= 0 or reserve_bps < 0 or min(entry_eur, stop_risk_eur) <= 0:
        raise ValueError('Invalid risk inputs or planned risk above the absolute €20 ceiling.')
    if budget > 15:
        approval = asset.get('risk_approval') or {}
        expires = stamp(approval.get('expires_at'))
        if (not approval.get('user_approval_reference') or approval.get('symbol') != asset.get('ticker', asset.get('symbol'))
                or not expires or expires <= now or approval.get('amount_eur') != budget):
            raise ValueError('Planned risk above €15 requires explicit, current, asset-scoped user approval.')
    costs_per_unit = entry_eur * reserve_bps / 10000
    units = min(budget / (stop_risk_eur + costs_per_unit), capital / entry_eur)
    units = math.floor(units * 1_000_000) / 1_000_000
    return {'units': units, 'risk_budget_eur': budget,
            'planned_loss_eur': round(units * (stop_risk_eur + costs_per_unit), 2),
            'cost_reserve_eur': round(units * costs_per_unit, 2)}
