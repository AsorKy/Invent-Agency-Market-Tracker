"""Hand-computed tests for text tagging and topics (REQ-LNS-004)."""

import pytest

from libs.domain import topics


def test_normalize_and_tokenize_strip_accents_and_stopwords() -> None:
    assert topics.normalize("Ubicaci\u00d3n Log\u00edstica") == "ubicacion logistica"
    assert topics.tokenize("La bodega de log\u00edstica") == ["bodega", "logistica"]


def test_dictionary_tagging_is_accent_insensitive_and_whole_word() -> None:
    dictionary = {"logistics": ["log\u00edstica", "bodega"], "tech": ["software"]}
    assert topics.tag_by_dictionary("Nueva BODEGA en Bogot\u00e1", dictionary) == ["logistics"]
    assert topics.tag_by_dictionary("bodegaje y softwares", dictionary) == []


def test_regex_tagging() -> None:
    patterns = {"nit": r"\b\d{9}-\d\b", "expansion": r"expansi[o]n"}
    assert topics.tag_by_regex("NIT 900123456-7 y Expansi\u00f3n", patterns) == ["expansion", "nit"]


def test_tfidf_hand_computed() -> None:
    result = topics.tfidf_top_terms(["ventas ventas bodega", "bodega log\u00edstica"], top_k=2)
    assert result[0] == [("ventas", 0.936977), ("bodega", 0.333333)]
    assert result[1] == [("logistica", 0.702733), ("bodega", 0.5)]


def test_tfidf_rejects_bad_top_k() -> None:
    with pytest.raises(ValueError):
        topics.tfidf_top_terms(["a b"], top_k=0)


def test_lazy_wrappers_name_missing_extra() -> None:
    try:
        import yake  # noqa: F401
    except ImportError:
        with pytest.raises(topics.MissingDependencyError, match="topics"):
            topics.yake_keywords("texto de prueba")
    try:
        import gensim  # noqa: F401
    except ImportError:
        with pytest.raises(topics.MissingDependencyError, match="topics"):
            topics.monthly_lda(["texto de prueba"])
