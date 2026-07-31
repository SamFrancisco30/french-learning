from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas import (
    ExpressionOut,
    VocabEditIn,
    VocabItemOut,
    VocabListOut,
    VocabSavedKeysOut,
    VocabSaveIn,
    WordGlossOut,
)


@pytest.mark.parametrize("extra", [{"learner_key": "x"}, {"user_id": 3}, {"x": 1}])
def test_vocab_save_rejects_all_extra_fields(extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VocabSaveIn.model_validate(
            {"language": "fr", "headword": "mot", **extra}
        )


def test_vocab_save_requires_headword_string() -> None:
    with pytest.raises(ValidationError):
        VocabSaveIn.model_validate({"language": "fr"})
    with pytest.raises(ValidationError):
        VocabSaveIn.model_validate({"language": "fr", "headword": 123})


@pytest.mark.parametrize(
    ("field", "maximum"),
    [("headword", 128), ("gloss_en", 1000), ("example", 2000)],
)
def test_vocab_save_enforces_exact_raw_code_point_limits(
    field: str, maximum: int
) -> None:
    payload: dict[str, object] = {"language": "fr", "headword": "mot"}
    payload[field] = "é" * maximum
    assert getattr(VocabSaveIn.model_validate(payload), field) == "é" * maximum

    payload[field] = "é" * (maximum + 1)
    with pytest.raises(ValidationError):
        VocabSaveIn.model_validate(payload)


def test_vocab_save_only_applies_raw_headword_length_rules() -> None:
    assert VocabSaveIn(language="fr", headword="").headword == ""


@pytest.mark.parametrize("payload", [{}, {"unit_id": 1}, {"gloss_en": "x", "x": 1}])
def test_vocab_edit_requires_an_allowed_field_and_rejects_extras(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        VocabEditIn.model_validate(payload)


@pytest.mark.parametrize("field", ["gloss_en", "example"])
@pytest.mark.parametrize("value", [None, ""])
def test_vocab_edit_allows_null_or_empty_values_to_clear(
    field: str, value: str | None
) -> None:
    edit = VocabEditIn.model_validate({field: value})
    assert field in edit.model_fields_set
    assert getattr(edit, field) == value


@pytest.mark.parametrize(
    ("field", "maximum"), [("gloss_en", 1000), ("example", 2000)]
)
def test_vocab_edit_enforces_raw_code_point_limits(
    field: str, maximum: int
) -> None:
    assert getattr(VocabEditIn.model_validate({field: "é" * maximum}), field)
    with pytest.raises(ValidationError):
        VocabEditIn.model_validate({field: "é" * (maximum + 1)})


def _item_payload(*, source: dict[str, object] | None) -> dict[str, object]:
    now = datetime(2026, 7, 30, 18, 0, tzinfo=UTC)
    return {
        "id": 42,
        "language": "fr",
        "headword": "écouter",
        "normalized_headword": "écouter",
        "gloss_en": "to listen",
        "example": "J'écoute la radio.",
        "zipf": 5.1,
        "reps": 0,
        "due_at": None,
        "created_at": now,
        "updated_at": now,
        "source": source,
    }


def test_full_vocab_item_and_list_shapes_validate_exactly() -> None:
    source = {
        "lesson_id": 7,
        "lesson_title": "Une émission",
        "unit_id": 12,
        "unit_index": 2,
    }
    item = VocabItemOut.model_validate(_item_payload(source=source))
    result = VocabListOut.model_validate(
        {"items": [item], "next_cursor": None, "total": 1}
    )

    assert result.model_dump() == {
        "items": [_item_payload(source=source)],
        "next_cursor": None,
        "total": 1,
    }
    with pytest.raises(ValidationError):
        VocabItemOut.model_validate({**_item_payload(source=source), "extra": 1})
    with pytest.raises(ValidationError):
        VocabListOut.model_validate(
            {"items": [], "next_cursor": None, "total": 0, "extra": 1}
        )


def test_vocab_item_accepts_nullable_source() -> None:
    assert VocabItemOut.model_validate(_item_payload(source=None)).source is None


def test_saved_keys_shape_validates_exactly() -> None:
    saved = VocabSavedKeysOut.model_validate(
        {
            "language": "fr",
            "items": [{"id": 42, "normalized_headword": "écouter"}],
        }
    )
    assert saved.model_dump() == {
        "language": "fr",
        "items": [{"id": 42, "normalized_headword": "écouter"}],
    }
    with pytest.raises(ValidationError):
        VocabSavedKeysOut.model_validate(
            {
                "language": "fr",
                "items": [
                    {
                        "id": 42,
                        "normalized_headword": "écouter",
                        "headword": "écouter",
                    }
                ],
            }
        )


def test_lookup_outputs_require_normalized_headword() -> None:
    word_fields = {
        "surface": "Écoute",
        "lemma": "écouter",
        "gloss_en": "listen",
    }
    expression_fields = {
        "canonical": "être en train de",
        "surface": "est en train de",
        "kind": "expression",
        "gloss_en": "to be doing",
        "char_start": 0,
        "char_end": 15,
        "confidence": 1.0,
        "source": "precomputed",
    }
    with pytest.raises(ValidationError):
        WordGlossOut.model_validate(word_fields)
    with pytest.raises(ValidationError):
        ExpressionOut.model_validate(expression_fields)

    assert (
        WordGlossOut.model_validate(
            {**word_fields, "normalized_headword": "écouter"}
        ).normalized_headword
        == "écouter"
    )
    assert (
        ExpressionOut.model_validate(
            {**expression_fields, "normalized_headword": "être en train de"}
        ).normalized_headword
        == "être en train de"
    )
