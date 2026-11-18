# Classification prototype

Set `OPENAI_API_KEY` in the project root `.env`, then install `requirements.txt`.

Preview or save a reproducible sample (one event and up to ten candidates by default):

```sh
python -m evaluation.classification select --output data/embeddings/classification-sample.json --preview
python -m evaluation.classification select --output data/embeddings/classification-sample.json
python -m evaluation.classification run data/embeddings/classification-sample.json --output data/embeddings/classification-run.json
```

Use `--events N` when selecting, and set `--max-pairs` to at least the displayed pair count when running. Use `--variant context` for a second run with the same sample and a different output path. Use `--retry-failed` to retry unsuccessful pairs in an existing run. The browser review interface supports reviewing and scoring one run at a time.

## Review predictions

Start the existing report browser:

```sh
python -m embeddings.reporting.browser
```

Open http://127.0.0.1:8765/classification and choose your run JSON file. For each candidate, accept the prediction or select the correct label, and optionally enter a note. Reviews save automatically in browser storage; export a backup to keep a portable copy or import one later. The confusion matrix and accuracy use reviewed pairs with successful predictions. Failed and refused requests are counted separately. Labels are shared across runs by event pair; changed titles are flagged for review.

## Revised labels and prompt

New runs use `same outcome`, `direct outcome relationship`, `no direct relationship`, and `insufficient information`. A direct relationship requires a specific outcome of either event to necessarily constrain an outcome of the other; shared topics or correlations alone do not qualify. Historical runs retain their original prompts and choices when resumed. The review page maps the old names to the new names for comparison, without modifying the saved historical files. Earlier labels were judged under a broader definition of “related”; revisit those reviews when applying the new criterion.

To test the revised prompt on the existing 20-event selection, choose a new output file:

```sh
python -m evaluation.classification run data/propose/sample-20.json --max-pairs 200 --output data/propose/run-20-revised.json
```
