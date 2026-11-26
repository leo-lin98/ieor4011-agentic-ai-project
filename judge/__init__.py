"""LLM judge for rules-template pairs (DECISIONS.md D10: the LLM reviews templates, not markets).

    python -m judge sports football/nfl              # judge template pairs not yet reviewed
    python -m judge sports basketball/nba --all      # re-judge every template pair (new prompt or model)
    python -m judge sports football/nfl --dry-run    # print the prompts, call nothing
    python -m judge sports football/nfl --show       # print the verdicts on file

One call per template pair. The call sees both rulebooks in full, one concrete example pair for each
direction, and the claim the matching code makes. It returns identical / not_identical / unsure with a
filled checklist. Verdicts go to the league's template_approvals.json (which the candidates run reads) and
every request/response is appended to data/judge/<category>/<subcategory>/verdicts.jsonl.

Human entries in template_approvals.json (reviewed_by not starting with "llm:") are never overwritten.
"""
