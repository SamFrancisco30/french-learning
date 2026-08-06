"""The generator's assembly step, with the model stubbed out.

The MCQ path was restructured to take an `answer` plus surplus `distractors` rather than an
options list and an index, so these tests cover the new shape: that the surplus is used to hide the
key, that a malformed item is dropped rather than served broken, and that the trap labels survive
the shuffle attached to the right options.
"""

from __future__ import annotations

import random

import pytest

from app.asr.base import Word
from app.languages import get_language
from app.models import EX_MCQ, EX_ORDERING, EX_TRUE_FALSE, EX_VOCAB_MATCH
from app.skills.listening import generator
from app.skills.listening.generator import generate_unit_exercises

# Options far too short for any selection to hide a normal-length key behind.
SHORT_DISTRACTORS = ["Non", "Oui", "Rien", "Jamais", "Peut-être"]

TRANSCRIPT = (
    "Le chômage a reculé de trente pour cent dans la région selon le ministre. "
    "Mais l'opposition conteste ce chiffre et parle d'une hausse des inégalités."
)


def _words() -> list[Word]:
    out, t = [], 0.0
    for token in TRANSCRIPT.split():
        out.append(Word(token, t, t + 0.3))
        t += 0.35
    return out


def _distractor(text: str, trap: str = "wrong_referent") -> dict:
    return {"text": text, "trap_type": trap, "why_wrong_en": "punishes a misheard referent"}


def _response(**overrides) -> dict:
    base = {
        "gist_en": "A minister claims unemployment fell; the opposition disputes it.",
        "title_target": "Le chômage en recul",
        "topic": "economics",
        "mcq": [
            {
                "question": "Que déclare le ministre ?",
                "answer": "Que le chômage a reculé de trente pour cent",
                "distractors": [
                    _distractor("Non"),
                    _distractor("Rien du tout"),
                    _distractor("Que l'inflation a reculé de trente pour cent", "wrong_referent"),
                    _distractor("Que le chômage a progressé de trente pour cent", "negation_flip"),
                    _distractor("Que le chômage a reculé de treize pour cent", "near_homophone"),
                ],
                "explanation_en": "The minister reports a fall; the traps swap the metric.",
                "quote": "Le chômage a reculé de trente pour cent",
            }
        ],
        "true_false": [
            {
                "statement": "L'opposition accepte ce chiffre.",
                "is_true": False,
                "explanation_en": "The opposition disputes it.",
                "quote": "l'opposition conteste ce chiffre",
            }
        ],
        "vocab": [
            {
                "word": "reculer",
                "gloss_en": "to fall back",
                "definition_target": "diminuer",
                "example_from_audio": "Le chômage a reculé",
            },
            {
                "word": "conteste",
                "gloss_en": "disputes",
                "definition_target": "met en doute",
                "example_from_audio": "l'opposition conteste ce chiffre",
            },
        ],
        "ordering": [
            "Le ministre annonce un recul.",
            "L'opposition conteste.",
            "On parle des inégalités.",
        ],
    }
    base.update(overrides)
    return base


class StubLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def _generate(payload: dict, seed: int = 3) -> dict:
    return generate_unit_exercises(
        transcript=TRANSCRIPT,
        words=_words(),
        lang=get_language("fr"),
        cefr="B1",
        wpm=140.0,
        unit_start_s=0.0,
        unit_end_s=30.0,
        source_title="Journal",
        llm=StubLLM(payload),
        rng=random.Random(seed),
    )


def _mcqs(result: dict) -> list[dict]:
    return [e for e in result["exercises"] if e["kind"] == EX_MCQ]


class TestMcqAssembly:
    def test_builds_four_options_from_answer_plus_selected_distractors(self):
        mcq = _mcqs(_generate(_response()))
        assert len(mcq) == 1
        assert len(mcq[0]["payload"]["options"]) == 4

    def test_the_answer_index_points_at_the_answer_after_shuffling(self):
        item = _mcqs(_generate(_response()))[0]
        options, answer = item["payload"]["options"], item["answer"]
        assert options[answer["index"]] == answer["value"]

    def test_the_short_filler_distractors_are_discarded(self):
        # "Non" and "Rien du tout" would leave the key conspicuously the longest.
        options = _mcqs(_generate(_response()))[0]["payload"]["options"]
        assert "Non" not in options
        assert "Rien du tout" not in options

    def test_the_key_is_not_left_uniquely_longest(self):
        item = _mcqs(_generate(_response()))[0]
        options = item["payload"]["options"]
        key = item["answer"]["value"]
        others = [o for o in options if o != key]
        assert not all(len(key) > len(o) for o in others)

    def test_trap_labels_stay_aligned_with_their_options(self):
        item = _mcqs(_generate(_response()))[0]
        options = item["payload"]["options"]
        traps = item["payload"]["traps"]
        assert len(traps) == len(options)
        # The key carries no trap; every distractor does.
        assert traps[item["answer"]["index"]] is None
        expected = {
            "Que l'inflation a reculé de trente pour cent": "wrong_referent",
            "Que le chômage a progressé de trente pour cent": "negation_flip",
            "Que le chômage a reculé de treize pour cent": "near_homophone",
        }
        for option, trap in zip(options, traps):
            if option != item["answer"]["value"]:
                assert trap == expected[option]

    def test_a_clean_item_records_no_give_aways(self):
        assert "give_aways" not in _mcqs(_generate(_response()))[0]["payload"]

    def test_the_quote_is_anchored_to_a_timestamp_inside_the_unit(self):
        item = _mcqs(_generate(_response()))[0]
        assert 0.0 <= item["audio_start_s"] < item["audio_end_s"] <= 30.0


class TestMalformedItems:
    def test_item_without_an_answer_is_dropped(self):
        payload = _response()
        payload["mcq"][0]["answer"] = "  "
        assert _mcqs(_generate(payload)) == []

    def test_item_without_a_question_is_dropped(self):
        payload = _response()
        payload["mcq"][0]["question"] = ""
        assert _mcqs(_generate(payload)) == []

    def test_item_with_too_few_distractors_is_dropped(self):
        payload = _response()
        payload["mcq"][0]["distractors"] = [_distractor("Un"), _distractor("Deux")]
        assert _mcqs(_generate(payload)) == []

    def test_distractors_that_all_repeat_the_answer_drop_the_item(self):
        # Nothing usable survives deduplication, so there is no defensible key.
        payload = _response()
        answer = payload["mcq"][0]["answer"]
        payload["mcq"][0]["distractors"] = [_distractor(answer) for _ in range(5)]
        assert _mcqs(_generate(payload)) == []

    def test_non_dict_distractors_are_ignored_not_fatal(self):
        payload = _response()
        payload["mcq"][0]["distractors"] = [None, "plain string", *payload["mcq"][0]["distractors"]]
        assert len(_mcqs(_generate(payload))) == 1

    def test_a_name_recognition_item_is_dropped_not_served(self):
        # Options that are one sentence with a different name dropped in. Explicitly unwanted: it
        # asks whether a label was caught, not whether anything was understood.
        payload = _response()
        payload["mcq"][0]["question"] = "Où se tient le sommet ?"
        payload["mcq"][0]["answer"] = "Le sommet a lieu à Berlin cette semaine"
        payload["mcq"][0]["distractors"] = [
            _distractor("Le sommet a lieu à Madrid cette semaine"),
            _distractor("Le sommet a lieu à Varsovie cette semaine"),
            _distractor("Le sommet a lieu à Lisbonne cette semaine"),
            _distractor("Le sommet a lieu à Rome cette semaine"),
            _distractor("Le sommet a lieu à Vienne cette semaine"),
        ]
        assert _mcqs(_generate(payload, seed=5)) == []

    def test_a_residual_give_away_is_recorded_rather_than_dropped(self):
        # Every candidate is far shorter than the key, so no selection can hide it. The item is
        # still worth serving — but the flaw has to be visible.
        payload = _response()
        payload["mcq"][0]["distractors"] = [
            _distractor(t) for t in ("Non", "Oui", "Rien", "Jamais", "Peut-être")
        ]
        item = _mcqs(_generate(payload))[0]
        assert "length_cue" in item["payload"]["give_aways"]


class TestLengthRepair:
    """The second pass, for keys that no offered distractor can cover.

    Measured on a 10-unit live sample: telling the model to match the key's length left the key
    uniquely longest on 60% of items — worse than the 41.5% baseline, because the richer distractor
    brief also produced a richer key. So the conspicuous ones are sent back with character counts.
    """

    @staticmethod
    def _payload_with_short_distractors() -> dict:
        payload = _response()
        payload["mcq"][0]["distractors"] = [
            _distractor(t) for t in SHORT_DISTRACTORS
        ]
        return payload

    class TwoCallStub:
        """Returns the unit payload first, then a repair payload."""

        def __init__(self, first: dict, second: dict) -> None:
            self.responses = [first, second]
            self.calls: list[dict] = []

        def complete_json(self, **kwargs):
            self.calls.append(kwargs)
            return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]

    def _run(self, stub, **kwargs) -> dict:
        return generate_unit_exercises(
            transcript=TRANSCRIPT,
            words=_words(),
            lang=get_language("fr"),
            cefr="B1",
            wpm=140.0,
            unit_start_s=0.0,
            unit_end_s=30.0,
            source_title="Journal",
            llm=stub,
            rng=random.Random(3),
            **kwargs,
        )

    def test_a_clean_item_does_not_trigger_a_second_call(self):
        # The repair costs a call per unit; it must only fire when it is needed.
        stub = StubLLM(_response())
        self._run(stub)
        assert len(stub.calls) == 1

    def test_an_unhideable_key_triggers_the_repair(self):
        stub = self.TwoCallStub(self._payload_with_short_distractors(), {"items": []})
        self._run(stub)
        assert len(stub.calls) == 2
        assert "characters" in stub.calls[1]["user"]

    def test_the_repair_prompt_states_the_keys_length_in_characters(self):
        payload = self._payload_with_short_distractors()
        stub = self.TwoCallStub(payload, {"items": []})
        self._run(stub)
        key = payload["mcq"][0]["answer"]
        assert f"{len(key)} characters" in stub.calls[1]["user"]
        assert key in stub.calls[1]["user"]

    def test_replacements_in_the_requested_band_hide_the_key(self):
        payload = self._payload_with_short_distractors()
        key = payload["mcq"][0]["answer"]
        target = len(key)
        # Written to the band the repair prompt actually asks for — straddling the key, some longer
        # and some shorter. An earlier version of this fixture made them all much longer, which
        # exposed a real flaw: that only converts "key is longest" into "key is shortest".
        replacements = {
            "items": [
                {
                    "question": payload["mcq"][0]["question"],
                    "distractors": [
                        _distractor("Que l'inflation a reculé de trente pour cent", "wrong_referent"),
                        _distractor("Que le chômage a progressé de trente pour cent un peu", "negation_flip"),
                        _distractor("Que le chômage a reculé de treize pour cent", "near_homophone"),
                        _distractor("Que l'opposition a reculé de trente pour cent ici", "attribution_swap"),
                        _distractor("Que le chômage pourrait reculer de trente", "scope_shift"),
                    ],
                }
            ]
        }
        for d in replacements["items"][0]["distractors"]:
            assert int(target * 0.85) <= len(d["text"]) <= int(target * 1.25), (
                f"fixture is outside the band the prompt requests: {len(d['text'])} vs {target}"
            )

        stub = self.TwoCallStub(payload, replacements)
        item = _mcqs(self._run(stub))[0]
        options = item["payload"]["options"]
        others = [o for o in options if o != key]
        assert not all(len(key) > len(o) for o in others), options
        assert not all(len(key) < len(o) for o in others), options
        assert "give_aways" not in item["payload"], options
        # The short originals lost to the replacements rather than being merged in beside them.
        assert not any(o in SHORT_DISTRACTORS for o in options)

    def test_the_repair_prompt_asks_for_options_on_both_sides_of_the_key(self):
        # The guard against fixing one give-away by creating its mirror image.
        payload = self._payload_with_short_distractors()
        stub = self.TwoCallStub(payload, {"items": []})
        self._run(stub)
        user = stub.calls[1]["user"]
        target = len(payload["mcq"][0]["answer"])
        assert f"between {int(target * 0.85)} and {int(target * 1.25)}" in user
        assert "LONGER" in user

    def test_a_failed_repair_keeps_the_original_item(self):
        from app.llm.openai_client import LLMError

        class FailingStub:
            def __init__(self, first: dict) -> None:
                self.first = first
                self.calls = 0

            def complete_json(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return self.first
                raise LLMError("upstream refused")

        stub = FailingStub(self._payload_with_short_distractors())
        item = _mcqs(self._run(stub))[0]
        # Degraded, not lost: the question still works, and the flaw is on the record.
        assert "length_cue" in item["payload"]["give_aways"]

    def test_replacements_for_an_unrecognised_question_are_ignored(self):
        # The model is asked to echo the question verbatim; if it paraphrases, the replacements
        # cannot be matched and must not be attached to the wrong item.
        payload = self._payload_with_short_distractors()
        stub = self.TwoCallStub(
            payload,
            {"items": [{"question": "A completely different question?", "distractors": [_distractor("x" * 60)]}]},
        )
        options = _mcqs(self._run(stub))[0]["payload"]["options"]
        assert "x" * 60 not in options

    def test_repair_can_be_switched_off(self):
        stub = StubLLM(self._payload_with_short_distractors())
        self._run(stub, repair_give_aways=False)
        assert len(stub.calls) == 1


class TestOtherExerciseKinds:
    def test_all_four_kinds_are_produced(self):
        kinds = {e["kind"] for e in _generate(_response())["exercises"]}
        assert kinds == {EX_MCQ, EX_TRUE_FALSE, EX_VOCAB_MATCH, EX_ORDERING}

    def test_ordering_answer_keeps_the_spoken_order_and_the_prompt_does_not(self):
        item = next(e for e in _generate(_response())["exercises"] if e["kind"] == EX_ORDERING)
        assert item["answer"]["order"] == [
            "Le ministre annonce un recul.",
            "L'opposition conteste.",
            "On parle des inégalités.",
        ]
        assert item["payload"]["items"] != item["answer"]["order"]

    def test_order_indexes_are_unique_and_contiguous(self):
        exercises = _generate(_response())["exercises"]
        assert [e["order_idx"] for e in exercises] == list(range(len(exercises)))

    def test_metadata_is_passed_through(self):
        result = _generate(_response())
        assert result["topic"] == "economics"
        assert result["title"] == "Le chômage en recul"
        assert result["gist_en"].startswith("A minister")


class TestPromptContract:
    def test_the_system_prompt_names_the_traps_and_the_exam(self):
        stub = StubLLM(_response())
        generate_unit_exercises(
            transcript=TRANSCRIPT,
            words=_words(),
            lang=get_language("fr"),
            cefr="B1",
            wpm=140.0,
            unit_start_s=0.0,
            unit_end_s=30.0,
            source_title="Journal",
            llm=stub,
            rng=random.Random(1),
        )
        system = stub.calls[0]["system"]
        # The traps are the substance of the change; a silent drop would quietly restore the old
        # "three vaguely wrong paraphrases" behaviour with every test still green.
        for trap in ("wrong_referent", "attribution_swap", "negation_flip", "plausible_unstated"):
            assert trap in system
        assert "DELF/DALF" in system
        assert "B1" in system
        # The anti-give-away rule, which is what the measured 41.5% was about.
        assert "longest" in system

    def test_the_exam_reference_table_covers_the_planned_languages(self):
        # Asserted against the table rather than through a generation for all three: Chinese
        # frequency lookup needs `jieba`, which is not installed in this environment, and a prompt
        # string is not worth pulling in a Chinese tokeniser to check.
        assert generator.EXAM_HINTS["zh"] == "HSK"
        assert generator.EXAM_HINTS["ru"].startswith("ТРКИ")
        assert generator.EXAM_HINTS.get("de", generator.DEFAULT_EXAM_HINT) == (
            generator.DEFAULT_EXAM_HINT
        ), "an unlisted language must still get a sensible exam reference"

    @pytest.mark.parametrize("code,hint", [("fr", "DELF/DALF"), ("ru", "ТРКИ")])
    def test_the_exam_reference_follows_the_language(self, code, hint):
        stub = StubLLM(_response())
        generate_unit_exercises(
            transcript=TRANSCRIPT,
            words=_words(),
            lang=get_language(code),
            cefr="B1",
            wpm=140.0,
            unit_start_s=0.0,
            unit_end_s=30.0,
            source_title="Journal",
            llm=stub,
            rng=random.Random(1),
        )
        assert hint in stub.calls[0]["system"]
