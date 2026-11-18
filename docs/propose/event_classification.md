# Event-pair classification

## Goal

Classify candidate Kalshi and Polymarket US event pairs after embedding-based retrieval. This stage identifies pairs worth examining at the contract level; it does not establish that contracts match or that an arbitrage opportunity exists.

## Approach

Use the OpenAI [Decisions API](https://developers.openai.com/api/docs/guides/decisions) to classify **one event pair at a time**: a Kalshi query event and one retrieved Polymarket US candidate. Evaluate each candidate independently; more than one candidate may be relevant, and none may be relevant.

The classifier selects one of four categories:

| Category | Meaning | Next step |
| --- | --- | --- |
| Exact event match | The events ask the same underlying question about the same occurrence. | Compare their contracts and resolution rules. |
| Related event | The events ask different questions, but their contracts could have a useful logical relationship. | Investigate possible contract relationships and resolution rules. |
| Unrelated | The events have no plausible relationship useful for contract matching. | Do not advance the pair to contract analysis. |
| Uncertain | The available information is insufficient to decide. | Retain for further context or review; do not silently discard. |

For example, Champions League winner and semi-finalist markets can be related even though they ask different questions. A particular team's winning may imply that it reached the semi-finals. The contract stage must check the team, season, direction of the implication, and resolution rules before relying on it.

## Input

Test the same event pairs with two inputs:

1. **Titles only:** Send the Kalshi and Polymarket US event titles.
2. **Titles plus subtitles and descriptions:** Send the same titles, adding each event's subtitle and event description where available.

Keep the classification instructions and choices the same for both tests so we can measure the effect of the added text. Do not include categories, tags, or contract information in either test. Use event IDs to track results, but keep them outside the classifier input. Missing or conflicting subtitles and descriptions should be marked as unknown.

## Browser review and evaluation

Use a browser-based review tool to work through a randomized sample of Kalshi query events. For each query, show the query and its 10 retrieved Polymarket US candidates in their original retrieval order, alongside the classifier's label for each candidate. Keep the query visible while reviewing the candidates. Moving to the next query should return the view to the top.

For each candidate, mark the classification as correct or incorrect. If it is incorrect, select the correct label: exact event match, related event, unrelated, or uncertain. Allow reviews to be saved and resumed, and provide an export of the reviewed pairs and their labels. Record the sample and classifier configuration so the review can be reproduced.

Run both input variants on the same reviewed pairs and compare their classifications against the human labels. The resulting labels can also support future in-context learning experiments: keep examples used to develop prompts separate from a held-out set for evaluation.

## Evaluation considerations

- Evaluate on human-reviewed pairs covering all four categories, including hard negatives, recurring events, and related but non-identical questions. Keep a held-out set for final evaluation.
- Compare the two input variants on the same human-reviewed pairs. Measure results by category, especially exact or related pairs incorrectly rejected as unrelated, as well as cost and latency. Record missing and duplicated subtitle or description fields to interpret the comparison.
- Compare in-context examples when useful, using development examples separate from the held-out evaluation set.
- Evaluate retrieval separately: a classifier cannot find a related event that was absent from its candidate list. Include relationships such as winner versus semi-finalist when measuring candidate recall.
- Decide from evaluation how uncertain pairs receive additional context or review and how many retrieved candidates to classify. A probability score, if used, needs validation on labeled data before setting thresholds.

This document records the proposed workflow; the classification stage has not yet been implemented.
