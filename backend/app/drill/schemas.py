"""Response shapes for drill mode.

The split that matters is between `DrillQuestionOut` and `DrillResultOut`. An exam
item's answer is derivable from three separate fields — the letter, which option
carries `is_correct`, and an explanation that usually names the answer outright
("正确答案：A") — so withholding it means withholding all three. The question
schema has no field that could carry any of them, rather than relying on every
endpoint to remember to strip them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DrillOptionOut(BaseModel):
    label: str
    text: str


class DrillCollectionOut(BaseModel):
    id: int
    skill: str
    name: str
    level: str | None = None
    item_count: int
    # How many of its items are the copy kept after de-duplication; a practice
    # queue draws from these.
    distinct_count: int


class DrillQuestionOut(BaseModel):
    """An item as the learner meets it. Carries nothing that reveals the answer."""

    id: int
    skill: str
    kind: str
    collection: str
    level: str | None = None
    seq: int | None = None
    title: str | None = None
    time_limit_s: int | None = None
    # Reading passage, listening transcript, or production prompt. The corrected
    # text when the proofreading layer produced one, since that is the reading a
    # learner should be given.
    document: str
    question: str | None = None
    options: list[DrillOptionOut] = Field(default_factory=list)
    # Short-lived signed URLs, minted per request.
    image_url: str | None = None
    audio_url: str | None = None
    # Listening items are meant to be heard, not read. The transcript is still
    # sent — a learner who has answered wants it — so the client is told whether
    # to keep it hidden until then.
    document_is_spoiler: bool = False
    # True for the 513 items whose four choices are spoken and never written. The
    # bank stores them as four blanks for that reason; the wording was recovered
    # from the explanation, and showing it would turn a listening item into a
    # matching exercise. `options` carry empty text on these — the letters are all
    # there is to answer with, and the wording arrives with the result.
    options_are_spoken: bool = False


class DrillResultOut(BaseModel):
    """What comes back after an attempt: the key, and everything it unlocks."""

    attempt_id: int
    question_id: int
    # The choices in full. Only meaningful for a spoken-options item, where the
    # question payload deliberately sent them blank — this is where the learner
    # finally gets to read what they just heard.
    options: list[DrillOptionOut] = Field(default_factory=list)
    correct: bool | None = None
    answer: str | None = None
    selected: str | None = None
    explanation: str | None = None
    document_zh: str | None = None
    model_answer: str | None = None
    # Present when the answer was not the bank's own — recovered by inference
    # rather than shipped with the item.
    answer_source: str | None = None


class DrillAttemptIn(BaseModel):
    question_id: int
    # Null when the learner ran out of time or skipped.
    selected: str | None = None
    elapsed_ms: int | None = None
    # Free text for a writing task; a recording key for speaking.
    response: dict = Field(default_factory=dict)


class DrillProgressOut(BaseModel):
    skill: str
    level: str | None = None
    attempted: int
    correct: int
    # Attempts that could be marked at all. Production tasks have no key, so they
    # count as attempted but not as graded, and `accuracy` is over `graded` — a
    # learner who wrote three essays is not 0% correct.
    graded: int
    accuracy: float | None = None
