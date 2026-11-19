# Classification results database plan

## Goal

Store classification runs, predictions, and human evaluations in a shared SQLite database. Reuse a successful prediction whenever the same pair is submitted with the same classifier input and configuration, while preserving the history needed to reproduce and compare evaluations.

SQLite becomes the source of truth for the classification workflow. The updated workflow does not need to maintain compatibility with the current JSON run format or browser storage. Retain the existing JSON files as migration sources and backups.

## Database design

Use a dedicated database under `data/`, with schema migrations and foreign keys enabled. Choose its final path and document it in the classification README.

- **Runs:** a run ID, creation time, sample selection metadata, classifier configuration, and provenance. Preserve the model, exact instructions, label choices, and input variant used for each run.
- **Run pairs:** a run ID, query and candidate event IDs, input text, candidate rank, and links to the relevant prediction or request attempt. Preserve the original ordering and the complete manifest data needed to reconstruct the sample.
- **Predictions:** a cache key, exact input and classifier configuration, outcome, label, confidence, usage, and timestamps. Deduplicate successful predictions by cache key; multiple runs may reference one prediction.
- **Attempts:** each API attempt, including status, error or refusal details, elapsed time, usage, and timestamps. Failures and refusals remain visible and can be retried without deleting history.
- **Human reviews:** append-only judgments with event IDs, reviewed event text or input, label, note, timestamp when available, and source provenance. Link to a prediction or run when the source supports it. Record corrections as new judgments and identify the current judgment only when it can be determined safely.
- **Imports and conflicts:** record the source file, content fingerprint, import time, and migration outcome. Track conflicting reviews for resolution without discarding either judgment.

Preserve raw historical labels and their original definitions. Earlier runs used a broader meaning of “related”; mapping their label names to newer names does not make the judgments equivalent. Accuracy comparisons must specify compatible label definitions and exclude unresolved or stale reviews as appropriate.

## Prediction reuse

Build a deterministic cache key from a canonical serialization of the ordered event IDs, exact classifier input, model, instructions, label choices, and input variant. Include every other request setting that can affect the result. Store the configuration itself as well as its hash so that cached predictions remain auditable.

Before sending a request, look up the key. Reuse a successful prediction if one exists; otherwise, make a request and save the attempt and result. A change to event text, prompt, labels, model, or other relevant setting produces a new key. Failed and refused attempts do not satisfy a successful cache lookup. Provide a deliberate way to force a fresh prediction when repeatability or model behavior is being studied, retaining both attempts and making the selected result explicit.

## Migrate existing data

1. Inventory the saved manifests, classification runs, and exported reviews in `data/propose`, as well as any other explicitly identified exports. Include the latest review export in Downloads if it is available. Browser-only reviews cannot be imported until exported.
2. Import manifests and runs with their original pair order, inputs, configuration, predictions, statuses, and available timing and usage. Link repeated identical successful predictions to the same cache entry without losing run membership or provenance.
3. Import review exports with their original labels, notes, reviewed inputs, and source information. Deduplicate identical judgments across exports. Retain differing judgments for the same pair and reviewed context; do not infer which is current from file order or modification time alone. Report conflicts for resolution.
4. Make imports repeatable: importing the same source twice must not duplicate runs, attempts, or reviews. Preserve missing historical fields as unknown rather than inventing values. Never assign import time as the original review time.
5. Produce a reconciliation report with counts per source: runs, queries, pairs, prediction statuses, reviews, duplicates, conflicts, and missing data. Compare imported counts and key fields to the source files and investigate discrepancies before switching the workflow to the database.

At planning time, `data/propose` contains a 1-query run, three 20-query runs, a 10-query run, their available manifests, and an export containing 310 reviews. Recount and inventory again at migration time; files may change.

## Implementation sequence

1. Implement the schema, database initialization, and small storage API for runs, predictions, attempts, and reviews.
2. Implement the import and reconciliation process. Run it against copies or a new database, review its report, and preserve the original files.
3. Update sample selection and classification to create database-backed runs, consult the cache, record attempts, and resume interrupted work safely.
4. Update the review interface to select runs and save judgments in the database. Show the relevant event text, flag changed inputs and unresolved conflicts, and support correcting reviews without erasing history.
5. Update reporting to calculate accuracy and confusion matrices only from successful predictions and applicable human judgments. Report failures, refusals, missing reviews, changed inputs, and unresolved conflicts separately.
6. Document initialization, migration, selection, classification, review, conflict resolution, and backup procedures in the classification README.

## Verification and acceptance

- Reconcile every identified source file against the imported database, including run membership, pair order, statuses, review counts, and conflict counts.
- Verify that identical request inputs and configuration reuse a successful prediction without an API call; changes to relevant inputs or configuration trigger a new call.
- Verify retries preserve failed and refused attempts, and that interrupted runs can resume without losing results.
- Verify repeated imports are idempotent, differing reviews remain available, and unknown historical timestamps remain unknown.
- Verify the review interface persists judgments across sessions and flags changed text and unresolved conflicts.
- Compare database-derived metrics with a manually checked sample, accounting for historical label definitions. Run a small classification twice and confirm that the second run reuses the first run's successful predictions.
