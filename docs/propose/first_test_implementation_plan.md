# First test: event classifier

## Goal

Test whether the classifier can identify useful relationships between events. Review its answers, save the correct labels, and use those labels to see whether changes to the input or instructions improve its performance.

The classifier uses the four categories defined in [event classification](event_classification.md): **exact event match**, **related event**, **unrelated**, and **uncertain**. A related event may contain contracts with a useful logical relationship. The classifier does not verify those contracts or find arbitrage.

## 1. Choose events and run the classifier

1. Choose how many **Kalshi events** to test and, optionally, a category. The tool randomly selects those events from the existing embedding results and saves the selection so it can be repeated.
2. For each selected Kalshi event, use all its saved Polymarket suggestions, up to 10, in their original order.
3. Send each Kalshi event and one suggestion to the Decisions API. The API assigns one of the four labels. Repeat for every suggestion.
4. Save the results, including the label, confidence, original rank, and any failed or refused requests. Allow failed requests to be retried.

**Example:** Choose 15 Kalshi events. If each has 10 suggestions, the tool classifies 150 pairs. Before starting, show the number of events and pairs to be classified. Some events may have fewer than 10 saved suggestions.

Read the API key from the environment, show progress, and record usage and estimated cost when available.

## 2. Review the answers

Open the results in a browser and review one Kalshi event at a time:

- Keep the Kalshi event visible while scrolling through its suggestions.
- Show all suggestions in their original order, with their similarity scores and classifier labels. Clearly mark failed or refused requests.
- Mark each classifier label correct or incorrect. If it is incorrect, choose the correct label; optionally add a note. Allow a label to be entered directly when a request failed.
- Save progress, resume later, and export the reviewed pairs. Warn before leaving unsaved changes. Next and previous return to the top of the next event.

Keep these reviews separate from the existing retrieval reviews, which use different labels.

## 3. Try different inputs

Start with the two inputs described in the existing proposal:

1. **Titles only:** the two event titles.
2. **Titles plus context:** the titles and any available event subtitles and descriptions.

For this comparison, leave categories, tags, and contract information out of the model input. The context fields are not separate in the current candidate records and need to be read from the catalog. Record missing or conflicting fields and the actual input sent.

Start without in-context examples. Make the instructions and optional examples editable so they can be tested later. Save each experiment separately, including its model, instructions, input, event selection, and results. Allow the same events and suggestions to be run again with a different configuration; keep earlier results for comparison.

## 4. Measure performance

Store your correct label separately from each model run so the same reviewed pairs can be used to compare experiments. Keep the event content and the prediction shown during review for context; if event content changes, flag the label for review.

Show how often the model agrees with your labels for each category and which labels it confuses. Pay particular attention to exact or related events that the model calls unrelated. Count failed and refused requests separately. Compare input versions on the same reviewed pairs, and show cost and time where available.

Split reviewed examples into a **development set** for improving instructions and choosing ICL examples, and a **held-out test set** for checking the final result. Keep related instances of the same event together, and never put held-out examples in the prompt.

## 5. Verify before use

Test event selection, classifier responses and failures, saving and exporting reviews, and comparing runs. Check that navigation preserves reviews and works with a keyboard. Then, if an API key and network access are available, run a small live test and review the results.

This test measures the classifier on suggestions that retrieval already found. It does not measure relevant events missing from the suggestions. The classifier and evaluator are not yet implemented.
