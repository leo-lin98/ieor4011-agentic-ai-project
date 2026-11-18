# Embeddings system: structure and technical debt

This is a historical review from November 17, 2026. Some workflows described below have since been changed or removed; see [the evaluation guide](../../evaluation/retrieval/README.md) for the current files.

## Purpose and current state

The embeddings system finds potentially related **events** across Kalshi and Polymarket US. An event groups one or more individual market contracts. The output is a ranked list of candidate event pairs for human review; a similarity score does not establish that contracts are equivalent or that an arbitrage opportunity exists.

The default model is `google/embeddinggemma-300m`, loaded locally through Sentence Transformers. The repository contains retrieval, caching, review, and evaluation code, but the event and contract evaluation CSVs currently contain only headers. There is therefore no reviewed benchmark in the repository to establish recall or select the best text representation.

## How the pieces fit together

```text
Collector → market_profiles.parquet
                  │
                  ▼
      Group contracts into events
                  │
                  ▼
   Build title or title + options text
                  │
                  ▼
 Embed Kalshi queries and Polymarket documents
       ↕ reuse vectors from SQLite cache
                  │
                  ▼
  Filter by category/sport, rank by similarity
                  │
                  ▼
    Candidate JSON → HTML review → CSV labels
                         │             │
                         └─────────────┴→ Evaluation
```

1. **Input:** The collector produces `data/market_profiles/market_profiles.parquet`, a snapshot of market-level records for both platforms.
2. **Preparation:** Contracts are grouped by platform and event ID. Inconsistent or missing classification is handled conservatively so that uncertain categories do not automatically exclude candidates. The model receives either the event title or the title plus up to 12 sorted, distinct option descriptions. Rules remain available for review but are not embedded.
3. **Embedding and cache:** Kalshi events are encoded as queries and Polymarket events as documents. Normalized vectors are stored in SQLite, keyed by event, input fingerprint, model, resolved model revision, representation, and role. Batches are committed as they are generated.
4. **Retrieval:** Eligible Polymarket events are selected using category and sport mappings, then ranked using the dot product of normalized vectors (cosine similarity). The default pool includes all Polymarket events in the profile snapshot.
5. **Review:** JSON results can be rendered as HTML cards showing event candidates and their contract details. Reviewers can label pairs and export CSV; labels must be moved or merged into the evaluation dataset manually.
6. **Evaluation:** Event evaluation reports ranks, filter exclusions, and recall at 5 and 10 for labeled matches. A separate lexical baseline ranks contracts inside labeled event pairs.

## Key files and documents

| File | Role |
| --- | --- |
| [README.md](../../README.md) | Project setup and collector overview; describes the market profiles used as input. It does not currently introduce the embeddings workflow. |
| [embeddings/README.md](../../embeddings/README.md) | Practical commands, model access, and the basic review workflow. |
| [docs/embeddings/event_matching_plan.md](event_matching_plan.md) | Event-first design and intended phases, including representation comparison and review. Closest high-level description of the current architecture. |
| [embeddings/embed.py](../../embeddings/embed.py) | Command-line entry point: selects events, loads the model, calls the cache and ranking code, and writes results and run metadata. |
| [embeddings/prepare.py](../../embeddings/prepare.py) | Loads profiles, groups contracts into events, detects metadata issues, and creates embedding text. |
| [embeddings/cache.py](../../embeddings/cache.py) | Generates and persists normalized vectors in a content-addressed SQLite cache. |
| [embeddings/search.py](../../embeddings/search.py) | Validates mappings, applies category and sports filters, and performs exact similarity ranking. |
| [embeddings/config/categories.json](../../embeddings/config/categories.json), [sports.json](../../embeddings/config/sports.json) | Hand-maintained cross-platform category and sport mappings used by search. |
| [embeddings/reporting/browser.py](../../embeddings/reporting/browser.py), [database.py](../../embeddings/reporting/database.py) | Serve the local results browser and build its report database. |
| [evaluation/retrieval/README.md](../../evaluation/retrieval/README.md) | Current evaluation data, scripts, and saved results. |
| [tests/test_embeddings.py](../../tests/test_embeddings.py), [tests/test_volume.py](../../tests/test_volume.py) | Small unit-test suite for grouping, caching, filtering, ranking, reporting, evaluation, and volume aggregation. |

The [category breakdown](figures/market-category-breakdown.svg) shows the initial November 5, 2026 profile snapshot.

Generated caches and reports normally live in `data/embeddings/`, which is ignored by Git. The checked-in profiles are a snapshot, so results describe that data rather than necessarily current markets.

## What technical debt means here

Technical debt is work deferred through an expedient design or incomplete process that makes future changes more costly or risky. Some items below are unfinished product or evaluation work as well as code debt; they matter because they limit how safely the system can be changed or trusted.

### Highest priority

1. **No measured retrieval quality.** Both label files are empty. We cannot yet say how often true counterparts are found, whether category filters discard them, or whether adding options improves results. Build independently reviewed, event-grouped development and held-out test sets before selecting a representation. Reviews of only displayed suggestions can miss counterparts that retrieval failed to surface; include independently identified positives.
2. **Limited automated coverage of the full workflow.** The core tests use a fake model and small examples. They do not establish that the real model loads, that a run produces usable outputs end to end, or that all review/export and evaluation paths work together. Add focused integration checks and representative fixtures after establishing a reproducible environment.
3. **Multiple output formats are coupled to consumers.** Normal output stores full candidate events in each result; `--top-only` stores event IDs with a shared lookup in metadata. Full-rank evaluation explicitly rejects top-only output, and the contract evaluator expects the normal format. These constraints are easy to miss and increase maintenance cost. Define and document a versioned result schema and compatible consumers.
4. **Review labels require manual transfer and discipline.** Browser-local storage and CSV import/export make review portable, but labels can be lost if not exported and must be manually merged. Grouping related events and avoiding split leakage also depend on human editing. The evaluator checks whether a declared group spans splits, but cannot establish that related events were assigned the same group. A more reliable review data workflow and validation would help.

### Next priorities

5. **Documentation has drifted.** At the time of this review, the earlier plan described a market-level design and the project README did not link the usage guide. The outdated plan has since been removed; consult the event-matching plan and current guides.
6. **Reproducibility is incomplete.** The code records the resolved model commit, representation, mapping hash, candidate IDs, and profile issue counts, which is useful. Dependencies in `requirements.txt` have lower bounds rather than a lock, and output metadata does not fingerprint the input profile snapshot or record the full runtime configuration. A pinned environment and input/run provenance would make comparisons easier to reproduce.
7. **Scaling is costly.** Ranking scans eligible candidates per query; normal output also stores full rankings and exclusions per query and repeats displayed event details. The streamed writer reduces peak output memory, but full outputs and evaluation can still grow substantially. Measure real runtimes and file sizes before choosing indexing or format changes.
8. **Presentation and pipeline responsibilities are mixed.** The report generator includes HTML, CSS, and Python rendering in one file; batch reporting adds a custom streaming JSON reader and optional compressed pages. The entry point also handles orchestration, selection, model loading, and serialization. Separating stable interfaces from presentation would make changes and tests easier.
9. **Input and label validation could be stronger.** Missing or conflicting event titles are reported but events remain eligible for embedding; an empty title can produce little or no useful input. Evaluation silently skips unrecognized relationship labels, and the correctness of the human-assigned split depends on the labels. Validate and report these cases explicitly rather than letting them be overlooked.
10. **Cache and configuration need operational maintenance.** Cache entries accumulate as inputs or revisions change and are reclaimed by deleting the cache. Category and sport mappings are manually curated; inaccurate mappings can exclude real counterparts. Review exclusions with labeled examples and establish a maintenance policy before changing filtering rules.

## Suggested cleanup order

1. Make setup and the current architecture easy to reproduce and keep the current guides up to date.
2. Create a small, independently reviewed benchmark with difficult positives and negatives; preserve a held-out split.
3. Agree on a versioned result schema and add targeted end-to-end tests for generation, reporting, and evaluation.
4. Use benchmark results and profiling to guide representation, filter, performance, and refactoring decisions.

## Verification performed for this review

I inspected the implementation, plans, configuration, label files, and tests. I attempted `python -m unittest discover -s tests -v` using the workspace virtual environment, but test discovery failed because that environment lacks `numpy` and `pandas`; no test results or real-model run are claimed here. No application code or data was changed for this review.
