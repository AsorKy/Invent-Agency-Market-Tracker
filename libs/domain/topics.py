"""Text tagging and topic helpers for the descriptive lens D3 (REQ-LNS-004).

Pure parts: accent-insensitive Spanish normalization, regex and dictionary
tagging, and a pure-Python TF-IDF that ranks top terms per document. Lazy
parts: ``yake_keywords`` (needs the ``yake`` package) and ``monthly_lda``
(needs ``gensim``); both raise ``MissingDependencyError`` naming the extra when
the package is absent. No I/O happens here. It is used by
``services/ml/app/schedule.py``.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from importlib import import_module
from typing import Any

SPANISH_STOPWORDS = frozenset(
    "a al con de del el en es la las lo los para por que se su sus un una y o u "
    "no ni si como mas pero este esta estos estas ese esa son fue ser han hay".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")
LDA_SEED = 42


class MissingDependencyError(RuntimeError):
    """Raised when an optional heavy library is not installed."""


def normalize(text: str) -> str:
    """Lowercase ``text`` and strip accents, so ``Ubicacion`` with accents equals ``ubicacion``."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def tokenize(text: str, *, stopwords: frozenset[str] = SPANISH_STOPWORDS) -> list[str]:
    """Return normalized word tokens without stopwords or one-letter tokens."""
    return [t for t in _TOKEN.findall(normalize(text)) if len(t) > 1 and t not in stopwords]


def tag_by_dictionary(text: str, dictionary: Mapping[str, Sequence[str]]) -> list[str]:
    """Return sorted tags whose any term appears as a whole word or phrase in ``text``."""
    haystack = f" {' '.join(_TOKEN.findall(normalize(text)))} "
    tags = []
    for tag, terms in dictionary.items():
        for term in terms:
            needle = " ".join(_TOKEN.findall(normalize(term)))
            if needle and f" {needle} " in haystack:
                tags.append(tag)
                break
    return sorted(tags)


def tag_by_regex(text: str, patterns: Mapping[str, str]) -> list[str]:
    """Return sorted tags whose regex matches the normalized ``text``."""
    normalized = normalize(text)
    return sorted(tag for tag, pattern in patterns.items() if re.search(pattern, normalized))


def tfidf_top_terms(documents: Sequence[str], *, top_k: int = 5) -> list[list[tuple[str, float]]]:
    """Return the ``top_k`` (term, score) pairs per document.

    ``tf`` is count / length, ``idf`` is ``ln((1 + n) / (1 + df)) + 1``. Ties
    break alphabetically so the output is deterministic.
    """
    if top_k < 1:
        raise ValueError("top_k must be positive")
    tokens = [tokenize(d) for d in documents]
    n = len(tokens)
    df: Counter[str] = Counter()
    for doc in tokens:
        df.update(set(doc))
    result: list[list[tuple[str, float]]] = []
    for doc in tokens:
        counts = Counter(doc)
        scores = {
            t: (c / len(doc)) * (math.log((1 + n) / (1 + df[t])) + 1.0) for t, c in counts.items()
        }
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
        result.append([(t, round(s, 6)) for t, s in ranked])
    return result


def _require(module: str, extra: str) -> Any:
    try:
        return import_module(module)
    except ImportError as exc:
        raise MissingDependencyError(
            f"{module} is not installed; install the '{extra}' extra"
        ) from exc


def yake_keywords(text: str, *, top_k: int = 5, language: str = "es") -> list[tuple[str, float]]:
    """Return YAKE keywords as (phrase, score); lower score means more relevant."""
    yake = _require("yake", "topics")
    extractor = yake.KeywordExtractor(lan=language, n=2, top=top_k)
    return [(str(k), float(s)) for k, s in extractor.extract_keywords(text)]


def monthly_lda(
    documents: Sequence[str], *, num_topics: int = 5, top_terms: int = 8, seed: int = LDA_SEED
) -> list[list[str]]:
    """Fit LDA on one month of documents and return the top terms per topic."""
    if num_topics < 1:
        raise ValueError("num_topics must be positive")
    gensim_corpora = _require("gensim.corpora", "topics")
    gensim_models = _require("gensim.models", "topics")
    texts = [tokenize(d) for d in documents]
    dictionary = gensim_corpora.Dictionary(texts)
    corpus = [dictionary.doc2bow(t) for t in texts]
    model = gensim_models.LdaModel(
        corpus, num_topics=num_topics, id2word=dictionary, random_state=seed, passes=5
    )
    return [
        [str(term) for term, _ in model.show_topic(i, topn=top_terms)] for i in range(num_topics)
    ]
