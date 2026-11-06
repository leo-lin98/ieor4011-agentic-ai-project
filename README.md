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
