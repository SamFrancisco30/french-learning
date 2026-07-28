"""Language registry."""

from __future__ import annotations

from .base import LanguageProfile
from .chinese import CHINESE
from .french import FRENCH
from .russian import RUSSIAN

_REGISTRY: dict[str, LanguageProfile] = {
    FRENCH.code: FRENCH,
    RUSSIAN.code: RUSSIAN,
    CHINESE.code: CHINESE,
}

DEFAULT_LANGUAGE = FRENCH.code


def get_language(code: str) -> LanguageProfile:
    key = (code or DEFAULT_LANGUAGE).lower().split("-")[0]
    try:
        return _REGISTRY[key]
    except KeyError:
        raise ValueError(
            f"Unsupported language {code!r}. Available: {', '.join(sorted(_REGISTRY))}"
        ) from None


def supported_languages() -> list[LanguageProfile]:
    return list(_REGISTRY.values())


__all__ = ["LanguageProfile", "get_language", "supported_languages", "DEFAULT_LANGUAGE"]
