#!/usr/bin/env python
"""Render the spoken punctuation names once, as committed assets.

    python scripts/build_punctuation_audio.py --language fr           # macOS `say`
    python scripts/build_punctuation_audio.py --language fr --engine openai
    python scripts/build_punctuation_audio.py --language fr --list

Why these are committed rather than synthesised at runtime: there are a dozen of them, they never
change, and the alternative is a TTS dependency in the request path. macOS `say` is the easiest
way to make them but only exists on a Mac, so depending on it at runtime would mean the feature
works in development and quietly does not in production. Rendering once and committing ~12 small
files makes it work everywhere with no TTS installed and nothing to pay per request.

The voice is deliberately a neutral one, and deliberately NOT the voice in the passage. This is the
reader's voice: a learner should hear immediately that "virgule" is an instruction and not something
the speaker said. A clear female narrator does that job, and it contrasts with the male speakers in
most of the ingested material.

The committed assets are rendered with the `openai` engine rather than macOS `say`. Not for quality
alone — it is that `say` only offers whatever voices a given Mac happens to have installed, so the
one word a learner hears most in a dictée would depend on whose machine last rebuilt these. The
hosted voice is the same everywhere.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.languages import get_language  # noqa: E402
from app.media.punctuation import ASSET_ROOT, asset_name  # noqa: E402

# 22.05 kHz mono is plenty for a single spoken word and keeps the committed assets small.
SAMPLE_RATE = 22_050

# Fallback engine only. Female fr_FR voices vary between macOS versions; Thomas (male) was the
# original and is kept only as a last resort for a machine with no API key.
SAY_VOICE = {"fr": "Audrey"}
# Chosen by measurement, not by the description in the docs. Rendering "virgule" with each candidate
# and taking its median fundamental frequency: alloy (the original) 132Hz and nova 145Hz both sit in
# the male range, sage 179Hz, coral 204Hz. Adult female speech is roughly 165-255Hz, so coral is the
# only one that is unambiguously in it rather than on the boundary.
OPENAI_VOICE = "coral"
# gpt-4o-mini-tts takes direction as well as text. These words are instructions being read over a
# learner's shoulder, so they want to be articulated and flat — not performed.
#
# THE DIRECTION MUST NAME THE LANGUAGE, and this is the whole reason these were re-rendered. The
# first version asked only for "a calm, clear female narrator" and never mentioned French, so the
# model applied English phonetics to French words: "virgule" came out as an English speaker reading
# it — a hard English r, the final -e sounded, stress on the wrong syllable. Every one of these is a
# French word, and a dictée read in an English accent teaches the learner the wrong sound for the
# one word they hear most.
#
# Written IN FRENCH deliberately. Asking in English for a French accent is a weaker signal than
# simply speaking French to the model: the language of the instruction is itself evidence about how
# the text should be read, and the two together leave no room to infer English.
OPENAI_INSTRUCTIONS_BY_LANGUAGE = {
    "fr": (
        "Tu es une narratrice française. Lis ce mot en français, avec une prononciation "
        "parisienne standard et un accent français natif — jamais un accent anglais ou américain. "
        "Articule nettement, à un rythme mesuré, d'un ton neutre et factuel. "
        "Aucune emphase, aucune intonation montante, aucune interprétation : "
        "c'est une consigne dictée à un apprenant, pas une réplique. "
        "Respecte les liaisons et les voyelles nasales du français."
    ),
}

# For a language with no direction of its own yet. Names the language explicitly rather than leaving
# it to be guessed, so adding Russian or Chinese later cannot silently repeat the English-accent bug.
OPENAI_INSTRUCTIONS_FALLBACK = (
    "Read this word as a native speaker of {language_name}, with that language's own accent and "
    "phonetics — never an English accent. Speak as a calm, clear female narrator dictating "
    "punctuation to a language learner: crisp, even, measured, neutral and matter-of-fact. "
    "This is an instruction, not speech."
)


def instructions_for(language_code: str, language_name: str) -> str:
    """The TTS direction for a language. Always names the language, one way or the other."""
    specific = OPENAI_INSTRUCTIONS_BY_LANGUAGE.get(language_code)
    if specific is not None:
        return specific
    return OPENAI_INSTRUCTIONS_FALLBACK.format(language_name=language_name)


def render_say(text: str, dest: Path, voice: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "out.aiff"
        subprocess.run(
            ["say", "-v", voice, "-r", "170", "-o", str(aiff), text],
            check=True, capture_output=True,
        )
        _encode(aiff, dest)


def render_openai(text: str, dest: Path, instructions: str) -> None:
    from app.config import settings

    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set")
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "out.mp3"
        with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice=OPENAI_VOICE,
            input=text,
            instructions=instructions,
            response_format="mp3",
        ) as resp:
            resp.stream_to_file(raw)
        _encode(raw, dest)


def _encode(src: Path, dest: Path) -> None:
    """Normalise loudness and trim leading/trailing silence, then write a small mono wav.

    Trimming matters: `say` pads the start, and an untrimmed asset would insert a gap before every
    spoken comma, which reads as hesitation rather than instruction.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(src),
            "-af",
            "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02:"
            "detection=peak,areverse,"
            "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.02:"
            "detection=peak,areverse,"
            "loudnorm=I=-19:TP=-2:LRA=7",
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            str(dest),
        ],
        check=True, capture_output=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--language", default="fr")
    ap.add_argument("--engine", choices=("say", "openai"), default="say")
    ap.add_argument("--list", action="store_true", help="show what would be rendered")
    args = ap.parse_args()

    lang = get_language(args.language)
    if not lang.punctuation_names:
        raise SystemExit(f"{lang.code} has no punctuation_names in its profile")

    # Several symbols share a spoken name ("..." and "…"), so render each distinct name once.
    names = sorted({name for _, name in lang.punctuation_names})
    out_dir = ASSET_ROOT / lang.code

    if args.list:
        for name in names:
            print(f"{name:24s} -> {out_dir / (asset_name(name) + '.wav')}")
        return

    voice = SAY_VOICE.get(lang.code)
    if args.engine == "say" and not voice:
        raise SystemExit(f"no `say` voice configured for {lang.code}; use --engine openai")

    instructions = instructions_for(lang.code, lang.name_en)
    if args.engine == "openai":
        print(f"direction ({lang.code}): {instructions[:78]}…\n")

    total = 0
    for name in names:
        dest = out_dir / f"{asset_name(name)}.wav"
        if args.engine == "say":
            render_say(name, dest, voice)
        else:
            render_openai(name, dest, instructions)
        size = dest.stat().st_size
        total += size
        dur = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(dest)],
            capture_output=True, text=True,
        ).stdout.strip()
        print(f"{name:24s} {float(dur):.2f}s  {size / 1024:5.1f} KB  {dest.name}")
    print(f"\n{len(names)} assets, {total / 1024:.0f} KB total -> {out_dir}")


if __name__ == "__main__":
    main()
