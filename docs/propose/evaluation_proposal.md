# Event classification evaluation proposal

## Goal

Review event-pair classifications in a browser and build a reusable human-labeled dataset. Compare the titles-only and titles-plus-subtitles/descriptions input variants on the same pairs, and later use reviewed examples to test in-context learning.

## Browser review

Select a randomized sample of Kalshi query events. For each query, display the query and its 10 retrieved Polymarket US candidates in their original retrieval order, with the classifier's label for each candidate. Keep the query visible while reviewing the candidates. Moving to the next query returns the view to the top.

For each candidate, the reviewer marks the model label correct or incorrect. If incorrect, the reviewer selects the correct label: exact event match, related event, unrelated, or uncertain. Allow an optional note, saving and resuming progress, and exporting reviewed pairs.

## What to store

### Classification run

Assign a run ID to each configuration. Store the shared settings once per run:

- Model name and exact version or snapshot, when available.
- Full classification instructions, category definitions, and any in-context examples.
- Input variant and relevant API settings.
- Retrieval dataset and configuration, classification code version, and run date and time.

Create a new run when the model, prompt, examples, input variant, or other relevant settings change.

### Pair result

For each classified pair, store its run ID, both event IDs, the actual event input sent, retrieval rank and similarity score, and the model's returned label, confidence, and probabilities. Record errors and refusals separately. Store token usage and cost when available or calculated.

Store the actual input per pair because titles and descriptions can change. Shared instructions and model settings need only be stored once per run when they are identical for every request.

### Human review

Store the human label independently of a model run, keyed to the event pair and the reviewed event content or snapshot. Retain the run and model decision shown to the reviewer, the correct/incorrect judgment, any corrected label, and an optional note. This preserves review context while allowing the same human label to be compared with future runs. A changed event or ambiguous review may require a new judgment rather than automatically reusing an old label.

### Review sample

Record the sampling method, random seed, source dataset, and selected query IDs so the review can be reproduced. Preserve all available candidates and their original ranks for each selected query.

## Compare results

Compare both input variants against the same reviewed pairs. Report results by category, especially exact or related pairs incorrectly labeled unrelated, alongside cost and latency. Keep examples used to develop prompts separate from a held-out evaluation set, grouping related events together when splitting.

This review evaluates classifications among retrieved candidates; it does not by itself measure relevant events missed by retrieval.
