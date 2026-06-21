# Paper Mode Pricing Architecture

## Overview

Paper trades fill at a deterministic price derived from Redis market data. The price
resolution pipeline was redesigned in the bugfix phase to eliminate a mismatch between
what `get_symbol_snapshot` showed and what the execution adapter saw.

---

## Price Resolution Priority

All three entry points (MCP paper trade facade, paper execution adapter, `get_symbol_snapshot`)
now use **the same priority order**:

| Priority | Source | Redis key pattern | TTL |
|----------|--------|-------------------|-----|
| 1 | Analytics snapshot | `analytics:{market_type}:{symbol}:snapshot` | 60 s |
| 2 | Market price key | `market:{market_type}:{symbol}:price` | 60 s |
| 3 | Book ticker mid | `market:{market_type}:{symbol}:book_ticker` | 10 s |
| 4 | Cross-market fallback | same 1-3, complementary market_type | — |
| 5 | size_usd / size derivation | in-memory calculation | — |
| 6 | Failure | returns `paper_price_unavailable` | — |

The analytics snapshot is tried **first** because it is the source of `get_symbol_snapshot`
and is refreshed every ~1 second by the analytics service from orderbook/trade events.

---

## Cross-Market Fallback (Paper Mode Only)

BTC/ETH spot and perpetual futures prices are nearly identical (< $1 difference). When a
strategy is configured with `market_type=spot` but only futures data streams are active,
all "spot" Redis keys will be absent. The cross-market fallback silently retries with the
complementary market type:

```
spot strategy → spot keys empty → try futures keys → fill at futures price
```

This fallback is applied in:
- `services/execution/adapter/paper.py` — `_resolve_fill_price` / `_redis_price`
- `services/mcp_server/facades/execution.py` — `request_paper_trade` size_usd derivation

The fallback is **paper mode only**. Live/assisted adapters must not use it.

---

## Why the Mismatch Existed

Before the fix, two problems caused "price_unavailable" errors on paper trades:

1. **Market-type mismatch**: `get_symbol_snapshot` defaulted to `market_type="futures"` in
   `services/mcp_server/tools/read.py:11`. The strategy was created with `market_type="spot"`.
   Only futures ingest streams were active, so all spot keys were empty.

2. **Priority order mismatch**: The paper adapter tried `market_price` first, then
   `analytics_snapshot` last. `get_symbol_snapshot` tried `analytics_snapshot` first.
   This meant even when spot analytics data existed, the adapter would fail before reaching it.

---

## Bid/Ask in Snapshots

`get_symbol_snapshot` returns `bid`, `ask`, `spread`, and `spread_bps` fields. These were
null when no explicit `bookTicker` WebSocket stream was configured.

The snapshot builder (`services/analytics/snapshot/builder.py:_build_market_state`) now
falls back to the top-of-book from the depth orderbook (`last_book`) when no `last_book_ticker`
is available. The depth20@100ms stream is always configured, so bid/ask now populate reliably.

Priority:
1. `state.last_book_ticker` (requires explicit bookTicker stream subscription)
2. `state.last_book` top level (derived from depth orderbook, always available)

---

## Incident Logging

Any execution failure (not just price unavailability) now produces an incident record
visible via `get_incidents`. The `incident_type` field distinguishes:

- `paper_price_unavailable` — fill price could not be resolved from any source
- `execution_failure` — adapter returned `success=False` for any other reason

Incidents are logged by:
- `services/mcp_server/facades/execution.py` — on price lookup failure before stream publish
- `services/execution/consumer.py:_on_failed` — on adapter rejection after risk approval

---

## Risk Policy and Account Context

`AccountContextLoader` (`services/execution/account/context.py`) loads from DB:
- `ExchangeAccount` → account existence / mode check
- `ApprovalLevelRecord` → current approval level
- `RiskPolicy` → converts to `RiskLimits` (max_position_size_usd, max_daily_loss_usd,
  max_concurrent_positions, symbol_cooldown_seconds)

The execution consumer queries live risk data from the repository before each trade:
- `get_daily_realized_loss_usd(account_id)` — from fills table
- `get_open_positions_count(account_id)` — from orders table

Both `check_max_daily_loss_placeholder` and `check_max_concurrent_exposure_placeholder`
in `services/execution/risk/engine.py` run as real checks when data is available; they
pass silently when `daily_loss_usd` or `open_positions` is None (no fills yet).

---

## Files Changed in This Phase

| File | Change |
|------|--------|
| `services/execution/adapter/paper.py` | Full rewrite: analytics_snapshot-first lookup, cross-market fallback, zero-price guard |
| `services/execution/consumer.py` | Default adapter now receives `redis`; `_on_failed` logs incidents for all failures |
| `services/mcp_server/facades/execution.py` | `_resolve_price_from_redis` helper; cross-market fallback for size_usd derivation |
| `services/analytics/snapshot/builder.py` | `_build_market_state`: orderbook fallback for bid/ask when no book_ticker |
| `tests/unit/test_price_unavailable_incident.py` | Updated priority-order tests; added cross-market fallback tests |
| `tests/unit/test_snapshot_bid_ask.py` | New: 8 tests for bid/ask/spread from book_ticker and orderbook |
