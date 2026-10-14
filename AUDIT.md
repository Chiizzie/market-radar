# Market Radar overhaul audit — 14 October 2026

Baseline: main `2ae3072`; existing unmerged PR #10 `3da3d3b`. Status below describes deployed main at inspection, not proposed changes. This overhaul preserves and builds on #10 in a separate branch. No merge or production deployment is authorized by this document.

| Requirement | Status | Evidence / finding |
|---|---|---|
| Repository/workflow audit | COMPLETE | All Python modules, tests and `.github/workflows/market-radar.yml` inspected; main baseline tests and #10 compared. |
| 15-minute cadence | PARTIAL | Workflow cron requests every 15 minutes; latest five run starts span 34–41 minutes. #10 offsets cron and measures gaps; exact cadence remains unguaranteed. |
| GitHub → ChatGPT notifications | PARTIAL | `publish_alert.py:main` pushes `radar-alert-live`, open PR #1. Run 38608779701 published at 17:43:29 UTC; task last ran 17:44:29 UTC. Trigger execution works; end-user delivery/acknowledgement is not observable from GitHub. |
| Reliable failure diagnostics | PARTIAL | Artifacts saved with `always()`; bridge failures can occur before `bridge_status.json` is written; outcomes use continue-on-error. |
| Duplicate alert prevention | PARTIAL | `publish_alert.py:material_change` persists prior payload, but 1% threshold misses material stop/target changes on tight setups. Telegram sends every snapshot. |
| Data completeness and retries | PARTIAL | `market_scanner.py` reports failures/deferred counts; crypto has venue fallback; stock chart fetch has no retry. News/FX/provider gaps remain. |
| Broad stock coverage and ranking | PARTIAL | `discovery.py:select_stock_candidates` hard-caps 60 charts, top 150 shortlist. Main repeatedly selects same ranking; latest run deferred 1,170. #10 adds whole-pool rotation, but still caps 60. |
| Catalyst-led discovery | PARTIAL | `catalyst_scanner.py`, `primary_news.py`, `event_timing.py`: broad headline/entity matching and bounded issuer feeds; original event verification remains manual. Latest SEC and GlobeNewswire feeds failed. |
| Independent chart-led detection | COMPLETE | `chart_analysis.py`, `trade_filters.py`: range/opening retests and trend pullback/reclaim, independent of positive catalyst. |
| Early breakout / developing alerts | MISSING | Main requires confirmed patterns; no pre-breakout pattern lifecycle. |
| VWAP reclaim / support bounce | MISSING | Recent VWAP used for extension, not a session VWAP reclaim detector; no support-bounce detector. |
| Momentum continuation | PARTIAL | `TREND_PULLBACK_RECLAIM` exists; no explicit tight momentum continuation pattern. |
| Multi-timeframe confirmation | MISSING | Five-minute bars only; no completed 15-minute context. |
| Broad liquid crypto | PARTIAL | Top 250 market-cap universe, 12 deep candidates, EUR-only exchange pairs; main exclusions can consume slots. #10 filters before rotation. |
| Bearish setups where permitted | MISSING | Long-only filter; configured Invest and spot catalogues do not establish short capability. Must not invent CFD/derivative access. |
| EUR entry / stop / TP1 / TP2 / R:R / size | PARTIAL | `trade_filters.py` generates all levels, but defaults to €5 risk, hardcoded €500 cap, no explicit expiry or cost reserve. |
| €15 default risk; €20 only with approval | MISSING | €5 sizing and informational €20 ceiling; no explicit approval policy. |
| Freshness / no chasing / broker availability | PARTIAL | 15-minute evidence freshness and extension gates exist; cached published plans may survive with old levels; no explicit entry expiry. |
| 22:00 Europe/Dublin intraday exit | COMPLETE | `trade_filters.py` blocks entries from 21:30, includes DST-aware 22:00 exit deadline. No automatic closing orders. |
| Never auto-execute | COMPLETE | No order placement path; broker API is metadata-only. |
| Historical signals / outcomes | PARTIAL | `outcome_tracker.py` retains 30 days of sampled research returns; initial watch plan never becomes actionable baseline; not execution P&L. |
| Missed opportunities / false positives / latency | MISSING | No systematic rejected/deferred cohort measurement or actionable-to-observation latency; sparse snapshots cannot establish actual stop/target order. |
| Automated regression tests | COMPLETE | Main test suite and PR #10 tests exist; 86 tests pass on #10 baseline. More tests needed for overhaul. |
| Observability / replay | PARTIAL | Health artifacts exist; #10 adds state, cadence, causal chart replay. No production proof until approved deployment. |

## Implementation order and acceptance

1. Reliability: preserve #10 rotation; bounded additional deep checks, retries, coverage reasons, robust publication state and precise material changes. Never label deferred/missing data as invalidated trades.
2. Discovery: add causal developing/breakout, VWAP reclaim, support bounce and continuation evidence with completed 15-minute context; expand eligible crypto. Watch-only evidence must never bypass entry/risk gates.
3. Planning: enforce €15 default, approval-bound €20 absolute cap, timestamp/expiry, cost allowance and known-broker long-only capabilities. Preserve EUR and cutoff/no-execution rules.
4. Measurement: record first actionable plan and latency, sampled stop/target observations, rejected/deferred opportunity proxies and coverage limitations. Add regression tests and review-only CI.

## Deployment gates

- Review the combined overhaul PR, including preserved #10 changes; merge only with user approval.
- Validate hosted tests before approval. After merge, inspect several regular-session runs for runtime, provider throttling, coverage and delivery.
- GitHub publication is not a delivery receipt. Task execution timestamp supports event reception, not proof of a push notification on the user's device.
- Free provider coverage, live broker spreads/account permissions, exact scheduler timing and real fills remain external limitations.

## Implemented on the review branch

| Area | Review-branch status | Supporting files / remaining limit |
|---|---|---|
| Rotation, expanded deep coverage, ranking | COMPLETE (bounded implementation) | `discovery.py`, `market_scanner.py`, inherited `scan_state.py`: 120 default checks, whole-pool 40-slot rotation, timestamped snapshot changes, manifest and time-budget reporting. Market-wide exhaustive coverage remains PARTIAL. |
| Retries and delivery reliability | COMPLETE (code); PARTIAL (end-to-end production proof) | `reliability.py`, `publish_alert.py`, `delivery.py`, `run_radar.py`: bounded reads, failure diagnostics, no checkout mutation, material dedupe, current observations separate from comparison state. Device notification receipt still unverified. |
| Early breakout, VWAP reclaim, support bounce, continuation | COMPLETE (heuristic implementation) | `chart_analysis.py`, `trade_filters.py`: causal completed-bar evidence, developing stages, completed 15-minute confirmation; no profitability claim. |
| Expanded liquid crypto | COMPLETE (bounded implementation) | `market_scanner.py`: 30 eligible candidates and rotation; EUR-pair/provider availability still limits coverage. |
| Bearish tradability | PARTIAL / unavailable on configured products | `broker_availability.py`, `trade_filters.py`: explicit capability flags and fail-closed short requests. No unverified derivative adapter added. |
| Risk, EUR, entry freshness, cutoff | COMPLETE | `risk_policy.py`, `trade_filters.py`, `run_radar.py`: €15 default, scoped approval above €15, absolute €20 ceiling, cost-aware R:R and size, explicit expiry, final stale-plan removal. Existing cutoff/no-execution rules preserved. Live task policy amendment remains pending approval. |
| Historical outcomes and latency | COMPLETE (sampled research) | `outcome_tracker.py`, `performance.py`: rejected/deferred cohorts, first actionable plan, latency, sampled barriers and missed-move proxies. Realised trade performance / true false-positive rate remains MISSING without fill and ground-truth data. |
| Regression and observability | COMPLETE locally and in hosted CI | 111 passing tests, including 25 added tests; `tests/test_overhaul.py`, existing tests updated only for requested risk/lifecycle behavior. `git diff --check` clean; 111 tests also pass on pandas 3.0.6 and GitHub run 38611506964. |
| Deployment | PENDING USER APPROVAL | `DEPLOYMENT.md`: merge sequence, exact task amendment, smoke-test and rollback steps. |

Validation does not include live order placement, a production scan from the review branch, live test messages, or assertions of profitable historical performance.

Hosted CI initially exposed a pandas datetime-resolution incompatibility inherited from #10. `swing_zones.py` now compares timedeltas rather than assuming `.asi8` nanoseconds; a regression covers ns/us/ms/s storage.
