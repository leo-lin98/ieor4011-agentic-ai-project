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

From the repository root, use the repository's Python environment. Create
`.venv` with `python -m venv .venv` if it is missing, and install the
embedding dependencies there.

```bash
.venv/bin/python -m pip install -r embeddings/requirements.txt
```

If you need to cache the model, accept the EmbeddingGemma license on Hugging
Face, authenticate with `.venv/bin/hf auth login`, and download the pinned revision.

```bash
.venv/bin/hf download google/embeddinggemma-300m --revision 57c266a740f537b4dc058e1b0cda161fd15afa75
```

Smoke-test the workflow on a small fixture, then run it on the saved catalog.

```bash
EMBEDDINGS_MODEL_SMOKE=1 .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m embeddings run
```

The run command embeds the open contracts in the saved T1 catalogs using the
catalog timestamps as the cutoff. It assigns the next run ID automatically and
prints the completed directory. Each run keeps its frozen inputs, metadata, and
candidate pairs together. Frozen catalogs with identical content share one
physical copy via hard links. The model runs locally from the cache.

Press Ctrl-C to pause a run once it prints a resume command. Continue with
the same run ID to reuse its saved embedding batches, ranked blocks, and
original settings.

```bash
.venv/bin/python -m embeddings run --resume run-001
```

Incomplete runs stay in hidden checkpoint directories. Checkpoints store the
embeddings and ranking progress on disk. Retrieval removes the checkpoint
after publishing the validated results for review.

### Try a smaller embedding model

`--model nomic` uses Nomic Embed Text v1.5 with a 1,024-token input limit.
Cache the pinned revision once, then start a separate run.

```bash
.venv/bin/hf download nomic-ai/nomic-embed-text-v1.5 \
  --revision e9b6763023c676ca8431644204f50c2b100d9aab \
  --include '*.json' --include model.safetensors --include vocab.txt
.venv/bin/python -m embeddings run --model nomic
```

Add `--snapshot-run run-001` with a completed run ID to reuse its exact
catalog. A resumed run uses its original model and settings.

### Review and evaluate a run

Open the URL printed by the review command.

```bash
.venv/bin/python -m embeddings review --run-id run-001
```

Compare the settlement conditions before marking a pair **Match** or **No
match**. Open **Find more Polymarket US contracts** to search titles, Yes
outcomes, and contract IDs in the frozen catalog beyond the top 10. Select a
result to review it, or use the ID lookup when you already know the contract ID.
A Kalshi contract can also be marked **No corresponding Polymarket US market**
for the frozen catalog if you have checked beyond the ranked candidates.
Kalshi contracts appear in a fixed shuffled order for each run. The review
queue skips contracts with a saved no-market finding or **Mark review complete**.
Use the contract ID lookup and **Reopen review** to revisit completed work.
A match means the conditions permit an arbitrage payoff at suitable prices.
Order books and fees are checked separately. Review decisions are shared in
`evaluation/retrieval/reviews.sqlite`, alongside the review and evaluation code.

In **Benchmarks**, create and select a set. Save each verified pair as a match
and use **Add to benchmark** to include it. Mark pairs you found independently
of retrieval, including eligible missed pairs, then freeze the set to keep it
fixed for evaluation.

```bash
.venv/bin/python -m embeddings evaluate --run-id run-001 --benchmark first-review
.venv/bin/python -m embeddings run --snapshot-run run-001 --truncation head-tail
.venv/bin/python -m embeddings evaluate --run-id run-001 --benchmark first-review --compare-run run-002
```

Use the run IDs printed by the corresponding commands. Abandoned runs may
leave gaps in the numbering. Pair recall counts the fraction of eligible benchmark
pairs retrieved. Query hit rate counts the fraction of eligible benchmark
queries with at least one retrieved match.
