"""Mandarin Chinese profile.

Chinese is the interesting stress test for the abstraction: no whitespace word
boundaries, so `tokenize` must segment. wordfreq's tokenizer handles this when the
CJK extra is installed (`uv pip install 'wordfreq[cjk]'`); we degrade to
per-character tokens otherwise rather than crashing the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import LanguageProfile

CHINESE_FUNCTION_WORDS = frozenset(
    """
的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有
看 好 自己 这 那 他 她 它 们 个 之 与 或 但 而 因为 所以 如果 虽然 还是
把 被 让 给 对 从 向 为 以 于 及 等 什么 怎么 为什么 哪 哪里 谁 吗 呢 吧
""".split()
)


@dataclass(frozen=True)
class ChineseProfile(LanguageProfile):
    def _segment(self, text: str) -> list[str]:
        try:
            from wordfreq import tokenize as wf_tokenize

            return wf_tokenize(text, self.freq_code)
        except (ImportError, LookupError, ValueError):
            # No CJK extra installed — fall back to characters. Cloze still works,
            # it's just character-level instead of word-level.
            return [c for c in text if "一" <= c <= "鿿"]


CHINESE = ChineseProfile(
    code="zh",
    name_en="Chinese (Mandarin)",
    name_native="中文",
    asr_code="zh",
    freq_code="zh",
    function_words=CHINESE_FUNCTION_WORDS,
    filler_words=frozenset("""嗯 呃 啊 唉 哦 欸""".split()),
    elision_prefixes=(),
    needs_segmentation=True,
    # Mandarin news reads at roughly 240-260 characters/min.
    baseline_wpm=250.0,
    diacritics_significant=False,
)
