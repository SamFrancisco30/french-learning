# Vocabulary Book and Identity Compatibility Design

**Date:** 2026-07-30  
**Status:** Approved in conversation; written-spec review in progress
**Scope:** Vocabulary-book MVP plus an authentication-compatible learner identity boundary

## 1. Context

The application already supports listening and reading exercises, contextual lookup, and
anonymous progress tracking. A learner can save a word or expression from the lookup popup,
but there is no page where saved vocabulary can be viewed or managed.

The current identity is a short random `learner_key` stored in browser `localStorage`.
Requests pass that value through query parameters or JSON bodies. The backend trusts it.
This is sufficient for local development, but it is not a secure identity mechanism for a
public commercial application.

The project is expected to become a public commercial application in the long term, but
there is no near-term public launch requirement. The product priority is therefore to
validate the vocabulary-learning workflow before investing in a complete registration and
login experience. Authentication must nevertheless be able to land without redesigning the
vocabulary feature.

## 2. Verified Current State

A read-only inspection of the configured Supabase project found 12 public tables:

- `alembic_version`
- `attempts`
- `exercises`
- `expressions`
- `gloss_cache`
- `lessons`
- `listening_units`
- `segments`
- `sentence_analyses`
- `sources`
- `transcripts`
- `vocab_items`

`vocab_items` already contains:

- anonymous and future account ownership fields: `learner_key`, `user_id`
- vocabulary content: `language`, `headword`, `gloss_en`, `example`, `unit_id`
- review fields: `reps`, `lapses`, `ease`, `interval_days`, `due_at`
- metadata: `zipf`, `created_at`

At inspection time, `vocab_items` contained no rows and `attempts` contained five rows.
The vocabulary migration therefore has low data risk, while the identity design must
preserve existing anonymous attempt records.

The service credential configured in `.env` is a server-side Supabase secret. It must never
be exposed through Vite, embedded in browser code, logged, or returned by an API.

## 3. Decision

Build in this order:

1. Vocabulary-book MVP and a small identity compatibility layer.
2. Supabase Auth and anonymous-to-account data adoption before any public beta.
3. Spaced-repetition review flows after the collection workflow has been validated.

This order ships learner value quickly and keeps authentication from becoming a blocking
dependency. The identity boundary prevents vocabulary and progress code from having to be
rewritten when authentication arrives.

## 4. Goals

### 4.1 Vocabulary MVP

- Let learners see all words and expressions they have saved.
- Filter by learning language and search by word or meaning.
- Sort by most recently saved or alphabetically.
- Edit the learner-controlled gloss and example.
- Delete an owned vocabulary item.
- Return to the source lesson when a valid `unit_id` is present.
- Keep saved state synchronized between the lookup popup and vocabulary page.
- Work for anonymous learners now and authenticated learners later.

### 4.2 Identity Compatibility

- Resolve learner ownership in one backend component.
- Remove `learner_key` and `user_id` from vocabulary business payloads.
- Support an anonymous credential without mistaking it for secure authentication.
- Prefer a verified Supabase user when a valid access token is present.
- Ensure every vocabulary read and mutation is scoped to the resolved identity.
- Preserve a clear path for adopting anonymous data into an authenticated account.

## 5. Non-goals

The first implementation will not include:

- registration, login, password reset, or email verification
- spaced-repetition scheduling actions or a "due today" queue
- tags, folders, decks, favorites, bulk import, or export
- generated pronunciation audio
- collaborative or shared vocabulary lists
- offline synchronization
- a general rewrite of attempts or progress screens

The existing SRS fields remain untouched until the review workflow is designed.

## 6. Architecture

### 6.1 Identity Resolution

Introduce a backend `LearnerIdentity` value and one request dependency responsible for
creating it.

Conceptually:

```text
Request
  |
  +-- Authorization present -------> 401 during vocabulary MVP
  |
  +-- X-Learner-Key ---------------> anonymous learner_key
                                      |
                                      v
                               LearnerIdentity
                                      |
                   +------------------+------------------+
                   |                  |                  |
                 vocab            attempts           progress
```

During the MVP, vocabulary endpoints support anonymous identity only. The client sends the
anonymous credential in `X-Learner-Key`, not a URL or business payload. A malformed or
missing credential is rejected with `401`. If an `Authorization` header is present during
the MVP, the vocabulary identity dependency returns `401` with a generic
"authentication is not enabled" response; it must not decode, trust, or silently ignore an
unverified bearer token.

Attempts and progress retain their existing contracts during this MVP: attempts continue to
accept `learner_key` in their request body and progress continues to accept it in the query
string. The application continues passing the same anonymous value to those legacy calls.
Moving attempts and progress onto the identity dependency belongs to the authentication
phase. This temporary difference is explicit so the vocabulary work does not accidentally
expand into an application-wide authentication refactor.

When authentication is added in a separate phase, the dependency will verify the bearer
token server-side and derive `user_id` from its subject claim. A client-provided `user_id`
is never trusted. A valid authenticated identity will then take precedence over an
anonymous identity for ordinary queries.

Business routers call identity-aware query helpers rather than duplicating ownership
conditions. The helper produces exactly one ownership predicate:

```text
authenticated: vocab_items.user_id = identity.user_id
anonymous:     vocab_items.user_id IS NULL
               AND vocab_items.learner_key = identity.learner_key
```

The same abstraction will later be applied to attempts and progress. The vocabulary MVP
does not change those routers.

### 6.2 Anonymous Credential

New anonymous credentials use `crypto.randomUUID()` instead of `Math.random()`. Existing
short `learner_key` values remain readable so current development progress is not orphaned.
The stored/header form remains prefixed as `learner_<token>`. The backend accepts only
`^learner_[A-Za-z0-9-]{1,48}$` and a maximum total length of 56 characters, which covers
both the legacy base-36 suffix and the new UUID suffix. Values outside this format receive
`401`.

The anonymous key acts as a bearer credential: anyone who possesses it can access that
anonymous learner's data. It must therefore:

- use high-entropy generation
- be sent in a header rather than a query string
- be excluded from logs and error messages
- never be presented as equivalent to a registered account

This is an acceptable pre-authentication bridge, not a permanent security boundary for
commercial user data.

### 6.3 Frontend State

Add a frontend identity provider that owns the anonymous key today and can own the
Supabase session later. Vocabulary API helpers obtain `X-Learner-Key` from this provider
rather than accepting `learnerKey` in each vocabulary payload. Attempts and progress may
still read the raw key from the provider and pass it through their legacy contracts until
the authentication phase.

Add a vocabulary provider that caches lightweight saved-word identities for the active
language. It updates that cache after save and delete operations, allowing the lookup popup
and vocabulary page to show the same saved state without fetching all examples on every
lookup.

## 7. Data Model

### 7.1 Vocabulary Normalization

Add `normalized_headword` to `vocab_items`. It is derived on the server and is never trusted
from client input.

Normalization is implemented once on the Python backend and reused by migration backfill
and runtime writes in this exact order:

1. Apply Unicode NFKC normalization with `unicodedata.normalize("NFKC", value)`.
2. Map U+2019 RIGHT SINGLE QUOTATION MARK, U+02BC MODIFIER LETTER APOSTROPHE,
   U+FF07 FULLWIDTH APOSTROPHE, U+0060 GRAVE ACCENT, and U+00B4 ACUTE ACCENT to ASCII
   U+0027 APOSTROPHE.
3. Trim leading/trailing whitespace and collapse every internal Unicode-whitespace run to
   one ASCII space using `" ".join(value.split())`.
4. Apply Python `str.casefold()`.

It does not remove accents or punctuation other than the declared apostrophe mapping.
The original `headword` remains unchanged for display.

Required test vectors include:

| Input | Normalized |
|---|---|
| ` Écouter ` | `écouter` |
| `L’EAU` | `l'eau` |
| `lʼeau` | `l'eau` |
| `mise   en œuvre` | `mise en œuvre` |
| `côte` | `côte` |
| `cote` | `cote` |

`côte` and `cote` must remain distinct.

### 7.2 Ownership Uniqueness

The MVP migration replaces the current anonymous-only uniqueness rule with both partial
unique indexes:

```text
(learner_key, language, normalized_headword)
WHERE user_id IS NULL

(user_id, language, normalized_headword)
WHERE user_id IS NOT NULL
```

The supported SQLite minimum is 3.8.0, which supports partial indexes. SQLAlchemy declares
matching predicates with `postgresql_where` and `sqlite_where`; local development and
PostgreSQL therefore enforce the same ownership uniqueness. No application-only uniqueness
fallback is permitted.

Add `updated_at`, initialized from `created_at` during migration and changed whenever
learner content or review state changes.

Add indexes that support:

- ownership plus newest-first listing
- ownership plus language plus normalized word lookup

### 7.3 Migration Safety

The Alembic migration:

1. Adds `normalized_headword` as nullable.
2. Backfills it for existing rows using the same normalization implementation.
3. Adds nullable `updated_at` and backfills it from `created_at`.
4. Resolves normalization collisions deterministically.
5. Uses Alembic batch operations on SQLite to make both new columns non-null and remove the
   named `uq_vocab_learner_word` constraint; PostgreSQL uses normal `ALTER TABLE`.
6. Creates matching PostgreSQL and SQLite partial unique indexes.

For a collision, sort rows by `(updated_at DESC, id DESC)` and keep the first row as the
survivor. For each of `gloss_en`, `example`, and `unit_id`, retain the survivor's non-empty
value or fill it from the first newer-to-older row with a non-empty value. Copy the complete
SRS tuple (`reps`, `lapses`, `ease`, `interval_days`, `due_at`) only from the survivor; do
not combine counters. Delete the remaining collision rows after the survivor is complete.

Although the verified cloud table is currently empty, the migration remains safe for local
or newly-created data. Collision resolution follows the deterministic algorithm above.

No production migration is executed as part of design or planning. Applying it is an
explicit implementation and deployment step.

## 8. Authentication-phase Compatibility Contract

Full account adoption is a separate authentication-phase feature and requires its own
design. The vocabulary MVP provides only the stable compatibility points that feature will
need:

- every vocabulary row has `learner_key`, nullable `user_id`, and deterministic
  `normalized_headword`
- both anonymous and authenticated ownership uniqueness indexes already exist
- vocabulary ownership is resolved through one replaceable backend dependency
- the frontend identity provider is the only owner of identity state
- attempts retain their existing anonymous key so a later transaction can associate them
  with a verified account

The later authentication design must specify bearer-token verification, replay-safe and
idempotent adoption, collision merging, an auditable server-side adoption record, and
transactional rollback. No account-adoption endpoint or bearer-token decoder is implemented
in this MVP.

## 9. API

All endpoints resolve ownership from the request identity. They do not accept
`learner_key` or `user_id` in query parameters or JSON bodies.

### 9.1 Vocabulary Item Representation

List, save, and edit responses use the same vocabulary item shape:

```json
{
  "id": 42,
  "language": "fr",
  "headword": "écouter",
  "normalized_headword": "écouter",
  "gloss_en": "to listen",
  "example": "J'écoute la radio.",
  "zipf": 5.1,
  "reps": 0,
  "due_at": null,
  "created_at": "2026-07-30T18:00:00Z",
  "updated_at": "2026-07-30T18:00:00Z",
  "source": {
    "lesson_id": 7,
    "lesson_title": "Une émission de radio",
    "unit_id": 12,
    "unit_index": 2
  }
}
```

`source` is `null` when `unit_id` is absent or its source unit no longer exists. The API
joins `ListeningUnit` and `Lesson` to build it. The client constructs
`#/listening/lesson/{lesson_id}/unit/{unit_id}` from these IDs; routes are not stored in the
database.

### 9.2 List Vocabulary

```http
GET /api/vocab?language=fr&q=écouter&sort=recent&limit=50&cursor=...
```

Response:

```json
{
  "items": [],
  "next_cursor": null,
  "total": 0
}
```

Rules:

- `language` is optional; absence lists all learner languages.
- `q` is at most 128 characters and searches normalized headword and gloss.
- `sort` supports `recent` and `alphabetical`.
- `limit` defaults to 50 and must be between 1 and 100.
- `total` is the count after ownership, language, and search filters.
- `recent` orders by `(created_at DESC, id DESC)`.
- `alphabetical` orders by `(normalized_headword ASC, id ASC)`.
- the opaque base64url cursor contains a version, sort, language, normalized search text,
  the last row's sort value, and last row ID
- a cursor whose sort, language, or search text does not match the current request is
  rejected with `400`
- malformed or unsupported cursor versions are rejected with `400`

Search treats `%`, `_`, and `\` as literal characters. SQL `LIKE` patterns escape those
characters with `\` and declare `ESCAPE '\'`; user input cannot introduce wildcards.

### 9.3 Saved Vocabulary Keys

Register this static route before `/api/vocab/{item_id}`:

```http
GET /api/vocab/saved-keys?language=fr
```

Response:

```json
{
  "language": "fr",
  "items": [
    {
      "id": 42,
      "normalized_headword": "écouter"
    }
  ]
}
```

`language` is required and validated. The endpoint returns every active saved key for that
owner and language without examples or glosses.

### 9.4 Save Vocabulary

```http
POST /api/vocab
Content-Type: application/json

{
  "language": "fr",
  "headword": "écouter",
  "gloss_en": "to listen",
  "example": "J'écoute la radio.",
  "unit_id": 12
}
```

`headword` is limited to 128 characters, `gloss_en` to 1,000 characters, and `example` to
2,000 characters.

Saving is idempotent for the resolved owner, language, and normalized headword. For an
existing item:

- a non-empty incoming gloss fills `gloss_en` only when the stored gloss is empty
- a non-empty incoming example fills `example` only when the stored example is empty
- a valid incoming `unit_id` fills the source only when the stored source is absent
- empty incoming fields never clear stored fields
- the displayed `headword` and the complete SRS tuple remain unchanged
- `updated_at` changes only if a stored field was actually filled

Learner edits use `PATCH`; a repeated lookup save never overwrites those edits.
If concurrent inserts race, the backend catches the unique-constraint failure, rolls back
that transaction, reloads the winning row under the same ownership predicate, applies the
same fill-only rules, and returns it.

### 9.5 Edit Vocabulary

```http
PATCH /api/vocab/{id}
Content-Type: application/json

{
  "gloss_en": "to listen to",
  "example": "Elle écoute attentivement."
}
```

The MVP edits only learner-controlled gloss and example content. Each field is optional,
but at least one must be present. `null` or an empty string clears that field. The same
1,000/2,000 character limits apply. A successful edit updates `updated_at`. Renaming a
headword is not part of the first release.

### 9.6 Delete Vocabulary

```http
DELETE /api/vocab/{id}
```

Returns `204 No Content`. A missing item and an item owned by another learner both return
`404 Not Found`. Deletion is permanent in the MVP; confirmation is the safeguard.

## 10. User Experience

Add a global "My Words" entry separate from the skill tabs. The route is
`#/vocabulary`.

The page:

- defaults to the application's active learning language
- supports language filtering, text search, and recent/alphabetical sorting
- shows headword, gloss, example, saved date, and source when available
- links a valid source back to its lesson/unit
- supports inline editing of gloss and example
- asks for confirmation before deletion
- uses compact rows on desktop and cards on narrow screens
- provides loading, empty, error, and retry states

The empty state directs the learner to Listening or Reading and explains that selecting a
word opens the save action.

The lookup popup:

- displays "Saved" immediately when the word or expression exists in the vocabulary cache
- updates the cache after a successful save
- shows a visible retryable error when saving fails
- does not silently swallow network failures

## 11. Error Handling and Security

- During the MVP, any bearer token on a vocabulary request: `401 Unauthorized` with
  authentication-not-enabled semantics.
- In the later authentication phase, invalid or expired bearer token: `401 Unauthorized`.
- Missing or malformed anonymous identity on learner-owned endpoints: `401 Unauthorized`.
- Invalid language, cursor, sort, or payload: `400` or `422`.
- Item absent or not owned by the requester: `404 Not Found`.
- Duplicate races on save: resolve as an idempotent fetch/update, not a user-visible `409`.
- Temporary database failure: return a generic `503`; do not leak connection details.
- Frontend keeps the last successfully loaded list visible during refresh errors and offers
  retry.
- The Supabase service secret remains server-only.
- Every edit and delete includes the ownership predicate in the database operation itself;
  a prior ownership check followed by an unscoped mutation is not sufficient.

## 12. Testing

### 12.1 Backend

Add automated tests for:

- anonymous identity parsing and rejection of malformed credentials
- ownership isolation between two anonymous learners
- Unicode and apostrophe normalization
- case-insensitive idempotent save
- repeated save preserving SRS fields
- language filtering, search, sorting, and cursor pagination
- edit and delete scoped to the current owner
- indistinguishable `404` behavior for absent and foreign items
- collision-safe migration behavior
- matching ownership constraints on PostgreSQL and SQLite
- a vocabulary request carrying `Authorization` is rejected during the MVP
- existing attempts and progress still use their legacy learner-key contracts

### 12.2 Frontend

Verify:

- the global route and navigation entry
- loading, empty, populated, and failed list states
- language filter, search, and sort behavior
- inline edit and delete confirmation
- saved state synchronization between popup and vocabulary page
- visible retry behavior on save failures
- mobile and desktop layout behavior
- TypeScript checking and production build

## 13. Acceptance Criteria

The vocabulary MVP is complete when:

1. An anonymous learner can save a word from Listening or Reading and see it in My Words.
2. Re-saving the same normalized word does not create a duplicate or reset review state.
3. Two anonymous identities cannot read or mutate each other's vocabulary through the API.
4. A learner can filter, search, sort, edit, and permanently delete after confirmation.
5. The lookup popup and vocabulary page agree on whether a word is saved.
6. Existing anonymous attempt records remain readable.
7. The backend has one reusable learner identity boundary ready for Supabase Auth.
8. No Supabase service credential is exposed to the browser or logs.
9. Backend tests, frontend type checks, and the production build pass.

## 14. Delivery Boundary

The MVP may be used for local or controlled testing without registration. It must not be
presented as secure cross-device account storage.

Before public beta, the project must complete:

- Supabase Auth registration and login
- backend bearer-token verification
- anonymous-to-account adoption
- activation and verification of authenticated ownership queries using the indexes already
  created by the MVP migration
- a security review of learner-owned endpoints
