# Vocabulary Book and Identity Compatibility Design

**Date:** 2026-07-30  
**Status:** Approved in conversation; pending written-spec review  
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
  +-- valid Supabase bearer token --> authenticated user_id
  |
  +-- no bearer token -------------> anonymous learner_key
                                      |
                                      v
                               LearnerIdentity
                                      |
                   +------------------+------------------+
                   |                  |                  |
                 vocab            attempts           progress
```

During the MVP, the normal path is anonymous. The client sends the anonymous credential in
a dedicated request header, not a URL or business payload. A malformed or missing anonymous
credential is rejected for learner-owned endpoints.

When authentication is added, the dependency verifies the bearer token server-side and
derives `user_id` from its subject claim. A client-provided `user_id` is never trusted. A
valid authenticated identity takes precedence over an anonymous identity for ordinary
queries.

Business routers call identity-aware query helpers rather than duplicating ownership
conditions. The helper produces exactly one ownership predicate:

```text
authenticated: vocab_items.user_id = identity.user_id
anonymous:     vocab_items.user_id IS NULL
               AND vocab_items.learner_key = identity.learner_key
```

The same abstraction will later be applied to attempts and progress. The vocabulary MVP
must not require that broader refactor to be completed first.

### 6.2 Anonymous Credential

New anonymous credentials use `crypto.randomUUID()` instead of `Math.random()`. Existing
short `learner_key` values remain readable so current development progress is not orphaned.

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
Supabase session later. API helpers obtain identity headers from this provider rather than
accepting `learnerKey` arguments at each call site.

Add a vocabulary provider that caches lightweight saved-word identities for the active
language. It updates that cache after save and delete operations, allowing the lookup popup
and vocabulary page to show the same saved state without fetching all examples on every
lookup.

## 7. Data Model

### 7.1 Vocabulary Normalization

Add `normalized_headword` to `vocab_items`. It is derived on the server and is never trusted
from client input.

Normalization must:

- trim surrounding whitespace
- normalize Unicode consistently
- lowercase using language-appropriate behavior
- normalize supported apostrophe variants
- preserve the original `headword` for display

The normalization function is a focused, independently tested unit. It must not remove
accents or collapse words that are distinct in the target language.

### 7.2 Ownership Uniqueness

Replace the current anonymous-only uniqueness rule with two PostgreSQL partial unique
indexes:

```text
(learner_key, language, normalized_headword)
WHERE user_id IS NULL

(user_id, language, normalized_headword)
WHERE user_id IS NOT NULL
```

SQLite development must receive equivalent behavior. If SQLite partial-index support in
the supported runtime is sufficient, use matching partial indexes. Otherwise, enforce the
authenticated constraint transactionally in application code and document the local-only
difference.

Add indexes that support:

- ownership plus newest-first listing
- ownership plus language plus normalized word lookup

### 7.3 Migration Safety

The migration:

1. Adds `normalized_headword` as nullable.
2. Backfills it for existing rows using the same normalization implementation.
3. Resolves any normalization collisions deterministically.
4. Makes the column non-null.
5. Replaces the old unique constraint with the partial unique indexes.

Although the verified cloud table is currently empty, the migration remains safe for local
or newly-created data. Collision resolution keeps one vocabulary item, preferring the most
recent non-empty learner content and preserving the most recent SRS state.

No production migration is executed as part of design or planning. Applying it is an
explicit implementation and deployment step.

## 8. Anonymous-to-Account Adoption

Account adoption runs only after a Supabase access token has been verified. It runs in one
database transaction and is idempotent.

For the current anonymous identity and verified user:

1. Load anonymous vocabulary and attempt rows.
2. For every anonymous vocabulary item, look for an account item with the same language and
   normalized headword.
3. If none exists, assign the item to the authenticated user.
4. If one exists, merge into the account item:
   - prefer a non-empty learner-edited gloss and example
   - retain a valid source unit when the account item has none
   - use the most recently updated review state rather than adding counters
5. Set `user_id` on anonymous attempt rows while retaining `learner_key` for provenance.
6. Mark adoption complete for that anonymous credential and user.
7. Commit all changes together, or roll back all changes on failure.

The exact mechanism used to mark adoption as complete belongs to the authentication design.
It may be a dedicated adoption table or another auditable server-side record. A client-only
flag is insufficient as the source of truth.

Existing short development keys may be adopted during controlled testing. Before public
beta, all newly issued anonymous credentials must use the stronger generator.

## 9. API

All endpoints resolve ownership from the request identity. They do not accept
`learner_key` or `user_id` in query parameters or JSON bodies.

### 9.1 List Vocabulary

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
- `q` searches normalized headword and gloss.
- `sort` supports `recent` and `alphabetical`.
- `limit` is bounded by the server.
- cursors are opaque to the client and stable for the selected sort.

### 9.2 Saved Vocabulary Keys

Provide a lightweight endpoint or equivalent list projection for the active language:

```http
GET /api/vocab/keys?language=fr
```

It returns IDs and normalized headwords needed for saved-state synchronization, without
shipping all examples. The concrete URL may be replaced by a `fields` projection if that
keeps the router simpler, but the client must not load the full collection for every
lookup.

### 9.3 Save Vocabulary

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

Saving is idempotent for the resolved owner, language, and normalized headword. Re-saving
may fill non-empty content but must not reset SRS fields.

### 9.4 Edit Vocabulary

```http
PATCH /api/vocab/{id}
Content-Type: application/json

{
  "gloss_en": "to listen to",
  "example": "Elle écoute attentivement."
}
```

The MVP edits only learner-controlled gloss and example content. Renaming a headword is not
part of the first release.

### 9.5 Delete Vocabulary

```http
DELETE /api/vocab/{id}
```

Returns `204 No Content`. A missing item and an item owned by another learner both return
`404 Not Found`.

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

After deletion, the page offers a short undo action. Undo re-saves the captured item through
the normal idempotent save endpoint.

## 11. Error Handling and Security

- Invalid or expired bearer token: `401 Unauthorized`.
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
- authenticated identity precedence once the authentication path exists
- transactional and idempotent account adoption in the later auth phase

### 12.2 Frontend

Verify:

- the global route and navigation entry
- loading, empty, populated, and failed list states
- language filter, search, and sort behavior
- inline edit, delete confirmation, and undo
- saved state synchronization between popup and vocabulary page
- visible retry behavior on save failures
- mobile and desktop layout behavior
- TypeScript checking and production build

## 13. Acceptance Criteria

The vocabulary MVP is complete when:

1. An anonymous learner can save a word from Listening or Reading and see it in My Words.
2. Re-saving the same normalized word does not create a duplicate or reset review state.
3. Two anonymous identities cannot read or mutate each other's vocabulary through the API.
4. A learner can filter, search, sort, edit, delete, and undo deletion.
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
- authenticated ownership indexes and enforcement
- a security review of learner-owned endpoints

