# Market Radar V2

Runs around every 15 minutes on GitHub Actions (cron can be delayed), keeps Telegram as a scan backup, and publishes materially changed leads to the open draft [feed PR #1](https://github.com/Chiizzie/market-radar/pull/1). Leave that PR open; do not merge it.

## What qualifies

- Stocks use completed five-minute bars during the NYSE regular session, including holiday, DST and early-close handling. Volume compares the latest 15-minute window with the same window on previous sessions. The configured universe is the explicit liquid-stock list in `market_scanner.py`, not the entire stock market. Premarket and after-hours trades are intentionally unsupported.
- Crypto prices and traded volume are EUR-denominated. Scan the top 100 CoinGecko assets by market cap; enrich historical structure for the five most liquid positive movers. Availability on the user's broker must be confirmed separately. Free API failures and limits are recorded; missing history cannot qualify.
- Catalyst output provides a specific headline, source, publication time, link, identity match and freshness. Headlines older than 24 hours, undated items, obvious ticker collisions and speculative commentary earn zero points. Adverse events do not qualify a long setup. RSS proves what a headline reports, not the underlying event: ChatGPT must open the source and verify it before alerting.
- Classify candidates as `ACTIONABLE NOW`, `WATCH FOR ENTRY`, `EXTENDED—DO NOT CHASE` or `REJECTED`. Require fresh price data, positive recent momentum, liquidity, nearby trigger, observed support, two historical resistance targets and at least 1.5R/2R. Do not invent targets as fixed multiples. A score is only a research rank.
- Plans are expressed in EUR, with a €500 account cap, about €5 planned risk and a hard €20 ceiling. Stops cannot guarantee the loss cap through gaps/slippage. Reject new intraday leads after 21:30 Dublin time; exit by 22:00. No trades are executed.
- CoinGecko history consists of sampled prices, not exchange OHLC candles. Crypto structural plans are provisional and require independent broker-chart confirmation. This conservative implementation can miss valid setups rather than force an entry.

## Event and delivery path

`scan → conservative filters → material-change dedupe → radar-alert-live commit → PR synchronize event → ChatGPT event task → independent live review → notification if actionable`

The automation is scoped to PR #1 and must have **commit updates enabled**. It reads `radar_alert.json` from the current PR head SHA, not main. Its normal instruction is to remain silent when no current setup qualifies. A completed task run alone does not prove that a phone notification was delivered.

Deduplication compares against the last published signal state on the feed branch, so it survives ephemeral runners. Timestamp-only changes and sub-1% level/price drift do not cause new commits. Status changes, new catalysts, cumulative 1% changes, new leads and removed/invalidated leads do. An empty scan clears previously published leads once; it does not leave old trade candidates indefinitely active.

Each run saves `latest_scan.json` and `bridge_status.json` as a seven-day Actions artifact and adds health/classification/delivery status to the workflow summary. `API_ACCEPTED` means Telegram accepted the request. `GITHUB_PUBLISHED` means GitHub accepted the branch update. Neither is a ChatGPT delivery receipt. Missing provider data is reported as `DATA_UNAVAILABLE`, not evidence that no opportunities existed.

A controlled `bridge_healthcheck` field in the payload is a diagnostic only. The ChatGPT task acknowledges each test ID once even when no trade qualifies. It must never be treated as a trade.

## Run and test

```sh
pip install -r requirements.txt
python -m unittest discover -s tests -v
python run_radar.py
```

`publish_alert.py` requires a git checkout with permission to push to `radar-alert-live`. The workflow uses its existing GitHub token; Telegram secrets remain optional. No OpenAI API key or API analysis is used in this scanner.
