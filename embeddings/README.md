# Event matching

This tool finds Polymarket US events that may match Kalshi events. Each event can contain several contracts. The results are suggestions: check the contracts and their rules before deciding whether they match.

## How it works

1. **Group contracts into events.** The collector saves open markets from both platforms. The tool groups contracts that belong to the same event.
2. **Turn titles into embeddings.** The `google/embeddinggemma-300m` model runs locally and turns each event title into a vector. Vectors are saved in `data/embeddings/events.sqlite3` so they can be reused.
3. **Choose candidates.** Category mappings limit which events are compared. See the explanation below.
4. **Rank suggestions.** Each Kalshi event is compared with eligible Polymarket US events. The most similar titles appear first.

Sports made up a large share of the events in our evaluation snapshot: 7,411 of 21,054 Kalshi events and 7,515 of 8,779 Polymarket US events. Elections was the largest Kalshi category, with 9,310 events. These counts change as markets open and close.

### How category filtering works

We compare sports events with sports events, and non-sports events with non-sports events. We treat each platform's saved category as its classification: for example, a Polymarket US event in **culture** is treated as non-sports. All recognized non-sports categories can be compared with one another.

When both events are classified as sports, the tool also checks their tags and subcategories. For example, it can rule out a football event when looking for a basketball match. If the category or sport is missing or unclear, the tool keeps the candidate eligible to avoid missing a potential match. The mappings are in `embeddings/config/`.

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

Run a small test with 10 Kalshi events and all Polymarket US events in the saved data:

```bash
python -m embeddings.embed --queries 10 --top-k 10 --representation title
```

The command saves up to 10 suggestions per Kalshi event in `data/embeddings/candidates.json`. Each suggestion includes a similarity score. The 10 Kalshi events are spread across the saved data; they are not a random sample.

Useful options:

- `--query-id KALSHI:<event-id>` selects an event. Repeat it to select more than one.
- `--queries <number>` changes how many Kalshi events to search. Set it at least as high as the number of saved Kalshi events to search all of them.
- `--device mps` uses a supported Apple GPU.
- `--revision <commit>` pins the model version. The output records the version used.

The saved profiles, embeddings, and results stay on your computer; they are not committed to Git.

## Review the results

Create a report database and start the local browser:

```bash
python -m embeddings.reporting.database data/embeddings/candidates.json data/embeddings/report.sqlite3 --volume-snapshots data/pricing_snapshots/KALSHI
python -m embeddings.reporting.browser
```

Open <http://127.0.0.1:8765/> and select **View candidates**. The browser shows only the events and suggestions saved in your run. Rebuild the database after a new run. Volume comes from saved Kalshi data and may be missing or incomplete.

Reviews are saved in your browser. Download the review CSV to keep a backup. Press Ctrl+C to stop the server.

## What we found

We reviewed 20 randomly selected non-sports Kalshi events, sampled with higher-volume events more likely to be selected. For 17, the suggestions included a likely exact or possible match (human labeled based on event title). The other three were marked as having no counterpart on Polymarket US.

We also reviewed five handpicked pairs whose Polymarket events were missing from the original candidate pool and confirmed them as likely exact matches. In a later run using a larger snapshot, all five appeared first. The 15 likely-exact matches labeled in the random sample also appeared first. These results used the earlier category mappings, before the sports versus non-sports filter was introduced.

The [evaluation folder](../evaluation/retrieval/README.md) contains the reviewed pairs and saved results. The results are a useful early check, but the sample is small, contains no sports events, and many labels came from the original suggestions.
