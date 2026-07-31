from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest

if importlib.util.find_spec("numpy") is None:
    # Importing app.main also imports the optional audio timestretch module. Startup logging
    # does not execute that module; this placeholder keeps this focused test independent of
    # the heavyweight local-audio extra.
    sys.modules.setdefault("numpy", ModuleType("numpy"))

main = importlib.import_module("app.main")

POSTGRES_URL = (
    "postgresql+psycopg://fake_user:fake_password@db.example.test:6543/words_prod"
    + "?sslmode=require&token=fake_query_secret"
)


@pytest.mark.parametrize(
    ("database_url", "expected"),
    [
        (
            POSTGRES_URL,
            "postgresql+psycopg db.example.test/words_prod",
        ),
        (
            "sqlite:////private/fake-user/projects/polyglot.sqlite?mode=ro",
            "sqlite polyglot.sqlite",
        ),
        ("sqlite:///:memory:", "sqlite :memory:"),
    ],
)
def test_safe_database_label_exposes_only_a_safe_database_summary(
    database_url: str,
    expected: str,
) -> None:
    sanitizer = getattr(main, "safe_database_label", None)

    assert callable(sanitizer), "main.safe_database_label must sanitize startup logs"
    assert sanitizer(database_url) == expected


def test_startup_logs_only_the_sanitized_database_label(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database_url = POSTGRES_URL
    fake_settings = SimpleNamespace(
        asr_backend="test-asr",
        llm_model="test-llm",
        ensure_dirs=lambda: None,
        resolved_database_url=lambda: database_url,
    )
    monkeypatch.setattr(main, "settings", fake_settings)
    monkeypatch.setattr(main, "init_db", lambda: None)
    caplog.set_level(logging.INFO, logger=main.__name__)

    main._startup()

    assert "db=postgresql+psycopg db.example.test/words_prod" in caplog.text
    assert "fake_user" not in caplog.text
    assert "fake_password" not in caplog.text
    assert "fake_query_secret" not in caplog.text
    assert "6543" not in caplog.text
