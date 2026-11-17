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
