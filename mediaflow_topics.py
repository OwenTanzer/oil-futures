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


CHINA_RE = re.compile(
    r"\b(?:"
    r"china|chinese|beijing|"
    r"sinopec|cnooc|petrochina|"
    r"shandong|teapot refiner|"
    r"malacca strait|south china sea|"
    r"yuan|renminbi|"
    r"pla navy|people's liberation army"
    r")\b",
    re.IGNORECASE,
)


def is_china_item(item: dict) -> bool:
    """Return whether an already-relevant MediaFlow item concerns China."""
    text = " ".join(
        str(item.get(field, "") or "")
        for field in ("source", "title", "summary", "arc_summary")
    )
    return bool(CHINA_RE.search(text))
