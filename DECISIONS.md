# Data Collection Decisions

Last updated: 2026-11-05
Status: agreed before build. Change this file before changing the code.

Related: [GAMEPLAN_11_5.md](GAMEPLAN_11_5.md), [project_plan.md](project_plan.md)

---

## D1. Venues

| Platform | API | Base URL | Auth for market data |
|---|---|---|---|
| Kalshi | Trade API v2 | `https://external-api.kalshi.com/trade-api/v2` (alias `https://api.elections.kalshi.com/trade-api/v2`) | None for REST reads |
| Polymarket US | Retail API | `https://gateway.polymarket.us` (`/v1/...`) | None for public endpoints |

- We use **Polymarket US** (CFTC-regulated) for now, not international Polymarket (`gamma-api` / `clob.polymarket.com`). Reason: our question is about arbitrage we can actually trade, and US users can trade on Polymarket US.
- Later we may add international Polymarket as a third venue. The schema must not assume only two platforms.
- Platform codes used in every table: `KALSHI`, `POLYUS`.

---

## D2. Unique identifiers

Every row in every table carries `market_uid`. The `market_uid` value for a market never changes.

| Field | Format | Kalshi example | Polymarket US example |
|---|---|---|---|
| `market_uid` | `{platform}:{native_market_id}` | `KALSHI:<market ticker>` | `POLYUS:<market id>` |
| `event_uid` | `{platform}:{native_event_id}` | `KALSHI:<event_ticker>` | `POLYUS:<event id>` |
| `pair_id` | `{kalshi market_uid}__{polyus market_uid}` | n/a | n/a |

- One market means one tradable binary contract (one Yes/No outcome). A multi-outcome event has many markets. This replaces the `Yes_Title_{i}` / `Yes_Price_{i}` columns: outcome `i` is its own `market_uid`. The wide layout can be rebuilt as a view if needed.
- We store the Polymarket US `slug` too, because the order book endpoint (`GET /v1/markets/{slug}/book`) uses the slug. The uid uses `id`, which should not change if a slug is edited (to verify, see Open items).

---

## D3. Tables

| # | Table | Grain | Refresh | Purpose |
|---|---|---|---|---|
| T0 | `market_profiles` | 1 row per **open** `market_uid` | Rebuilt from T1 after every catalog refresh | **First input to the agentic workflow.** Same columns on both platforms: title, summary, rules, category, time until close (added 2026-11-05) |
| T1 | `market_catalog` | 1 row per `market_uid` (current version) | Incremental, on change | Everything the LLM needs to decide "market identical" |
| T2 | `match_flags` | 1 row per `pair_id` | When matching runs | Output of matching and verification. Decides which order books we pull |
| T3 | `pricing_snapshots` | 1 row per `market_uid` per tick | Every tick, open markets only | Top-of-book prices, per-market volume, status, **all fee parameters** |
| T4 | `orderbook_snapshots` | 1 row per `market_uid` per tick per price level | Flagged markets only | Full depth for arbitrage and slippage |
| T5 | `platform_counts` | 1 row per platform per tick | Every tick | Total open events and markets on each platform |
| T6 | `market_stats` | 1 row per `market_uid` per fetch | Flagged: every book tick. All others: rolling background sweep | Polymarket US per-market volume, notional, open interest and last trade. The list endpoint does not return these (added 2026-11-05) |

### T0. `market_profiles` (agent input)

Built from T1. Only open markets whose close time has not passed. File: `data/market_profiles/market_profiles.parquet`.

| Column | Kalshi source | Polymarket US source |
|---|---|---|
| `market_uid`, `platform`, `event_uid` | Keys | Keys |
| `category`, `subcategory`, `tags` | Series category, series tags | `category`, `eventSubcategory`, tag labels |
| `title` | Event `title` | Event `title` |
| `market_title` | Market `title` | Market `question` (+ `title` if it adds something) |
| `yes_means` | `yes_sub_title` | YES side label, or the question if the label is just "Yes" |
| `summary` | `Event: … \| Market: … \| YES means: …` | same format |
| `rules` | `rules_primary` + `rules_secondary` + early-close condition + settlement source names | `description` + `rulesDisclaimer` + event `description` (if it differs from the title) |
| `open_time_ms`, `close_time_ms` | `open_time`, `close_time` | `startDate`, `endDate` |
| `time_until_close_hours` | `(close_time − as_of) / 1h` | same |
| `status`, `market_url`, `rules_hash`, `as_of_ms` | From T1 | From T1 |

- Neither API has an "about" or summary field. `summary` is built from the titles, so it is the same format on both platforms. The workflow could later replace it with an LLM-written summary.
- `time_until_close_hours` is correct as of `as_of_ms` (the last catalog refresh, at most about an hour old). For exact values, recompute from `close_time_ms`.

### T1. `market_catalog` (events and markets in one table)

Event fields are copied onto each market row so the LLM gets everything in one record.

| Group | Kalshi source | Polymarket US source |
|---|---|---|
| Keys | `market_uid`, `event_uid`, `ticker`, `event_ticker`, `series_ticker` | `market_uid`, `event_uid`, market `id`, `slug`, event `id`, event `ticker`, event `slug` |
| Titles | event `title`, event `sub_title`, `yes_sub_title`, `no_sub_title` | event `title`, event `subtitle`, market `question`, `title`, `subtitle`, `marketSides` labels |
| Classification | `market_type`, series `category`, event `mutually_exclusive`, `collateral_return_type`, `strike_date`, `strike_period` | `category`, `subcategory`, `tags`, `marketTypes` |
| Rules and resolution | `rules_primary`, `rules_secondary`, event `settlement_sources` | market `description`, `rulesDisclaimer`, event `description`, event `resolutionSource` |
| Dates (UTC) | `open_time`, `close_time`, `expected_expiration_time`, `latest_expiration_time`, `settlement_ts`, `created_time`, `can_close_early` | `startDate`, `endDate`, event `startDate`, event `endDate`, event `closedTime`, `createdAt` |
| Status (all raw fields) | see D5 | see D5 |
| Result | `result`, `settlement_value_dollars`, `expiration_value` | book `stats.settlementPx` |
| Link | `market_url` (format to verify) | `market_url` (format to verify) |
| Bookkeeping | `first_seen_ts`, `last_seen_ts`, `catalog_updated_ts`, `rules_hash` | same |

- `rules_hash` is a hash of all rules and resolution text. If it changes, every `match_flags` row that uses that market is marked stale and must be re-verified.
- Note: the request listed `description`, `rulesDisclaimer`, `resolutionSource` under Kalshi. These are Polymarket US fields, so they are listed under Polymarket US here.
- Kalshi has no "About" field in the API. "About" is dropped; the rules fields above replace it.
- Found in the first pull (2026-11-05):
  - The Polymarket US API never returns `resolutionSource`, so that column is always null. `rulesDisclaimer` is present on about 12% of markets.
  - Polymarket US has no No-side title, so `no_title` is null there.
  - For multi-team markets (for example moneylines), which team is the YES side is in `yes_side_label` and the market title. Matching must check this.

### T2. `match_flags`

| Field | Notes |
|---|---|
| `pair_id`, `kalshi_market_uid`, `polyus_market_uid` | Keys |
| `market_identical` | Boolean. The flag that turns on order book collection |
| `relationship` | `identical`, `complementary`, `basket`, `none` |
| `proposer_explanation`, `judge_verdict`, `numeric_checks_passed` | From workstreams 3–4 |
| `rules_review_status`, `human_review_status` | From workstream 5 |
| `kalshi_rules_hash`, `polyus_rules_hash` | Rules hashes when flagged (see T1) |
| `flagged_ts`, `is_active` | `is_active = false` when stale, settled or rejected |

### T3. `pricing_snapshots`

| Group | Fields |
|---|---|
| Keys and time | `tick_ts`, `market_uid`, `platform`, `request_sent_ts`, `response_received_ts` (see D7) |
| Prices (cents, 0–100) | `yes_bid`, `yes_ask`, `no_bid`, `no_ask`, `last_price`, `price_derived` (flag for prices computed as 100 − the other side) |
| Sizes (contracts) | `yes_bid_size`, `yes_ask_size` |
| Per-market volume | Kalshi: `volume_fp`, `volume_24h_fp`, `open_interest_fp`. Polymarket US: `volume`, `volume24hr`, `volume1wk`, `volume1mo`, `volume1yr`, book `stats.openInterest`, `sharesTraded`, `notionalTraded` |
| Status | All raw status fields and `status_canonical` (D5) |
| Fees (D6) | `fee_formula`, `fee_coefficient`, `fee_type_override`, `fee_multiplier_override`, `maker_fee_coefficient`, plus trade limits `tick_size` (`orderPriceMinTickSize`) and `min_order_qty` (`minimumTradeQty`) |

- Volume is always **per market**, never a platform total. On both platforms it counts contracts with a $1 payout, so the numbers can be compared.
- The Polymarket US market list returns best bid and ask only. It has no sizes, last trade, or volume, so those are null in T3 for Polymarket US and are filled in T6 instead.
- Kalshi uses yes_bid = 0 / yes_ask = 100 when one side of the book is empty. Treat these as "no quote", not as prices.
- `time_until_close` is computed at query time as `close_time − tick_ts`. It is not stored.

### T4. `orderbook_snapshots`

Pulled for **every market in an active `match_flags` row**. Long format, one row per price level:

| Field | Notes |
|---|---|
| `tick_ts`, `market_uid`, `platform`, `request_sent_ts`, `response_received_ts`, `exchange_ts` (if the API provides one) | Keys and time |
| `outcome` | `yes` or `no` |
| `side` | `bid` or `ask` |
| `level` | 0 = best price |
| `price` | Cents, float |
| `qty` | Contracts |
| `derived` | True if computed rather than returned by the API |
| `book_hash` | Hash of the whole book. Used for change-only writes (D9) |

- Kalshi returns **bids only** (`orderbook_fp.yes_dollars`, `no_dollars`). Asks are derived: YES ask = 100 − best NO bid, NO ask = 100 − best YES bid.
- Polymarket US returns `bids` and `offers`, each `{px: {value, currency}, qty}`, plus `state` and `stats`. We store `state` and `stats` with the snapshot.

### T5. `platform_counts`

`tick_ts`, `platform`, `open_events`, `open_markets`, `open_markets_excl_mve` (Kalshi), `flagged_pairs_open_both` (filled once matching exists).

---

## D4. Units and conventions

- **Time:** all timestamps are UTC, stored as int64 epoch milliseconds. ISO 8601 only for display.
- **Prices:** both APIs return dollars as decimal strings (for example `"0.1500"`). We store **cents as a float** (`15.0`), because sub-cent prices exist. We never round prices to integers.
- **Quantities:** contracts. Kalshi `_fp` fields are decimal strings and are parsed to float.
- **Raw payloads:** we keep the raw API JSON for each pull (compressed), so missed fields can be recovered later without re-pulling.

---

## D5. Status: keep every raw status field

We store every status field each API returns, with no information dropped. We add one `status_canonical` column for easy filtering.

| Source | Raw fields stored |
|---|---|
| Kalshi market | `status`: `initialized`, `inactive`, `active`, `closed`, `determined`, `disputed`, `amended`, `finalized` (`paused` also appears in the filter schema) |
| Kalshi market | `result`: `yes`, `no`, `scalar`, empty |
| Polymarket US market | `status`: `MARKET_STATUS_OPEN`, `_CLOSED`, `_RESOLVING`, `_RESOLVED`, `_HALTED`. Booleans `active`, `closed`, `archived` (deprecated) |
| Polymarket US book | `state`: `MARKET_STATE_OPEN`, `_PREOPEN`, `_SUSPENDED`, `_EXPIRED`, `_TERMINATED`, `_HALTED`, `_MATCH_AND_CLOSE_AUCTION` |
| Polymarket US event | `active`, `closed`, `archived`, `live`, `ended`, `eventState` |

`status_canonical` values: `unopened`, `open`, `halted`, `closed`, `resolving`, `settled`. The exact mapping from each raw value is written once in code and documented next to it.

---

## D6. Fees: record all of them

Fee parameters are stored per market in `pricing_snapshots` (T3), so every price row carries the fees in effect when it was pulled. Fees that depend on account tier, not on the market, are kept in config and listed here.

**Polymarket US** (from `docs.polymarket.us/fees`, effective 2026-10-28):
- Taker fee: `Θ × C × p × (1 − p)`, Θ = 0.0695 (0.10 for Table Tennis). Per-market value: `feeCoefficient`.
- Maker rebate: Θ from −0.0035 to −0.0104 by maker tier. Tier 1 gets none.
- Taker rebate: 0–30% by volume tier.
- Combo taker fee: `C × p × [0.0695 × (1 − p) + 0.085 × (1 − p)^4]`.
- Change on 2026-11-06 at 1:00 PM ET: rebate tiers switch to a rolling 30-day window.

**Kalshi:**
- Per-event overrides: `fee_type_override`, `fee_multiplier_override`. Series-level fee type and multiplier also apply.
- The general taker fee formula has the form `coef × C × P × (1 − P)` with per-contract rounding. The exact coefficient, rounding and maker fees still need to be checked against the current Kalshi fee schedule (Open items).

---

## D7. Timestamps and speed

This is speed arbitrage, so timestamps measure staleness, not just log time.

1. **One shared tick.** Each cycle has one `tick_ts`, shared by both platforms, so rows from Kalshi and Polymarket US at the same tick join directly.
2. **Per-request times.** Every request records `request_sent_ts` and `response_received_ts`, and `exchange_ts` when the API provides one. Latency = received − sent.
3. **Concurrent fetching.** Both platforms are fetched at the same time (async), never one after the other. The `response_received_ts` gap between the two legs of a pair is that pair's **skew**.
4. **Skew threshold.** An opportunity is only valid if both legs' books are fresh and the skew is below a threshold (value TBD from measured latency).
5. **Clock.** The collector machine is NTP-synced. Durations use a monotonic clock. Stored timestamps use UTC wall-clock time.
6. **Polling for discovery, streaming for trading.**
   - 60s polling is enough for the catalog, pricing and counts.
   - Flagged pairs should move to **WebSocket order book streams** where available. Kalshi has WebSocket channels; they likely need the API keys in `.env`. Polymarket US WebSocket support is not yet confirmed.
   - Until then, flagged books are polled faster than 60s, within rate limits.
7. **Keep the hot path fast.**
   - Keep persistent HTTP connections (keep-alive).
   - Arbitrage detection reads the latest books from memory.
   - Writing to disk is batched and runs in the background, so it never blocks detection.

---

## D8. Scope: order book first

- **The order book is the main data product.** Price discrepancies between Kalshi and Polymarket US are measured on executable prices and depth (T4), not last-trade prices.
- T1 and T3 support it: T1 feeds matching, which decides what goes into T4. T3 comes free with the list endpoints and is used for screening and counts.
- **Out of scope for now:** backfilling price history (Kalshi candlesticks, Polymarket US price history). Both can be pulled later because the endpoints keep history. Order book depth cannot be pulled later, so we collect it now.

---

## D9. Scale

**Problem:** pulling everything every 60s, plus order books, can exceed rate limits and storage if done naively.

**Measured on 2026-11-05 (first pull):**

| | Kalshi | Polymarket US |
|---|---|---|
| Open markets / events | ~182,500 / ~21,800 (combo markets excluded) | ~78,400 / ~5,500 |
| Pricing sweep per tick | ~110 requests, ~423 MB JSON, 25–28s | ~160–220 requests, ~231 MB JSON, 10–13s |
| Rows written per tick (change-only) | First tick 185k, then ~8–10k | First tick 78k, then ~1.7k |
| Catalog refresh | Reuses the pricing sweep (no extra requests) + 1 `/series` call | ~827 requests, ~50s (815 sports events re-fetched by slug) |
| Practical rate (unauthenticated) | ~4–5 req/s before 429s (well below the documented 20) | 20 req/s with no 429s |

- The Polymarket US `/v1/events` list truncates nested markets for big sports events. Spreads, totals and props (~53k markets) only appear when the event is fetched by `?slug=`.
- Kalshi `min_updated_ts` returns only ~100–250 changed markets per minute (mostly status changes). It can only be combined with `series_ticker` / `mve_filter`. Useful for incremental status updates, not for prices.
- The T6 sweep runs at 6 req/s, so a full pass over all Polymarket US markets takes about 3.6 hours. Flagged markets get T6 on every book tick.

| Constraint | Kalshi | Polymarket US |
|---|---|---|
| Rate limit | Basic tier: 200 read tokens/s, about 10 tokens per request ≈ **20 req/s** sustained, bursts up to ~60. 429 responses have no `Retry-After` | **25 req/s**, shared across endpoints, per IP |
| Page size | `GET /markets` up to 1000, `GET /events` up to 200, cursor pagination | To verify |
| Order book | 1 request per market (`GET /markets/{ticker}/orderbook`) | 1 request per market (`GET /v1/markets/{slug}/book`) |

**How we deal with it:**
1. **Measure first.** A one-off sizing pull counts open events and markets and bytes per row before any 60s loop runs.
2. **Filter at the source.**
   - Kalshi: `mve_filter=exclude` drops multivariate/combo markets.
   - Snapshots cover open markets only.
3. **Incremental catalog.** Re-pull only markets that changed (Kalshi `min_updated_ts`, Polymarket US `updatedAt`), not the full catalog every tick.
4. **Tiered polling.**
   - Tier A, flagged pairs: streaming, or fastest polling.
   - Tier B, all open markets: top of book every 60s from list endpoints.
   - Tier C, catalog text: on change, about hourly.
5. **Rate limiter per platform.**
   - Each platform gets a token bucket set to about 80% of its limit.
   - Tier A requests go first.
   - On a 429 we back off with jitter.
6. **Order book budget.** About 1,200 Kalshi and 1,500 Polymarket US requests per minute in total, so order books are limited to flagged markets. If flagged markets exceed the budget, the ones closest to a profitable spread go first.
7. **Change-only writes.** An order book is written only when `book_hash` changes. A pricing row is written only when a price, size or status changes, with one heartbeat row per market per N minutes.
8. **Storage.** Parquet, zstd-compressed, partitioned by `platform/date/table`. Queried with DuckDB. Raw JSON is archived separately.

---

## D10. Candidate generation (added 2026-11-17)

Narrow Kalshi × Polymarket US down to likely-identical pairs before any LLM call. Code in `candidates/`, one folder per category and, for sports, one per sport. Each subcategory documents its filters in its own `FILTERING.md`.

- **Scope, sports:** 12 sports kept (football, basketball, soccer, hockey, golf, baseball, motorsport, esports, tennis, MMA, boxing, table tennis). Dropped: chess, rugby, cricket, darts, volleyball, lacrosse, Kalshi "Other", pickleball. Label mapping in `candidates/sports/sport_map.py` and `SUBCATEGORIES.md`.
- **Events first, then markets.** Events are paired on a structured key (season for futures; teams + scheduled start for games), then markets inside them on player, team code or team name, plus direction (same or opposite side).
- **Each sport has its own code** in `candidates/sports/<sport>/`, written for that sport's markets. Nothing is shared across sports except loading, name normalisation, rules templates and output writing (`candidates/common.py`).
- **No game or market is hard-coded.** Each sport's `config.py` names its leagues, every bet type on each platform, and its futures series. Seasons, dates, teams, players and lines are read from the data. Anything the config does not recognise is reported by name and count, never dropped silently. Match on tickers, slugs and structured fields, never titles.
- **T1 gained bet-type fields (2026-11-17):** `competition`, `competition_scope` (Kalshi event `product_metadata`), `sports_market_type`, `line` (Polymarket US), `yes_team_code`, `no_team_code`, `event_teams`. Catalogs pulled before this lack them. The first refresh after the change rewrites every row into catalog history once, because the content hash includes the new fields.
- **The LLM reviews rules templates, not markets.** Rules text is reduced to a template (names, dates, numbers removed). Each Kalshi × Polymarket US template pair is reviewed once; approved pairs make every matching market pair `auto_confirmed`. Changed wording means a new template, so the pair goes back to review.
- **T7 `candidate_pairs`** is written to `data/candidate_pairs/<category>/<subcategory>/` with `pair_id` in the T2 format, `match_key`, `direction`, `template_pair_id` and `status`.
- **Close window (2026-11-17):** only pairs whose earlier close is within 7 days of the run. Kalshi `close_time`; Polymarket US event end (the market `endDate` is padded ~2 weeks).
- **Kalshi game start times:** sports milestones (`GET /milestones`), pulled with each catalog refresh into T1 `game_start_ms` and `game_id`. Football tickers carry only the date.
- **Football** is split into `football/nfl/` and `football/cfb/` (`candidates/sports/football/FILTERING.md`). Nov 25 catalog, 7-day window: NFL 16/16 games, 6,301 pairs / 75 template pairs; college 76 games, 5,477 / 82.
- **Basketball** (`candidates/sports/basketball/FILTERING.md`): NBA only (`basketball/nba/`), same layout as football. Nov 25: 30/30 Kalshi games, 6,288 pairs / 86 template pairs, mostly player lines.
- **Scope decision (2026-11-25):** NFL and NBA only until the end-to-end path (template review → T2 → books → edge) works. Other sports and leagues stay in the "Not covered" report.
- **Baseball** (`candidates/sports/baseball/FILTERING.md`): futures by season + player/team; games by league + scheduled start + teams, then (period, bet, team, line) for winner, run line, totals, team totals, first 5 innings, single innings, first-inning run, extra innings. Verified on the 2026 World Series Game 5 (`selftest.py`).

---

## Open items (to verify before or during build)

- [x] Polymarket US max page size: 500. The book has an exchange timestamp (`transactTime`). BBO path: `/v1/markets/{slug}/bbo`.
- [ ] Polymarket US: a Markets WebSocket exists (`docs.polymarket.us/api-reference/websocket/markets`). Auth and message format not yet checked. Also check whether market `id` stays fixed when a slug is edited.
- [x] Kalshi series fee fields: `fee_type` (`quadratic`, `quadratic_with_maker_fees`, `quadratic_with_combo_maker_fees`), `fee_multiplier`. `GET /series` returns all ~15.8k series in one call. Endpoint costs: default 10 tokens.
- [ ] Kalshi: exact coefficient for each `fee_type` (taker ≈ 0.07 × C × P × (1 − P), maker fees on `*_with_maker_fees`; check the published fee schedule). Rounding is documented at `docs.kalshi.com/getting_started/fee_rounding`.
- [ ] Kalshi WebSocket: **requires authentication**, even for public channels (`orderbook_delta`, `ticker`). Needs a Kalshi API key ID and RSA private key. `.env` currently holds only `KALSHI_API`.
- [ ] Unauthenticated Kalshi reads get ~4–5 req/s. Check whether authenticated REST reads get the documented Basic tier (20 req/s).
- [x] Polymarket US URL: `https://polymarket.us/event/{event_slug}` (confirmed 200). Kalshi URL: `https://kalshi.com/markets/{series_ticker}` (unconfirmed; kalshi.com blocks automated fetches).
- [ ] Final status/result for markets that leave the open list: use Kalshi `min_updated_ts` and Polymarket US `/v1/markets/{slug}/settlement`. Not built yet.
- [ ] Skew threshold for D7.4, set from measured latency.
- [ ] Mapping from each raw status to `status_canonical`.
- [ ] Data workstream ownership: Colin leads in the project plan. Confirm who builds this collector.
