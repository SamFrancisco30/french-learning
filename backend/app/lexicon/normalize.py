import unicodedata

_APOSTROPHES = str.maketrans(
    {
        "\u2019": "'",
        "\u02bc": "'",
        "\uff07": "'",
        "\u0060": "'",
        "\u00b4": "'",
    }
)


def normalize_vocab_v1(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.translate(_APOSTROPHES)
    normalized = " ".join(normalized.split())
    return normalized.casefold()
