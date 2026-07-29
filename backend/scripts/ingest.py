#!/usr/bin/env python
"""Command-line ingest / inspection tool.

    python scripts/ingest.py add "https://youtu.be/..." --topic world_news
    python scripts/ingest.py list
    python scripts/ingest.py show 1
    python scripts/ingest.py search "géographie documentaire" --limit 8
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.models import Exercise, Lesson, ListeningUnit  # noqa: E402
from app.skills.listening.pipeline import build_listening_lesson  # noqa: E402

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
) -> None:
    """Download, transcribe and build a listening lesson from a media URL."""
    init_db()
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
        )

    console.print(
        Panel.fit(
            f"[bold]{report.title}[/bold]\n"
            f"lesson [cyan]{report.lesson_id}[/cyan]  ·  {report.units} units  ·  "
            f"{report.exercises} exercises  ·  {report.expressions} expressions\n"
            f"level [magenta]{report.cefr}[/magenta] (difficulty {report.difficulty})  ·  "
            f"ASR {report.asr_backend}/{report.asr_model}"
            + ("  [dim](transcript reused)[/dim]" if report.reused_transcript else "")
            + (f"\n[yellow]LLM failures: {report.llm_failures}[/yellow]" if report.llm_failures else ""),
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
            if u.clip_path:
                console.print(f"  [dim]clip:[/dim] /media/{u.clip_path}")

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
                r = diff.analyze(u.text, u.duration_s, lang)
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
