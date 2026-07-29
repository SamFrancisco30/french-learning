"""Deterministic matcher for sentence-level grammatical constructions.

This is the layer that answers "I know every word and still don't understand the sentence".
Constructions like `ne ... que` (only) or `il n'y a pas que X` (X isn't the only thing) carry
meaning in their *shape*, not their words — so they need pattern matching over tokens rather
than the lexical lookup used for multiword expressions.

Why deterministic rather than asking a model: a pattern hit is instant and free, so the
popup can name the construction before any network call, and the model is then asked only to
explain the instance rather than to find it. Detection and explanation are different jobs
and the model is unreliable at the first one (it invents constructions that aren't present).

Pattern DSL — each step in `pattern_tokens` is one of:
    "ne|n'"     literal alternatives; the token must equal one of them
    "*<=6"      a gap of up to 6 intervening tokens
    "POS:INF"   a part-of-speech constraint (INF, VERB, ADJ, NOUN), needs spaCy

Tokenization keeps elided prefixes as their own token *with* the apostrophe, so `n'y a pas`
becomes ["n'", "y", "a", "pas"] and a pattern can name `n'` precisely. Without that,
`ne|n'` could never match the elided form, which is the form that actually appears.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"

# An elided prefix (l', qu', n', jusqu') keeps its apostrophe; otherwise a plain word.
_TOKEN_RE = re.compile(r"[^\W\d_]+['’]|[^\W\d_]+|\d+", re.UNICODE)

_POS_MAP: dict[str, tuple[frozenset[str], str | None]] = {
    # Infinitives: être/avoir are tagged AUX by spaCy, not VERB.
    "INF": (frozenset({"VERB", "AUX"}), "Inf"),
    "VERB": (frozenset({"VERB", "AUX"}), None),
    "AUX": (frozenset({"AUX"}), None),
    # Participial adjectives ("si fatigué que...") are tagged VERB/Part.
    "ADJ": (frozenset({"ADJ", "VERB"}), None),
    "NOUN": (frozenset({"NOUN", "PROPN"}), None),
    "ADV": (frozenset({"ADV"}), None),
    "PART": (frozenset({"VERB", "AUX"}), "Part"),
}


@dataclass(frozen=True)
class Token:
    text: str  # normalized: lowercased, straight apostrophe
    start: int  # char offset in the source text
    end: int


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    for m in _TOKEN_RE.finditer(text):
        raw = m.group(0).replace("’", "'").casefold()
        out.append(Token(raw, m.start(), m.end()))
    return out


# ------------------------------------------------------------------ steps


@dataclass(frozen=True)
class Literal:
    options: frozenset[str]


@dataclass(frozen=True)
class Gap:
    max_tokens: int
    # Tokens that must NOT appear inside the gap. This is what separates restrictive
    # `ne ... que` ("je n'ai que dix euros" = only) from a complementizer `que` after a
    # plain negation ("je ne pense pas que ce soit vrai" = I don't think that). Without it
    # the pattern fires on both and the learner is told something false about their sentence.
    forbidden: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Pos:
    tags: frozenset[str]
    verbform: str | None


@dataclass(frozen=True)
class CommaBefore:
    """Zero-width: a comma must sit between the previous token and this position.

    Distinguishes the correlative `plus ... , plus ...` from an ordinary coordination
    ("plus de temps et plus d'argent"), which has the same two tokens and no clause break.
    """


@dataclass(frozen=True)
class NotNext:
    """Zero-width: the token at this position must not be one of these.

    Separates consecutive `si ADJ que + clause` ("so old that...") from the degree
    construction `pas si ADJ que ça` ("not that old"), which share every token but differ
    in what follows `que`.
    """

    options: frozenset[str]


@dataclass(frozen=True)
class NoTagAhead:
    """Zero-width lookahead: none of the next `window` tokens may carry these tags.

    This is what separates restrictive `pas que` ("elle ne parle pas que du travail" —
    not only about work) from a plain negation plus complementizer ("je ne pense pas que
    ce soit vrai" — I don't think that it's true). The token shapes are identical; the
    difference is that a complementizer introduces a CLAUSE, so a verb follows within a
    token or two, whereas the restrictive reading is followed by a noun phrase.
    """

    tags: frozenset[str]
    window: int


Step = Literal | Gap | Pos | NoTagAhead | CommaBefore | NotNext

# "*<=6" or "*<=6!pas|plus|jamais" — the tail lists tokens barred from the gap.
_GAP_RE = re.compile(r"^\*<=(\d+)(?:!(.+))?$")



# "!POS:VERB|AUX@<=2" — no token with these tags within the next 2 positions.
_NOTAG_RE = re.compile(r"^!POS:([A-Z|]+)@<=(\d+)$")


def parse_step(raw: str) -> Step | None:
    raw = raw.strip()
    if raw == "@,":
        return CommaBefore()
    if raw.startswith("!") and not raw.startswith("!POS:"):
        opts = {o.replace("’", "'").casefold() for o in raw[1:].split("|") if o.strip()}
        return NotNext(frozenset(opts)) if opts else None
    m = _NOTAG_RE.match(raw)
    if m:
        return NoTagAhead(frozenset(m.group(1).split("|")), int(m.group(2)))
    m = _GAP_RE.match(raw)
    if m:
        forbidden = frozenset(
            t.replace("’", "'").casefold() for t in (m.group(2) or "").split("|") if t.strip()
        )
        return Gap(int(m.group(1)), forbidden)
    if raw.startswith("POS:"):
        spec = _POS_MAP.get(raw[4:].upper())
        return Pos(spec[0], spec[1]) if spec else None
    opts = {o.replace("’", "'").casefold() for o in raw.split("|") if o.strip()}
    return Literal(frozenset(opts)) if opts else None


def parse_pattern(raw_steps: list[str]) -> list[Step] | None:
    steps: list[Step] = []
    for r in raw_steps:
        s = parse_step(r)
        if s is None:
            log.warning("unparseable pattern step %r", r)
            return None
        steps.append(s)
    return steps or None


# ------------------------------------------------------------------ construction records


@dataclass(frozen=True)
class Construction:
    key: str
    schema_form: str
    name_en: str
    meaning_en: str
    why_opaque: str
    literal_trap: str | None
    cefr: str
    example_fr: str
    example_en: str
    register: str
    steps: tuple[Step, ...]
    required_markers: tuple[frozenset[str], ...]
    needs_pos: bool = field(default=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "schema_form": self.schema_form,
            "name_en": self.name_en,
            "meaning_en": self.meaning_en,
            "why_opaque": self.why_opaque,
            "literal_trap": self.literal_trap,
            "cefr": self.cefr,
            "example_fr": self.example_fr,
            "example_en": self.example_en,
            "register_note": self.register,
        }


def _load(language: str) -> list[Construction]:
    path = DATA_DIR / f"{language}_constructions.json"
    if not path.exists():
        log.info("no construction inventory for %r at %s", language, path)
        return []

    raw = json.loads(path.read_text("utf-8"))
    out: list[Construction] = []
    for item in raw.get("constructions", []):
        steps = parse_pattern(item.get("pattern_tokens", []))
        if not steps:
            continue
        markers = tuple(
            frozenset(o.replace("’", "'").casefold() for o in m.split("|") if o.strip())
            for m in item.get("required_markers", [])
            if m.strip()
        )
        out.append(
            Construction(
                key=item["key"],
                schema_form=item.get("schema_form", item["key"]),
                name_en=item.get("name_en", ""),
                meaning_en=item.get("meaning_en", ""),
                why_opaque=item.get("why_opaque", ""),
                literal_trap=(item.get("literal_trap") or "").strip() or None,
                cefr=item.get("cefr", "B1"),
                example_fr=item.get("example_fr", ""),
                example_en=item.get("example_en", ""),
                register=item.get("register", "neutral"),
                steps=tuple(steps),
                required_markers=markers,
                needs_pos=any(isinstance(s, (Pos, NoTagAhead)) for s in steps),
            )
        )
    log.info("loaded %d constructions for %s", len(out), language)
    return out


@lru_cache(maxsize=8)
def constructions_for(language: str) -> tuple[Construction, ...]:
    return tuple(_load(language))


def by_key(language: str, key: str) -> Construction | None:
    return next((c for c in constructions_for(language) if c.key == key), None)


# ------------------------------------------------------------------ POS tags


def _pos_tags(text: str, language: str, tokens: list[Token]) -> list[tuple[str, str | None]] | None:
    """(coarse tag, VerbForm) per token, or None when no tagger is available.

    Aligned back onto our own tokens by character overlap, because spaCy tokenizes
    elisions differently than we do.
    """
    from .lemmas import SPACY_MODELS, _load_pipeline

    model = SPACY_MODELS.get(language)
    nlp = _load_pipeline(model) if model else None
    if nlp is None:
        return None

    doc = nlp(text)
    spacy_toks = [
        (t.idx, t.idx + len(t.text), t.pos_, t.morph.get("VerbForm")) for t in doc if not t.is_space
    ]
    tags: list[tuple[str, str | None]] = []
    for tok in tokens:
        hit = next(
            (s for s in spacy_toks if tok.start < s[1] and s[0] < tok.end),
            None,
        )
        if hit is None:
            tags.append(("X", None))
        else:
            vf = hit[3][0] if hit[3] else None
            tags.append((hit[2], vf))
    return tags


# ------------------------------------------------------------------ matching


def _match_at(
    steps: tuple[Step, ...],
    tokens: list[Token],
    tags: list[tuple[str, str | None]] | None,
    si: int,
    ti: int,
    text: str = "",
) -> int | None:
    """End token index (exclusive) if `steps[si:]` matches starting at `tokens[ti]`."""
    if si == len(steps):
        return ti
    step = steps[si]

    if isinstance(step, Literal):
        if ti < len(tokens) and tokens[ti].text in step.options:
            return _match_at(steps, tokens, tags, si + 1, ti + 1, text)
        return None

    if isinstance(step, Pos):
        if ti >= len(tokens) or tags is None:
            return None
        tag, vf = tags[ti]
        if tag not in step.tags:
            return None
        # An ADJ step accepting VERB must still require the participle form, or it would
        # match any finite verb.
        if step.verbform and vf != step.verbform:
            return None
        if step.tags == frozenset({"ADJ", "VERB"}) and tag == "VERB" and vf != "Part":
            return None
        return _match_at(steps, tokens, tags, si + 1, ti + 1, text)

    if isinstance(step, NotNext):
        if ti < len(tokens) and tokens[ti].text in step.options:
            return None
        return _match_at(steps, tokens, tags, si + 1, ti, text)  # zero-width

    if isinstance(step, CommaBefore):
        if ti == 0 or ti >= len(tokens):
            return None
        between = text[tokens[ti - 1].end : tokens[ti].start]
        if "," not in between:
            return None
        return _match_at(steps, tokens, tags, si + 1, ti, text)  # zero-width

    if isinstance(step, NoTagAhead):
        if tags is None:
            return None
        for k in range(ti, min(ti + step.window, len(tokens))):
            if tags[k][0] in step.tags:
                return None
        return _match_at(steps, tokens, tags, si + 1, ti, text)  # zero-width

    # Gap: try the shortest skip first so matches stay tight, and abandon the gap as soon
    # as a forbidden token is crossed.
    for skip in range(step.max_tokens + 1):
        nxt = ti + skip
        if nxt > len(tokens):
            break
        if skip > 0 and tokens[nxt - 1].text in step.forbidden:
            break
        got = _match_at(steps, tokens, tags, si + 1, nxt, text)
        if got is not None:
            return got
    return None


@dataclass
class ConstructionHit:
    construction: Construction
    char_start: int
    char_end: int
    # Char spans of the construction's own anchor tokens, for highlighting in place.
    marker_spans: list[list[int]]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.construction.to_dict(),
            "char_start": self.char_start,
            "char_end": self.char_end,
            "marker_spans": self.marker_spans,
        }


def find_constructions(
    text: str, language: str = "fr", *, limit: int = 6
) -> list[ConstructionHit]:
    """Every construction whose pattern matches `text`, longest-anchored first."""
    cons = constructions_for(language)
    if not cons or not text.strip():
        return []

    tokens = tokenize(text)
    if not tokens:
        return []
    need_pos = any(c.needs_pos for c in cons)
    tags = _pos_tags(text, language, tokens) if need_pos else None

    hits: list[ConstructionHit] = []
    seen: set[tuple[str, int]] = set()
    for con in cons:
        # A pattern needing POS is skipped rather than loosened when no tagger exists —
        # a loosened match would fire on unrelated sentences.
        if con.needs_pos and tags is None:
            continue
        for start in range(len(tokens)):
            end = _match_at(con.steps, tokens, tags, 0, start, text)
            if end is None or end <= start:
                continue
            if (con.key, start) in seen:
                continue
            seen.add((con.key, start))
            literal_positions = _anchor_spans(con, tokens, start, end)
            hits.append(
                ConstructionHit(
                    construction=con,
                    char_start=tokens[start].start,
                    char_end=tokens[end - 1].end,
                    marker_spans=literal_positions,
                )
            )
            break  # one hit per construction is enough for a popup

    # Prefer the most specific: more literal steps, then shorter span.
    def specificity(h: ConstructionHit) -> int:
        return sum(1 for s in h.construction.steps if isinstance(s, Literal))

    hits.sort(key=lambda h: (-specificity(h), h.char_end - h.char_start))

    # Drop hits subsumed by a more specific one. `il n'y a pas que X` necessarily also
    # matches the general `ne ... pas que`, and reporting both as separate discoveries
    # implies the learner has met two constructions when they have met one.
    kept: list[ConstructionHit] = []
    for h in hits:
        subsumed = any(
            k.char_start <= h.char_start
            and h.char_end <= k.char_end
            and specificity(k) > specificity(h)
            for k in kept
        )
        if not subsumed:
            kept.append(h)
    return kept[:limit]


def _anchor_spans(
    con: Construction, tokens: list[Token], start: int, end: int
) -> list[list[int]]:
    """Char spans of the literal anchor tokens inside a match, for highlighting."""
    wanted: set[str] = set()
    for s in con.steps:
        if isinstance(s, Literal):
            wanted |= set(s.options)
    return [[t.start, t.end] for t in tokens[start:end] if t.text in wanted]


def uses_markers(answer: str, con: Construction) -> tuple[bool, list[str]]:
    """Did a learner's free answer actually employ the construction?

    Deterministic, and the primary signal when grading practice: the exercise exists to
    make them use the structure, so that specific thing must be checked exactly rather
    than left to a judge's opinion. Returns (ok, missing markers).
    """
    if not con.required_markers:
        return True, []
    present = {t.text for t in tokenize(answer)}
    missing = [
        "/".join(sorted(group)) for group in con.required_markers if not (group & present)
    ]
    return not missing, missing
