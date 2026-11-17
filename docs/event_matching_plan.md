# Event-based market matching plan

## Goal

Find candidate pairs of Kalshi and Polymarket US **events** first, then compare individual contracts within promising event pairs. Event similarity is a retrieval signal; it does not establish that contracts are equivalent.

## Phase 1: Build event records

- Group the existing market profiles by platform and `event_uid`, retaining the contracts belonging to each event.
- Store the event title, category, sport classification, option count, and contract references.
- Inspect missing IDs or titles and conflicting metadata. Keep ambiguous category or sport classifications broad rather than excluding candidates.
- Treat a single-contract event as an event with one option.

## Phase 2: Prepare and cache event embeddings

- Compare two representations:
  - **Title only:** the event title.
  - **Title plus options:** the title and a bounded, deterministic selection of distinct option descriptions.
- Keep rules out of event embeddings. Retain them for contract review.
- Cache vectors locally with the event ID, input fingerprint, model revision, and representation. Reuse unchanged embeddings and regenerate changed or missing ones.
- Print the selected device, progress, elapsed time, and cache reuse counts.

## Phase 3: Retrieve event candidates

- Embed each Kalshi event as a query and rank eligible Polymarket US events by similarity.
- Apply the existing broad category and sports mappings with conservative fallbacks for missing or ambiguous classifications.
- Return distinct event candidates, with scores, event IDs, titles, option counts, and example options.
- Support selecting a varied set of Kalshi events for testing rather than relying only on the first rows.

## Phase 4: Review events and contracts

- Update the HTML report to show one Kalshi event alongside horizontally scrollable Polymarket event cards.
- Let reviewers inspect the options belonging to each event.
- For a selected candidate event pair, rank or display its contract options together with YES meanings, dates, links, and full rules.
- Keep event similarity and contract-level review visibly separate.

## Phase 5: Evaluate and choose a representation

- Create a reviewed dataset of matching and non-matching event pairs across categories, including difficult examples such as NATO versus UN Secretary-General.
- Measure event recall at 5 and 10 and the rank of known counterparts against the full candidate pool. Record whether a known counterpart was excluded by filtering.
- Compare title-only against title-plus-options using the same cases. Keep related events together in development or held-out test splits.
- Evaluate contract retrieval separately within known matching event pairs.
- Select the representation based on held-out results and review of misses; don't require option lists to overlap completely.

## How to test it

- Run automated checks for event grouping, single-contract events, conflicting metadata, cache invalidation, filter fallbacks, and ranking.
- Run a small sample on MPS, then rerun it to confirm unchanged embeddings come from cache.
- Inspect the report for distinct event cards and accessible contract details.
- Run the labeled evaluation against all saved Polymarket events and compare the two input representations.
