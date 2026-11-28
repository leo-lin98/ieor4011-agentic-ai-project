# Polymarket US to Kalshi event matching plan

For each open Polymarket US non-sports event, retrieve up to **10 distinct Kalshi events** from the same frozen collector snapshot. Leo’s proposer compares the individual contracts within each event pair. Stefan’s settlement judge reviews the proposed contract pairs. Sports with a supported deterministic matcher continue through Stefan’s sports workflow.

## 1. Update the `embeddings` branch

- In [`embeddings.embed`](../../embeddings/embed.py), default the query platform to `POLYUS` and the document platform to `KALSHI`. Keep an explicit `--query-platform {POLYUS,KALSHI}` option for older workflows and targeted Kalshi lookups. Call `encode_query` and `encode_document` according to the selected roles. The [SQLite cache](../../embeddings/cache.py) already keys vectors by platform, model revision, representation, and role, so the first reversed run can populate the new entries alongside the old ones.
- Add `--scope {non-sports,sports,all}` with `non-sports` as the default. Group the open contract rows into complete events before applying scope to both venues. Use the existing category mappings and report counts for missing, unmapped, and conflicting classifications. For the initial scoped runs, exclude unclassified events from `non-sports` and `sports`, and make them available in `all`. Apply category and sport checks using each event’s actual platform in [`search.py`](../../embeddings/search.py).
- Process every eligible Polymarket US event by default. Keep repeatable `--query-id` for targeted runs and `--queries N` for samples. Validate that selected IDs belong to the query venue and scope. Rank against the full eligible Kalshi pool and emit the ten best candidates, or all eligible candidates when fewer than ten exist. Include a result even when a query has zero candidates.
- Keep title-only embeddings as the baseline. Preserve the existing `query` and `candidates` records, similarity scores, and child contract details. Add versioned metadata describing `query_platform`, `candidate_platform`, `scope`, `top_k`, and the input snapshot alongside the model revision and mapping hash. Keep both inline candidate events and the existing compact event-ID lookup format readable.

Proposed command for the full sweep. The output path keeps it alongside earlier Kalshi-first runs.

```bash
python -m embeddings.embed --query-platform POLYUS --scope non-sports \
  --top-k 10 --top-only --output data/embeddings/polyus-to-kalshi.json
```

## 2. Update Leo’s `feature/match-proposer` branch

- In `proposer/adapter.py` and `proposer/contract.py`, read the retrieval direction and validate each query and candidate against its venue. Represent the retrieval record as a query event and ranked candidates. Treat historical files without direction metadata as Kalshi-first, for both inline and compact records.
- In `proposer/runner.py`, normalize each queued event pair to **Kalshi, Polymarket US** before calling the existing proposer. Preserve each candidate’s retrieval score and rank. Keep venue-specific leg IDs, rule hashes, payout directions, and settlement inputs in their existing positions.
- Update `proposer/audit.py` and the run summary to identify the actual query event and venue. Keep the **10 retrieved candidates** in the audit and record which candidates were sent. Expose the proposer’s existing three-candidate limit as a separate setting so a run can send all ten when desired.

## 3. Wire the branches into `stefan_edits`

- Update `judge/pipeline.py` to use the Polymarket US query scope and pass ten retrieval candidates into the proposer handoff. Its snapshot and `--query-id` validation currently assume Kalshi queries. Use the shared event scope for this stage and keep the catalogs, proposals, and settlement reviews together in the run directory.
- Keep Stefan’s deterministic sports commands as the sports entry point. The integrated embedding pipeline uses `non-sports`. The standalone `sports` and `all` scopes support coverage checks and experiments.
- Verify the handoff through the existing `--proposer-root` checkout option, then integrate the tested embedder and proposer into `stefan_edits` so the pipeline can run from a single checkout.

## 4. Verify before running the full catalog

- Test both retrieval directions and old result files. Cover scope filtering on both venues, ambiguous categories, fewer than ten eligible candidates, and cache reuse when a run is repeated with the same roles.
- Run a saved non-sports sample through retrieval, proposal, and settlement review. Check the output rank and score, the Kalshi and Polymarket US IDs, and their rule hashes at each handoff. Include known counterparts and events with no known counterpart.
- Update the review browser and evaluation tools to read the query venue from run metadata. They currently assume a Kalshi query. Reuse confirmed event pairs as examples, group them by Polymarket US event, and supplement them with independently found matches. Measure recall at 3 and 10, track scope exclusions separately from ranking misses, and keep older run reports readable.

## Optional contract selection

The first pass needs the category scope and event selector. If we later want a narrower investigation, add a Polymarket US market-ID allowlist or a close horizon. Resolve eligible contracts to their parent events, keep sibling contracts available for context, and enforce the contract scope on proposed pairs before settlement review. Define the close-time semantics for each venue before enabling a horizon.
