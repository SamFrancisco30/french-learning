"""Optional lemmatization, for recognising a known expression in *unseen* text.

Precomputed annotations cover ingested transcripts. This module covers the other case:
the learner selects a word in arbitrary text, and we want to notice that it belongs to an
expression we already learned from some other lesson.

Why lemmatization is needed at all: French MWEs inflect. `mettre le feu` surfaces as
`a mis le feu`, so surface-string matching finds nothing. spaCy's French lemmatizer maps
`mis -> mettre` (measured ~95% lemma agreement against the PARSEME-FR gold corpus, ~4ms
per sentence), which makes lemma-keyed lookup work.

Why it is only a *secondary* signal: lemma-bag matching over a lexicon has excellent
recall (~99% of lexicon-covered items) but poor precision (~0.35) — it fires on ordinary
compositional uses of the same words. So matches from this path are gated by an
all-content-lemmas-present-within-a-window check, and still returned with reduced
confidence and marked `inferred` so the UI can hedge rather than assert.

spaCy is optional (`uv pip install -e '.[nlp]'`). Without it we fall back to the
LanguageProfile's headword, which handles nouns and compounds but not verb inflection —
degraded, not broken.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from ..languages import LanguageProfile

log = logging.getLogger(__name__)

# spaCy model per language. Small models are enough — we only need lemmas.
SPACY_MODELS = {"fr": "fr_core_news_sm", "ru": "ru_core_news_sm", "zh": "zh_core_web_sm"}

# All content lemmas of an expression must occur within this many content tokens of the
# selection for a lexicon match to count. PARSEME-FR shows French verbal MWEs span at most
# ~8 intervening tokens, so 8 is generous without being unbounded. Callers must ALSO
# restrict candidates to the selection's own sentence — an expression cannot straddle a
# sentence boundary, and allowing it to produced false matches (a literal "le feu brûle"
# matching "feu rouge" because "rouge" appeared in the next sentence).
LEXICON_WINDOW_TOKENS = 8


@lru_cache(maxsize=4)
def _load_pipeline(model_name: str):
    """Load a spaCy pipeline, or return None if unavailable."""
    try:
        import spacy
    except ImportError:
        log.debug("spaCy not installed; lemma matching degraded to headwords")
        return None
    try:
        # Lemmas only — disabling the parser and NER cuts load and per-call cost.
        return spacy.load(model_name, exclude=["parser", "ner", "textcat"])
    except OSError:
        log.info(
            "spaCy model %s not downloaded; run: python -m spacy download %s", model_name, model_name
        )
        return None


def lemmatize(text: str, lang: LanguageProfile) -> list[tuple[str, int, int]]:
    """[(lemma, char_start, char_end), ...] for the content-bearing tokens of `text`.

    Falls back to LanguageProfile tokenization when spaCy is unavailable.
    """
    model = SPACY_MODELS.get(lang.code)
    nlp = _load_pipeline(model) if model else None

    if nlp is not None:
        doc = nlp(text)
        out: list[tuple[str, int, int]] = []
        for tok in doc:
            if tok.is_space or tok.is_punct:
                continue
            lemma = (tok.lemma_ or tok.text).casefold()
            if not lemma or not lang.is_content_word(lemma):
                continue
            out.append((lemma, tok.idx, tok.idx + len(tok.text)))
        return out

    return _fallback_lemmas(text, lang)


def _fallback_lemmas(text: str, lang: LanguageProfile) -> list[tuple[str, int, int]]:
    """No spaCy: approximate lemmas with headwords, locating each by scan."""
    out: list[tuple[str, int, int]] = []
    cursor = 0
    for tok in lang.tokenize(text):
        head = lang.headword(tok)
        if not lang.is_content_word(head):
            continue
        at = text.casefold().find(tok.casefold(), cursor)
        if at == -1:
            continue
        out.append((head, at, at + len(tok)))
        cursor = at + len(tok)
    return out


def spacy_available(lang: LanguageProfile) -> bool:
    model = SPACY_MODELS.get(lang.code)
    return bool(model) and _load_pipeline(model) is not None


def lemma_key(lemmas: list[str], lang: LanguageProfile) -> str:
    """Canonical lexicon key: sorted, deduplicated content lemmas."""
    return "|".join(sorted({lang.headword(l).casefold() for l in lemmas if l.strip()}))


def selection_lemmas(
    text: str, start: int, end: int, lang: LanguageProfile
) -> tuple[list[str], list[tuple[str, int, int]]]:
    """(lemmas overlapping the selection, all lemmas in the text)."""
    all_lemmas = lemmatize(text, lang)
    hit = [lem for lem, s, e in all_lemmas if start < e and s < end]
    return hit, all_lemmas


def all_lemmas_within_window(
    required: set[str],
    all_lemmas: list[tuple[str, int, int]],
    selection_start: int,
    selection_end: int,
    *,
    window: int = LEXICON_WINDOW_TOKENS,
) -> bool:
    """True if every lemma in `required` appears near the selection.

    This is the precision gate on lexicon matching: bag-of-lemmas alone fires on any
    sentence that happens to contain the same words far apart.
    """
    if not required:
        return False
    idx_of_selection = next(
        (i for i, (_, s, e) in enumerate(all_lemmas) if selection_start < e and s < selection_end),
        None,
    )
    if idx_of_selection is None:
        return False
    lo = max(0, idx_of_selection - window)
    hi = min(len(all_lemmas), idx_of_selection + window + 1)
    nearby = {lem for lem, _, _ in all_lemmas[lo:hi]}
    return required.issubset(nearby)


def locate_lemmas(
    required: set[str], all_lemmas: list[tuple[str, int, int]]
) -> list[list[int]]:
    """Character spans for the tokens realising `required`, for highlighting."""
    return [[s, e] for lem, s, e in all_lemmas if lem in required]
