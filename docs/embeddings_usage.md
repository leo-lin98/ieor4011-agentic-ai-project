# Candidate retrieval

Install `pip install -r requirements.txt`, accept the terms for `google/embeddinggemma-300m` on Hugging Face, and authenticate with `hf auth login`. The first run downloads the model. Use a pinned Hugging Face commit with `--revision` for reproducibility. The saved profiles may be stale.

```sh
python -m unittest discover -s tests -v
python -m embeddings.embed --queries 10 --candidates 1000 --top-k 10 --revision main
python -m embeddings.report data/embeddings/candidates.json data/embeddings/report.html
python -m evaluation.retrieval.evaluate data/embeddings/candidates.json evaluation/retrieval/cases.csv
```

The initial candidate pool is the first N Polymarket profiles, not a representative sample. For meaningful evaluation ensure every labeled counterpart and query is in the pool and increase `--top-k` as needed. Evaluation rejects absent queries and counterparts; an unreturned counterpart is a miss, but the current output does not distinguish filtering from a low rank. Label binary market pairs in the CSV, using equivalent, complementary, unrelated, or uncertain; group related markets using `event_group` and assign whole event groups to development or test. Evaluate splits separately by supplying separate CSV files. Do not use held-out results to select inputs or mappings. Compare runs using `--include-rules`.

Reports are read-only. Resolve reviewer disagreements outside the report and retain reviewer and notes in the CSV. Generated files live under ignored `data/embeddings/`. No vectors are cached. Search is exact and iterates through the candidate pool for each query; large runs can be expensive. Scores are similarities, not probabilities or verification. League filtering and later trading integration are not enabled.
