# Écoute — listening comprehension from authentic media

Turns real French media into graded listening exercises:

```
YouTube URL → audio download → ASR with word timestamps → 60–120s listening units
            → CEFR difficulty estimate → cloze + MCQ + true/false + vocab + ordering
            → served to a React drill UI with per-blank audio replay
```

**Listening** is the first of five planned skills (`listening`, `speaking`, `writing`,
`reading`, `dictation`). French is the first of several languages — Russian and Chinese
profiles are already wired in.

---

## Quick start

Two servers. Backend first:

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Then the frontend:

```bash
cd frontend && npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` and `/media` to the backend, so the
browser only talks to one origin.

### Add a lesson

```bash
cd backend
.venv/bin/python scripts/ingest.py search "documentaire biologie cellule" --limit 8
.venv/bin/python scripts/ingest.py add "https://youtu.be/VIDEO_ID" --topic biology
.venv/bin/python scripts/ingest.py show 1
```

Useful flags: `--lang ru`, `--asr local`, `--no-llm` (cloze only, no API cost),
`--max-units 2`, `--re-transcribe`, `--require-cc`.

---

## Setup

```bash
# backend
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
cp .env.example .env      # then fill in OPENAI_API_KEY

# frontend
cd ../frontend && npm install
```

Requires `ffmpeg` on PATH (`brew install ffmpeg`).

---

## Model choices

### Transcription

Two interchangeable backends behind one `Transcriber` protocol (`ASR_BACKEND`):

| backend  | model                       | why |
|----------|-----------------------------|-----|
| `openai` | `whisper-1`                 | No local download; good for testing. |
| `local`  | `faster-whisper large-v3`   | **Production default.** Lower French WER, free, offline, built-in VAD. |

`whisper-1` rather than `gpt-4o-transcribe` deliberately: the newer transcription
models only return `json`/`text`, with no `verbose_json` and therefore **no word or
segment timestamps**. This whole pipeline is built on timestamp anchoring — a cloze
blank must map to an exact moment of audio — so `whisper-1` is the one that fits. Use
the `local` backend when you want better accuracy *and* timestamps.

Install local ASR with `uv pip install -e '.[local-asr]'`. On Apple Silicon it runs on
CPU with int8 (CTranslate2 has no Metal backend) — still roughly real-time for large-v3.

### Exercise generation

`gpt-4o` via `response_format: json_schema` with `strict: true`. Strict mode guarantees
the response shape, which eliminates the "model wrapped its JSON in prose" failure mode
entirely. One call per unit produces the gist, MCQs, true/false, vocabulary and ordering
items together.

---

## How the pipeline earns its keep

A few decisions that are load-bearing rather than incidental:

**Word timestamps are mandatory.** Every blank and every question carries an audio
window, so a wrong answer becomes "replay exactly that phrase" instead of a dead end.
The generator is required to cite a verbatim transcript quote for each item; that quote
is fuzzy-matched back onto the word timeline (`quotes.py`) to produce the replay window.

**Cloze text is aligned, not reconstructed.** Whisper's word array is bare tokens, so
joining it yields `On l a appris` — visibly wrong French with the elisions stripped. So
the punctuated segment text stays the display string and the word array is aligned onto
it by letter-stream matching (`align.py`), giving each blank a character span in the
*real* text. Measured 100% alignment on the sample sources.

**Blanks only land on confident words.** Blanking a word the ASR guessed at means
grading the learner against a possibly-wrong answer — the worst failure mode for this
exercise type. Candidates need ASR confidence ≥ 0.55.

**Blanks are spread across the clip.** Purely greedy "hardest words first" selection
clustered every blank in one region, leaving a third of the audio untested. Selection
now buckets the clip by time and takes the best candidate per bucket.

**Grading tests listening, not spelling.** `écologie` typed as `ecologie` is credited
with an accent note; a one-character typo is credited with a spelling note; `l'eau` is
accepted for `eau`. Being pedantic here trains the wrong skill.

**Difficulty weights speech rate highest** (0.35), then lexical rarity (0.30), rare-word
share (0.20) and sentence length (0.15) — listening difficulty is mostly about how fast
words arrive, not how obscure they are. Lesson level uses the 75th percentile of its
units, not the mean, because a lesson is as hard as its hard parts.

---

## Smart translation

Select any word or phrase in a transcript, gist or cloze passage → popup with an English
gloss **and** any multiword expression that word belongs to.

The hard case this is built around: `mettre le feu` appears in real text as
`a mis le feu`, `y a mis le feu`, `mettront le feu`. Measured against the PARSEME-FR gold
corpus, **~41% of French verbal MWEs have at least one intervening token**, so surface
string matching finds nothing and an envelope span would mis-resolve interior words.

Resolution order, in `lexicon/resolver.py`:

| order | path | when | cost |
|-------|------|------|------|
| 1 | **precomputed** | selection is in an ingested unit | pure SQL span overlap |
| 2 | **inferred** | expression known from another lesson | lemma match, sentence-bounded |
| 3 | **live** | arbitrary text, or a cache miss | one LLM call, then cached |

Path 1 is the common path and involves no model call, which is what makes the popup
instant. Expressions are extracted at ingest (`lexicon/extractor.py`) and stored as
**discontinuous `component_spans`**, so a selection touching any component resolves to
the whole expression.

Backfill units ingested before this existed:

```bash
.venv/bin/python scripts/ingest.py annotate
```

### Decisions worth knowing

**The model is never asked for character offsets.** It returns verbatim surface strings,
which are re-located in the real text by `lexicon/anchor.py`. Models miscount offsets, and
a wrong offset silently highlights the wrong words. It also makes hallucination mostly
self-limiting — an invented expression usually isn't in the passage to be found, so it gets
dropped. (3 of 58 were dropped on the sample library.)

**Lemma matching is a secondary signal only.** Lemma-bag lookup has ~99% recall on
lexicon-covered items but only ~0.35 precision — it fires on ordinary compositional uses.
So path 2 is gated on every content lemma appearing within 8 content tokens *and inside
the same sentence*. Without the sentence bound, a literal `le feu brûle.` matched
`feu rouge` because `rouge` appeared in the next sentence.

**Offsets are declared, not inferred.** A cloze passage is interleaved `<span>`/`<input>`
siblings, so walking `textContent` yields offsets that disagree with the server's string.
Every text chunk is instead rendered as `<span data-off="N">`, and `useTextSelection.ts`
resolves a DOM position by walking up to the nearest `[data-off]`. Anything without one —
an input, a decoration, the transcript's own heading — is outside the passage by
construction.

**Cloze selections cannot cross a blank.** `allowCrossSegment: false` clamps them to one
segment. Otherwise a selection dragged over a blank would ask the server to gloss a range
containing the hidden answer, printing it into the popup.

**The popup is portaled to `document.body`.** `.card.clickable:hover` applies a transform,
which creates a containing block for fixed-position descendants and would anchor the popup
to the card instead of the viewport.

**spaCy is optional.** `uv pip install -e '.[nlp]'` then
`python -m spacy download fr_core_news_sm` (~16 MB, ~4 ms/sentence). Without it, path 2
falls back to headwords — fine for nouns and compounds, but `mis` no longer resolves to
`mettre`, so inflected verbs stop matching in unseen text. Degraded, not broken; the
response reports which lemmatizer was used.

## Sentence grammar

Select a whole sentence and the popup explains its **constructions** — patterns whose
meaning isn't recoverable from the words, which is the case where knowing every word
still leaves you stuck. `il n'y a pas que le travail` reads word-by-word as "there is not
that work", which is close to the opposite of "work isn't the only thing".

Each construction shows its schematic form, what it means, **the wrong reading you'd
arrive at word-by-word**, and what it means in this particular sentence. Then a practice
item: an English sentence to render in French using that structure.

### Detection is a pattern problem; explanation is a language problem

The two are deliberately split. A model asked to *find* constructions hallucinates
confidently — it will announce `ne ... que` in any sentence containing both words. So
detection is 99 deterministic token patterns in
[`lexicon/data/fr_constructions.json`](backend/app/lexicon/data/fr_constructions.json),
and the model receives that list and explains only what it means *here*. A side benefit:
the popup names the construction instantly, and the explanation arrives after.

The pattern DSL, in `lexicon/constructions.py`:

| step | meaning |
|------|---------|
| `ne\|n'` | literal alternatives; the token must equal one |
| `*<=6` | a gap of up to 6 intervening tokens |
| `*<=5!pas\|plus` | …with those tokens **barred** from the gap |
| `POS:INF` | part-of-speech constraint (needs spaCy) |
| `!POS:VERB\|AUX@<=2` | zero-width: no verb within the next 2 tokens |
| `!ça\|cela` | zero-width: the next token isn't one of these |
| `@,` | zero-width: a comma sits just before here |

The last four exist because of one specific failure. `["ne", "*<=6", "que"]` also fires on
*"je ne pense pas que ce soit vrai"*, where `que` is a complementizer, not restrictive
"only" — and telling a learner their sentence contains a construction it doesn't is the
worst thing this feature can do. The distinctions needed were: bar negation words from the
gap; require that no verb follows `que` (a complementizer introduces a clause, restrictive
`que` introduces a noun phrase); and for the correlative `plus …, plus …`, require the
clause-break comma that separates it from *"plus de temps et plus d'argent"*.

Tokenization keeps elided prefixes as their own token *with* the apostrophe, so `n'y a pas`
is `["n'", "y", "a", "pas"]` and a pattern can name `n'` precisely.

### Quality gates

Two suites, both required to pass:

* **self-consistency** — every construction must match its own documented example. 99/99.
* **adversarial** — 51 innocent sentences that must *not* match, from an agent tasked with
  breaking the patterns. 51/51.
  See [`tests/negative_constructions.json`](backend/tests/negative_constructions.json).

23 curated constructions were **dropped** rather than shipped: one that can't match its own
example will never fire correctly, and one that fires on innocent sentences teaches
something false. Precision over coverage, the same rule the expression extractor follows.

### Practice grading keeps two signals apart

Free translation has many correct answers, so exact matching is useless and an
unconstrained judge is either a pushover or a pedant. But one thing *can* be checked
exactly — whether the construction was used:

1. **Structure** — deterministic, from `required_markers`. Free, exact, no model opinion.
2. **Meaning and grammar** — a judge, told explicitly that it is *not* judging the
   construction (checked precisely elsewhere) and *not* judging accents or typos.

Narrowing the judge to one question is what keeps it honest. And keeping the signals
separate is what lets the feedback say the most useful thing it can:

> **Good French — but it avoids il n'y a pas que X**
> ✗ missing `qu'/que` · ✓ meaning and grammar fine

A single blended score could never express that. Diacritic slips and one-character typos
are credited with a note, matching the cloze grader.

### Cost

`POST /api/sentence` is one model call, cached by sentence, so re-selecting is instant.
Deliberately *not* precomputed at ingest, unlike expressions: most sentences are never
selected, so precomputing every sentence of every unit would pay for analysis nobody reads.
Construction detection itself is free and needs no key.

## Layout

```
backend/
  app/
    config.py             settings (.env)
    models.py  db.py      SQLAlchemy models + engine
    schemas.py            API contracts — answers never serialized into GETs
    languages/            LanguageProfile registry: french, russian, chinese
    media/                yt-dlp ingest, ffmpeg normalize/chunk/clip
    asr/                  Transcriber protocol + openai / faster-whisper backends
    llm/                  structured-output client
    skills/listening/     segmenter, difficulty, align, cloze, quotes, generator,
                          grading, pipeline
    routers/              lessons, attempts, ingest
  scripts/ingest.py       CLI: add / list / show / search / config
frontend/src/
  api.ts  types.ts        typed client
  router.ts               ~30-line hash router (#/listening/lesson/2/unit/5)
  skills.ts               the five-skill registry + honest build status
  useClipPlayer.ts        audio hook; converts original-video ↔ clip time
  useTextSelection.ts     DOM selection → passage character offsets
  pages/                  ListeningPage, ReadingPage, SkillStatusPage
  components/             Library, UnitDrill, Exercises, Lookup
  index.css               silver-white theme, fully tokenized
```

### Skill pages

One route per skill, driven by `skills.ts`. The nav dot reflects real state — green
ready, amber partly built, grey not started — and the status pages list what already
exists versus what is genuinely missing rather than showing a "coming soon" splash.

| skill | route | state |
|-------|-------|-------|
| Listening 听力 | `#/listening` | complete |
| Dictation 听写 | `#/dictation` | 60% — cloze already covers 听音频填词; 整句听写 missing |
| Reading 阅读 | `#/reading` | **working** — paste any text, select a word; no new backend needed |
| Writing 写作 | `#/writing` | not started — needs rubric grading |
| Speaking 口语 | `#/speaking` | not started — needs mic capture + forced alignment |

Reading works on unseen text because `/api/lookup` takes arbitrary text with no
`unit_id`. Nothing is underlined up front there (no precomputed annotations), so
expression detection leans on the lemma-matched lexicon accumulated from ingested
lessons — it improves as the library grows. Run `scripts/ingest.py annotate` after
adding lessons.

### One coordinate-system gotcha

Exercise and blank timings are stored in **original-video seconds**, but each unit is
served as its own clip where `t=0` is `unit.start_s`. `useClipPlayer` converts, so
callers always pass original-timeline values. Keep it that way.

---

## Adding a language

Add one file to `app/languages/` and register it. Supply the ASR code, a `wordfreq`
code, a function-word set, elision prefixes, and a native speech-rate baseline. Nothing
in the pipeline needs to change — `ChineseProfile` overrides `_segment` for scripts with
no whitespace boundaries, which is the only structural variation so far.

For Chinese, install the CJK extra for real word segmentation:
`uv pip install 'wordfreq[cjk]'` (it degrades to per-character tokens otherwise).

## Adding a skill

`Lesson` and `Exercise` both carry a `skill` column, and the graders are registered by
exercise `kind` in `grading.py`. A new skill means a new `app/skills/<name>/` package
plus new kinds in that registry — the media, ASR, storage and attempt layers are shared.

---

## Notes and limits

- **Rotate the API key** in `.env` if it was ever shared in plaintext. `.env` is
  gitignored; the key is never sent to the frontend.
- **Source licensing.** `Source.license_name` records what YouTube reports, and
  `--require-cc` restricts ingest to Creative Commons material. Downloading audio for
  personal study is a different question from redistributing it — if this becomes a
  public service, serve clips only from material you have the right to serve, and check
  YouTube's Terms of Service for your use.
- **Ingest jobs are in-process.** `POST /api/ingest` tracks jobs in a dict, which is
  fine for single-worker local use. Move to a real queue (arq/RQ/Celery) plus a jobs
  table before deploying.
- **No auth.** `learner_key` is a random string in `localStorage`. Swap for real
  accounts and a `User` FK when needed.
- **Difficulty is a heuristic**, not a certified CEFR assessment. The component
  breakdown is stored on each unit (`difficulty_detail`) so it can be recalibrated
  against real learner accuracy data later.
- **SQLite** with WAL. The models are Postgres-ready; only `DATABASE_URL` changes.

## API

| method | path | purpose |
|--------|------|---------|
| GET  | `/api/health` | config + key presence |
| GET  | `/api/languages` | registered language profiles |
| GET  | `/api/lessons?language=fr&cefr=B1&topic=biology` | library |
| GET  | `/api/lessons/{id}` | lesson + its units |
| GET  | `/api/units/{id}` | unit + exercises (**no answers**) |
| GET  | `/api/units/{id}/transcript` | transcript reveal |
| POST | `/api/attempts` | submit + grade; returns answer & explanation |
| GET  | `/api/progress?learner_key=…` | accuracy overall and by exercise kind |
| POST | `/api/ingest` | start an ingest job (202 + job id) |
| GET  | `/api/ingest/{job_id}` | poll job |

Interactive docs at <http://localhost:8000/docs>.
