# Review and rollout gate

This overhaul includes the existing `fix/calibration-rotation-replay` (PR #10) commits and targets main as a combined review. PR #10 is preserved unchanged. No production merge, live task edit or test notification was performed by this implementation.

1. Require passing regression CI for this combined PR. Review the inherited #10 changes plus the overhaul and pandas compatibility fix together. Approve merging this PR directly to main; do not deploy #10 separately because its timestamp-resolution bug is fixed here.
2. Approve applying the task policy amendment below to the existing **Market Radar Alerts** task. Preserve its existing GitHub PR #1 commit-update trigger, its destination conversation, original-source/broker checks and actionable-only notification policy. Do not create a duplicate scheduled task.
3. After merge, inspect several regular-session runs: elapsed runtime, selected/received/deferred counts, rate limits, rotation persistence, bridge status, outcome summary and actual cadence. If runtime or throttling rises, lower RADAR_STOCK_CHART_LIMIT to 60; the persistent rotation still works. Revert this PR to restore prior behavior if needed.
4. Perform one explicitly labelled bridge healthcheck on the existing feed and verify the acknowledgement in the task conversation. A GitHub commit or task execution timestamp alone is not proof of device notification delivery.

## Exact task policy amendment to append after approval

These overhaul rules supersede earlier risk and freshness wording. Use €15 as the default maximum planned loss per trade, including spread/fees and a conservative slippage allowance. Above €15, up to the absolute €20 ceiling, requires Charlie's explicit approval for that asset and amount; a scanner field alone is not proof of user approval. Never exceed €20. Do not infer available cash from historical account balances or the scanner's conservative €500 per-idea cap.

Read each plan's generated_at, price_observed_at and expires_at. Never present an expired plan as actionable; obtain fresh evidence and rebuild the plan. Preserve the no-chasing gates, EUR reporting, 22:00 Europe/Dublin intraday exit and no automatic execution. `unavailable_signals` means unassessed/deferred/data missing, not disproved; discard old entry instructions pending revalidation. Only `potential_signals` holds current observations; `comparison_signals` is internal deduplication state and must never supply current trade levels.

Evaluate early breakout, VWAP reclaim, support bounce and momentum continuation evidence alongside existing patterns. A DEVELOPING stage or TECHNICAL WATCH remains research only. Require the relevant confirmation and fresh completed 15-minute context for new patterns. Do not send CHECK NOW for developing candidates. Preserve actionable-only notifications unless Charlie separately approves early-watch notifications.

Configured Trading 212 Invest and Robinhood Europe spot evidence does not establish short capability. Never substitute CFDs, perpetuals or other derivatives. Sampled stop/target outcomes, missed-move proxies and R multiples are research diagnostics, not actual fills, win rates or realised profits.

## Remaining external limitations

- GitHub cron is best-effort, not guaranteed every 15 minutes. An external scheduler is a separate deployment decision; this change does not claim to fix platform scheduling.
- Free feeds have missing timestamps, rate limits and inaccessible issuer feeds. Original catalyst verification and live broker chart/quote/account access remain necessary.
- A planning reserve is not a guaranteed maximum realised loss. Stops can gap or slip.
- No live broker execution history or complete market ground truth exists here; performance claims require substantially better data.


## Authorized bearish extension — 14 October 2026

The user confirmed Trading 212 CFD access and approved implementing this extension. The following replaces the older absolute CFD prohibition; all risk, source, freshness, no-execution and actionable-only notification rules remain.

Evaluate both LONG and SHORT directions. Use Trading 212 Invest for stock longs and Trading 212 CFD only for independently verified stock shorts. Robinhood Europe spot crypto remains long-only; bearish crypto is research/warning input. A CFD listing does not verify sell permission, short quantity limits, actual margin, bid/ask or costs. The current public CFD catalogue omits short-opening limits; `BEARISH WATCH` has no approved executable plan. Independently verify the exact CFD identity and current account/instrument short permission before promotion. If that evidence cannot be obtained, remain silent under the existing actionable-only policy; never claim a short is executable or invent missing permission. A user statement that the CFD account exists is not instrument-level permission evidence.

Read `qualification.direction`, `pattern_evidence`, `short_broker_availability` and target derivation. SHORT requires upper stop/invalidation, entry trigger below resistance, two feasible downside targets, negative momentum, confirming volume and fresh falling completed 15-minute context. Preserve equivalent long thresholds, €15 default planned risk including conservative spread/fees/slippage, €20 absolute ceiling only with explicit scoped approval, EUR reporting, 21:30 new-intraday-entry cutoff, 22:00 Europe/Dublin exit, expiry and no-chasing rules. Size from stop distance and full notional, never margin deposited. Verify minimum quantity/increments, maximum short size, margin and financing/rollover charges before any alert. These are probabilities/heuristics, not predictions of certain decline or proven profitability.

For independently verified shorts, start `🚨 CHECK NOW — <ticker> SHORT | <HH:mm Irish time>` and show entry, upper stop, downside targets, net R:R, units/notional, planned risk, expiry and CFD route. Developing patterns and bearish/technical watches are not entry alerts. Preserve the existing PR #1 commit-update trigger, destination conversation, bridge diagnostics and deduplication; do not create a second task.
