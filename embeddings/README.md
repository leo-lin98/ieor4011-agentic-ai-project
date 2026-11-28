# Event matching

This tool finds up to 10 Kalshi candidates for each Polymarket US event by default. Each event can contain several contracts. Check the suggested contracts and their rules before deciding whether they match.

## How it works

1. **Group contracts into events.** The collector saves open markets from both platforms. The tool groups contracts that belong to the same event.
2. **Turn titles into embeddings.** The `google/embeddinggemma-300m` model runs locally and turns each event title into a vector. Vectors are saved in `data/embeddings/events.sqlite3` so they can be reused.
3. **Choose candidates.** Category mappings limit which events are compared. See the explanation below.
4. **Rank suggestions.** Each Polymarket US event is compared with eligible Kalshi events. The most similar titles appear first.

Sports made up a large share of the events in our evaluation snapshot: 7,411 of 21,054 Kalshi events and 7,515 of 8,779 Polymarket US events. Elections was the largest Kalshi category, with 9,310 events. These counts change as markets open and close.

### How category filtering works

We compare sports events with sports events, and non-sports events with non-sports events. We treat each platform's saved category as its classification: for example, a Polymarket US event in **culture** is treated as non-sports. All recognized non-sports categories can be compared with one another.

The default scope includes recognized non-sports events on both venues. Choose `--scope sports` to search sports or `--scope all` to include events with missing, unmapped, or conflicting categories. Known sports and non-sports categories still stay apart in `all`. When both events are classified as sports, their tags and subcategories can rule out known sport mismatches. The mappings are in `embeddings/config/`.

## Get started

Run these commands from the project directory.

1. Install the dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Accept the terms for `google/embeddinggemma-300m` on Hugging Face and log in with `hf auth login`. The first run downloads the model.
3. If you do not have `data/market_profiles/market_profiles.parquet`, collect the market data:

   ```bash
   python -m collector once
   ```

## Find matches

Search all non-sports Polymarket US events against the eligible Kalshi pool. For a full run, `--top-only` saves each candidate event once and omits full ranking lists.

```bash
python -m embeddings.embed --top-only --output data/embeddings/polyus-to-kalshi.json
```

Each suggestion includes a similarity score. The output includes the query and candidate venues, scope, and a SHA-256 fingerprint of the input profiles.

Useful options:

- `--query-id POLYUS:<event-id>` selects an event in the current scope. Repeat it to select more than one.
- `--queries 10` samples up to 10 queries spaced evenly across the eligible events.
- `--query-platform KALSHI` searches from Kalshi to Polymarket US.
- `--device mps` uses a supported Apple GPU.
- `--revision <commit>` pins the model version. The output records the version used.

The saved profiles, embeddings, and results stay on your computer; they are not committed to Git.

## Review the results

Build a report from your results and the same input profiles, then start the local browser.

```bash
python -m embeddings.reporting.database data/embeddings/polyus-to-kalshi.json data/embeddings/polyus-to-kalshi-report.sqlite3
python -m embeddings.reporting.browser data/embeddings/polyus-to-kalshi-report.sqlite3
```

Open <http://127.0.0.1:8765/>. The browser reads the query and candidate venues from the report. Use `--profiles` when the retrieval command used a custom snapshot. For volume-weighted sampling, pass `--volume-snapshots` for the query venue when building the report, for example `data/pricing_snapshots/POLYUS`.

In **Random evaluation set**, choose a sample size, category, and sampling method. Label the suggestions and save each review. Use **Search collection** to label a related event you found outside the suggestions. Choose **Test current run** to see hit counts at 3 and 10. Use **Download evaluation CSV** to keep the labels and test a different run. [Evaluation commands](../evaluation/retrieval/README.md)
