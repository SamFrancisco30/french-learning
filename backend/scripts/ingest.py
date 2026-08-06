#!/usr/bin/env python
"""Command-line ingest / inspection tool.

    python scripts/ingest.py add "https://youtu.be/..." --topic world_news
    python scripts/ingest.py list
    python scripts/ingest.py show 1
    python scripts/ingest.py search "géographie documentaire" --limit 8
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import Exercise, Lesson, ListeningUnit, Source  # noqa: E402
from app.skills.listening import audio_quality as quality_mod  # noqa: E402
from app.skills.listening.pipeline import (  # noqa: E402
    SourceRejected,
    build_listening_lesson,
)

app_cli = typer.Typer(add_completion=False, help="Listening-lesson ingest pipeline")
console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


@app_cli.command("add")
def add(
    url: str,
    lang: str = typer.Option("fr", help="Target language code (fr, ru, zh)"),
    topic: str | None = typer.Option(None, help="Topic tag, e.g. world_news / biology"),
    asr: str | None = typer.Option(None, help="ASR backend: openai | local"),
    asr_model: str | None = typer.Option(None, help="Override the ASR model id"),
    max_units: int | None = typer.Option(None, help="Cap the number of units built"),
    max_duration: float = typer.Option(3600.0, help="Reject sources longer than this (seconds)"),
    llm: bool = typer.Option(True, "--llm/--no-llm", help="Generate LLM comprehension items"),
    reuse: bool = typer.Option(
        True, "--reuse/--re-transcribe", help="Reuse an existing transcript for this source"
    ),
    clips: bool = typer.Option(True, "--clips/--no-clips", help="Extract per-unit audio clips"),
    require_cc: bool = typer.Option(False, help="Only accept Creative Commons sources"),
    cleanup_local: bool | None = typer.Option(
        None,
        "--cleanup-local/--keep-local",
        help="Delete local working files once their objects are confirmed uploaded "
        "(defaults on for cloud storage, never applies to local storage)",
    ),
    ignore_quality: bool = typer.Option(
        False,
        "--ignore-quality",
        help="Build the lesson even if the audio fails the clarity check",
    ),
) -> None:
    """Download, transcribe and build a listening lesson from a media URL."""
    init_db()
    try:
        with session_scope() as db:
            report = build_listening_lesson(
                db,
                url,
                language=lang,
                topic=topic,
                asr_backend=asr,
                asr_model=asr_model,
                max_units=max_units,
                max_duration_s=max_duration,
                require_cc=require_cc,
                use_llm=llm,
                reuse_transcript=reuse,
                make_clips=clips,
                cleanup_local=cleanup_local,
                ignore_quality=ignore_quality,
            )
    except SourceRejected as exc:
        # An unsuitable video is an ordinary outcome of looking for material, not a crash, so it
        # gets a readable panel and a non-zero exit rather than a traceback. The transcript is
        # kept: it has already been paid for, and it is what a later --ignore-quality run reuses.
        console.print(
            Panel.fit(
                "\n".join(f"· {r}" for r in exc.report.reasons)
                + "\n\n[dim]"
                + "  ".join(f"{k}={v}" for k, v in exc.report.metrics.items() if v is not None)
                + "[/dim]\n\n[dim]Override with --ignore-quality if you disagree.[/dim]",
                title="source rejected — audio not clear enough",
                border_style="red",
            )
        )
        raise typer.Exit(code=2)

    quality_line = ""
    if report.audio_quality != "accept":
        quality_line = f"\n[yellow]audio {report.audio_quality}: " + "; ".join(
            report.audio_quality_notes
        ) + "[/yellow]"

    console.print(
        Panel.fit(
            f"[bold]{report.title}[/bold]\n"
            f"lesson [cyan]{report.lesson_id}[/cyan]  ·  {report.units} units  ·  "
            f"{report.exercises} exercises  ·  {report.expressions} expressions\n"
            f"level [magenta]{report.cefr}[/magenta] (difficulty {report.difficulty})  ·  "
            f"ASR {report.asr_backend}/{report.asr_model}"
            + ("  [dim](transcript reused)[/dim]" if report.reused_transcript else "")
            + (f"\n[yellow]LLM failures: {report.llm_failures}[/yellow]" if report.llm_failures else "")
            + (f"\n[dim]local disk freed: {report.local_mb_freed:.1f} MB[/dim]" if report.local_mb_freed else "")
            + quality_line,
            title="lesson built",
            border_style="green",
        )
    )


@app_cli.command("list")
def list_lessons(lang: str | None = typer.Option(None)) -> None:
    """List lessons in the library."""
    init_db()
    with session_scope() as db:
        q = db.query(Lesson)
        if lang:
            q = q.filter(Lesson.language == lang)
        lessons = q.order_by(Lesson.created_at.desc()).all()

        table = Table(title="Lessons", show_lines=False)
        for col in ("id", "lang", "level", "units", "exercises", "topic", "title"):
            table.add_column(col)
        for l in lessons:
            n_ex = (
                db.query(Exercise)
                .join(ListeningUnit, ListeningUnit.id == Exercise.unit_id)
                .filter(ListeningUnit.lesson_id == l.id)
                .count()
            )
            table.add_row(
                str(l.id),
                l.language,
                f"{l.cefr} ({l.difficulty_score:.0f})" if l.cefr else "-",
                str(len(l.units)),
                str(n_ex),
                l.topic or "-",
                (l.title[:58] + "…") if len(l.title) > 59 else l.title,
            )
        console.print(table)


@app_cli.command("show")
def show(lesson_id: int, unit: int | None = typer.Option(None, help="Only this unit index")) -> None:
    """Inspect the units and exercises of a lesson."""
    init_db()
    with session_scope() as db:
        lesson = db.get(Lesson, lesson_id)
        if not lesson:
            console.print(f"[red]lesson {lesson_id} not found[/red]")
            raise typer.Exit(1)

        console.print(
            Panel.fit(
                f"[bold]{lesson.title}[/bold]\n{lesson.source.channel or '-'}  ·  "
                f"{lesson.language}  ·  {lesson.cefr}  ·  topic {lesson.topic or '-'}\n"
                f"[dim]{lesson.source.url}[/dim]\n"
                f"licence: {lesson.source.license_name or 'standard YouTube'}",
                border_style="cyan",
            )
        )

        for u in lesson.units:
            if unit is not None and u.idx != unit:
                continue
            console.print(
                f"\n[bold cyan]Unit {u.idx}[/bold cyan]  "
                f"{u.start_s:.0f}s–{u.end_s:.0f}s ({u.duration_s:.0f}s)  "
                f"[magenta]{u.cefr}[/magenta] score {u.difficulty_score:.0f}  "
                f"{u.wpm:.0f} wpm  ·  {len(u.exercises)} exercises"
            )
            if u.gist:
                console.print(f"  [dim]gist:[/dim] {u.gist}")
            if u.clip_key:
                console.print(f"  [dim]clip:[/dim] {u.clip_key}")

            for e in u.exercises:
                console.print(f"\n  [yellow]{e.kind}[/yellow] #{e.order_idx}  {e.prompt}")
                if e.kind == "cloze":
                    console.print(f"    [dim]{e.payload.get('masked_text', '')[:400]}[/dim]")
                    console.print(f"    answers: {e.answer.get('blanks')}")
                    if e.payload.get("word_bank"):
                        console.print(f"    word bank: {e.payload['word_bank']}")
                elif e.kind == "mcq":
                    for i, opt in enumerate(e.payload.get("options", [])):
                        mark = "[green]✓[/green]" if i == e.answer.get("index") else " "
                        console.print(f"    {mark} {i}. {opt}")
                elif e.kind == "true_false":
                    console.print(f"    answer: {e.answer.get('value')}")
                elif e.kind == "vocab_match":
                    for w, g in e.answer.get("pairs", {}).items():
                        console.print(f"    {w} → {g}")
                elif e.kind == "ordering":
                    for i, item in enumerate(e.answer.get("order", []), 1):
                        console.print(f"    {i}. {item}")
                if e.explanation:
                    console.print(f"    [dim]why: {e.explanation}[/dim]")
                if e.audio_start_s is not None:
                    console.print(
                        f"    [dim]replay: {e.audio_start_s:.1f}s–{e.audio_end_s:.1f}s[/dim]"
                    )


@app_cli.command("search")
def search(
    query: str,
    limit: int = typer.Option(10),
    max_minutes: float = typer.Option(15.0, help="Hide results longer than this"),
) -> None:
    """Search YouTube for candidate sources (metadata only, no download)."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit * 2}:{query}", download=False)

    table = Table(title=f"YouTube: {query}")
    for col in ("duration", "channel", "title", "url"):
        table.add_column(col, overflow="fold")

    shown = 0
    for e in info.get("entries") or []:
        if not e:
            continue
        dur = e.get("duration") or 0
        if dur and dur > max_minutes * 60:
            continue
        table.add_row(
            f"{dur // 60:.0f}:{dur % 60:02.0f}" if dur else "?",
            (e.get("channel") or e.get("uploader") or "-")[:24],
            (e.get("title") or "-")[:60],
            e.get("url") or f"https://youtu.be/{e.get('id')}",
        )
        shown += 1
        if shown >= limit:
            break
    console.print(table)


@app_cli.command("rescore")
def rescore(
    dry_run: bool = typer.Option(False, help="Show what would change without writing"),
) -> None:
    """Recompute difficulty for every stored unit and lesson.

    Difficulty is derived purely from stored text and duration, so recalibrating the model
    costs nothing — no ASR, no LLM. Run this after changing the anchors in
    skills/listening/difficulty.py.
    """
    from collections import Counter

    from app.languages import get_language
    from app.skills.listening import difficulty as diff

    init_db()
    moved = 0
    before_levels: Counter[str] = Counter()
    after_levels: Counter[str] = Counter()

    with session_scope() as db:
        lessons = db.query(Lesson).order_by(Lesson.id).all()
        for lesson in lessons:
            lang = get_language(lesson.language)
            reports = []
            for u in lesson.units:
                old_cefr, old_score = u.cefr, u.difficulty_score or 0.0
                r = diff.analyze(u.text, u.duration_s, lang, words=u.words_json or [])
                reports.append(r)
                before_levels[old_cefr or "?"] += 1
                after_levels[r.cefr] += 1
                if old_cefr != r.cefr:
                    moved += 1
                    console.print(
                        f"  unit {lesson.id}/{u.idx}: [dim]{old_cefr} ({old_score:.0f})[/dim]"
                        f" → [cyan]{r.cefr} ({r.score:.0f})[/cyan]"
                    )
                if not dry_run:
                    u.cefr, u.difficulty_score, u.difficulty_detail = (
                        r.cefr,
                        r.score,
                        r.to_dict(),
                    )
            if reports:
                score, cefr = diff.aggregate(reports)
                if not dry_run:
                    lesson.difficulty_score, lesson.cefr = score, cefr
        if dry_run:
            db.rollback()

    table = Table(title="unit level distribution")
    for col in ("level", "before", "after"):
        table.add_column(col)
    for lvl in ("A1", "A2", "B1", "B2", "C1", "C2"):
        table.add_row(lvl, str(before_levels.get(lvl, 0)), str(after_levels.get(lvl, 0)))
    console.print(table)
    console.print(
        f"{moved} unit(s) changed band" + ("  [yellow](dry run — nothing written)[/yellow]" if dry_run else "")
    )


@app_cli.command("audit-audio")
def audit_audio(
    lesson_id: int | None = typer.Option(None, help="Only this lesson; omit for all"),
    verdict: str | None = typer.Option(
        None, "--verdict", help="Show only rows with this verdict: accept | warn | reject"
    ),
) -> None:
    """Re-score the clarity of every lesson already in the library.

    Reads the confidence figures stored with each transcript, so this costs nothing and needs no
    audio: it answers "which of the lessons I already published would today's gate have caught?".
    Loudness is not part of it — that needs the file — so a lesson can pass here and still warn on
    re-ingest.
    """
    from app.models import Transcript
    from app.skills.listening.pipeline import transcript_as_result

    init_db()
    rows: list[tuple] = []
    with session_scope() as db:
        q = db.query(Lesson).order_by(Lesson.id)
        if lesson_id:
            q = q.filter(Lesson.id == lesson_id)
        for lesson in q.all():
            tr = db.get(Transcript, lesson.transcript_id) if lesson.transcript_id else None
            if tr is None:
                rows.append((lesson.id, lesson.title, "-", "no transcript", {}))
                continue
            report = quality_mod.assess_transcript(transcript_as_result(tr), tr.duration_s)
            if verdict and report.verdict != verdict:
                continue
            rows.append(
                (lesson.id, lesson.title, report.verdict, "; ".join(report.reasons), report.metrics)
            )

    table = Table(title="listening audio quality")
    for col, just in (
        ("lesson", "right"), ("title", "left"), ("verdict", "left"),
        ("conf", "right"), ("non-speech", "right"), ("words/s", "right"), ("notes", "left"),
    ):
        table.add_column(col, justify=just, overflow="fold")

    colour = {"accept": "green", "warn": "yellow", "reject": "red"}
    counts: dict[str, int] = {}
    for lid, title, v, notes, metrics in rows:
        counts[v] = counts.get(v, 0) + 1
        style = colour.get(v, "dim")
        table.add_row(
            str(lid),
            (title or "")[:44],
            f"[{style}]{v}[/{style}]",
            _fmt(metrics.get("logprob_mean")),
            _fmt(metrics.get("fraction_non_speech")),
            _fmt(metrics.get("words_per_second")),
            notes,
        )
    console.print(table)
    console.print("  ".join(f"[{colour.get(k, 'dim')}]{k}: {n}[/]" for k, n in sorted(counts.items())))


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


CEFR_BANDS = ("A1", "A2", "B1", "B2", "C1", "C2")

# What kind of material actually lands in each band. Not guesses: these are the registers the
# difficulty scorer has been observed to place in each band — graded beginner French reads A1/A2,
# slow news B1, ordinary broadcast and documentary B2, lecture and criticism C1.
#
# C2 has no entry on purpose. It needs a score of 80 and the library's ceiling after 69 units is
# 70.4, so no search term can reach it; see the note in `coverage`.
BAND_HINTS = {
    "A1": ["français facile débutant", "apprendre le français A1 lentement"],
    "A2": ["français facile A2", "français authentique lent A2"],
    "B1": ["journal en français facile", "expliqué simplement français"],
    "B2": ["reportage documentaire", "France Culture émission"],
    "C1": ["conférence analyse", "débat approfondi spécialiste"],
}

# Bands only worth attempting where graded material genuinely exists. There is no A1 broadcast about
# biology or parliamentary procedure: beginner French is written for daily life, news-in-brief and
# culture, so aiming the low bands at every topic would only fill cells with mislabelled B1 audio.
GRADED_TOPICS = {"society", "world_news", "culture", "environment"}
LOW_BANDS = {"A1", "A2"}
# Reachable for every topic — this is where authentic French media actually lives.
CORE_BANDS = ("B1", "B2", "C1")

TOPIC_QUERIES = {
    "world_news": "actualité internationale",
    "politics": "politique institutions",
    "economics": "économie entreprises",
    "geography": "géographie territoire",
    "biology": "biologie vivant",
    "science": "science recherche",
    "technology": "technologie numérique",
    "environment": "environnement climat",
    "history": "histoire",
    "culture": "culture littérature art",
    "society": "société quotidien",
    "sport": "sport",
}


@app_cli.command("coverage")
def coverage(
    lang: str = typer.Option("fr", help="Language code"),
    target: int = typer.Option(2, help="Wanted lessons per topic per CEFR band"),
    queries: bool = typer.Option(
        False, "--queries", help="Print a suggested search query for each missing cell"
    ),
) -> None:
    """How far the library is from N lessons per CEFR band per topic.

    Worth knowing before planning any ingest: a lesson's CEFR is MEASURED from its audio by the
    difficulty scorer, not chosen. So a gap at a particular (topic, band) cell cannot be filled by
    deciding to fill it — it needs a source that happens to land in that band, and for some
    combinations authentic media barely exists.
    """
    from app.skills.listening.generator import TOPICS

    init_db()
    grid: dict[tuple[str, str], int] = {}
    with session_scope() as db:
        for lesson in db.query(Lesson).filter(Lesson.language == lang).all():
            if not lesson.cefr:
                continue
            grid[(lesson.topic or "other", lesson.cefr)] = (
                grid.get((lesson.topic or "other", lesson.cefr), 0) + 1
            )

    # "other" is the classifier's escape hatch, not a subject anyone studies, so it is shown for
    # visibility but never targeted — otherwise a single unclassifiable lesson invents a whole row
    # of gaps that no search could ever be aimed at.
    topics = sorted(({t for t, _ in grid} | set(TOPICS)) - {"other"})
    table = Table(title=f"{lang} listening lessons per CEFR band (target {target} each)")
    table.add_column("topic")
    for band in CEFR_BANDS:
        table.add_column(band, justify="center")
    table.add_column("gap", justify="right")

    total_gap = 0
    for topic in topics:
        row = [topic]
        gap = 0
        for band in CEFR_BANDS:
            have = grid.get((topic, band), 0)
            short = max(0, target - have)
            gap += short
            style = "green" if have >= target else "yellow" if have else "red"
            row.append(f"[{style}]{have}[/{style}]")
        total_gap += gap
        row.append(str(gap))
        table.add_row(*row)

    console.print(table)
    have_total = sum(grid.values())
    want_total = len(topics) * len(CEFR_BANDS) * target
    console.print(
        f"{have_total} lessons across {len(topics)} topics x {len(CEFR_BANDS)} bands; "
        f"target {want_total} — [bold]{total_gap} short[/bold] "
        f"({len(topics) * len(CEFR_BANDS) - sum(1 for t in topics for b in CEFR_BANDS if grid.get((t, b)))} "
        "empty cells)"
    )

    # The reachable subset, which is what `fill-coverage` actually targets.
    wanted = _target_cells(topics, target, grid)
    console.print(
        f"[bold]{sum(wanted.values())}[/bold] of those are reachable and targeted "
        f"({len(wanted)} cells); C2 is excluded because nothing can score into it, and A1/A2 only "
        f"for {', '.join(sorted(GRADED_TOPICS))}"
    )

    if queries:
        console.print("\n[bold]suggested searches for the reachable gaps[/bold]")
        console.print('[dim]feed one to: ingest.py search "<query>" --limit 8[/dim]\n')
        for (topic, band), short in sorted(wanted.items()):
            for q in _queries_for(topic, band):
                console.print(f"  {topic:12s} {band} (need {short})  [cyan]{q}[/cyan]")


def _target_cells(
    topics: list[str], target: int, grid: dict[tuple[str, str], int]
) -> dict[tuple[str, str], int]:
    """Which (topic, band) cells are both short and actually fillable, and by how many."""
    wanted: dict[tuple[str, str], int] = {}
    for topic in topics:
        bands = list(CORE_BANDS) + (sorted(LOW_BANDS) if topic in GRADED_TOPICS else [])
        for band in bands:
            short = max(0, target - grid.get((topic, band), 0))
            if short:
                wanted[(topic, band)] = short
    return wanted


def _queries_for(topic: str, band: str) -> list[str]:
    subject = TOPIC_QUERIES.get(topic, topic)
    # "en français" biases the search itself; `_looks_like_language` cleans up what still slips
    # through. Both are needed — the query alone returns English results for French terms.
    return [f"{subject} {hint} en français" for hint in BAND_HINTS[band]]


def _rejected_ledger() -> Path:
    """Where sources that failed the quality gate are remembered.

    A plain file rather than a column: the pipeline rolls its transaction back when it rejects a
    source, so nothing about the attempt survives in the database, and without this a long fill run
    would pay for the same bad audio again on every pass.
    """
    return Path(settings.data_dir) / "rejected_sources.json"


def _load_rejected() -> dict[str, str]:
    path = _rejected_ledger()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _remember_rejected(provider_id: str, reason: str) -> None:
    ledger = _load_rejected()
    ledger[provider_id] = reason
    path = _rejected_ledger()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2))


# Cheap language screening on the search result's title. YouTube search honours the query's words,
# not its language, so a French query returns English and Dutch results freely — the first dry run
# offered "Keyshawn Johnson Just Asked the Question LeBron Can't Escape" for a French sport cell.
# Forcing Whisper to transcribe English audio as French produces confident-looking nonsense, and
# finding that out costs a full ASR pass, so it is worth screening on the title first.
LANGUAGE_MARKERS: dict[str, tuple[str, set[str]]] = {
    "fr": (
        "éèêëàâäîïôöûüùçœ",
        {"le", "la", "les", "des", "du", "de", "un", "une", "et", "est", "dans", "pour", "qui",
         "que", "avec", "sur", "pas", "plus", "ce", "cette", "sont", "ont", "il", "elle", "nous",
         "vous", "ils", "au", "aux", "en", "son", "ses", "par", "comment", "pourquoi"},
    ),
    "ru": ("абвгдежзийклмнопрстуфхцчшщъыьэюя", {"и", "в", "не", "на", "что", "как", "это"}),
    "zh": ("的一是不了人我在有他这为之大来以个中上们", set()),
}
MIN_STOPWORD_HITS = 2


def _looks_like_language(title: str, lang: str) -> bool:
    """Is this title plausibly in the target language?

    Deliberately permissive — it only has to reject the obviously-wrong ones, since the difficulty
    scorer and the audio-quality gate catch what slips through. Unknown languages pass everything
    rather than silently filtering a language this table has no entry for.
    """
    markers = LANGUAGE_MARKERS.get(lang)
    if not markers:
        return True
    diacritics, stopwords = markers
    lowered = title.casefold()
    if any(c in lowered for c in diacritics):
        return True
    if not stopwords:
        return False
    words = set(re.findall(r"\w+", lowered))
    return len(words & stopwords) >= MIN_STOPWORD_HITS


def _candidates(
    query: str, *, limit: int, max_minutes: float, lang: str
) -> list[tuple[str, str, str]]:
    """(provider_id, url, title) for a search. Metadata only, no download."""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit * 4}:{query}", download=False)
    except Exception as exc:  # network, throttling, extractor changes
        console.print(f"    [yellow]search failed: {type(exc).__name__}[/yellow]")
        return []

    out: list[tuple[str, str, str]] = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        dur = e.get("duration") or 0
        # A floor as well as a ceiling: a 40-second clip cannot be segmented into listening units.
        if dur and not (90 <= dur <= max_minutes * 60):
            continue
        title = e.get("title") or "?"
        if not _looks_like_language(title, lang):
            continue
        out.append((e["id"], e.get("url") or f"https://youtu.be/{e['id']}", title))
        if len(out) >= limit:
            break
    return out


@app_cli.command("fill-coverage")
def fill_coverage(
    lang: str = typer.Option("fr", help="Language code"),
    target: int = typer.Option(2, help="Wanted lessons per topic per band"),
    max_ingests: int = typer.Option(10, help="Hard ceiling on sources ingested this run"),
    max_minutes: float = typer.Option(12.0, help="Skip candidates longer than this"),
    per_cell: int = typer.Option(6, help="Candidates to consider per search"),
    dry_run: bool = typer.Option(False, help="Show the plan and the candidates, ingest nothing"),
    topic_only: str | None = typer.Option(None, "--topic", help="Restrict to one topic"),
    bands: str | None = typer.Option(
        None,
        "--bands",
        help="Comma-separated bands to target, e.g. A1,A2,B1,B2. Use this to stop spending on a "
        "band the scorer rarely reaches: C1 has landed on 2 of 90 lessons, so aiming at it mostly "
        "buys B2 lessons at C1 prices.",
    ),
) -> None:
    """Work through the reachable coverage gaps, ingesting candidates until they are filled.

    Opportunistic by necessity: a lesson's CEFR is measured from its audio, not chosen, so a source
    picked for a B1 cell may land in B2. That is not a failure — it fills a real cell — so the grid
    is re-read after every ingest and the next search aims at whatever is still short. A cell that
    cannot be filled after its queries are exhausted is reported rather than forced.

    `--max-ingests` is the spend control. Every ingest is one ASR pass plus roughly a dozen LLM
    calls, so the default is deliberately small; raise it once a run looks sane.
    """
    from app.skills.listening.generator import TOPICS

    init_db()
    rejected = _load_rejected()
    ingested = 0
    filled: list[str] = []
    failures: list[str] = []

    def read_grid() -> tuple[dict[tuple[str, str], int], set[str]]:
        with session_scope() as db:
            grid: dict[tuple[str, str], int] = {}
            for lesson in db.query(Lesson).filter(Lesson.language == lang).all():
                if lesson.cefr:
                    key = (lesson.topic or "other", lesson.cefr)
                    grid[key] = grid.get(key, 0) + 1
            seen = {s.provider_id for s in db.query(Source).all()}
        return grid, seen

    grid, seen = read_grid()
    topics = [topic_only] if topic_only else sorted(set(TOPICS) - {"other"})
    wanted = _target_cells(topics, target, grid)
    if bands:
        keep = {b.strip().upper() for b in bands.split(",") if b.strip()}
        wanted = {cell: n for cell, n in wanted.items() if cell[1] in keep}

    console.print(
        f"[bold]{sum(wanted.values())} lessons needed across {len(wanted)} cells[/bold]; "
        f"ceiling {max_ingests} ingest(s) this run"
        + ("  [yellow](dry run)[/yellow]" if dry_run else "")
    )

    for (topic, band), _short in sorted(wanted.items(), key=lambda kv: -kv[1]):
        if ingested >= max_ingests:
            break
        for query in _queries_for(topic, band):
            # Re-read rather than trusting the plan: an earlier ingest in this run may already have
            # landed in this cell, in which case there is nothing left to do here.
            grid, seen = read_grid()
            if grid.get((topic, band), 0) >= target or ingested >= max_ingests:
                break

            console.print(f"\n[cyan]{topic} {band}[/cyan] — searching: {query}")
            found = _candidates(query, limit=per_cell, max_minutes=max_minutes, lang=lang)
            fresh = [c for c in found if c[0] not in seen and c[0] not in rejected]
            console.print(
                f"  {len(found)} candidate(s), {len(fresh)} new "
                f"({len(found) - len(fresh)} already seen or previously rejected)"
            )

            for provider_id, url, title in fresh:
                if ingested >= max_ingests:
                    break
                console.print(f"  -> {title[:70]}")
                if dry_run:
                    continue
                try:
                    with session_scope() as db:
                        report = build_listening_lesson(
                            db,
                            url,
                            language=lang,
                            # Deliberately NOT topic=topic. Forcing the searched-for tag onto
                            # whatever came back would label a car review as sport just because the
                            # query said sport; letting the generator classify it keeps the tag
                            # honest, and the loop re-reads the grid to see which cell was filled.
                            topic=None,
                            max_duration_s=max_minutes * 60,
                        )
                except SourceRejected as exc:
                    _remember_rejected(provider_id, exc.report.summary())
                    rejected[provider_id] = exc.report.summary()
                    console.print(f"     [red]rejected:[/red] {exc.report.summary()[:110]}")
                    continue
                except Exception as exc:
                    failures.append(f"{provider_id}: {type(exc).__name__}: {exc}")
                    console.print(f"     [red]failed:[/red] {type(exc).__name__}: {str(exc)[:110]}")
                    continue

                ingested += 1
                landed = (report.topic or "other", report.cefr)
                note = "as aimed" if landed == (topic, band) else f"aimed {topic}/{band}"
                filled.append(f"{report.title[:38]} -> {landed[0]}/{landed[1]} ({note})")
                console.print(
                    f"     [green]built[/green] lesson {report.lesson_id}: "
                    f"{report.units} units, {report.exercises} exercises, "
                    f"[magenta]{landed[0]}/{landed[1]}[/magenta] ({note})"
                    + (
                        f"  [yellow]{report.audio_quality}[/yellow]"
                        if report.audio_quality != "accept"
                        else ""
                    )
                )
                grid, seen = read_grid()
                if grid.get((topic, band), 0) >= target:
                    break

    console.print(f"\n[bold]{ingested} lesson(s) built this run[/bold]")
    for line in filled:
        console.print(f"  [green]+[/green] {line}")
    for line in failures:
        console.print(f"  [red]![/red] {line}")
    console.print("\nRe-run `coverage` to see the updated grid.")


@app_cli.command("audit-items")
def audit_items(lesson_id: int | None = typer.Option(None, help="Only this lesson; omit for all")) -> None:
    """Can the multiple-choice answers be guessed without listening?

    Reports the give-away rates across the library, with the chance baseline beside each so the
    numbers mean something. "Longest option is correct" at 25% is a coin toss working as intended;
    at 41.5% — which is what it measured before the distractor selection existed — it is a learner
    scoring 41% with the audio muted.
    """
    from collections import Counter

    from app.models import EX_MCQ, EX_TRUE_FALSE
    from app.skills.listening import itemquality

    init_db()
    problems: Counter[str] = Counter()
    traps: Counter[str] = Counter()
    total = longest = tf_true = tf_total = 0
    cues: list[float] = []

    with session_scope() as db:
        q = db.query(Exercise).join(ListeningUnit).join(Lesson)
        if lesson_id:
            q = q.filter(Lesson.id == lesson_id)
        for ex in q.all():
            if ex.kind == EX_TRUE_FALSE:
                tf_total += 1
                tf_true += bool((ex.answer or {}).get("value"))
                continue
            if ex.kind != EX_MCQ:
                continue
            options = (ex.payload or {}).get("options") or []
            value = (ex.answer or {}).get("value")
            if not options or value not in options:
                problems["no_correct_option"] += 1
                total += 1
                continue
            total += 1
            ci = options.index(value)
            others = [o for i, o in enumerate(options) if i != ci]
            if others and all(len(value) > len(o) for o in others):
                longest += 1
            cues.append(itemquality.length_cue(value, others))
            for name in itemquality.audit_mcq(ex.prompt, options, ci):
                problems[name] += 1
            for t in (ex.payload or {}).get("traps") or []:
                if t:
                    traps[t] += 1

    if not total:
        console.print("[yellow]no multiple-choice items found[/yellow]")
        return

    table = Table(title=f"answer leakage across {total} MCQ items")
    table.add_column("give-away")
    table.add_column("items", justify="right")
    table.add_column("rate", justify="right")
    table.add_column("baseline", justify="right")

    def row(label: str, count: int, baseline: str) -> None:
        rate = count / total
        style = "red" if rate > 0.32 else "yellow" if rate > 0.28 else "green"
        table.add_row(label, str(count), f"[{style}]{rate:.1%}[/{style}]", baseline)

    row("longest option is correct", longest, "25%")
    for name in ("length_cue", "answer_echoes_question", "duplicate_options", "no_correct_option"):
        row(name, problems.get(name, 0), "—")
    console.print(table)
    console.print(
        f"mean length advantage of the key: {sum(cues) / len(cues):+.1%} of its own length"
        if cues
        else ""
    )
    if tf_total:
        console.print(f"true/false: {tf_true}/{tf_total} true ({tf_true / tf_total:.1%}, baseline 50%)")
    if traps:
        console.print("\ntraps used: " + "  ".join(f"{k}={v}" for k, v in traps.most_common()))
    else:
        console.print("\n[dim]no trap labels recorded — these items predate the trap taxonomy[/dim]")


@app_cli.command("annotate")
def annotate(
    lesson_id: int | None = typer.Option(None, help="Only this lesson; omit for all"),
    force: bool = typer.Option(False, help="Re-annotate units that already have expressions"),
) -> None:
    """Extract multiword expressions for existing units (backfill).

    Ingest does this automatically now; this is for units created before the extractor
    existed, or after changing the extraction prompt.
    """
    from app.languages import get_language
    from app.lexicon.extractor import extract_expressions
    from app.models import Expression

    init_db()
    with session_scope() as db:
        q = db.query(ListeningUnit)
        if lesson_id:
            q = q.join(Lesson).filter(Lesson.id == lesson_id)
        units = q.order_by(ListeningUnit.lesson_id, ListeningUnit.idx).all()

        total = skipped = 0
        for u in units:
            existing = db.query(Expression).filter(Expression.unit_id == u.id).count()
            if existing and not force:
                skipped += 1
                continue
            if existing:
                db.query(Expression).filter(Expression.unit_id == u.id).delete()

            lang = get_language(u.lesson.language)
            found = extract_expressions(u.text, lang)
            for e in found:
                db.add(Expression(unit_id=u.id, language=lang.code, **e))
            db.flush()
            total += len(found)
            console.print(
                f"  lesson {u.lesson_id} unit {u.idx}: [green]{len(found)}[/green] expressions"
            )
            for e in found[:4]:
                disc = " [yellow](split)[/yellow]" if len(e["component_spans"]) > 1 else ""
                console.print(
                    f"      [dim]{e['surface']}[/dim] → {e['canonical']} "
                    f"= {e['gloss_en']}{disc}"
                )

    console.print(
        f"\n[green]{total}[/green] expressions across {len(units) - skipped} units"
        + (f" ([dim]{skipped} already annotated, use --force[/dim])" if skipped else "")
    )


@app_cli.command("config")
def show_config() -> None:
    """Print effective configuration."""
    console.print(
        Panel.fit(
            f"asr_backend      {settings.asr_backend}\n"
            f"asr_openai_model {settings.asr_openai_model}\n"
            f"asr_local_model  {settings.asr_local_model}\n"
            f"llm_model        {settings.llm_model}\n"
            f"openai key       {'set' if settings.openai_api_key else '[red]missing[/red]'}\n"
            f"database         {settings.resolved_database_url()}\n"
            f"data_dir         {settings.data_dir}",
            title="config",
            border_style="blue",
        )
    )


if __name__ == "__main__":
    app_cli()
