"""Code-side validation of an LLM response against the two events that were sent."""
from collections import Counter

from proposer.contract import Event
from proposer.response import ProposedMatch, ProposerResponse, Relationship


def find_failures(response: ProposerResponse, kalshi_event: Event, polyus_event: Event) -> list[str]:
    """Return every failure in the response ([] = valid). Messages name the 0-based match index and uid."""
    kalshi_uids = {m.market_uid for m in kalshi_event.markets}
    polyus_uids = {m.market_uid for m in polyus_event.markets}
    failures: list[str] = []
    for index, match in enumerate(response.matches):
        failures.extend(_match_failures(index, match, kalshi_uids, polyus_uids))
    return failures


def _match_failures(index: int, match: ProposedMatch, kalshi_uids: set[str], polyus_uids: set[str]) -> list[str]:
    """Return the failures of one match."""
    prefix = f'match {index}:'
    failures = [f'{prefix} market {u} is not in the Kalshi event' for u in match.kalshi_legs if u not in kalshi_uids]
    failures += [f'{prefix} market {u} is not in the Polymarket event' for u in match.polyus_legs
                 if u not in polyus_uids]
    failures += [f'{prefix} market {u} appears more than once in its side'
                 for side in (match.kalshi_legs, match.polyus_legs) for u, n in Counter(side).items() if n > 1]
    failures += [f'{prefix} market {u} is on both sides' for u in sorted(set(match.kalshi_legs) & set(match.polyus_legs))]
    failures += _shape_failures(prefix, match)
    failures += _condition_failures(prefix, match)
    return failures


def _shape_failures(prefix: str, match: ProposedMatch) -> list[str]:
    """Check the leg counts required by the relationship."""
    counts = (len(match.kalshi_legs), len(match.polyus_legs))
    if match.relationship is Relationship.basket:
        if min(counts) == 1 and max(counts) >= 2:
            return []
        return [f'{prefix} basket needs 1 leg on one side and at least 2 on the other, got {counts[0]} Kalshi and {counts[1]} Polymarket']
    if counts != (1, 1):
        return [f'{prefix} {match.relationship.value} needs exactly 1 leg per side, got {counts[0]} Kalshi and {counts[1]} Polymarket']
    return []


def _condition_failures(prefix: str, match: ProposedMatch) -> list[str]:
    """Check yes_conditions cover each leg exactly once and nothing else."""
    legs = set(match.kalshi_legs) | set(match.polyus_legs)
    given = Counter(c.market_uid for c in match.explanation.yes_conditions)
    failures = [f'{prefix} yes_conditions is missing leg {u}' for u in sorted(legs - given.keys())]
    failures += [f'{prefix} yes_conditions has {u}, which is not a leg' for u in sorted(given.keys() - legs)]
    failures += [f'{prefix} yes_conditions lists {u} more than once' for u, n in sorted(given.items()) if n > 1]
    return failures
