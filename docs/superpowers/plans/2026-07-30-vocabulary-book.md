# Vocabulary Book MVP Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an anonymous vocabulary-book MVP with secure-by-construction ownership boundaries, deterministic vocabulary normalization, saved-state synchronization, and an authentication-ready identity interface.

**Architecture:** Keep attempts and progress on their existing anonymous contracts, while moving vocabulary behind a focused `LearnerIdentity` dependency and a dedicated vocabulary router/service. Freeze the dynamic Alembic baseline before adding normalized vocabulary fields and partial ownership indexes. On the frontend, central identity and vocabulary providers supply a global My Words page and synchronize saved state with contextual lookup.

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL/Supabase, SQLite, pytest, React 19, TypeScript 6, Vite 8, Vitest, Testing Library.

**Approved spec:** `docs/superpowers/specs/2026-07-30-vocabulary-book-design.md`

**Implementation discipline:** Use `@superpowers:test-driven-development` for every behavior change, `@design-taste-frontend` for the My Words UI, and `@superpowers:verification-before-completion` before claiming completion.

---

## File Map

### Backend files to create

- `backend/app/identity.py` — parse the anonymous vocabulary credential into `LearnerIdentity`.
- `backend/app/lexicon/normalize.py` — immutable runtime `normalize_vocab_v1`.
- `backend/app/vocab/__init__.py` — vocabulary package marker.
- `backend/app/vocab/cursor.py` — encode, validate, and decode bound pagination cursors.
- `backend/app/vocab/service.py` — ownership-scoped vocabulary queries and mutations.
- `backend/app/routers/vocab.py` — vocabulary HTTP endpoints and response mapping.
- `backend/app/errors.py` — generic database-failure to HTTP 503 mapping.
- `backend/alembic_schema_0001.py` — frozen pre-vocabulary metadata snapshot with no import-name collision.
- `backend/alembic/versions/0002_vocabulary_book.py` — deterministic vocabulary migration.
- `backend/tests/conftest.py` — isolated SQLite sessions and FastAPI dependency overrides.
- `backend/tests/test_vocab_normalize.py` — normalization and input-boundary tests.
- `backend/tests/test_vocab_migrations.py` — frozen baseline and upgrade tests.
- `backend/tests/fixtures/schema_0001_signature.json` — canonical pre-change tables, constraints, indexes, foreign keys, and dialect type signatures.
- `backend/tests/test_vocab_identity.py` — vocabulary identity-header tests.
- `backend/tests/test_vocab_cursor.py` — cursor binding and malformed-cursor tests.
- `backend/tests/test_vocab_api.py` — list, keys, save, edit, delete, and isolation tests.
- `backend/tests/test_lookup_vocab_keys.py` — normalized lookup-key response tests.
- `backend/tests/test_vocab_release.py` — release target validation, manifest, backup, and cleanup tests.

### Backend files to modify

- `backend/app/models.py` — normalized fields, timestamps, and partial unique indexes.
- `backend/app/schemas.py` — vocabulary request/response contracts and lookup keys.
- `backend/app/routers/lexicon.py` — remove vocabulary routes; attach normalized lookup keys.
- `backend/app/main.py` — include the dedicated vocabulary router.
- `backend/alembic/versions/0001_initial_schema.py` — import frozen metadata.
- `backend/alembic/env.py` — support an injected test connection without reading configured development credentials.
- `backend/pyproject.toml` — keep pytest tooling explicit if test imports expose a missing dev dependency.
- `README.md` — document My Words APIs and the temporary anonymous identity contract.

### Frontend files to create

- `frontend/src/identity/IdentityContext.tsx` — generate/store `learner_<uuid>` and expose legacy key plus vocabulary headers.
- `frontend/src/vocab/VocabContext.tsx` — saved-key cache and vocabulary mutations.
- `frontend/src/pages/VocabularyPage.tsx` — list/search/sort/edit/delete page.
- `frontend/src/components/vocab/VocabularyToolbar.tsx` — language, search, and sort controls.
- `frontend/src/components/vocab/VocabularyRow.tsx` — one editable/deleteable vocabulary item.
- `frontend/src/components/vocab/VocabularyEmpty.tsx` — empty-state guidance.
- `scripts/vocabulary-qa.ps1` — bounded visual-QA server lifecycle, seed, and cleanup watchdog.
- `scripts/vocabulary-release.ps1` — credential-safe `.env` loading and target-validated release actions.
- `backend/scripts/vocab_release.py` — preflight, restricted backup, migration, verification, and smoke logic.
- `frontend/playwright.config.ts` — bounded Chromium visual-QA configuration.
- `frontend/e2e/vocabulary.visual.spec.ts` — desktop/mobile assertions and screenshots.
- `frontend/src/test/setup.ts` — DOM test setup.
- `frontend/src/identity/IdentityContext.test.tsx` — new and legacy identity behavior.
- `frontend/src/api.test.ts` — identity header/body/query placement and 204 handling.
- `frontend/src/vocab/VocabContext.test.tsx` — saved-state synchronization.
- `frontend/src/pages/VocabularyPage.test.tsx` — page behavior.
- `frontend/src/components/Lookup.test.tsx` — existing-save and visible-failure behavior.
- `frontend/vitest.config.ts` — jsdom test environment.

### Frontend files to modify

- `frontend/package.json` and `frontend/package-lock.json` — Vitest/Testing Library dependencies and scripts.
- `frontend/src/main.tsx` — install identity and vocabulary providers.
- `frontend/src/App.tsx` — use identity context and render the global vocabulary route.
- `frontend/src/router.ts` — document and recognize `#/vocabulary`.
- `frontend/src/api.ts` — header-aware requests and vocabulary API methods.
- `frontend/src/types.ts` — vocabulary pages, source, saved-key, and lookup normalized-key types.
- `frontend/src/components/Lookup.tsx` — consume vocabulary context instead of saving directly.
- `frontend/src/pages/ListeningPage.tsx` — stop threading vocabulary identity through lookup props while retaining the legacy attempt key.
- `frontend/src/pages/ReadingPage.tsx` — stop threading vocabulary identity through lookup props.
- `frontend/src/components/UnitDrill.tsx` — stop passing `learnerKey` to `LookupProvider`; retain it for attempts/progress.
- `frontend/src/index.css` — global My Words navigation and responsive page styling.

---

## Chunk 1: Data Foundation

### Task 1: Add backend test isolation and vocabulary normalization

**Files:**
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_vocab_normalize.py`
- Create: `backend/app/lexicon/normalize.py`

- [ ] **Step 1: Write the normalization tests**

Cover the approved vectors and boundaries:

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Écouter ", "écouter"),
        ("L’EAU", "l'eau"),
        ("lʼeau", "l'eau"),
        ("mise   en œuvre", "mise en œuvre"),
        ("côte", "côte"),
        ("cote", "cote"),
    ],
)
def test_normalize_vocab_v1(raw: str, expected: str) -> None:
    assert normalize_vocab_v1(raw) == expected


def test_accents_remain_distinct() -> None:
    assert normalize_vocab_v1("côte") != normalize_vocab_v1("cote")
```

Add a `db_session` fixture that creates a fresh SQLite database per test from
`Base.metadata`, enables foreign keys, and closes/disposes it after the test. Do not point
tests at `.env` or Supabase.

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_normalize.py -q
```

Expected: collection fails because `app.lexicon.normalize` does not exist.

- [ ] **Step 3: Implement the frozen v1 runtime function**

Use one explicit translation map and one function:

```python
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
    normalized = unicodedata.normalize("NFKC", value).translate(_APOSTROPHES)
    return " ".join(normalized.split()).casefold()
```

Do not add accent stripping or language-specific branches.

- [ ] **Step 4: Run the test and verify pass**

Run the command from Step 2.

Expected: all normalization tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/lexicon/normalize.py backend/tests/conftest.py backend/tests/test_vocab_normalize.py
git commit -m "test: define vocabulary normalization contract"
```

### Task 2: Freeze revision 0001 before changing the ORM

**Files:**
- Create: `backend/alembic_schema_0001.py`
- Create: `backend/tests/fixtures/schema_0001_signature.json`
- Create: `backend/tests/test_vocab_migrations.py`
- Modify: `backend/alembic/versions/0001_initial_schema.py`
- Modify: `backend/alembic/env.py`

- [ ] **Step 1: Write a failing frozen-baseline test**

Before editing models, generate and review a committed logical schema signature from the
current `Base.metadata`. The signature records every table/column/nullability/primary key,
named unique constraint, index, foreign key/on-delete action, plus each column type compiled
with both SQLite and PostgreSQL dialects.

The test creates a temporary file-backed SQLite engine and injects an open connection through
`alembic_config.attributes["connection"]`. It upgrades only to `0001_initial`, inspects the
actual schema, and compares its complete SQLite signature with the committed fixture. It
also compares the frozen metadata's SQLite and PostgreSQL logical signatures with the same
fixture. Include explicit safety assertions:

```python
assert "vocab_items" in inspector.get_table_names()
columns = {c["name"] for c in inspector.get_columns("vocab_items")}
assert "headword" in columns
assert "normalized_headword" not in columns
assert "normalized_gloss" not in columns
assert "updated_at" not in columns
```

Also assert that `0001_initial_schema.py` no longer imports `app.models`. No migration test
may read `.env`, instantiate the application engine, or touch configured development data.

- [ ] **Step 2: Run the migration test and verify failure**

Run:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_migrations.py::test_revision_0001_is_frozen -q
```

Expected: FAIL because revision 0001 still imports live `Base.metadata` and `env.py` ignores
the injected connection.

- [ ] **Step 3: Create the Alembic-owned metadata snapshot**

Copy the current pre-change table definitions into `backend/alembic_schema_0001.py` using a
standalone `MetaData`. Include every table, foreign key, named unique constraint, index, and
the JSON/JSONB dialect variant that current revision 0001 creates. Export only:

```python
metadata = MetaData()
```

Keep this module independent of `app.models`; it is historical infrastructure and must
never receive later application fields. The backend root is already placed on `sys.path` by
the Alembic environment, so this distinct module name cannot resolve to the installed
`alembic` package.

- [ ] **Step 4: Point revision 0001 at the snapshot**

Change:

```python
from app.models import Base
```

to:

```python
from alembic_schema_0001 import metadata
```

and use `metadata.create_all(...)` / `metadata.drop_all(...)`.

- [ ] **Step 5: Support an injected migration-test connection**

In `backend/alembic/env.py`, check `config.attributes.get("connection")`. When present, call
`context.configure(connection=injected_connection, ...)` and run migrations on it; do not
create an engine and do not resolve settings. Preserve the existing settings-driven path
when no connection is injected.

- [ ] **Step 6: Verify the frozen baseline**

Run:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_migrations.py::test_revision_0001_is_frozen -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add backend/alembic_schema_0001.py backend/alembic/env.py backend/alembic/versions/0001_initial_schema.py backend/tests/fixtures/schema_0001_signature.json backend/tests/test_vocab_migrations.py
git commit -m "fix: freeze initial database migration"
```

### Task 3: Add vocabulary fields, indexes, and revision 0002

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/0002_vocabulary_book.py`
- Modify: `backend/tests/test_vocab_migrations.py`
- Modify: `backend/tests/test_vocab_normalize.py`

- [ ] **Step 1: Add failing model/index assertions**

Assert `VocabItem` exposes non-null `normalized_headword`, non-null
`normalized_gloss`, and non-null `updated_at`. Compile its indexes for SQLite and
PostgreSQL and assert:

- anonymous and authenticated normalized-word partial uniqueness predicates
- `ix_vocab_anon_recent` covering `(learner_key, created_at, id)` for anonymous rows
- `ix_vocab_user_recent` covering `(user_id, created_at, id)` for authenticated rows

- [ ] **Step 2: Add failing migration-upgrade tests**

Create a pre-change database at revision 0001, insert:

- two case/apostrophe-equivalent anonymous vocabulary rows with complementary content
- one unrelated vocabulary row
- an attempt row that must remain untouched

Upgrade to `head`, then assert:

- collision rows merge deterministically
- the survivor has the newest SRS tuple and filled content
- normalized fields and `updated_at` are non-null
- the unrelated word and attempt remain
- both unique indexes exist
- both recent-list indexes exist

Add a second test that upgrades an empty database from no revisions through `head`.
Import revision 0002 with `importlib` and assert its frozen `_normalize_vocab_v1` returns the
same values as `app.lexicon.normalize.normalize_vocab_v1` for every approved shared vector.
Add a downgrade test that inserts a row with non-null `user_id` and asserts downgrade aborts
without schema or data changes.

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_migrations.py backend/tests/test_vocab_normalize.py -q
```

Expected: failures for missing fields, indexes, and revision 0002.

- [ ] **Step 4: Modify `VocabItem`**

Replace `uq_vocab_learner_word` with two named partial unique indexes using both
`postgresql_where` and `sqlite_where`. Their exact names are
`uq_vocab_anon_word` and `uq_vocab_user_word`, matching later race detection. Add the two
partial recent-list indexes named above. Add:

```python
normalized_headword: Mapped[str] = mapped_column(String(128))
normalized_gloss: Mapped[str] = mapped_column(Text, default="")
updated_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), default=utcnow, onupdate=utcnow
)
```

Keep `created_at` and all SRS fields unchanged.

- [ ] **Step 5: Implement revision 0002**

Use a migration-local `_normalize_vocab_v1` copy. Follow the approved six-stage migration:
nullable columns, frozen backfill, `updated_at` backfill, deterministic collision merge,
SQLite batch alteration/PostgreSQL alteration, old-constraint removal, and matching partial
indexes. Do not import the runtime normalizer.

The downgrade first queries for any row with non-null `user_id`. If one exists, raise a
clear `RuntimeError` before any DDL because the old schema cannot represent authenticated
ownership. If none exists, drop the four indexes/new fields and restore
`uq_vocab_learner_word`. Do not merge or delete rows during downgrade.

- [ ] **Step 6: Run focused migration tests**

Run the Step 3 command.

Expected: PASS on SQLite; PostgreSQL DDL compilation assertions also pass.

- [ ] **Step 7: Verify both dialect schema signatures**

Run the migration tests plus a focused signature assertion:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_migrations.py -q
```

Expected: fresh and populated SQLite upgrades pass; frozen/runtime normalizers agree; the
SQLite and PostgreSQL compiled signatures include all four indexes; unsafe downgrade is
refused without data loss. Do not use Alembic offline SQL for this data-dependent migration.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/models.py backend/alembic/versions/0002_vocabulary_book.py backend/tests/test_vocab_migrations.py backend/tests/test_vocab_normalize.py
git commit -m "feat: add vocabulary ownership schema"
```

---

## Chunk 2: Backend Identity and Vocabulary API

### Task 4: Add the vocabulary-only learner identity dependency

**Files:**
- Create: `backend/app/identity.py`
- Create: `backend/tests/test_vocab_identity.py`

- [ ] **Step 1: Write dependency tests**

Test:

- `X-Learner-Key: learner_abc123` is accepted for legacy compatibility
- `learner_<uuid>` is accepted
- missing, overlong, malformed, or unprefixed keys return `401`
- any `Authorization` header returns `401` even when the anonymous header is also present
- error bodies never echo either credential

- [ ] **Step 2: Run tests and verify failure**

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_identity.py -q
```

Expected: FAIL because `LearnerIdentity` and dependency do not exist.

- [ ] **Step 3: Implement the dependency**

Use a frozen dataclass and the approved regex:

```python
@dataclass(frozen=True)
class LearnerIdentity:
    learner_key: str
    user_id: str | None = None


_ANON_KEY = re.compile(r"^learner_[A-Za-z0-9-]{1,48}$")
```

The FastAPI dependency reads `X-Learner-Key` and `Authorization`. It rejects any
authorization value during the MVP and never logs credentials.

- [ ] **Step 4: Run tests and verify pass**

Run the Step 2 command.

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/identity.py backend/tests/test_vocab_identity.py
git commit -m "feat: add vocabulary learner identity"
```

### Task 5: Define vocabulary schemas and bound cursors

**Files:**
- Modify: `backend/app/schemas.py`
- Create: `backend/app/vocab/__init__.py`
- Create: `backend/app/vocab/cursor.py`
- Create: `backend/tests/test_vocab_cursor.py`
- Create: `backend/tests/test_vocab_schemas.py`

- [ ] **Step 1: Write cursor tests**

Test recent and alphabetical round trips, version rejection, malformed base64/JSON, and
request-binding mismatches for `sort`, `language`, and normalized `q`.

- [ ] **Step 2: Run cursor tests and verify failure**

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_cursor.py -q
```

Expected: FAIL because cursor helpers do not exist.

- [ ] **Step 3: Implement cursor values**

Use a versioned Pydantic/dataclass payload serialized as compact JSON then base64url without
padding. The payload contains:

```python
{
    "v": 1,
    "sort": "recent",
    "language": "fr",
    "q": "écouter",
    "last_created_at": "...",
    "last_headword": None,
    "last_id": 42,
}
```

Alphabetical cursors use `last_headword`; recent cursors use `last_created_at`. Decode with
strict validation and raise a domain `InvalidCursor` mapped later to `400`.

- [ ] **Step 4: Write and run strict schema tests**

Assert:

- `VocabSaveIn` rejects extra `learner_key` and `user_id`
- raw headword/gloss/example limits are exactly 128/1,000/2,000 code points
- `VocabEditIn` rejects an empty object and extra fields
- list, saved-keys, source-null, and full item response shapes validate exactly

Set `model_config = ConfigDict(extra="forbid")` on vocabulary input models. Run:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_schemas.py -q
```

Expected before implementation: FAIL for missing/lenient schemas.

- [ ] **Step 5: Replace vocabulary schemas**

Remove `learner_key` from `VocabSaveIn`. Add:

- `VocabSaveIn` with raw field limits
- `VocabEditIn` with at least one of `gloss_en`/`example`
- `VocabSourceOut`
- full `VocabItemOut`
- `VocabListOut`
- `VocabSavedKeyOut` and `VocabSavedKeysOut`

Add `normalized_headword` to `WordGlossOut` and `ExpressionOut`.

- [ ] **Step 6: Run cursor and schema tests**

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_cursor.py backend/tests/test_vocab_schemas.py -q
uv run --project backend python -c "from app.schemas import VocabListOut; print(VocabListOut.__name__)"
```

Expected: tests pass and command prints `VocabListOut`.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/schemas.py backend/app/vocab backend/tests/test_vocab_cursor.py backend/tests/test_vocab_schemas.py
git commit -m "feat: define vocabulary API contracts"
```

### Task 6: Implement ownership-scoped vocabulary reads

**Files:**
- Create: `backend/app/vocab/service.py`
- Create: `backend/app/routers/vocab.py`
- Create: `backend/app/errors.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/lexicon.py`
- Create: `backend/tests/test_vocab_api.py`

- [ ] **Step 1: Write failing list and saved-key API tests**

Using an overridden test DB, create two anonymous learners, multiple languages, duplicate
timestamps, source lessons/units, and mixed glosses. Assert:

- missing identity is `401`
- an anonymous identity selects only rows with matching `learner_key` and `user_id IS NULL`
- a direct service test with an authenticated `LearnerIdentity` selects by `user_id` and
  never by `learner_key` (the HTTP dependency does not emit this identity during MVP)
- filters and literal `%`, `_`, `\` search work
- search matches stored normalized headword and gloss identically on SQLite
- `total` is filtered count
- recent pagination traverses duplicate `created_at` values with
  `(created_at DESC, id DESC)` and produces no skips/duplicates
- alphabetical pagination traverses duplicate normalized sort values with
  `(normalized_headword ASC, id ASC)` and produces no skips/duplicates
- `limit`, non-null intermediate `next_cursor`, and final `next_cursor: null` are exact
- cursor mismatch is `400`
- source response contains lesson/unit IDs, title, and unit index
- a source-less item remains in results with `source: null`
- `/api/vocab/saved-keys` returns only ID/key pairs
- saved-key language is required/validated
- invalid list language/sort/limit and raw `q` over 128 code points return the specified
  `400`/`422` response

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_api.py -k "list or saved_keys" -q
```

Expected: list assertions fail against the legacy unprotected list shape/behavior, while
`/api/vocab/saved-keys` returns `404`.

- [ ] **Step 3: Implement read service boundaries**

In `service.py`, keep focused functions:

```python
def owner_clause(identity: LearnerIdentity): ...
def list_vocab(db, identity, filters) -> VocabPage: ...
def list_saved_keys(db, identity, language) -> list[VocabKey]: ...
def to_vocab_out(row) -> VocabItemOut: ...
```

Use the ownership predicate in the SQL statement itself. Normalize search once with
`normalize_vocab_v1`, escape SQL wildcard characters, and apply keyset predicates matching
the selected deterministic ordering.

- [ ] **Step 4: Verify cross-dialect search construction**

Factor wildcard escaping into a pure helper and test its exact output. Compile the list
statement with SQLite and PostgreSQL dialects and assert both use the two stored normalized
columns, bound parameters, and an explicit escape character; neither may call
database-specific `LOWER`.

Add an engine-parametrized integration test that always runs on SQLite and runs the same
search cases on PostgreSQL when `TEST_POSTGRES_URL` is set. This test may be skipped locally,
but Task 15 requires a non-skipped run against an isolated PostgreSQL test database or
Supabase branch before production migration.

The PostgreSQL fixture generates a schema named `vocab_test_<32 lowercase hex characters>`,
validates that exact pattern before every create/drop, sets that schema as the connection
search path, runs migrations into it, and drops only that schema in `finally`. It never
creates test tables in `public`.

Run the local compiler/helper coverage explicitly:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_api.py -k "search_escape or search_statement or search_results" -q
```

Expected: helper, SQLite results, and both dialect-compilation cases pass; only the live
PostgreSQL execution case may be skipped before Task 15.

- [ ] **Step 5: Remove the legacy GET and add static routes before dynamic routes**

Remove only the legacy `GET /api/vocab` from `lexicon.py` in this task so it cannot shadow
the new route. Leave the legacy POST until Task 7 replaces it. Register:

```text
GET /api/vocab
GET /api/vocab/saved-keys
```

before any `/{item_id}` route. Include `vocab.router` in `main.py`.

- [ ] **Step 6: Map database failures without leaking details**

Add one FastAPI exception handler for SQLAlchemy operational/database errors. It logs only
the exception class, a generated correlation ID, and request path. Do not log `str(exc)`,
`exc.args`, SQL parameters, a traceback, or the connection URL. It returns only:

```json
{"detail": "database temporarily unavailable"}
```

with `503`. Tests monkeypatch the list service to raise an error containing a fake
connection URL/password, capture both response and logs, and assert neither secret appears
in either channel.

- [ ] **Step 7: Run focused tests and verify pass**

Run the full API test file so compiler/helper cases are not deselected:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/vocab/service.py backend/app/routers/vocab.py backend/app/errors.py backend/app/main.py backend/app/routers/lexicon.py backend/tests/test_vocab_api.py
git commit -m "feat: add vocabulary list API"
```

### Task 7: Implement idempotent save, edit, and permanent delete

**Files:**
- Modify: `backend/app/vocab/service.py`
- Modify: `backend/app/routers/vocab.py`
- Modify: `backend/app/routers/lexicon.py`
- Modify: `backend/tests/test_vocab_api.py`

- [ ] **Step 1: Write failing mutation tests**

Cover:

- raw and normalized headword limits
- whitespace-only normalized headword
- unit exists and lesson language matches
- missing/mismatched unit returns `422`
- new save returns `200`
- repeat save fills only empty gloss/example/source
- repeat save never resets SRS or display headword
- duplicate-race recovery returns the winning row
- PATCH clears with `null`/empty string and recomputes `normalized_gloss`
- instrumented statement tests prove PATCH and DELETE include item ID and the exact
  ownership predicate in the `UPDATE`/`DELETE`; a check-then-unscoped mutation is forbidden
- zero affected rows and foreign IDs return indistinguishable `404`
- DELETE returns `204` and is permanent
- unrelated foreign-key/operational `IntegrityError` values are re-raised and become generic
  `503`, never idempotent success

- [ ] **Step 2: Run mutation tests and verify failure**

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_api.py -k "save or edit or delete" -q
```

Expected: FAIL because mutations are not implemented.

- [ ] **Step 3: Implement mutation services**

Add focused functions:

```python
def save_vocab(db, identity, payload) -> VocabItemOut: ...
def edit_vocab(db, identity, item_id, payload) -> VocabItemOut: ...
def delete_vocab(db, identity, item_id) -> None: ...
```

Validate unit/language before writing. PATCH uses an ownership-scoped `UPDATE` and DELETE
uses an ownership-scoped `DELETE`; determine `404` from zero affected/returned rows.

On insert race, a helper recognizes only:

- PostgreSQL SQLSTATE `23505` whose `diag.constraint_name` is
  `uq_vocab_anon_word` or `uq_vocab_user_word`
- SQLite's unique-failure message matching the exact columns for one of those indexes

Only then roll back, reselect under the same owner/language/normalized key, and apply the
fill-only merge. Re-raise every other `IntegrityError`. Never return a foreign row.

- [ ] **Step 4: Add routes and remove legacy vocabulary routes**

Move the remaining `POST /api/vocab` out of `lexicon.py`. Add POST/PATCH/DELETE to the
dedicated router with exact `200/200/204` status behavior.

- [ ] **Step 5: Run mutation and full backend tests**

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_api.py -q
uv run --project backend --extra dev pytest backend/tests -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/vocab/service.py backend/app/routers/vocab.py backend/app/routers/lexicon.py backend/tests/test_vocab_api.py
git commit -m "feat: add vocabulary mutations"
```

### Task 8: Add normalized vocabulary keys to every lookup path

**Files:**
- Modify: `backend/app/routers/lexicon.py`
- Modify: `backend/app/schemas.py`
- Create: `backend/tests/test_lookup_vocab_keys.py`

- [ ] **Step 1: Write failing lookup-key tests**

Exercise online/cache, precomputed expression, inferred expression, and offline/fallback
results. Assert:

```python
assert body["word"]["normalized_headword"] == normalize_vocab_v1(
    body["word"]["lemma"] or body["selection"]
)
assert body["expressions"][0]["normalized_headword"] == normalize_vocab_v1(
    body["expressions"][0]["canonical"]
)
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
uv run --project backend --extra dev pytest backend/tests/test_lookup_vocab_keys.py -q
```

Expected: response validation failure or absent key.

- [ ] **Step 3: Attach normalized keys at the response boundary**

Do not change resolver ranking or cache records. After lookup resolution and before
`LookupOut` validation, derive word and expression keys from the exact save headwords.

- [ ] **Step 4: Run lookup and backend tests**

```powershell
uv run --project backend --extra dev pytest backend/tests/test_lookup_vocab_keys.py backend/tests/test_vocab_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/routers/lexicon.py backend/app/schemas.py backend/tests/test_lookup_vocab_keys.py
git commit -m "feat: expose lookup vocabulary keys"
```

---

## Chunk 3: Frontend Identity, Cache, and API

### Task 9: Install frontend test infrastructure and centralize identity

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `.gitignore`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/identity/IdentityContext.tsx`
- Create: `frontend/src/identity/IdentityContext.test.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add test dependencies**

Install:

```powershell
npm --prefix frontend install --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event
```

Add scripts:

```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 2: Configure jsdom and write failing identity tests**

Test:

- existing `learner_abc123` remains unchanged
- empty storage creates `learner_${crypto.randomUUID()}`
- the key is written once and stable across rerenders
- context exposes the raw key for legacy attempts and `{ "X-Learner-Key": key }` for vocab

- [ ] **Step 3: Run test and verify failure**

```powershell
npm --prefix frontend test -- src/identity/IdentityContext.test.tsx
```

Expected: FAIL because the provider does not exist.

- [ ] **Step 4: Implement and install `IdentityProvider`**

Move learner-key ownership out of `App.tsx`. Wrap the app in `main.tsx`. Inside `App`, use
the context key for both existing `ListeningPage` and `ReadingPage` props until Task 12
removes lookup-only threading. Listening attempts/progress keep their current payload/query
placement. Do not change those backend contracts.

- [ ] **Step 5: Run identity test and build**

```powershell
npm --prefix frontend test -- src/identity/IdentityContext.test.tsx
npm --prefix frontend run build
```

Expected: test passes and production build succeeds.

- [ ] **Step 6: Commit**

```powershell
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/src/test frontend/src/identity frontend/src/main.tsx frontend/src/App.tsx
git commit -m "feat: centralize learner identity"
```

### Task 10: Add typed vocabulary API and synchronized cache

**Files:**
- Modify: `frontend/src/api.ts`
- Create: `frontend/src/api.test.ts`
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/vocab/VocabContext.tsx`
- Create: `frontend/src/vocab/VocabContext.test.tsx`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Add types and failing provider tests**

Add the exact spec types: `VocabSource`, `VocabItem`, `VocabList`, `VocabSavedKeys`,
`VocabSaveInput`, and `VocabEditInput`. Extend lookup word/expression types with
`normalized_headword`.

Test lazy per-language key loading, exact-key saved status, save cache insertion, edit cache
stability, delete cache removal, and visible error propagation. Also require:

- two concurrent `ensureKeys(language)` calls issue one request
- a rejected in-flight request is removed and a later call retries
- save-before-first-load is retained when the server keys later merge
- delete-before-first-load is not reintroduced by the later server result
- a load replaces neither local additions nor local deletions
- idle/loading/error states return `unknown`, never false

- [ ] **Step 2: Run provider test and verify failure**

```powershell
npm --prefix frontend test -- src/vocab/VocabContext.test.tsx
```

Expected: FAIL because the provider and vocabulary API do not exist.

- [ ] **Step 3: Write failing request-boundary tests**

In `frontend/src/api.test.ts`, mock `fetch` and assert:

- vocabulary identity appears only as `X-Learner-Key`
- vocabulary JSON bodies and query strings contain no `learner_key` or `user_id`
- existing attempts still put `learner_key` in JSON
- existing progress still URL-encodes `learner_key` in the query
- vocabulary list URL-encodes Unicode and reserved characters in `q`, `language`, and the
  opaque cursor independently
- caller headers merge with `Content-Type` rather than replacing it
- DELETE `204 No Content` resolves without attempting `res.json()`

Run:

```powershell
npm --prefix frontend test -- src/api.test.ts
```

Expected: FAIL because vocabulary request helpers and 204 handling do not exist yet.

- [ ] **Step 4: Make fetch helpers header-aware without breaking Lookup**

Allow request helpers to accept a `HeadersInit` without leaking identity into JSON. Add:

```typescript
vocab.list(params, headers)
vocab.savedKeys(language, headers)
vocab.save(input, headers)
vocab.edit(id, input, headers)
vocab.remove(id, headers)
```

Keep the existing `lexicon.saveVocab` method and its legacy input temporarily so the current
`Lookup.tsx` remains type-correct in this intermediate commit. Implement it as a compatibility
adapter: destructure `learner_key`, send it only as `X-Learner-Key`, and pass the remaining
fields to the strict `vocab.save` body. It must not send identity JSON that the revised
backend rejects. Task 12 migrates Lookup and then removes the adapter.

- [ ] **Step 5: Implement `VocabProvider` with explicit state**

Consume identity headers. Cache per-language entries:

```typescript
type KeyState = {
  status: 'idle' | 'loading' | 'ready' | 'error'
  keys: Set<string>
  localAdds: Set<string>
  localDeletes: Set<string>
  error: Error | null
}
```

Keep a separate in-flight promise map and remove each promise in `finally`. Merge server
keys as `(server ∪ localAdds) − localDeletes`. Expose:

```typescript
ensureKeys(language)
savedStatus(language, normalizedHeadword) // 'saved' | 'not-saved' | 'unknown'
keyState(language)
save(input)
edit(id, input)
remove(item)
list(params)
```

Only a `ready` state (or a confirmed local add/delete) may return saved/not-saved. Keep
network errors as explicit state/returned errors; never silently convert failure to "not
saved."

- [ ] **Step 6: Install provider and run tests**

Wrap it inside `IdentityProvider` in `main.tsx`, then run:

```powershell
npm --prefix frontend test -- src/api.test.ts src/vocab/VocabContext.test.tsx
npm --prefix frontend run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/api.ts frontend/src/api.test.ts frontend/src/types.ts frontend/src/vocab frontend/src/main.tsx
git commit -m "feat: add vocabulary client state"
```

---

## Chunk 4: My Words UI and Lookup Integration

### Task 11: Add the global My Words route and page behavior

**Files:**
- Create: `frontend/src/pages/VocabularyPage.tsx`
- Create: `frontend/src/pages/VocabularyPage.test.tsx`
- Create: `frontend/src/components/vocab/VocabularyToolbar.tsx`
- Create: `frontend/src/components/vocab/VocabularyRow.tsx`
- Create: `frontend/src/components/vocab/VocabularyEmpty.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/router.ts`

- [ ] **Step 1: Use `@design-taste-frontend` and write page tests first**

Test:

- `#/vocabulary` renders My Words and does not render Listening
- global My Words navigation works with browser history
- brand title is exactly "My Words" on the utility route
- no skill tab is active while the My Words utility button is active
- changing language on My Words stays on `#/vocabulary`
- loading, empty, error/retry, and populated states
- current language default and language switching
- debounced search
- recent/alphabetical sort
- next-page loading from `next_cursor`
- changing language, debounced raw search, or sort clears old rows and the old cursor before
  committing the new first page
- a stale slower response from an earlier debounced search is ignored
- refresh failure keeps the last successful rows visible alongside retry UI
- source link points to `#/listening/lesson/{lesson_id}/unit/{unit_id}`
- inline gloss/example edit
- deletion requires confirmation and disappears only after success

- [ ] **Step 2: Run page tests and verify failure**

```powershell
npm --prefix frontend test -- src/pages/VocabularyPage.test.tsx
```

Expected: FAIL because page/route do not exist.

- [ ] **Step 3: Implement the page as focused components**

Keep `VocabularyPage.tsx` responsible for page-level request/state coordination. Put
`VocabularyToolbar`, `VocabularyRow`, and `VocabularyEmpty` in the focused
`frontend/src/components/vocab/` files listed above.

Use controlled edit drafts, preserve the loaded list during refresh failures, and do not
implement tags/decks/SRS. Use a monotonically increasing request ID or `AbortController`;
only the latest language/search/sort request may replace/reset results.

- [ ] **Step 4: Add global navigation**

Treat vocabulary as a utility route, not a `Skill`. On the route:

- brand title is "My Words"
- no skill tab is falsely active
- current language controls remain available
- the My Words button is active

- [ ] **Step 5: Run tests and build**

```powershell
npm --prefix frontend test -- src/pages/VocabularyPage.test.tsx
npm --prefix frontend run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/pages/VocabularyPage.tsx frontend/src/pages/VocabularyPage.test.tsx frontend/src/components/vocab frontend/src/App.tsx frontend/src/router.ts
git commit -m "feat: add My Words page"
```

### Task 12: Synchronize contextual lookup with My Words

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Lookup.tsx`
- Create: `frontend/src/components/Lookup.test.tsx`
- Modify: `frontend/src/pages/ListeningPage.tsx`
- Modify: `frontend/src/pages/ReadingPage.tsx`
- Modify: `frontend/src/components/UnitDrill.tsx`

- [ ] **Step 1: Write failing lookup tests**

Test:

- provider keys are loaded for the active language
- a case/apostrophe-equivalent result displays `✓ saved` from server-provided normalized key
- idle/loading saved-key state renders neutral unknown/loading UI, never "not saved"
- failed saved-key load renders a visible retry and retry success updates the save button
- saving a word/expression updates the shared cache
- save failure displays an inline retry action
- changing selection clears only transient errors, not global saved state

- [ ] **Step 2: Run lookup tests and verify failure**

```powershell
npm --prefix frontend test -- src/components/Lookup.test.tsx
```

Expected: FAIL because Lookup still owns transient save state and calls the old API.

- [ ] **Step 3: Replace local saved state with vocabulary context**

Remove `learnerKey` from `LookupProvider` and popup props. Use the exact
`normalized_headword` from lookup results for cache comparison. Save the original
lemma/selection or canonical expression as the request headword.

Expose a specific save error and retry button; do not retain the current empty `catch`.
After all callers use `VocabContext`, remove the temporary legacy `lexicon.saveVocab`
method and its `learner_key` input type from `api.ts`.

- [ ] **Step 4: Stop threading vocabulary identity through pages**

Remove only the lookup-related `learnerKey` props. Keep the key wherever attempts and
progress still need their legacy contract.

- [ ] **Step 5: Run frontend tests and build**

```powershell
npm --prefix frontend test
npm --prefix frontend run build
```

Expected: all tests and build pass.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/api.ts frontend/src/App.tsx frontend/src/components/Lookup.tsx frontend/src/components/Lookup.test.tsx frontend/src/pages/ListeningPage.tsx frontend/src/pages/ReadingPage.tsx frontend/src/components/UnitDrill.tsx
git commit -m "feat: sync lookup with vocabulary"
```

### Task 13: Add responsive styling and accessibility checks

**Files:**
- Create: `scripts/vocabulary-qa.ps1`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/vocabulary.visual.spec.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `.gitignore`
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/pages/VocabularyPage.tsx`
- Modify: `frontend/src/components/vocab/VocabularyToolbar.tsx`
- Modify: `frontend/src/components/vocab/VocabularyRow.tsx`
- Modify: `frontend/src/pages/VocabularyPage.test.tsx`

- [ ] **Step 1: Add structural accessibility assertions**

Assert labeled search/sort/language controls, edit fields, confirmation dialog semantics,
focusable source links, and an `aria-live` region for save/delete errors.

- [ ] **Step 2: Run the page test and verify failure**

```powershell
npm --prefix frontend test -- src/pages/VocabularyPage.test.tsx
```

Expected: new accessibility assertions fail.

- [ ] **Step 3: Implement semantics and responsive styles**

Add the tested labels, dialog roles, focus behavior, and `aria-live` error region in the
page/components, then use existing tokens for styling.

Add:

- a separate utility navigation treatment for My Words
- compact desktop vocabulary rows
- card layout below 700px
- visible focus states
- non-color-only error/saved indicators
- reduced-motion compliance

Do not redesign unrelated skill pages.

- [ ] **Step 4: Verify at desktop and mobile widths**

Create `scripts/vocabulary-qa.ps1`. The script creates a task-specific directory under
`[IO.Path]::GetTempPath()`, resolves it, and verifies its absolute path starts with the
resolved system temp path. It points its own process-local `DATABASE_URL` at SQLite, starts
and seeds the servers, runs a bounded Playwright visual suite inside the same `try`, and
always cleans up in `finally`. It never uses the configured Supabase database for visual
QA.

The following PowerShell blocks are the script body, not separate interactive commands:

```powershell
function Get-DescendantProcessRows([int]$rootId) {
  $all = @(Get-CimInstance Win32_Process)
  $queue = @(@{ Id=$rootId; Depth=0 })
  $rows = @()
  while ($queue.Count) {
    $current = $queue[0]
    $queue = @($queue | Select-Object -Skip 1)
    $children = @($all | Where-Object { $_.ParentProcessId -eq $current.Id })
    foreach ($child in $children) {
      $row = [pscustomobject]@{ Id=[int]$child.ProcessId; Depth=[int]$current.Depth + 1 }
      $rows += $row
      $queue += @{ Id=$row.Id; Depth=$row.Depth }
    }
  }
  return $rows
}
$backendProc = $null
$frontendProc = $null
$backendTree = @()
$frontendTree = @()
$qaRoot = $null
$priorDatabaseUrlPresent = Test-Path Env:DATABASE_URL
$priorDatabaseUrl = $env:DATABASE_URL
try {
  $occupied = Get-NetTCPConnection -State Listen -LocalPort 8000,5173 -ErrorAction SilentlyContinue
  if ($occupied) { throw "QA ports 8000/5173 already have listeners" }

  $qaRoot = Join-Path ([IO.Path]::GetTempPath()) ("french-vocab-qa-" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Path $qaRoot | Out-Null
  $qaResolved = (Resolve-Path -LiteralPath $qaRoot).Path
  $tempResolved = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
  if (-not $qaResolved.StartsWith($tempResolved, [StringComparison]::OrdinalIgnoreCase)) { throw "QA path escaped temp root" }
  $qaDbPath = Join-Path $qaResolved 'vocabulary-qa.sqlite'
  $env:DATABASE_URL = "sqlite:///$($qaDbPath.Replace('\','/'))"
  uv run --project backend alembic -c backend/alembic.ini upgrade head
  if ($LASTEXITCODE -ne 0) { throw "temporary Alembic migration failed with exit code $LASTEXITCODE" }
  $backendProc = Start-Process -FilePath 'uv.exe' -ArgumentList 'run','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory (Join-Path (Get-Location) 'backend') -WindowStyle Hidden -PassThru
  $frontendProc = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev','--','--host','127.0.0.1' -WorkingDirectory (Join-Path (Get-Location) 'frontend') -WindowStyle Hidden -PassThru
```

All health, seed, ancestry, and Playwright commands below remain inside this open `try`
block.

Poll health, seed three rows, and verify the seed:

```powershell
$healthy = $false
for ($i = 0; $i -lt 40; $i++) {
  try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -Method Get
    if ($health.status -eq 'ok') { $healthy = $true; break }
  } catch {}
  Start-Sleep -Milliseconds 250
}
if (-not $healthy) { throw "temporary backend did not become healthy" }

$headers = @{ 'X-Learner-Key' = 'learner_qa-vocabulary-20260730'; 'Content-Type' = 'application/json' }
$seed = @(
  @{ language='fr'; headword='écouter'; gloss_en='to listen carefully'; example="J'écoute la radio chaque matin."; unit_id=$null },
  @{ language='fr'; headword='mettre de côté'; gloss_en='to set aside for later, especially money or time'; example="Elle met un peu d'argent de côté chaque mois pour préparer un long voyage."; unit_id=$null },
  @{ language='fr'; headword='pourtant'; gloss_en=$null; example=$null; unit_id=$null }
)
foreach ($item in $seed) {
  $saved = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/vocab' -Method Post -Headers $headers -Body ($item | ConvertTo-Json)
  if (-not $saved.id) { throw "QA vocabulary seed failed" }
}
$listed = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/vocab?language=fr&limit=50' -Method Get -Headers $headers
if ($listed.total -ne 3) { throw "expected exactly three QA vocabulary rows" }
```

Expected: health is `ok`; each POST returns an ID; list total is exactly 3.

Verify the listeners belong to the retained wrapper ancestry trees:

```powershell
$backendTree = @([pscustomobject]@{Id=$backendProc.Id;Depth=0}) + @(Get-DescendantProcessRows $backendProc.Id)
$frontendTree = @([pscustomobject]@{Id=$frontendProc.Id;Depth=0}) + @(Get-DescendantProcessRows $frontendProc.Id)
$allowedIds = @($backendTree.Id) + @($frontendTree.Id)
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8000,5173)
if ($listeners.Count -ne 2) { throw "expected one listener on each QA port" }
foreach ($listener in $listeners) {
  if ($listener.OwningProcess -notin $allowedIds) { throw "QA port belongs to an unrelated process" }
}
```

Expected: exactly one listener on each port and both owning PIDs descend from the retained
wrappers.

Create a Playwright config with a 30-second test timeout, one Chromium project, no automatic
web server, and `baseURL: "http://127.0.0.1:5173"`. The visual test:

- installs `learner_qa-vocabulary-20260730` in localStorage with `page.addInitScript` before
  the app loads, matching the API seed owner
- opens `/#/vocabulary`
- verifies exactly three rows
- asserts `document.documentElement.scrollWidth <= window.innerWidth`
- edits one gloss and verifies the saved value
- captures `test-results/vocabulary-desktop.png` at 1280×800
- repeats layout/overflow/card assertions and captures
  `test-results/vocabulary-mobile.png` at 390×844

Run it from inside the open `try`:

```powershell
npm --prefix frontend run test:visual
if ($LASTEXITCODE -ne 0) { throw "Playwright visual QA failed with exit code $LASTEXITCODE" }
```

Install Chromium once during Task 13 with:

```powershell
npm --prefix frontend install --save-dev @playwright/test
npm --prefix frontend exec playwright install chromium
```

Add `"test:visual": "playwright test e2e/vocabulary.visual.spec.ts"` to package scripts.
Add `frontend/test-results/` and `frontend/playwright-report/` to `.gitignore`; screenshots
remain local QA evidence and do not create unexplained untracked files.
After Playwright evidence is captured, close the `try` and run cleanup in `finally`:

```powershell
} finally {
  $cleanupErrors = @()
  $trees = @()
  if ($backendTree.Count) {
    $trees += $backendTree
  } elseif ($backendProc) {
    $trees += @([pscustomobject]@{Id=$backendProc.Id;Depth=0}) + @(Get-DescendantProcessRows $backendProc.Id)
  }
  if ($frontendTree.Count) {
    $trees += $frontendTree
  } elseif ($frontendProc) {
    $trees += @([pscustomobject]@{Id=$frontendProc.Id;Depth=0}) + @(Get-DescendantProcessRows $frontendProc.Id)
  }
  $treeIds = @($trees.Id | Select-Object -Unique)
  $trees | Sort-Object Depth -Descending | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
  }
  for ($i = 0; $i -lt 40; $i++) {
    $alive = @($treeIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if (-not $alive) { break }
    Start-Sleep -Milliseconds 250
  }
  $alive = @($treeIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
  if ($alive) { $cleanupErrors += "QA process tree did not terminate" }
  $remainingListeners = @(Get-NetTCPConnection -State Listen -LocalPort 8000,5173 -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -in $treeIds })
  if ($remainingListeners) { $cleanupErrors += "terminated QA tree still owns a listener" }
  if ($priorDatabaseUrlPresent) { $env:DATABASE_URL = $priorDatabaseUrl } else { Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue }
  if ($qaRoot -and (Test-Path -LiteralPath $qaRoot)) {
    $cleanupResolved = (Resolve-Path -LiteralPath $qaRoot).Path
    $cleanupTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $cleanupResolved.StartsWith($cleanupTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
      $cleanupErrors += "refusing unsafe QA cleanup"
    } else {
      Remove-Item -LiteralPath $cleanupResolved -Recurse -Force
    }
  }
  if ($cleanupErrors) { throw ($cleanupErrors -join "; ") }
}
```

Expected: both ports have no listeners from the launched trees, the prior
`DATABASE_URL` state is restored exactly, and only the validated QA directory is removed.

Run the complete bounded QA script directly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/vocabulary-qa.ps1
```

Expected: exit 0, both screenshots exist under `frontend/test-results`, both QA ports are
released, and the temporary SQLite directory is gone.

- [ ] **Step 5: Run tests, lint, and build**

```powershell
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```powershell
git add .gitignore scripts/vocabulary-qa.ps1 frontend/playwright.config.ts frontend/e2e/vocabulary.visual.spec.ts frontend/package.json frontend/package-lock.json frontend/src/index.css frontend/src/pages/VocabularyPage.tsx frontend/src/components/vocab/VocabularyToolbar.tsx frontend/src/components/vocab/VocabularyRow.tsx frontend/src/pages/VocabularyPage.test.tsx
git commit -m "style: finish responsive vocabulary UI"
```

---

## Chunk 5: Documentation, Verification, and Supabase Checkpoint

### Task 14: Update documentation and run full local verification

**Files:**
- Modify: `README.md`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_safe_database_logging.py`
- Modify: `docs/superpowers/specs/2026-07-30-vocabulary-book-design.md` only if implementation exposes an approved-spec discrepancy

- [ ] **Step 1: Update README**

Document:

- `#/vocabulary`
- all vocabulary endpoints
- `X-Learner-Key`
- attempts/progress still use their legacy learner-key contracts
- bearer tokens are intentionally rejected by vocabulary endpoints until the auth phase
- production migration command and rollback warning

- [ ] **Step 2: Add and verify credential-safe startup logging**

Write a failing test with a URL containing a fake username/password and assert the startup
database label contains only the hostname/database name, never user info, password, query,
or full URL. Replace `settings.resolved_database_url()` in the startup log with that tested
sanitizer.

Run:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_safe_database_logging.py -q
```

Expected: FAIL before the sanitizer; PASS after it.

- [ ] **Step 3: Run backend verification**

```powershell
uv run --project backend --extra dev pytest backend/tests -q
uv run --project backend ruff check backend/app backend/tests backend/alembic backend/alembic_schema_0001.py
```

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 4: Run frontend verification**

```powershell
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: all commands exit 0.

- [ ] **Step 5: Verify a clean local migration from scratch**

Run the isolated test that injects its own temporary SQLite connection:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_migrations.py::test_fresh_database_upgrades_to_vocabulary_head -q
```

Expected: PASS and no configured `.env` database is opened.

- [ ] **Step 6: Run the local API lifecycle smoke test**

Run the TestClient test whose fixture owns and disposes its temporary SQLite engine:

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_api.py::test_vocabulary_lifecycle_smoke -q
```

The test asserts `200, 200, 200, 200, 204`, foreign access `404`, and zero surviving
disposable rows.

- [ ] **Step 7: Commit documentation and any approved spec correction**

```powershell
git add README.md backend/app/main.py backend/tests/test_safe_database_logging.py docs/superpowers/specs/2026-07-30-vocabulary-book-design.md
git commit -m "docs: document vocabulary book"
```

### Task 15: Inspect and migrate Supabase only after an explicit checkpoint

**Files:**
- Create: `scripts/vocabulary-release.ps1`
- Create: `backend/scripts/vocab_release.py`
- Create: `backend/tests/test_vocab_release.py`
- Modify: `.gitignore`

- [ ] **Step 1: Implement and test the release tooling locally**

`scripts/vocabulary-release.ps1` accepts:

```text
-Action Validate|Preflight|PostgresTest|Backup|Migrate|Verify|Smoke|Restore
-ExpectedApiHost <exact project API hostname>
-ExpectedDbHost <exact database/pooler hostname>
-ExpectedProjectRef <exact Supabase project reference>
-ExpectedBackupSchema <exact user-confirmed schema; required only for Restore>
-EnvFile <path, defaults to root .env>
```

For every invocation it:

1. Parses root `.env` without printing values.
2. Replaces stale process values with the root file's `DATABASE_URL`, `SUPABASE_URL`, and
   server key for the child process only.
3. Parses both URLs with platform URI libraries.
4. Requires the API host to equal `-ExpectedApiHost`, the database URL host to equal
   `-ExpectedDbHost`, and the API host/database URL user info to match
   `-ExpectedProjectRef`.
5. Calls `backend/scripts/vocab_release.py` with the selected action.
6. Fails when the child exit code is non-zero.
7. Restores the caller's prior environment in `finally`.

`backend/scripts/vocab_release.py` uses psycopg/FastAPI TestClient and never prints a full
URL, username, password, service key, SQL parameters, or row contents. Unit-test target
validation, manifest hashing, skip-as-failure behavior, restore refusal/transactionality,
and smoke cleanup against temporary SQLite/mocked connections before any Supabase action.

`Validate` performs only credential-free parsing/host/project-ref checks and never opens a
network connection. `backend/tests/test_vocab_release.py` invokes the PowerShell wrapper
with a temporary fake `-EnvFile` and fake secrets, then asserts correct success/failure and
that stdout/stderr contain none of those secrets.

Add `.release/` to `.gitignore`; it stores only a non-secret release manifest containing
expected host, database name, backup schema name, revision, row count, and SHA-256 checksum.

- [ ] **Step 2: Run release-tool tests and commit**

```powershell
uv run --project backend --extra dev pytest backend/tests/test_vocab_release.py::test_powershell_wrapper_validate_is_secret_safe -q
uv run --project backend --extra dev pytest backend/tests/test_vocab_release.py -q
uv run --project backend ruff check backend/scripts/vocab_release.py
```

Expected: PASS.

```powershell
git add .gitignore scripts/vocabulary-release.ps1 backend/scripts/vocab_release.py backend/tests/test_vocab_release.py
git commit -m "ops: add guarded vocabulary release tooling"
```

- [ ] **Step 3: Run read-only preflight, then stop for confirmation**

Use the already verified project host:

```powershell
.\scripts\vocabulary-release.ps1 -Action Preflight -ExpectedApiHost 'euxiucesdvibhwlkqzct.supabase.co' -ExpectedDbHost 'aws-0-ca-central-1.pooler.supabase.com' -ExpectedProjectRef 'euxiucesdvibhwlkqzct'
if ($LASTEXITCODE -ne 0) { throw "Supabase vocabulary preflight failed" }
```

Preflight opens a read-only transaction and verifies:

- URL host and server-reported database identity match the expected target
- Alembic revision is exactly `0001_initial`
- `vocab_items` has the pre-change columns/constraint
- new columns/indexes do not already exist
- current vocabulary/attempt row counts
- normalization-v1 collision count and expected survivor count

Any non-zero normalization collision count stops preflight and blocks migration for a
separate data review; the normal release path therefore requires exact vocabulary row-count
preservation.

Output contains only sanitized host, database name, revision, and counts. Report that output
and local verification results to the user. Do not run any later action until the user
explicitly confirms this production-write checkpoint.

- [ ] **Step 4: Execute the required PostgreSQL test in an isolated schema**

After confirmation:

```powershell
.\scripts\vocabulary-release.ps1 -Action PostgresTest -ExpectedApiHost 'euxiucesdvibhwlkqzct.supabase.co' -ExpectedDbHost 'aws-0-ca-central-1.pooler.supabase.com' -ExpectedProjectRef 'euxiucesdvibhwlkqzct'
if ($LASTEXITCODE -ne 0) { throw "isolated PostgreSQL vocabulary tests failed" }
```

The Python tool generates and validates
`^vocab_test_[0-9a-f]{32}$`, creates that schema with privileges revoked from `PUBLIC`, sets
it as search path, runs migrations and the PostgreSQL-marked search suite in required mode,
and drops only that exact schema in `finally`. Required mode converts a missing URL or any
skip into non-zero exit. Success requires the test report to contain `0 skipped` and a
read-only check that `public` revision/schema/counts are unchanged.

- [ ] **Step 5: Create and restore-verify a restricted database backup**

```powershell
.\scripts\vocabulary-release.ps1 -Action Backup -ExpectedApiHost 'euxiucesdvibhwlkqzct.supabase.co' -ExpectedDbHost 'aws-0-ca-central-1.pooler.supabase.com' -ExpectedProjectRef 'euxiucesdvibhwlkqzct'
if ($LASTEXITCODE -ne 0) { throw "vocabulary backup or restore probe failed" }
```

The backup action:

1. Revalidates target host/database/revision immediately.
2. Creates a validated schema named `vocab_backup_<UTC timestamp>_<8 hex>`.
3. Revokes all schema privileges from `PUBLIC`.
4. Copies `public.vocab_items` and `public.alembic_version` into typed backup tables.
5. Stores count and a Python-computed SHA-256 over canonical ordered rows in a manifest
   table and `.release/vocabulary-backup-manifest.json`.
6. Creates a temporary restore-probe table using the pre-change `public.vocab_items`
   structure, restores all backup rows, verifies count/checksum, and drops the probe.
7. Leaves the restricted backup schema in place and reports only its name/count/checksum.

Do not continue unless restore verification passes. Keep the backup schema until the user
separately approves its later removal.

- [ ] **Step 6: Apply the migration with immediate target and exit checks**

```powershell
.\scripts\vocabulary-release.ps1 -Action Migrate -ExpectedApiHost 'euxiucesdvibhwlkqzct.supabase.co' -ExpectedDbHost 'aws-0-ca-central-1.pooler.supabase.com' -ExpectedProjectRef 'euxiucesdvibhwlkqzct'
if ($LASTEXITCODE -ne 0) { throw "production vocabulary migration failed" }
```

The migrate action revalidates host/database and backup manifest, confirms public revision
is still `0001_initial`, invokes Alembic programmatically, and returns success only when the
revision is exactly `0002_vocabulary_book`. PostgreSQL transactional DDL must roll back on
failure; the script stops without running verification/smoke after a non-zero result.

- [ ] **Step 7: Verify production schema, then run a cleanup-safe smoke**

```powershell
.\scripts\vocabulary-release.ps1 -Action Verify -ExpectedApiHost 'euxiucesdvibhwlkqzct.supabase.co' -ExpectedDbHost 'aws-0-ca-central-1.pooler.supabase.com' -ExpectedProjectRef 'euxiucesdvibhwlkqzct'
if ($LASTEXITCODE -ne 0) { throw "production vocabulary verification failed" }
.\scripts\vocabulary-release.ps1 -Action Smoke -ExpectedApiHost 'euxiucesdvibhwlkqzct.supabase.co' -ExpectedDbHost 'aws-0-ca-central-1.pooler.supabase.com' -ExpectedProjectRef 'euxiucesdvibhwlkqzct'
if ($LASTEXITCODE -ne 0) { throw "production vocabulary smoke failed or cleanup was incomplete" }
```

Verify is read-only and checks revision, all new columns, all four indexes, constraints,
preflight row-count preservation, and backup manifest availability.

Smoke records the before count, uses FastAPI TestClient with a cryptographically random
disposable `learner_<uuid>`, tracks the returned vocabulary ID, exercises
save/list/keys/edit/delete and foreign-owner `404`, and deletes only the tracked ID in
`finally`. It requires the final production count to equal the before count. Startup logging
uses the sanitizer from Task 14.

- [ ] **Step 8: Document and test the recovery action; run it only after a second confirmation**

`Restore` is implemented and unit-tested but is not part of the successful release path. If
post-migration verification finds data corruption:

1. Stop and report the exact failed invariant and retained backup schema.
2. Ask for a second explicit user confirmation naming that backup schema.
3. Run:

```powershell
$confirmedBackupSchema = Read-Host 'Enter the exact backup schema named in the user confirmation'
if ($confirmedBackupSchema -notmatch '^vocab_backup_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}$') { throw "confirmed backup schema has an invalid format" }
.\scripts\vocabulary-release.ps1 -Action Restore -ExpectedApiHost 'euxiucesdvibhwlkqzct.supabase.co' -ExpectedDbHost 'aws-0-ca-central-1.pooler.supabase.com' -ExpectedProjectRef 'euxiucesdvibhwlkqzct' -ExpectedBackupSchema $confirmedBackupSchema
if ($LASTEXITCODE -ne 0) { throw "vocabulary restore failed and requires manual intervention" }
```

Restore requires `-ExpectedBackupSchema`, reads the schema from the target-validated
manifest, and refuses unless the argument, manifest, and existing database schema match
exactly, along with host/database/revision/checksum. In one transaction it locks
`public.vocab_items`,
copies the current failed state to a new restricted `vocab_failed_<timestamp>_<hex>` schema,
replaces rows from the retained pre-change backup, derives new normalized/timestamp fields
with v1 rules, resets the sequence, and verifies original-column count/checksum before
commit. Any failure rolls back without changing public rows. After a successful restore,
rerun `Verify` and `Smoke`.

- [ ] **Step 9: Final verification and handoff**

Use `@superpowers:verification-before-completion`. Run:

```powershell
git diff --check
git check-ignore frontend/test-results frontend/playwright-report .release
$status = git status --porcelain
if ($status) { $status; throw "working tree is not clean" }
git log --oneline --decorate -15
```

The clean-tree gate relies on the Task 13 `.gitignore` entries for
`frontend/test-results/` and `frontend/playwright-report/`, plus this task's `.release/`
entry. Confirm those generated paths are ignored with `git check-ignore` before evaluating
status.

Report:

- tests/lint/build results
- migration revision and verification
- retained restricted backup schema and manifest
- files and commits
- any remaining auth-phase limitations
