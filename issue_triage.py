"""Explainable local suggestions for campus facility issue reports."""

import re
from collections.abc import Mapping


CATEGORY_CUES = {
    "electrical": (
        "electric",
        "electrical",
        "power",
        "wire",
        "socket",
        "outlet",
        "light",
        "lighting",
        "breaker",
        "projector",
    ),
    "plumbing": (
        "water",
        "leak",
        "pipe",
        "faucet",
        "toilet",
        "drain",
        "flush",
        "flood",
    ),
    "furniture": (
        "chair",
        "desk",
        "table",
        "door",
        "window",
        "drawer",
        "furniture",
        "hinge",
    ),
    "network": (
        "wifi",
        "wi fi",
        "internet",
        "network",
        "router",
        "ethernet",
        "connection",
    ),
    "cleaning": (
        "dirty",
        "clean",
        "trash",
        "waste",
        "spill",
        "odor",
        "smell",
        "pest",
        "restroom",
        "litter",
    ),
}

PRIORITY_CUES = (
    (
        "Critical",
        (
            "fire",
            "smoke",
            "sparking",
            "electric shock",
            "exposed wire",
            "gas leak",
            "structural collapse",
            "injury",
            "trapped",
        ),
    ),
    (
        "High",
        (
            "leak",
            "flood",
            "outage",
            "broken",
            "not working",
            "no power",
            "blocked",
            "overheating",
        ),
    ),
    (
        "Low",
        ("cosmetic", "minor", "loose", "squeak", "scratched", "paint", "small"),
    ),
)
VALID_CATEGORIES = frozenset((*CATEGORY_CUES, "other"))


def _contains_cue(text: str, cue: str) -> bool:
    normalized_text = " ".join(re.findall(r"[a-z0-9]+", text.casefold()))
    normalized_cue = " ".join(re.findall(r"[a-z0-9]+", cue.casefold()))
    return f" {normalized_cue} " in f" {normalized_text} "


def recommend_issue_triage(issue: Mapping[str, object]) -> dict[str, str]:
    """Return suggested category, priority, and a concise rule explanation."""
    title = issue.get("title")
    description = issue.get("description")
    submitted_category = issue.get("category")
    text = " ".join(
        value for value in (title, description) if isinstance(value, str)
    )

    category_matches = {
        category: tuple(cue for cue in cues if _contains_cue(text, cue))
        for category, cues in CATEGORY_CUES.items()
    }
    matched_categories = {
        category: cues for category, cues in category_matches.items() if cues
    }
    if matched_categories:
        highest_match_count = max(map(len, matched_categories.values()))
        strongest = {
            category
            for category, cues in matched_categories.items()
            if len(cues) == highest_match_count
        }
    else:
        strongest = set()

    if len(strongest) == 1:
        recommended_category = next(iter(strongest))
        category_reason = (
            f"Category suggested from report terms: "
            f"{', '.join(category_matches[recommended_category])}."
        )
    elif isinstance(submitted_category, str) and submitted_category in VALID_CATEGORIES:
        recommended_category = submitted_category
        category_reason = (
            "Category text was ambiguous or absent; the submitted category is retained "
            "as a suggestion."
        )
    else:
        recommended_category = "other"
        category_reason = (
            "Category text was ambiguous or absent and no valid submitted category "
            "was available; Other is suggested."
        )

    priority = "Medium"
    priority_reason = (
        "No clear priority indicator matched; Medium is the cautious default."
    )
    for candidate, cues in PRIORITY_CUES:
        matched_cues = tuple(cue for cue in cues if _contains_cue(text, cue))
        if matched_cues:
            priority = candidate
            priority_reason = (
                f"{candidate} suggested from report terms: {', '.join(matched_cues)}."
            )
            break

    return {
        "recommended_category": recommended_category,
        "recommended_priority": priority,
        "triage_reason": f"{priority_reason} {category_reason}",
    }
