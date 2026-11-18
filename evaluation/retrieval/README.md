# Retrieval evaluation

This folder contains the event reviews and tools for checking retrieval results.

- `combined_event_evaluation.csv` contains the 20 random event reviews and five handpicked pairs confirmed by the reviewer.
- `evaluate_event_sample.py` summarizes a browser-exported event review or compares it with another retrieval run.
- `run_expanded_sample.py` reruns the 25 events against all Polymarket US events in a saved profile snapshot and records ranks with and without the filters.
- `results/` contains the detailed and summarized results of the expanded-pool run. These are historical results from before the sports versus non-sports filter.

To summarize the saved event reviews, from the project directory:

```bash
python -m evaluation.retrieval.evaluate_event_sample evaluation/retrieval/combined_event_evaluation.csv
```

Without a results file, the command cannot verify which targets were in the candidate pool. The five handpicked targets were outside the original pool, so do not interpret their absence from the original suggestions as a ranking failure. The saved review comes from a small non-sports sample; many labels were selected from the original suggestions, so it is not an independent recall benchmark. The historical results in `results/` retain the labels as they were when those runs were saved.

To rerun the expanded-pool evaluation without overwriting the saved historical result:

```bash
python -m evaluation.retrieval.run_expanded_sample --output data/embeddings/new-evaluation.json
```

This requires `data/embeddings/evaluation-profile-snapshot.parquet` and the pinned model to be available locally. The snapshot is ignored by Git. The script uses the current category and sport filters, which may differ from the historical run.
