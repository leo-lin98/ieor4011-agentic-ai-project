# Market embedding input

Embed one combined text per market with local EmbeddingGemma 300M through Sentence Transformers. Use each open Kalshi contract to query open Polymarket US contracts from the same frozen catalog snapshot.

```text
Event: <event_title>
Market: <market_title>
Yes title: <yes_title>
Rules: <rules_primary for Kalshi, or description for Polymarket US>
```

## Why these fields

In the saved catalog snapshots, `event_title`, `market_title`, and `yes_title` are populated for 100% of markets on both platforms; `rules_primary` is populated for 100% of Kalshi markets, and `description` for 100% of Polymarket US markets. Future coverage may differ.

Together, they provide the event, the specific market or outcome, and the condition for a Yes payout. This helps distinguish markets within the same event and recognize related markets across platforms. The text is not guaranteed to be unique: retain `market_uid` as the unique identifier and `platform` and `event_uid` for filtering and grouping.

## Preparation and evaluation

Omit empty fields and exact duplicates after normalizing capitalization and whitespace, keeping the first occurrence. Retain partially redundant text. Embed the full selected rule field where the model's input limit permits; make truncation explicit and measure its effect.

Filter candidates by the parent event’s sports status before ranking. After trimming and case-folding the catalog category, `sports` marks a sports event. Other populated categories mark non-sports events. When both events have a known sports status, only rank pairs in the same group. A Kalshi query with a missing category searches both groups, and a Polymarket US candidate with a missing category is eligible for either group. Report missing categories and events excluded due to conflicting categories in the run metadata. Return up to 10 candidates per Kalshi contract, ordered by cosine similarity.

Measure recall@10 using known matches, including matches found independently of ranked results. Report how many benchmark queries and known matches were eligible under the run’s filters. Use a fixed benchmark snapshot when comparing runs.

## Output and review storage

Store candidate-generation runs separately from human reviews:

```text
embeddings/                       # Input preparation, retrieval, and evaluation
data/embeddings/runs/run-001/     # Local run artifacts
├── metadata.json
└── candidates.parquet
evaluation/retrieval/             # Review, evaluation, and shared labels
└── reviews.sqlite
```

Each run has its own folder. `metadata.json` records the run ID and creation time, embedding model and version, input fields and their platform-specific mapping, preprocessing and truncation settings, similarity metric, candidate selection settings, and the catalog snapshot or input data version. Preserve or identify the actual input snapshot so a run can be reproduced even after the current catalog changes.

`candidates.parquet` contains one row per Kalshi–Polymarket US pair in that run, with `run_id`, `kalshi_market_uid`, `polyus_market_uid`, and `similarity_score`. The `run_id` remains with each row if files are copied or combined; validate that it matches the run ID in `metadata.json` and the folder name. Keep the Parquet file and metadata together so the run configuration remains available. A market can appear in multiple pairs; do not impose a one-to-one constraint.

Commit `evaluation/retrieval/reviews.sqlite` to share labels and frozen benchmarks. Key decisions by the two market UIDs and store the label, review timestamp, and rule fingerprints for both markets. A `match` means the contracts' settlement rules permit an arbitrage payoff at suitable prices. A `no_match` means the pair doesn't support that payoff. The pricing step uses current order books and fees to check executability. Leave uncertain pairs unreviewed. Carry labels forward when the same pair appears again and flag changed rules for re-review.

Show one Kalshi contract at a time with its top 10 Polymarket US results and their scores. Join against the selected run's catalog snapshot to show titles, outcomes, complete settlement rules, clickable links, and exact contract IDs. Label series-level and event-level links accordingly. Allow reviewers to look up and save missed matches by contract ID.

Keep candidate reviews distinct from the existing `match_flags` workflow. A reviewed match can feed that workflow once its verification criteria are met. Labels collected only from retrieved candidates do not reveal missed matches; use independently identified matches as well when measuring recall.
