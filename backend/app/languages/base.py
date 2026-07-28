"""Language profiles.

Everything language-specific in the pipeline goes through a LanguageProfile so that
adding Russian / Chinese / etc. means adding one file here, not editing the pipeline.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LanguageProfile:
    code: str  # BCP-47-ish short code, e.g. "fr"
    name_en: str  # "French"
    name_native: str  # "Français"

    # ISO-639-1 code handed to the ASR model.
    asr_code: str

    # wordfreq language code used for Zipf frequency lookups.
    freq_code: str

    # Words that make bad cloze targets (articles, pronouns, auxiliaries...).
    function_words: frozenset[str] = field(default_factory=frozenset)

    # Contraction/elision prefixes to split off when tokenizing ("l'eau" -> "l", "eau").
    elision_prefixes: tuple[str, ...] = ()

    # True for scripts without whitespace word boundaries (zh, ja, th).
    needs_segmentation: bool = False

    # Typical native speech rate in words/min, used to calibrate difficulty.
    # For unsegmented scripts this is characters/min.
    baseline_wpm: float = 160.0

    # Whether diacritics are meaning-bearing enough that we should *tell* the learner
    # about a diacritic-only miss rather than silently accepting it.
    diacritics_significant: bool = True

    _word_re: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE),
        repr=False,
        compare=False,
    )

    # ---------- tokenization ----------

    def tokenize(self, text: str) -> list[str]:
        """Split text into word tokens, lowercased, elisions separated."""
        if self.needs_segmentation:
            return self._segment(text)
        out: list[str] = []
        for raw in self._word_re.findall(text):
            out.extend(self.split_elision(raw.lower()))
        return out

    def _segment(self, text: str) -> list[str]:
        """Segment scripts with no whitespace boundaries. Overridden per language."""
        from wordfreq import tokenize as wf_tokenize

        return wf_tokenize(text, self.freq_code)

    def split_elision(self, token: str) -> list[str]:
        """"l'eau" -> ["l", "eau"]. Returns [token] when no elision applies."""
        for apos in ("'", "’"):
            if apos in token:
                head, _, tail = token.partition(apos)
                if head in self.elision_prefixes and tail:
                    return [head, tail]
        return [token]

    def headword(self, token: str) -> str:
        """The part of a token that carries meaning ("l'eau" -> "eau")."""
        parts = self.split_elision(token.lower())
        return parts[-1] if parts else token.lower()

    # ---------- frequency / difficulty ----------

    def zipf(self, word: str) -> float:
        """Zipf frequency 0-8 (8 = most common). 0 means unknown/very rare."""
        from wordfreq import zipf_frequency

        return zipf_frequency(self.headword(word), self.freq_code)

    def is_content_word(self, token: str) -> bool:
        head = self.headword(token)
        if len(head) < 2:
            return False
        return head not in self.function_words

    # ---------- grading ----------

    @staticmethod
    def strip_diacritics(text: str) -> str:
        decomposed = unicodedata.normalize("NFD", text)
        return unicodedata.normalize(
            "NFC", "".join(c for c in decomposed if not unicodedata.combining(c))
        )

    def normalize_answer(self, text: str, *, fold_diacritics: bool = False) -> str:
        """Canonical form for comparing a learner's answer to the expected one."""
        t = unicodedata.normalize("NFC", text).strip().lower()
        t = t.replace("’", "'").replace("ʼ", "'")
        t = re.sub(r"[^\w\s'-]", " ", t, flags=re.UNICODE)
        t = re.sub(r"\s+", " ", t).strip()
        if fold_diacritics:
            t = self.strip_diacritics(t)
        return t
