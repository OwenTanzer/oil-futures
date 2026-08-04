"""Actor/topic filters layered over MediaFlow's event-type arcs."""

import re


RUSSIA_RE = re.compile(
    r"\b(?:"
    r"russia|russian|moscow|kremlin|putin|"
    r"ukraine|ukrainian|kyiv|zelenskyy?|"
    r"urals crude|druzhba|novorossiysk|primorsk|ust-luga"
    r")\b",
    re.IGNORECASE,
)


def is_russia_item(item: dict) -> bool:
    """Return whether an already-relevant MediaFlow item concerns Russia."""
    text = " ".join(
        str(item.get(field, "") or "")
        for field in ("source", "title", "summary", "arc_summary")
    )
    return bool(RUSSIA_RE.search(text))
