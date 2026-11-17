# Embedding-based market matching plan

## Goal

Find likely pairs of Kalshi and Polymarket US markets for the later match explanation and verification stages. Embedding similarity ranks candidates; it does not establish that contracts are equivalent or approved for trading.

## Proposed structure

```text
collector/                         # Existing collection and standardized profiles
embeddings/
    prepare.py                     # Load profiles and build model input text
    embed.py                       # Generate and optionally store embeddings
    search.py                      # Rank cross-platform candidate markets
evaluation/
    retrieval/
        cases.csv                  # Human-labeled pairs and review notes
        evaluate.py                # Retrieval metrics and missed examples
notebooks/
    explore_matches.ipynb          # Explore profiles, candidates, and results
docs/
    embeddings_plan.md             # This plan
```

This is a starting point, not a fixed design. Keep the implementation small and split files only when useful. The collector's `data/market_profiles/market_profiles.parquet` is the initial input; it contains historical snapshots until the collector is refreshed. Decide with the team which collector and Polymarket platform will be used before integrating the workflow.

## Approach

1. **Prepare profiles.** Load market profiles for both platforms, retain market IDs and metadata, and build consistent text from the event title, market title, and YES meaning. Handle missing fields. Keep full rules and dates available for review. The generated `summary` repeats other fields, so omit it from the initial input.
2. **Generate embeddings.** Try `google/embeddinggemma-300m` locally using its recommended query/document conventions. Begin with a small sample. Record the model version and input configuration. Model access requires accepting its terms and authenticating with Hugging Face; the first download requires network access. Decide whether to cache vectors after measuring the cost of regeneration.
3. **Retrieve candidates.** Search Polymarket US profiles for each Kalshi market and rank the nearest candidates by similarity. Display IDs, scores, titles, YES meanings, dates, and rules so people can inspect them. Avoid treating a score as a probability or a verified match.
4. **Evaluate and iterate.** Create reviewed examples and measure how often a known matching market is returned in the top 5 and top 10, as well as its rank. Search against a realistic pool that includes each known counterpart, rather than only comparing labeled pairs. Include difficult non-matches and review misses. Compare the initial text representation with one that adds rules; keep a held-out test set separate from model and input selection.
5. **Integrate later.** Pass candidates to the planned explanation, numeric checks, rules review, and human review stages. Determine the interface with the team before integrating with match flags or any trading workflow.

## Evaluation across the workflow

Evaluation should cover each stage and the overall system. This branch starts with retrieval: recall at K and ranks against human-labeled examples. Later stages need their own checks—for example, whether verification catches different deadlines or outcome definitions, and whether profit calculations match hand-calculated examples. End-to-end evaluation should test whether valid opportunities survive the full pipeline and false opportunities are rejected. Retrieval scores alone cannot establish contract equivalence or profitability.

## Open decisions

- Confirm the team's choice of collector and whether the target is Polymarket US or international Polymarket.
- Agree on how reviewers label equivalent, non-equivalent, and uncertain pairs, including complementary outcomes and baskets.
- Choose a realistic candidate pool, evaluation split, and initial sample size.
- Decide where cached vectors and generated results belong, and whether they should be excluded from Git.
- Select a date-filtering strategy only after testing for false exclusions; trading close dates do not necessarily equal resolution dates.
