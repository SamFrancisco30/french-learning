"""Russian language profile.

Not the current focus, but wired in to prove the abstraction holds: swapping
`--lang ru` changes ASR language, frequency lookups, cloze filtering and grading
without touching the pipeline.
"""

from __future__ import annotations

from .base import LanguageProfile

RUSSIAN_FUNCTION_WORDS = frozenset(
    """
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы
по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг
ли если уже или ни быть был него до вас нибудь опять уж вам сказал ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была
сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому
этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда
зачем всех никогда можно при наконец два об другой хоть после над больше тот
через эти нас про всего них какая много разве три эту моя впрочем свою этой
перед иногда лучше чуть том нельзя такой им более всегда конечно всю между
""".split()
)

RUSSIAN = LanguageProfile(
    code="ru",
    name_en="Russian",
    name_native="Русский",
    asr_code="ru",
    freq_code="ru",
    function_words=RUSSIAN_FUNCTION_WORDS,
    elision_prefixes=(),
    needs_segmentation=False,
    baseline_wpm=140.0,
    # Russian ё/е and stress marks are usually optional in writing.
    diacritics_significant=False,
)
