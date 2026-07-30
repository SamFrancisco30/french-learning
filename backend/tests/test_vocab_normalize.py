import conftest as test_config
import pytest

from app.lexicon.normalize import normalize_vocab_v1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" Écouter ", "écouter"),
        ("L’EAU", "l'eau"),
        ("lʼeau", "l'eau"),
        ("mise   en œuvre", "mise en œuvre"),
        ("côte", "côte"),
        ("cote", "cote"),
    ],
)
def test_normalize_vocab_v1(value: str, expected: str) -> None:
    assert normalize_vocab_v1(value) == expected


def test_normalize_vocab_v1_preserves_accent_distinctions() -> None:
    assert normalize_vocab_v1("côte") != normalize_vocab_v1("cote")


def test_db_session_disposes_engine_when_schema_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = test_config.create_engine("sqlite:///:memory:")
    original_dispose = engine.dispose
    disposed = False

    def track_dispose() -> None:
        nonlocal disposed
        disposed = True
        original_dispose()

    def fail_schema_creation(_engine: object) -> None:
        raise RuntimeError("schema creation failed")

    monkeypatch.setattr(test_config, "create_engine", lambda _url: engine)
    monkeypatch.setattr(engine, "dispose", track_dispose)
    monkeypatch.setattr(test_config.Base.metadata, "create_all", fail_schema_creation)

    fixture_generator = test_config.db_session.__wrapped__()
    with pytest.raises(RuntimeError, match="schema creation failed"):
        next(fixture_generator)

    assert disposed
