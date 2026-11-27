# Kalshi × Polymarket US arbitrage: data collector

Collects the tables defined in [DECISIONS.md](DECISIONS.md) into `data/` as Parquet files.
Market-data endpoints on both platforms are public, so no API keys are needed.

## Setup

```bash
pip install -r requirements.txt   # httpx, pyarrow, pandas
```

## Run

```bash
python -m collector once      # catalog + one pricing tick + counts (~90s), good smoke test
python -m collector run       # scheduler: pricing every 60s, books every 60s, catalog hourly
python -m collector status    # what is on disk
```

Options: `--price-interval`, `--book-interval`, `--catalog-interval` (seconds), and `--no-stats-sweep`.

## Candidate pairs (NFL, NBA)

Deterministic matching of Kalshi × Polymarket US markets on structured fields (teams, tip-off, bet type, line),
before any LLM call. See [DECISIONS.md D10](DECISIONS.md) and each sport's `FILTERING.md`.

```bash
python -m collector catalog                       # refresh T1 first (needs the bet-type fields added 2026-11-17)
python -m candidates sports football/nfl          # writes data/candidate_pairs/sports/football/nfl/
python -m candidates sports basketball/nba        # writes data/candidate_pairs/sports/basketball/nba/
python -m candidates sports football/nfl --no-write   # report only
```

Outputs per league: `candidate_pairs.parquet` (T7), `template_pairs.parquet` (what the LLM / a person reviews,
once per rules-template pair), `candidate_pairs_<date>.csv`, `report.json`.

## Flag a pair for order-book collection

Order books (T4) are only pulled for active rows in `match_flags`. Until the matching
pipeline writes flags, add pairs by hand:

```bash
python -m collector flag add KALSHI:<market ticker> POLYUS:<market id> --note "why"
python -m collector flag list
python -m collector flag off <pair_id>
```

## Layout

| Path | Table |
|---|---|
| `data/market_profiles/market_profiles.parquet` | T0: open markets, same columns on both platforms (title, summary, rules, category, time until close). **Input to the agentic workflow** |
| `data/market_catalog/current/{KALSHI,POLYUS}.parquet` | T1: latest version of every market ever seen |
| `data/market_catalog/history/<PLATFORM>/<date>/` | T1: new or changed rows per refresh |
| `data/match_flags/match_flags.parquet` | T2 |
| `data/pricing_snapshots/<PLATFORM>/<date>/` | T3: one file per tick, change-only rows |
| `data/orderbook_snapshots/<PLATFORM>/<date>/` | T4: flagged markets, one row per price level |
| `data/platform_counts/<PLATFORM>/<date>/` | T5 |
| `data/market_stats/POLYUS/<date>/` | T6: Polymarket US per-market volume / open interest |
| `data/raw/<PLATFORM>/<date>/` | Raw catalog JSON, once per UTC day |
| `data/logs/` | Run logs |
| `data/display/T*.ipynb` | Static view of each table: concatenates its files and shows 15 rows per platform. Rebuild with `python -m collector display` |

All times are UTC epoch ms and all prices are cents. Every table has `market_uid`.

```python
import pandas as pd
cat = pd.read_parquet("data/market_catalog/current/KALSHI.parquet")
px  = pd.read_parquet("data/pricing_snapshots/KALSHI")          # all ticks
```

Pricing rows are **change-only**. A market's row is written on its first tick, when anything
changes, and every 15 minutes as a heartbeat. To get the state at time *t*, forward-fill from
the last row per `market_uid`.

## Judge non-sports contract pairs

`python -m judge pairs` reads Leo's proposer rows from T2 by default. To run independently of the proposer branch, supply a JSON array containing the contract IDs and proposed relationship.

```json
[
  {
    "kalshi_market_uid": "KALSHI:TICKER",
    "polyus_market_uid": "POLYUS:ID",
    "relationship": "identical"
  }
]
```

Use `identical` for YES-to-YES and `complementary` for Kalshi YES-to-Polymarket US NO. The optional `pair_id`, `kalshi_rules_hash`, and `polyus_rules_hash` fields can be copied from T2. Replace the example IDs with contracts in the current collector catalogs.

```bash
python -m judge pairs --input pairs.json --dry-run
python -m judge pairs --input pairs.json --model MODEL > reviews.json
```

The judge selects an economics, politics, or crypto rubric based on the catalog categories and uses the common rubric for other non-sports pairs. It checks each concrete pair's rules, including sources, deadlines, time zones, and exceptional outcomes. Sports pairs use the existing sports template workflow by default. Use `--include-sports` to test individual sports contracts with the pair judge.

Use `ordinary_mapping` to inspect the verdict for ordinary settlement. Share `human_review_questions` with the reviewer. Sports exception findings can share a question when the event, Kalshi secondary rules, and Polymarket US contract and description match. Each pair's scenario remains visible in the question. The `human_review_queue` contains the full evidence for each flagged pair. Each queued result includes the available contract evidence, proposed side mapping, and reason it needs review. A checklist difference or uncertainty goes to this queue when the model names a plausible scenario and explains the effect on both mapped payouts. Generic omissions stay visible in the checklist. Missing or changed core rules and failed judgments also go to the queue. A result of `no_difference_found` is a model finding. The pair judge does not change flags or grant trading approval.

### Run non-sports retrieval, proposal, and review together

Use the Python environment with Leo's proposer and embedding dependencies installed.
Export `OPENAI_API_KEY` and use an account with access to the embedding model.
Pass Leo's checkout when the modules live in a separate worktree.

```bash
python -m judge pipeline --proposer-root ../ieor-match-proposer \
  --query-id KALSHI:KXFEDDECISION-26DEC
```

For a live batch, run:

```bash
python -m judge pipeline --live --proposer-root ../ieor-match-proposer --queries 3
```

`--live` refreshes public Kalshi and Polymarket US markets and uses the tier judge.
`--queries` limits the sampled open non-sports events. Alternatively, repeat
`--query-id` to target known events. Use `--live --dry-run` to test collection and
retrieval without OpenAI calls.

The saved-data mode uses the proposer checkout's collector data by default.
`--data-dir` selects a different collector data directory, and `--tier` enables
tier scoring on saved data.

Each run saves frozen catalogs, candidates, run-local proposer flags, and
`reviews.json` under `data/non_sports_runs/`. The live report marks Material
and Blocking pairs with `needs_human`. The run uses isolated data and review
reports for a read-only market workflow.

### Compare independently extracted rules

Add `--structured` to `judge pairs` or `judge pipeline`. The judge reads each
contract independently and saves the extracted fields, source text, and verified
quotes. It compares supported single threshold literals and ISO-8601 cutoff
literals with explicit offsets. The model plans a shared set of scenarios from
the terms, then assesses both mapped payouts for each set of facts. The report
records the assessed amounts and exact supporting quotes under `scenario_results`.
Code derives a payout difference only when both assessed amounts are known and
different. A missing or discretionary payout remains unresolved.

Add `--check-order` to reassess the same scenario IDs and facts with the venues
presented in reverse order. The side mapping stays fixed. A change in an assessed
payout leaves that scenario unresolved and sends it to review. A structured run
uses two extraction calls, one scenario planning call, and one payout assessment.
Order checking adds a second payout assessment.

```bash
python -m judge pairs --structured --check-order --input pairs.json
```

### Assign settlement-risk tiers

`judge tier` reads exact market pairs. It verifies quoted passages, caches each
market extraction, runs deterministic checks, and calls the semantic judge once
per pair. The final tier is the highest supported finding. Material and Blocking
pairs enter human review. The report includes the code tier and cited scenarios.

Use `--fetch-only` to save market evidence without model calls. A live manifest row
includes `pair_id`, `kalshi_event_ticker`, `kalshi_ticker`, `polymarket_venue`,
`polymarket_market_slug`, and optionally `polymarket_side` (defaults to Yes).
Gamma pairs specify `polymarket_venue: "polymarket_international"` and
`polymarket_event_slug`. The Polymarket US gateway uses `polymarket_venue: "polymarket_us"`.
A pair can also use the existing `kalshi_market_uid` and `polyus_market_uid` fields
to load from the local catalogs.

```bash
python -m judge tier run --pairs pairs.json --fetch-only --output evidence.json
python -m judge tier run --pairs evidence.json --output tier-reviews.json
python -m judge tier score --labels human-tiers.csv --predictions tier-reviews.json
```

Use `--code-only` for the provisional code tier. That mode still needs model
access for any uncached extraction. The scorer uses `Your tier` from a CSV or TSV
with a `#` or `pair_id` column. Missing predictions count toward the denominator
for Material and Blocking recall and appear separately in the report. Include
the expected check in `Your notes` so it appears alongside missed cases.
