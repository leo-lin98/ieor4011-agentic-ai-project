"""One rubric per market category. A rubric tells the judge what to check and what the desk accepts.

A rubric module defines:
  NAME             short name, recorded with every verdict
  VERSION          bump when any text below changes (part of the prompt version)
  DIMENSIONS       ordered dict {dimension: one-line definition}; becomes the checklist enum
  ACCEPTED_RISKS   paragraph: what is recorded but never drives the verdict
  VERDICT_SCOPE    paragraph: what "identical" means in this category
  notes(subcategory) -> str   category / league specific facts the model should know (may be "")
  claim(t, examples) -> str   what the matching code has already verified for this template pair

Pick one with rubrics.for_category("sports", "football/nfl").
"""
import importlib

CATEGORY_RUBRIC = {
    "sports": "sports",
    # "crypto": "crypto", "economics": "economics", "politics": "politics"   # when their candidates matchers exist
}


def for_category(category, subcategory=None):
    name = CATEGORY_RUBRIC.get(category)
    if not name:
        raise SystemExit(f"no judge rubric for category {category!r}; add one in judge/rubrics/ and register it")
    return importlib.import_module(f"judge.rubrics.{name}")
