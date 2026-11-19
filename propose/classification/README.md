# Classification workflow

The source of truth is `data/propose/classification.sqlite3`. The database is initialized and migrated automatically on first use; SQLite foreign keys are enabled on every connection. Install `requirements.txt` in the project environment and set `OPENAI_API_KEY` in `.env` before making requests. Data under `data/` is ignored by Git.

## Import and reconciliation

The original project JSON files are retained in `data/archive/classification-json-sources.zip`. Extract them before reimporting them into a new database. Import runs, manifests, and exported browser reviews with:

```sh
unzip data/archive/classification-json-sources.zip -d data/propose
python -m propose.classification import data/propose/*.json ~/Downloads/classification-reviews*.json > reconciliation.json
```

Pass explicit existing paths if the Downloads glob has no matches. Imports are transactional and repeatable; a changed file is recorded as a new source version. The JSON report gives per-source counts and missing timestamps and lists conflicting judgments. Browser-only reviews must first be exported from the old browser. A historical review without a timestamp retains a null timestamp. Historical labels and instructions are stored verbatim. Old and new meanings of “related” are not interchangeable. The initial migration is summarized in [the reconciliation report](../../docs/propose/classification_database_reconciliation.md).

## Select and classify

```sh
python -m propose.classification select --events 10 --seed 42
python -m propose.classification run --sample SAMPLE_ID --max-pairs 100
python -m propose.classification run --resume RUN_ID --max-pairs 100 --retry-failed
```

Selection prints a sample ID and stores the complete manifest in SQLite. Add `--output path.json` for an optional backup, or `--preview` to avoid saving. An existing JSON manifest can also be passed as the positional argument to `run`. The run prints its ID before requests begin. Results commit after every pair, so the same ID can resume interrupted work. `--retry-failed` retries failures and refusals; `--force` deliberately makes fresh requests even for previously successful or cached pairs. A forced result is selected for that run while earlier predictions and attempts remain in the database. `--variant context`, `--profiles`, `--model`, and `--instructions` control the request. Exact input, ordered IDs, model, instructions, choices, variant, and request settings form the cache key. Cache hits do not require an API key.

## Review and reporting

The review and scoring code lives in `evaluation/propose/classification/` and uses the same classification database.

```sh
python -m embeddings.reporting.browser
```

Open http://127.0.0.1:8765/classification and select a run. The reporting browser also needs its existing embedding report database. Each card displays the classifier input and previous judgments. Saving a judgment appends a record, explicitly resolves the displayed earlier judgments for that run and input, and retains their history. A changed input and conflicting or definition-uncertain judgments are flagged. Review the source context before resolving a conflict. New corrections are appended rather than overwriting prior judgments.

Accuracy and the confusion matrix include successful predictions with an explicit judgment for the selected run and exact input, using that run's labels and definition. Failures, refusals, missing predictions and reviews, changed inputs, incompatible historical judgments, and conflicts are reported separately. Historical reviews whose definitions cannot be established safely require a new judgment for the selected run.

## Backup

Stop writers before copying the database, or use SQLite's backup API for a consistent live backup. Back up `data/propose/classification.sqlite3` together with the archived original JSON sources; copying browser storage is not necessary for reviews saved in the new interface. Use `--database` on `run` and `import`, `--classification-database` on `select` and the report browser, to work with an alternate database.
