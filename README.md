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

## Contract retrieval and review

Install the embedding dependencies and cache the pinned model once. Accept the
EmbeddingGemma license on Hugging Face and authenticate before caching it.

```bash
pip install -r embeddings/requirements.txt
hf download google/embeddinggemma-300m --revision 57c266a740f537b4dc058e1b0cda161fd15afa75
python -m embeddings run
```

The run command embeds the open contracts in the saved T1 catalogs using the
catalog timestamps as the cutoff. It assigns the next run ID automatically and
prints the completed directory. Each run keeps its frozen inputs, metadata, and
candidate pairs together. The model runs locally from the cache.

Open the URL printed by the review command.

```bash
python -m embeddings review --run-id run-001
```

Compare the settlement conditions before marking a pair **Match** or **No
match**. Use the Polymarket US ID lookup to review a pair outside the top 10.
A match means the conditions permit an arbitrage payoff at suitable prices.
Order books and fees are checked separately. Review decisions are shared in
`review/embeddings/reviews.sqlite`.

In **Benchmarks**, create and select a set. Save each verified pair as a match
and use **Add to benchmark** to include it. Mark pairs you found independently
of retrieval, including eligible missed pairs, then freeze the set to keep it
fixed for evaluation.

```bash
python -m embeddings evaluate --run-id run-001 --benchmark first-review
python -m embeddings run --snapshot-run run-001 --truncation head-tail
python -m embeddings evaluate --run-id run-001 --benchmark first-review --compare-run run-002
```

Use the run IDs printed by the corresponding commands. A failed run may leave
a gap in the numbering. Pair recall counts the fraction of eligible benchmark
pairs retrieved. Query hit rate counts the fraction of eligible benchmark
queries with at least one retrieved match.
