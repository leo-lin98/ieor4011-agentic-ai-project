"""The LLM's structured output models and match identity. No defaults: strict structured-output schema."""
import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ResponseModel(BaseModel):
    """Base for LLM output models: unknown fields are rejected."""

    model_config = ConfigDict(extra='forbid')


class Relationship(StrEnum):
    """Relationship the LLM may propose. 'none' is an empty match list, not a value."""

    identical = 'identical'
    complementary = 'complementary'
    basket = 'basket'


class LegCondition(ResponseModel):
    """When one leg pays YES."""

    market_uid: str
    yes_condition: str


class Explanation(ResponseModel):
    """Why a match holds."""

    yes_conditions: list[LegCondition]
    assumptions: list[str]
    rule_differences: list[str]
    summary: str


class ProposedMatch(ResponseModel):
    """One relationship between Kalshi legs and Polymarket legs."""

    relationship: Relationship
    kalshi_legs: list[str]
    polyus_legs: list[str]
    explanation: Explanation


class ProposerResponse(ResponseModel):
    """All matches proposed for one event pair; empty means none."""

    matches: list[ProposedMatch]


def match_id(relationship: Relationship, kalshi_legs: list[str], polyus_legs: list[str]) -> str:
    """Return sha256 hex of 'relationship|sorted kalshi uids|sorted polyus uids' (comma-joined)."""
    text = f"{relationship.value}|{','.join(sorted(kalshi_legs))}|{','.join(sorted(polyus_legs))}"
    return hashlib.sha256(text.encode('utf-8')).hexdigest()
