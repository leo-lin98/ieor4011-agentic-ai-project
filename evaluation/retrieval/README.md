# Retrieval evaluation

Create a set in the local [review browser](../../embeddings/README.md#review-the-results). For each event, label the suggestions or search the candidate venue's collection for a missed match. Save each review, then choose **Test current run** to score the loaded report or **Download evaluation CSV** to save the labels.

## Score another run

Use the downloaded CSV with a retrieval run in the same direction. Include the reviewed queries in the run.

```bash
python -m evaluation.retrieval.evaluate_event_sample path/to/evaluation.csv \
  --results data/embeddings/polyus-to-kalshi.json
```

The report gives hit rates for likely exact and possible matches and counts reviewed queries missing from the run and targets outside the candidate pool. Omit `--results` to score the suggestions stored in the CSV. Searching independently for missed events reduces the bias from labeling only displayed suggestions.

## Inspect full rankings

```bash
python -m evaluation.retrieval.run_expanded_sample --reviews path/to/evaluation.csv \
  --scope non-sports --output data/embeddings/expanded-sample-results.json
```

This uses the local pinned model and compares ranks before and after per-pair category and sport checks within the selected scope. Use `--profiles` to point to the snapshot that contains the reviewed queries.
