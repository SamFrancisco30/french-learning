#!/usr/bin/env python
"""Migrate the local SQLite library and audio into Supabase (Postgres + Storage).

    python scripts/migrate_to_supabase.py plan          # what would happen, no writes
    python scripts/migrate_to_supabase.py audio         # upload objects only
    python scripts/migrate_to_supabase.py rows          # copy database rows only
    python scripts/migrate_to_supabase.py all           # audio, then rows
    python scripts/migrate_to_supabase.py verify        # compare counts and spot-check objects

Design notes
------------
* **The source database is read with raw SQL, not the ORM.** The models have moved on
  (`audio_path` -> `audio_key`, JSON -> JSONB, new columns), so mapping the old file through
  the new classes would fail or silently coerce. Raw reads keep the old shape explicit and
  the column mapping visible in one place.

* **Ids are preserved.** Any lesson or unit id already shared or bookmarked keeps working,
  and foreign keys copy across without a translation table. Postgres sequences don't advance
  on explicit-id inserts, so they are reset afterwards — skip that and the next ingest fails
  with a duplicate key.

* **Everything is idempotent.** Objects already in the bucket are skipped; rows already
  present are skipped. A failed run is re-runnable rather than needing a manual cleanup.

* **Derived working files are not uploaded.** The `.upload.mp3` and `.asr.wav` copies exist
  only to feed the ASR API and are regenerable — about 40% of local disk.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import JSON, create_engine, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKEND_ROOT, settings  # noqa: E402
from app.models import (  # noqa: E402
    Attempt,
    Base,
    Exercise,
    Expression,
    GlossCache,
    Lesson,
    ListeningUnit,
    Segment,
    SentenceAnalysis,
    Source,
    Transcript,
    VocabItem,
)
from app.storage import get_store, is_derived  # noqa: E402
from app.storage.base import clip_key, source_key, transcript_key  # noqa: E402

cli = typer.Typer(add_completion=False, help="Migrate local data into Supabase")
console = Console()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("hpack").setLevel(logging.WARNING)

SQLITE_PATH = BACKEND_ROOT / "data" / "polyglot.sqlite"

# Copy order matters: parents before children, so foreign keys are always satisfiable.
# (model, sqlite table, {new_column: old_column} for renames)
TABLES: list[tuple[type, str, dict[str, str]]] = [
    (Source, "sources", {"audio_key": "audio_path"}),
    (Transcript, "transcripts", {"raw_key": "raw_path"}),
    (Segment, "segments", {}),
    (Lesson, "lessons", {}),
    (ListeningUnit, "listening_units", {"clip_key": "clip_path"}),
    (Exercise, "exercises", {}),
    (Attempt, "attempts", {}),
    (Expression, "expressions", {}),
    (GlossCache, "gloss_cache", {}),
    (SentenceAnalysis, "sentence_analyses", {}),
    (VocabItem, "vocab_items", {}),
]


# ---------------------------------------------------------------- helpers


def _sqlite() -> sqlite3.Connection:
    if not SQLITE_PATH.exists():
        console.print(f"[red]no local database at {SQLITE_PATH}[/red]")
        raise typer.Exit(1)
    conn = sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _target_session():
    url = settings.resolved_database_url()
    if url.startswith("sqlite"):
        console.print(
            "[red]DATABASE_URL still points at SQLite.[/red] Set it to the Supabase "
            "session-mode pooler connection string first (see backend/.env.example)."
        )
        raise typer.Exit(1)
    engine = create_engine(url, future=True, pool_pre_ping=True)
    return sessionmaker(bind=engine, future=True)(), engine


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _audio_plan() -> list[tuple[str, Path]]:
    """(object key, local file) pairs to upload, derived working files excluded."""
    plan: list[tuple[str, Path]] = []
    conn = _sqlite()
    try:
        cols = _columns(conn, "sources")
        path_col = "audio_path" if "audio_path" in cols else "audio_key"
        for row in conn.execute(f"SELECT provider_id, {path_col} AS p FROM sources"):
            if not row["p"]:
                continue
            local = Path(row["p"])
            if not local.is_absolute():
                local = settings.data_dir / local
            if local.exists() and not is_derived(local):
                plan.append((source_key(row["provider_id"], local.suffix), local))

        unit_cols = _columns(conn, "listening_units")
        clip_col = "clip_path" if "clip_path" in unit_cols else "clip_key"
        q = (
            f"SELECT u.idx AS idx, u.{clip_col} AS p, s.provider_id AS pid "
            "FROM listening_units u JOIN lessons l ON l.id = u.lesson_id "
            "JOIN sources s ON s.id = l.source_id"
        )
        for row in conn.execute(q):
            if not row["p"]:
                continue
            local = settings.data_dir / row["p"] if not Path(row["p"]).is_absolute() else Path(row["p"])
            if local.exists():
                plan.append((clip_key(row["pid"], row["idx"]), local))

        tcols = _columns(conn, "transcripts")
        rcol = "raw_path" if "raw_path" in tcols else "raw_key"
        q = (
            f"SELECT t.{rcol} AS p, t.asr_backend AS b, s.provider_id AS pid "
            "FROM transcripts t JOIN sources s ON s.id = t.source_id"
        )
        for row in conn.execute(q):
            if not row["p"]:
                continue
            local = Path(row["p"])
            if not local.is_absolute():
                local = settings.data_dir / local
            if local.exists():
                plan.append((transcript_key(row["pid"], row["b"]), local))
    finally:
        conn.close()
    return plan


# ---------------------------------------------------------------- commands


@cli.command()
def plan() -> None:
    """Show what would be migrated. Touches nothing."""
    conn = _sqlite()
    table = Table(title="rows to copy")
    table.add_column("table"); table.add_column("rows", justify="right")
    total = 0
    for _, name, _ in TABLES:
        try:
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
        except sqlite3.OperationalError:
            n = 0
        total += n
        table.add_row(name, str(n))
    table.add_row("[bold]total[/bold]", f"[bold]{total}[/bold]")
    conn.close()
    console.print(table)

    objects = _audio_plan()
    size = sum(p.stat().st_size for _, p in objects)
    console.print(
        f"\n[bold]{len(objects)}[/bold] objects to upload, "
        f"[bold]{size / 1e6:.0f} MB[/bold] "
        f"([dim]{size / 1024 / 1024 / 1024 * 100:.0f}% of a 1 GB free-tier bucket[/dim])"
    )
    console.print(f"storage backend: [cyan]{settings.storage_backend}[/cyan]")
    console.print(f"target database: [cyan]{settings.resolved_database_url().split('@')[-1]}[/cyan]")


@cli.command()
def audio(
    dry_run: bool = typer.Option(False, help="List uploads without performing them"),
    overwrite: bool = typer.Option(False, help="Re-upload objects that already exist"),
) -> None:
    """Upload audio and transcript objects to the configured store."""
    objects = _audio_plan()
    store = get_store()
    if store.name == "local":
        console.print("[yellow]storage backend is 'local' — set STORAGE_BACKEND=supabase[/yellow]")
        raise typer.Exit(1)

    if hasattr(store, "ensure_bucket") and not dry_run:
        store.ensure_bucket(public=False)  # private: audio must not be hotlinkable

    done = skipped = failed = 0
    for key, local in objects:
        mb = local.stat().st_size / 1e6
        if dry_run:
            console.print(f"  [dim]would upload[/dim] {key} ({mb:.1f} MB)")
            continue
        try:
            if not overwrite and store.exists(key):
                skipped += 1
                continue
            store.put_file(key, local, overwrite=overwrite)
            done += 1
            console.print(f"  [green]uploaded[/green] {key} ({mb:.1f} MB)")
        except Exception as exc:  # noqa: BLE001 - report and continue; the run is resumable
            failed += 1
            console.print(f"  [red]failed[/red] {key}: {exc}")

    if not dry_run:
        console.print(
            f"\nuploaded [green]{done}[/green], skipped [dim]{skipped}[/dim]"
            + (f", [red]{failed} failed[/red]" if failed else "")
        )
        if failed:
            console.print("[dim]re-run to retry — already-uploaded objects are skipped[/dim]")


@cli.command()
def rows(dry_run: bool = typer.Option(False, help="Report without writing")) -> None:
    """Copy database rows into the target Postgres, preserving ids."""
    session, engine = _target_session()
    Base.metadata.create_all(engine)  # no-op if alembic already ran

    conn = _sqlite()
    try:
        for model, name, renames in TABLES:
            try:
                src_rows = conn.execute(f"SELECT * FROM {name}").fetchall()
            except sqlite3.OperationalError:
                console.print(f"  [dim]{name}: not in the source database, skipping[/dim]")
                continue
            if not src_rows:
                console.print(f"  [dim]{name}: empty[/dim]")
                continue

            existing = set(session.scalars(select(model.id)).all())
            target_cols = {c.name for c in model.__table__.columns}
            # SQLite keeps JSON columns as TEXT, so a raw read hands back Python strings.
            # Passing a str into a jsonb column stores a *quoted JSON string* rather than an
            # object, which round-trips as `'{"text": ...}'` instead of a dict and breaks
            # every consumer. So JSON-typed columns are decoded before insert.
            json_cols = {
                c.name
                for c in model.__table__.columns
                if isinstance(c.type, (JSON, JSONB)) or c.type.__class__.__name__ == "JSON"
            }
            inserted = 0
            for row in src_rows:
                data = dict(row)
                if data.get("id") in existing:
                    continue
                mapped = {}
                for col in target_cols:
                    old = renames.get(col, col)
                    if old in data:
                        val = data[old]
                        if col in json_cols and isinstance(val, str):
                            try:
                                val = json.loads(val)
                            except (ValueError, TypeError):
                                pass  # leave a genuinely non-JSON string alone
                        mapped[col] = val
                # Path columns become storage keys. The stored value was a data_dir-relative
                # path whose shape differs from the key convention, so it is recomputed
                # rather than reused.
                if not dry_run:
                    session.add(model(**mapped))
                inserted += 1
            if not dry_run:
                session.flush()
            console.print(
                f"  {name}: {'would insert' if dry_run else 'inserted'} "
                f"[green]{inserted}[/green] of {len(src_rows)} "
                f"([dim]{len(existing)} already present[/dim])"
            )

        if dry_run:
            session.rollback()
            console.print("\n[yellow]dry run — nothing written[/yellow]")
            return

        session.commit()
        _fix_keys(session)
        _reset_sequences(session, engine)
        session.commit()
        console.print("\n[green]rows copied[/green]")
    finally:
        conn.close()
        session.close()


def _fix_keys(session) -> None:
    """Rewrite migrated path values into object-store keys.

    The old columns held data_dir-relative paths ("clips/VIDEOID/unit_000.m4a" happens to
    match, but "data/audio/VIDEOID.m4a" does not), so keys are recomputed from the
    convention rather than trusted.
    """
    for src in session.scalars(select(Source)).all():
        if src.audio_key:
            suffix = Path(src.audio_key).suffix or ".m4a"
            src.audio_key = source_key(src.provider_id, suffix)
    for tr in session.scalars(select(Transcript)).all():
        if tr.raw_key and tr.source:
            tr.raw_key = transcript_key(tr.source.provider_id, tr.asr_backend)
    for unit in session.scalars(select(ListeningUnit)).all():
        if unit.clip_key and unit.lesson and unit.lesson.source:
            unit.clip_key = clip_key(unit.lesson.source.provider_id, unit.idx)
    session.flush()
    console.print("  [dim]rewrote path columns to storage keys[/dim]")


def _reset_sequences(session, engine) -> None:
    """Advance Postgres identity sequences past the migrated ids.

    Explicit-id inserts leave sequences at their starting value, so without this the very
    next ingest fails with a duplicate primary key.
    """
    if engine.dialect.name != "postgresql":
        return
    for model, name, _ in TABLES:
        max_id = session.scalar(select(func.max(model.id)))
        if max_id:
            session.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:t, 'id'), :v, true)"
                ).bindparams(t=name, v=max_id)
            )
    console.print("  [dim]reset id sequences[/dim]")


@cli.command()
def verify() -> None:
    """Compare row counts between source and target, and spot-check objects."""
    session, engine = _target_session()
    conn = _sqlite()
    table = Table(title="row counts")
    for col in ("table", "sqlite", "target", "ok"):
        table.add_column(col, justify="right" if col != "table" else "left")
    all_ok = True
    try:
        for model, name, _ in TABLES:
            try:
                a = conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
            except sqlite3.OperationalError:
                a = 0
            b = session.scalar(select(func.count()).select_from(model)) or 0
            ok = b >= a
            all_ok &= ok
            table.add_row(name, str(a), str(b), "[green]yes[/green]" if ok else "[red]NO[/red]")
    finally:
        conn.close()
    console.print(table)

    store = get_store()
    missing = []
    for unit in session.scalars(select(ListeningUnit).limit(200)).all():
        if unit.clip_key and not store.exists(unit.clip_key):
            missing.append(unit.clip_key)
    session.close()

    if missing:
        console.print(f"\n[red]{len(missing)} clip object(s) missing[/red] from {store.name}:")
        for k in missing[:8]:
            console.print(f"  {k}")
        console.print("[dim]run the 'audio' command to upload them[/dim]")
    else:
        console.print(f"\n[green]all checked clip objects present in {store.name}[/green]")
    if not all_ok:
        raise typer.Exit(1)


@cli.command("all")
def do_all() -> None:
    """Upload objects, then copy rows."""
    audio(dry_run=False, overwrite=False)
    rows(dry_run=False)
    verify()


if __name__ == "__main__":
    cli()
