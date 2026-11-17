# Event candidate retrieval

Install `pip install -r requirements.txt`, accept the terms for `google/embeddinggemma-300m` on Hugging Face, and authenticate with `hf auth login`. The first run downloads the model. Pin a Hugging Face commit with `--revision` for reproducibility. If the model loader cannot resolve a moving branch to a commit, the command requires a pinned revision so stale vectors cannot be reused under an unchanged branch name.

```sh
python -m unittest discover -s tests -v
python -m embeddings.embed --queries 10 --top-k 10 --revision main --representation title
python -m embeddings.report data/embeddings/candidates.json data/embeddings/report.html
python -m embeddings.embed --queries 10 --top-k 10 --revision main --representation title_options --output data/embeddings/options.json
```

The default candidate pool contains all saved Polymarket events. `--candidates` restricts it for a quick trial, and `--query-id ID` (repeatable) selects specific Kalshi events. Without IDs, queries are spread across the sorted event list. Use the same explicit IDs and full candidate pool to compare representations. `--device mps` requests Apple GPU acceleration.

The SQLite cache at `data/embeddings/events.sqlite3` persists normalized vectors across runs, committing every batch. Its key includes platform, event ID, SHA-256 of the exact input, model and resolved revision, representation, and query/document role. Changes generate new vectors; existing entries remain available. Delete the cache to reclaim space or force a complete rebuild. The cache and generated reports are ignored by Git. Event inputs contain the title and, for `title_options`, up to 12 distinct deterministically sorted option descriptions, each capped at 200 characters. Rules are only shown in the contract review. Progress includes device, elapsed time and reuse counts.

Review event pairs in `evaluation/retrieval/cases.csv`, with event IDs in `kalshi_uid` and `polyus_uid` and `matching` or `nonmatching` in `relationship`. Keep related events in the same `event_group` and split (`development` or `test`), and record reviewer and notes. The file currently contains no reviewed cases; labels must be independently reviewed before results or a representation can be selected. Include difficult nonmatches such as NATO versus UN Secretary-General when present in the saved data.

Run both representations with every labeled Kalshi event specified by `--query-id` and no candidate limit, then evaluate each output:

```sh
python -m evaluation.retrieval.evaluate data/embeddings/candidates.json evaluation/retrieval/cases.csv
python -m evaluation.retrieval.evaluate data/embeddings/options.json evaluation/retrieval/cases.csv
```

Evaluation records full eligible-pool ranks even when `--top-k` limits the report, identifies filter exclusions, and computes recall at 5 and 10 for matching labels by split. Missing labeled queries or candidates are errors. Choose a representation using development results and assess it on held-out groups. Contract options are displayed for manual comparison within each event pair. For a separate lexical contract baseline, add reviewed contract pairs to `evaluation/retrieval/contract_cases.csv`, ensure the known event pairs appear among the returned cards (increase `--top-k` as needed), and run `python -m evaluation.retrieval.contracts data/embeddings/candidates.json evaluation/retrieval/contract_cases.csv`. Contract and event labels still need independent review. Similarity is a retrieval signal, not proof of equivalence. Profiles are a snapshot and may be stale.

## Recording reviews in the report

Each candidate card has a review selector and notes. The Kalshi card also has a separate “No match found among displayed suggestions” choice; it does not claim no match exists elsewhere in the pool. Set the reviewer and split before recording a decision. Those settings apply to reviews saved or edited afterward. Each review defaults its event group to the Kalshi event ID; edit the CSV to put related events in the same group before evaluation.

The browser saves reviews locally, subject to its storage settings and its handling of `file:` pages. Download the reviews CSV as a backup. Use Import reviews CSV to merge a previous export into another report page; existing reviews take precedence when a pair appears in both. The export is not written automatically into `evaluation/retrieval/cases.csv`; move or merge it there when ready. `no_match_found` rows are notes and do not count toward recall. Reviews and exports may be lost if browser storage is cleared, so export regularly.

For the compact full-run output, render manageable ranges using `python -m embeddings.report_batches data/embeddings/full-candidates.json data/embeddings/review-101 --start 101 --limit 100`. Import the previous CSV before reviewing another page, then download an updated CSV to carry your reviews forward.

## Querying results by category and volume

Build a separate SQLite report from an existing retrieval run; this does not load the model or change the vector cache:

```sh
python -m embeddings.report_db data/embeddings/full-candidates.json data/embeddings/report.sqlite3 \
  --volume-snapshots data/pricing_snapshots/KALSHI
sqlite3 -header -column data/embeddings/report.sqlite3
```

For example, in SQLite, list classified non-sports events with the highest observed total volume and their top candidate:

```sql
SELECT e.event_uid, e.title, e.category, e.total_volume,
       e.markets_with_volume, e.market_count,
       m.candidate_title, m.score
FROM events AS e
LEFT JOIN matches AS m ON m.event_uid = e.event_uid AND m.rank = 1
WHERE e.category IS NOT NULL AND trim(e.category) <> ''
  AND lower(trim(e.category)) <> 'sports'
ORDER BY e.total_volume IS NULL, e.total_volume DESC, e.event_uid
LIMIT 50;
```

`events` contains the Kalshi queries actually included in that retrieval run; `matches` contains its displayed candidates in similarity order (not the entire eligible pool). Join without the rank condition to see all of them. Category is the saved profile classification; missing or conflicting classifications are excluded by the example, rather than assumed to be non-sports. Volume is the sum of each event's latest saved per-market Kalshi total contract count, not dollar volume or 24-hour volume. `markets_with_volume` and `market_count` reveal partial totals; missing volume is NULL. Different markets may have observations from different times. The latest timestamp across the snapshot files and the source paths are in `run_metadata`. Rebuild the report to pick up a newer retrieval run or snapshots; rebuilding replaces the report database.
