"""Can the answer be guessed without listening?

The measured baseline on the library's 207 MCQ items, before any of this existed:

    longest option is correct   41.5%  (chance 25%)
    answer echoes question       6.3%  (chance ~25%)
    duplicate options            0.0%
    true/false true              50.7%

Only the first was a real leak, so that is what `choose_distractors` is built to remove; the
others are audited to stop them appearing later. The simulation at the bottom is what shows the
mechanism actually works, since the real proof needs a paid regeneration run.
"""

from __future__ import annotations

import random

import pytest

from app.skills.listening import itemquality as iq


class TestLengthCue:
    def test_answer_longer_than_distractors_is_positive(self):
        assert iq.length_cue("a" * 40, ["a" * 20, "a" * 20]) == pytest.approx(0.5)

    def test_answer_shorter_is_negative(self):
        # Being conspicuously short is just as much a tell as being conspicuously long.
        assert iq.length_cue("a" * 10, ["a" * 20, "a" * 20]) == pytest.approx(-1.0)

    def test_matched_lengths_are_zero(self):
        assert iq.length_cue("a" * 20, ["b" * 20, "c" * 20]) == 0.0

    def test_no_distractors_is_not_a_division_by_zero(self):
        assert iq.length_cue("anything", []) == 0.0


class TestChooseDistractors:
    def test_picks_the_subset_that_hides_the_answer(self):
        # Two candidates are much shorter than the key and would leave it the obvious longest;
        # two are comparable. The comparable ones must win.
        answer = "Le chômage a reculé de trente pour cent"
        chosen = iq.choose_distractors(
            answer,
            [
                "Non",                                        # far too short
                "Peut-être",                                  # far too short
                "L'inflation a reculé de trente pour cent",   # matched
                "Le chômage a progressé de trente pour cent",  # matched
                "Le chômage a reculé de treize pour cent",    # matched
            ],
            n=3,
        )
        assert "Non" not in chosen
        assert "Peut-être" not in chosen
        assert len(chosen) == 3

    def test_answer_is_not_left_uniquely_longest_when_avoidable(self):
        answer = "a" * 30
        chosen = iq.choose_distractors(
            answer, ["b" * 5, "c" * 6, "d" * 7, "e" * 31, "f" * 29], n=3
        )
        assert any(len(c) >= len(answer) for c in chosen), (
            "a candidate at least as long as the key was available and should have been used"
        )

    def test_drops_a_distractor_identical_to_the_answer(self):
        # An option repeating the key means the item has two correct answers.
        chosen = iq.choose_distractors("Trente pour cent", ["trente pour cent!", "Vingt", "Dix", "Cinq"])
        assert all(iq._fold(c) != iq._fold("Trente pour cent") for c in chosen)

    def test_drops_duplicates_among_the_distractors(self):
        chosen = iq.choose_distractors("Answer here", ["Same one", "same ONE", "Other", "Third"])
        folded = [iq._fold(c) for c in chosen]
        assert len(folded) == len(set(folded))

    def test_returns_what_it_can_when_candidates_are_too_few(self):
        # A thin item still teaches something; a missing item does not.
        assert iq.choose_distractors("Answer", ["One", "Two"], n=3) == ["One", "Two"]

    def test_ignores_non_string_candidates(self):
        chosen = iq.choose_distractors("Answer", ["One", None, 42, "Two", "Three"], n=3)
        assert chosen == ["One", "Two", "Three"]

    def test_ties_break_toward_the_models_own_ordering(self):
        # All five are the same length, so nothing distinguishes them on the length criterion. The
        # first three are then the right answer: the model listed its best traps first.
        chosen = iq.choose_distractors("aaaa", ["bbbb", "cccc", "dddd", "eeee", "ffff"], n=3)
        assert chosen == ["bbbb", "cccc", "dddd"]

    def test_is_deterministic(self):
        args = ("Le taux atteint quarante pour cent", ["a" * 12, "b" * 30, "c" * 33, "d" * 8, "e" * 31])
        assert iq.choose_distractors(*args) == iq.choose_distractors(*args)


class TestTrapVariety:
    """The model does not vary its traps unprompted — 8 of 11 came back `plausible_unstated` on the
    first live run — so selection prefers a spread of mechanisms once length is settled."""

    def test_prefers_three_distinct_mechanisms_over_a_repeated_one(self):
        # All five are the same length, so the length criterion cannot separate them and variety
        # decides. The first three share a trap; the honest choice reaches for the others.
        chosen = iq.choose_distractors(
            "aaaa",
            ["bbbb", "cccc", "dddd", "eeee", "ffff"],
            n=3,
            traps=[
                "plausible_unstated",
                "plausible_unstated",
                "plausible_unstated",
                "negation_flip",
                "scope_shift",
            ],
        )
        assert chosen != ["bbbb", "cccc", "dddd"]
        assert "eeee" in chosen and "ffff" in chosen

    def test_variety_never_outranks_hiding_the_answer(self):
        # A varied but length-revealing set is worse than a monotonous hidden one: a guessable item
        # is a bigger failure than a repetitive one.
        answer = "a" * 30
        chosen = iq.choose_distractors(
            answer,
            ["b" * 5, "c" * 6, "d" * 31, "e" * 30, "f" * 29],
            n=3,
            traps=["negation_flip", "scope_shift", "wrong_referent", "wrong_referent", "wrong_referent"],
        )
        assert all(len(c) >= 29 for c in chosen), chosen

    def test_missing_trap_labels_are_harmless(self):
        chosen = iq.choose_distractors("aaaa", ["bbbb", "cccc", "dddd", "eeee"], n=3, traps=None)
        assert len(chosen) == 3

    def test_labels_stay_aligned_when_a_candidate_is_deduplicated(self):
        # The second candidate repeats the first, so it is dropped. If the trap list were not
        # filtered alongside it, every later candidate's label would shift by one.
        chosen = iq.choose_distractors(
            "key",
            ["same", "SAME!", "bbbb", "cccc", "dddd"],
            n=3,
            traps=["negation_flip", "IGNORED", "scope_shift", "scope_shift", "time_inversion"],
        )
        # "same" and the two distinct-trap options give three mechanisms; a misaligned list would
        # have made "bbbb" look like the duplicate's label instead.
        assert "same" in chosen
        assert len(chosen) == 3


class TestProperNounRecognition:
    def test_same_sentence_with_different_countries_is_flagged(self):
        assert iq.differs_only_by_proper_noun(
            [
                "Le sommet a lieu à Berlin",
                "Le sommet a lieu à Madrid",
                "Le sommet a lieu à Varsovie",
            ]
        )

    def test_different_claims_are_not_flagged(self):
        assert not iq.differs_only_by_proper_noun(
            [
                "Le chômage a reculé de trente pour cent",
                "L'inflation a reculé de trente pour cent",
                "Le chômage a progressé de trente pour cent",
            ]
        )

    def test_options_differing_only_by_a_number_are_flagged(self):
        assert iq.differs_only_by_proper_noun(
            ["Il y avait 300 personnes", "Il y avait 3000 personnes", "Il y avait 30 personnes"]
        )

    def test_identical_options_are_not_a_proper_noun_problem(self):
        # No proper noun varies, so this is the duplicate-options fault instead. Reporting it here
        # too would send a reader looking for the wrong defect.
        assert not iq.differs_only_by_proper_noun(["Le même texte", "Le même texte"])

    def test_a_sentence_initial_capital_is_not_a_proper_noun(self):
        # Otherwise every option in a language that capitalises sentences looks like a name.
        assert not iq.differs_only_by_proper_noun(
            ["Les prix montent", "Des prix montent", "Certains prix montent"]
        )

    def test_audit_reports_it(self):
        problems = iq.audit_mcq(
            "Où se tient le sommet ?",
            ["Le sommet a lieu à Berlin", "Le sommet a lieu à Madrid", "Le sommet a lieu à Rome"],
            0,
        )
        assert "proper_noun_recognition" in problems


class TestIsWorthLearning:
    @pytest.mark.parametrize("word", ["reculer", "un chiffre", "mettre en cause", "l'écart"])
    def test_real_vocabulary_is_kept(self, word):
        assert iq.is_worth_learning(word)

    @pytest.mark.parametrize(
        "word", ["Guterres", "Berlin", "Antonio Guterres", "ONU", "2024", "30%", "A", ""]
    )
    def test_names_numbers_and_stubs_are_rejected(self, word):
        assert not iq.is_worth_learning(word)


class TestAuditMcq:
    def test_clean_item_has_nothing_to_report(self):
        assert iq.audit_mcq(
            "Quel est le taux annoncé ?",
            ["Trente pour cent", "Treize pour cent", "Quarante pour cent", "Quatorze pour cent"],
            0,
        ) == []

    def test_flags_a_conspicuously_long_key(self):
        problems = iq.audit_mcq(
            "Que dit le journaliste ?",
            ["Le journaliste explique que la réforme est reportée à l'automne prochain", "Non", "Oui", "Rien"],
            0,
        )
        assert "length_cue" in problems

    def test_flags_a_conspicuously_short_key(self):
        problems = iq.audit_mcq(
            "Que dit le journaliste ?",
            ["Rien", "Le journaliste explique que la réforme arrive", "Il annonce une réforme fiscale", "Il conteste la réforme"],
            0,
        )
        assert "length_cue" in problems

    def test_small_length_differences_are_not_flagged(self):
        # Within tolerance. Flagging these would make the audit useless — nearly every honest item
        # has some variation, and an audit that fires constantly gets ignored.
        problems = iq.audit_mcq(
            "Quel est le chiffre ?", ["Trente pour cent", "Vingt pour cent", "Dix pour cent"], 0
        )
        assert "length_cue" not in problems

    def test_flags_duplicate_options(self):
        problems = iq.audit_mcq("Q ?", ["Oui", "oui", "Non", "Rien"], 0)
        assert "duplicate_options" in problems

    def test_flags_an_answer_that_echoes_the_question(self):
        problems = iq.audit_mcq(
            "Pourquoi la réforme fiscale est-elle reportée ?",
            [
                "Parce que la réforme fiscale manque de soutien",
                "Parce que le budget arrive tard",
                "Parce que l'automne approche vite",
                "Parce que le vote a échoué",
            ],
            0,
        )
        assert "answer_echoes_question" in problems

    def test_shared_function_words_are_not_an_echo(self):
        # "dans", "avec", "pour" appear everywhere; counting them would flag every item.
        problems = iq.audit_mcq(
            "Que se passe-t-il dans la ville ?",
            ["Dans la ville, un marché", "Dans la ville, un train", "Dans la ville, un bus"],
            0,
        )
        assert "answer_echoes_question" not in problems

    def test_reports_a_missing_key(self):
        assert "no_correct_option" in iq.audit_mcq("Q ?", ["a", "b", "c"], 7)

    def test_reports_too_few_options(self):
        assert "too_few_options" in iq.audit_mcq("Q ?", ["a", "b"], 0)


class TestSelectionActuallyReducesLeakage:
    """Does the surplus-candidate trick work? Simulated, and labelled as such.

    The real number can only come from a paid regeneration run, so this is a model, not a
    measurement. Its one honest claim to relevance is that it is *fitted to reproduce the observed
    41.5%* before it is used to argue anything: a mixture where the model writes an elaborated,
    noticeably longer key on about a quarter of items and an unremarkable one on the rest.

    What it then shows is that the fix needs BOTH halves of the change, and that neither is
    sufficient alone:

      * selecting 3 distractors from 5 candidates, if the candidates are written without regard to
        the key's length, only partly helps — often none of the five is long enough to cover an
        elaborated key
      * the schema therefore also asks for distractors "roughly the same length as `answer`", which
        is what supplies coverable candidates; with those, selection brings the rate to chance

    A simulation cannot prove the prompt half works — only that it is necessary, and that the
    deterministic half does its job once the candidates allow it.
    """

    # Fitted, not derived: p such that p*(nearly always longest) + (1-p)*(1/4) = 0.415.
    P_ELABORATED_KEY = 0.23
    ELABORATION = 1.4

    @classmethod
    def _simulate(
        cls, *, select: bool, match_lengths: bool, trials: int = 4000, seed: int = 11
    ) -> float:
        rng = random.Random(seed)
        longest = counted = 0
        while counted < trials:
            base = 30.0
            elaborated = rng.random() < cls.P_ELABORATED_KEY
            key_len = max(6, int(rng.gauss(base * (cls.ELABORATION if elaborated else 1.0), 7)))
            # `match_lengths` is the schema instruction: candidates written to the key's length
            # rather than to the passage's own average.
            centre = key_len if match_lengths else base
            cand_lens = [max(6, int(rng.gauss(centre, 7))) for _ in range(5)]
            # Distinct lengths keep the strings distinct, so deduplication never changes the count.
            # Skipped trials must not be counted, or they silently read as "key not longest".
            if len({*cand_lens, key_len}) != 6:
                continue
            counted += 1
            answer = "a" * key_len
            candidates = ["b" * n for n in cand_lens]
            chosen = iq.choose_distractors(answer, candidates, n=3) if select else candidates[:3]
            if chosen and all(len(answer) > len(c) for c in chosen):
                longest += 1
        return longest / counted

    def test_the_model_reproduces_the_measured_baseline(self):
        # Without this the rest of the class argues from a made-up starting point.
        rate = self._simulate(select=False, match_lengths=False)
        assert rate == pytest.approx(0.415, abs=0.04), rate

    def test_selection_alone_helps_but_does_not_finish_the_job(self):
        # Stated as a limitation, not a win: when all five candidates are short, there is nothing
        # to choose that covers an elaborated key.
        before = self._simulate(select=False, match_lengths=False)
        after = self._simulate(select=True, match_lengths=False)
        assert after < before, f"{before:.1%} -> {after:.1%}"
        assert after > 0.10, (
            f"{after:.1%} — if selection alone were this effective, the schema would not need to "
            "ask for length-matched distractors, and this test should be revisited"
        )

    def test_selection_with_length_matched_candidates_reaches_chance(self):
        rate = self._simulate(select=True, match_lengths=True)
        assert rate < 0.05, f"{rate:.1%} still lets 'pick the longest' work"

    def test_length_matching_without_selection_is_also_not_enough(self):
        # The other half of the pair. Asking for matched lengths centres the candidates ON the key,
        # so each one is a coin toss to land above it and the key is longest than all three about
        # 1/8 of the time — better than 41.5%, still two and a half times what selection achieves.
        rate = self._simulate(select=False, match_lengths=True)
        assert rate > 0.10, rate
        assert rate == pytest.approx(0.125, abs=0.03), rate
