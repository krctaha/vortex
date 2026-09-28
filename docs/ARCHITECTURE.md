# Architecture

## Data and research flow

```text
Exchange public REST + WebSocket
  -> shared feed/cache + freshness checks
  -> browser live price/order-book updates
  -> unique universe (up to 200 markets)
  -> 4h context + closed 15m structure analysis (per-candle cache)
  -> explanatory quality score + direction control
  -> research candidates -> SQLite paper lifecycle/outcome journal
  -> bounded ranking feedback and optional deduplicated Telegram notifications
```

The HTTP API exposes chart data and research observations. Templates render the
page shell; JavaScript manages navigation and live subscriptions. SQLite stores
local accounts, settings, observations and paper records. One deployment shares
the feed/scanner rather than starting a new upstream scan for each visitor.

Key modules: `binance_ws.py`, `smc_market.py`, `smc_ict_engine.py`,
`premium_engine.py`, `smc_quality.py`, `smc_journal.py`, `smc_controls.py`,
`smc_rsi.py`, `smc_notifications.py`. Historical module names and `/premium` API
paths are compatibility names, not evidence of a paid product.

## Different clocks

Trade/book updates arrive via exchange events. RSI can reflect a forming candle;
confirmed strategy structure uses closed candles. The scanner normally schedules
around 15-minute candle boundaries, reuses the same candle analysis and retries
data errors. Cached REST/news/macro data have different update intervals.
Do not claim that all panels have millisecond freshness or that every tick creates
a new strategy decision.

## Outcome feedback

Paper outcomes can adjust ranking within bounded rules. This is not a trained
large language model, proof of a profitable strategy, or calibrated confidence.
The mechanism is implemented in journal/scoring code and must be reviewed with
reproducible samples. Unfilled plans and observations are not executed trades.

## Research-only boundary

`run_smc.py` starts research and notification services, not legacy execution
workers. Its middleware rejects writes to the legacy trading namespace. Other
legacy modules are retained for compatibility. Changing the entrypoint or adding
order workers changes the safety boundary and is outside this snapshot's purpose.

## Known limitations

Single-node SQLite design; no promised horizontal scaling or uptime SLA.
Exchange access and upstream feeds can fail. News is polling-based, not a
low-latency licensed newswire. Paper price touches do not reproduce order fills.
Legacy code increases maintenance/review burden. Most UI strings are Turkish;
English UI localization, accessible chart alternatives, broader integration tests,
and independent security/performance audits remain roadmap work.
