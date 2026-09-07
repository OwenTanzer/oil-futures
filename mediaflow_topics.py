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


# Unambiguous China-specific markers/entities: any one of these alone is
# enough to call an item China-related. Bare "china" is excluded when it's
# the middle word of "South China Sea" -- that phrase names a body of water
# claimed by multiple countries, and a mention of it alone (e.g. a
# Philippines/Vietnam story) is not evidence the item concerns China.
CHINA_ENTITY_RE = re.compile(
    r"\b(?:"
    r"chinese|beijing|"
    r"sinopec|cnooc|petrochina|cnpc|china national petroleum|unipec|sinochem|"
    r"shandong|teapot refiners?|"
    r"pla navy|people[’']s liberation army"
    r")\b"
    r"|(?<!south )\bchina\b(?!\s+sea)",
    re.IGNORECASE,
)

# "malacca strait", "south china sea", "yuan", and "renminbi" are
# deliberately NOT matched here: they come up in stories about other
# claimants/parties (Vietnam, Philippines, Indonesia, generic currency
# mentions) with no China connection at all. Gating them on "also contains
# a China marker" doesn't add anything either, since a China marker already
# matches by itself above -- so a real China-Malacca story is still caught
# via CHINA_ENTITY_RE, just not via the ambiguous term.


def is_china_item(item: dict) -> bool:
    """Return whether an already-relevant MediaFlow item concerns China.

    Deliberately excludes the "source" field: for search-query feeds
    (GNews/Bing) that field holds the collector's query label (e.g.
    "GNews: china iran oil imports"), not the article's actual outlet or
    content, so including it would let the query label alone decide
    topic membership regardless of what the article says.
    """
    text = " ".join(
        str(item.get(field, "") or "")
        for field in ("title", "summary", "arc_summary")
    )
    return bool(CHINA_ENTITY_RE.search(text))
