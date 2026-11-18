# Event candidate retrieval

Install `pip install -r requirements.txt`, accept the terms for `google/embeddinggemma-300m` on Hugging Face, and authenticate with `hf auth login`. The first run downloads the model. Pin a Hugging Face commit with `--revision` for reproducibility. If the model loader cannot resolve a moving branch to a commit, the command requires a pinned revision so stale vectors cannot be reused under an unchanged branch name.

```sh
python -m unittest discover -s tests -v
python -m embeddings.embed --queries 10 --top-k 10 --revision main --representation title
python -m embeddings.reporting.html data/embeddings/candidates.json data/embeddings/report.html
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

For the compact full-run output, render manageable ranges using `python -m embeddings.reporting.batches data/embeddings/full-candidates.json data/embeddings/review-101 --start 101 --limit 100`. Import the previous CSV before reviewing another page, then download an updated CSV to carry your reviews forward.

## Querying results by category and volume

Build a separate SQLite report from an existing retrieval run; this does not load the model or change the vector cache:

```sh
python -m embeddings.reporting.database data/embeddings/full-candidates.json data/embeddings/report.sqlite3 \
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

## Browse the report in a web browser

After building the SQLite report, start the local read-only explorer from the project directory:

```sh
python -m embeddings.reporting.browser
```

Open <http://127.0.0.1:8765/>. Choose a category, search, set a minimum volume, and sort the results; select **View candidates** to see the saved matches for an event. The default non-sports filter excludes unclassified events; select **Unclassified** or **All categories** to include them. The server only listens on this computer. Stop it with Ctrl+C. Pass a different report path as the first argument or use `--port 8766` if the default port is occupied. The explorer reads the current report database, so rebuild it when you want newer retrieval results or snapshots.

## Search and review contracts

Rebuild the reporting database with `embeddings.reporting.database` after upgrading, so it includes contracts from both platforms' saved profiles. Start the explorer as above and select **Search and review contracts**. Search Kalshi or Polymarket US by title, option, or ID; the page reports whether the contract's event was included in the retrieval run. This searches the saved profile snapshot, not the live platforms. Results are limited to the first 100; refine broad searches.

For a Kalshi contract, choose **Review this contract**. A confirmed match in the dataset requires the exact Polymarket contract ID; use the built-in Polymarket search to find it. The review records whether that contract's event appeared in the displayed event candidates. Other outcomes distinguish a match found on Polymarket but missing from the dataset, verified absence on Polymarket, an incomplete search, and uncertainty. Check rules and record evidence before claiming absence; use the incomplete or unsure outcome when you cannot verify it. A missing event candidate does not tell you why it was missed, and event similarity does not establish contract equivalence.

Reviews are saved in the browser's local storage, not in SQLite, and may be lost if browser data is cleared. Download the CSV regularly. The CSV is a separate contract review format; it is not directly compatible with the existing `contract_cases.csv` evaluator. To summarize the exported reviews:

```sh
python -m evaluation.retrieval.review_contracts path/to/contract-reviews.csv
```

This counts outcomes and computes the fraction of reviewed matching contracts whose corresponding event was among the displayed candidates, excluding source events not run and targets outside the candidate pool. It is a contract-weighted measure of displayed **event** coverage, not full-pool recall or a contract matching accuracy score. Reviews selected by convenience may not represent the whole dataset. Set reviewer, split, and event group for later analysis, and keep related events in the same split.

To check collection independently of event retrieval, use **Search and review contracts** at the top of the comparison page; it searches all saved profiles, including contracts whose events were not in the embedding run. The comparison page also links to the contracts belonging to each event.

### Top-10 event review on the comparison page

Select **Review suggestions** on a Kalshi event and mark each relevant suggestion as **Likely exact match** or **Possible match**. Save once to associate all selected event IDs, ranks, and judgments with that Kalshi event. Earlier related labels remain unclassified until you choose a category. If none is related, choose whether a related event is in the collection but was not suggested, is on Polymarket US but missing from the collection, does not exist on Polymarket US (verified), or remains uncertain. Use **Find an event in the collection** to search by title or ID and select an event that was missed. A listing on international Polymarket does not establish availability on Polymarket US. Saved profiles are a snapshot; absence from them alone does not prove platform-wide absence.

Progress counts reviewed events rather than individual selections. The found-versus-missed count is conditional on the reviewed sample and labels; it measures candidate selection and retrieval together, not contract-level accuracy. If fewer than 10 suggestions were saved, only those saved are reviewable. Reviews are stored in the browser; download the CSV regularly. Each CSV row represents one Kalshi event and stores selected event IDs and ranks as JSON arrays. It is a separate format from the existing pairwise evaluator. Export reviews before changing runs, because the labels describe the suggestions at review time.

### Random evaluation set

In the comparison page's **Random evaluation set** panel, choose volume-weighted or uniform sampling, choose non-sports or all categories, then create a set of 20. Review and save each event before moving to the next. Classify multiple related suggestions as likely exact or possible, or record why none was found. The set picker lets you return to previous sets, and the evaluation CSV includes all 20 events (including unreviewed ones), their saved suggestions, choices, sampling method, seed, volume coverage, and source snapshot timestamp. The sample and labels are stored in this browser; download the CSV as a backup.

Sampling is without replacement from the Kalshi events in the saved retrieval run. Volume-weighted selection uses each event's observed total volume as its relative weight; events with zero or missing volume are excluded, and partial totals are used as observed. Uniform sampling includes them. Non-sports excludes unclassified events. The sample's fixed seed makes the draw reproducible from the same report database. Suggestions are frozen with the sample, but collection searches use the currently loaded database; keep the report database unchanged during an evaluation. Creating another set does not replace earlier sets.

The displayed counts separate likely exact and possible matches for reviewed events in this sample; an event may count in both. A volume-weighted sample emphasizes high-volume events and its raw fraction is not an unbiased estimate of performance across all events. A sample of 20 also has substantial uncertainty. Events marked missing from the platform, missing from the collection, or unsure are counted separately from found and missed-in-collection.

### Evaluate an exported random set

Summarize its saved original suggestions:

```sh
python -m evaluation.retrieval.evaluate_event_sample path/to/evaluation.csv
```

For another embedding run generated for the same Kalshi IDs, compare the saved labels with its top suggestions:

```sh
python -m evaluation.retrieval.evaluate_event_sample path/to/evaluation.csv --results data/embeddings/other-model.json
```

Run retrieval with `--query-id` for each of the sampled event IDs and `--top-k 10` or more. Keep the profile snapshot, candidate pool, category mappings, and representation fixed across models. The evaluator reports the saved run configuration and excludes events whose labeled targets are outside that run's candidate pool from that target-type hit-rate denominator; check pool sizes, membership and mappings independently. It reports likely-exact and possible judgments separately and counts at most one hit per Kalshi event per judgment type. Events with no reviewed labels of a given type do not enter its denominator. The labels came from the original model's suggestions, so these results are an exploratory comparison, not unbiased recall: review the other model's unique suggestions before drawing conclusions. Event-review CSVs do not yet record a match category for an event marked as in the collection but missed, so those cases are not treated as likely-exact or possible positives automatically.
