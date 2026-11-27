"""Pure-Python stylometric features for writing-style drift detection.

No ML libraries, no network calls. All features are normalized per word or per
sentence so they do not depend on essay length. Feature order is defined by
``FEATURE_NAMES`` and must stay stable: the running profile stores one mean and
one M2 value per index.
"""
from __future__ import annotations

import math
import re
from collections import Counter

# A length-robust vocabulary measure: moving-average TTR over this many words.
MATTR_WINDOW = 50

# Common English function words (closed class, largely topic-independent).
FUNCTION_WORDS = (
    "the of and however although because therefore a about above after again "
    "against all also am among an any are as at back be been before being below "
    "between both but by can could did do does doing down during each either else "
    "even ever every few first for from further had has have having he her here "
    "him his how i if in into is it its just last less let like made make many may "
    "me might more most much must my no nor not now off on once only or other our "
    "out over own rather same"
).split()

_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+[\"')\]]*|\n+")
# Quill emits one newline per block, so paragraphs are separated by any run
# of newlines (not just blank lines). Splitting on blank lines alone made the
# whole draft a single "paragraph" and turned this feature into word count.
_PARAGRAPH_SPLIT_RE = re.compile(r"\n+")
_CONTRACTION_RE = re.compile(r"\b\w+['\u2019](?:t|s|re|ve|ll|d|m)\b", re.IGNORECASE)
_DASH_CHARS = "-–—"

# Human-readable, in the exact order of the float vector returned by style_features.
FEATURE_NAMES: list[str] = [
    "average sentence length",
    "sentence length variation",
    "average word length",
    "vocabulary richness",
    "comma use",
    "semicolon use",
    "colon use",
    "dash use",
    "parenthesis use",
    "exclamation use",
    "question use",
    "contraction use",
    "average paragraph length",
] + list(FUNCTION_WORDS)

FEATURE_COUNT = len(FEATURE_NAMES)


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _sentence_lengths(text: str) -> list[int]:
    lengths = []
    for part in _SENTENCE_SPLIT_RE.split(text):
        count = len(_WORD_RE.findall(part))
        if count:
            lengths.append(count)
    return lengths


def _mattr(words: list[str], window: int = MATTR_WINDOW) -> float:
    """Moving-average type-token ratio; robust to essay length."""
    if not words:
        return 0.0
    if len(words) <= window:
        return len(set(words)) / len(words)
    ratios = [
        len(set(words[i:i + window])) / window
        for i in range(len(words) - window + 1)
    ]
    return sum(ratios) / len(ratios)


def style_features(text: str) -> list[float]:
    """Return the stylometric feature vector for ``text``.

    Empty or very short text yields a valid (mostly zero) vector and never
    raises. Every rate is per word, and sentence/paragraph lengths are counts,
    so the result is independent of essay length.
    """
    raw = text or ""
    words = _words(raw)
    word_count = len(words)
    features: list[float] = []

    # 1-2. Sentence length mean and standard deviation (in words).
    sent_lengths = _sentence_lengths(raw)
    if sent_lengths:
        mean_sent = sum(sent_lengths) / len(sent_lengths)
        var_sent = sum((n - mean_sent) ** 2 for n in sent_lengths) / len(sent_lengths)
        std_sent = math.sqrt(var_sent)
    else:
        mean_sent = 0.0
        std_sent = 0.0
    features.extend((mean_sent, std_sent))

    # 3. Average word length.
    features.append(
        sum(len(w) for w in words) / word_count if word_count else 0.0
    )

    # 4. Vocabulary richness (MATTR-50).
    features.append(_mattr(words))

    # 5-12. Punctuation and contraction rates, per word.
    def per_word(count: int) -> float:
        return count / word_count if word_count else 0.0

    features.append(per_word(raw.count(",")))
    features.append(per_word(raw.count(";")))
    features.append(per_word(raw.count(":")))
    features.append(per_word(sum(raw.count(ch) for ch in _DASH_CHARS)))
    features.append(per_word(min(raw.count("("), raw.count(")"))))
    features.append(per_word(raw.count("!")))
    features.append(per_word(raw.count("?")))
    features.append(per_word(len(_CONTRACTION_RE.findall(raw))))

    # 13. Average paragraph length (words per paragraph).
    paragraphs = [
        len(_WORD_RE.findall(block))
        for block in _PARAGRAPH_SPLIT_RE.split(raw)
        if _WORD_RE.search(block)
    ]
    features.append(
        sum(paragraphs) / len(paragraphs) if paragraphs else 0.0
    )

    # 14+. Function-word rates, per word.
    counts = Counter(words)
    features.extend(per_word(counts.get(word, 0)) for word in FUNCTION_WORDS)

    return features
