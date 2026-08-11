"""Drill mode: a fixed-format exam bank, kept apart from the study pipeline.

Study mode grows its own content — a video is downloaded, transcribed, cut into
units, and questions are generated from it. Everything hangs off `sources`, and
a unit is a time slice with a start and an end.

Drill mode imports content that was already authored. A TCF item has no video
behind it, no ASR pass, and no timeline; a reading item has no audio at all.
Fitting it into `exercises` would mean either inventing a source, a lesson and a
unit for every question, or relaxing `exercises.unit_id` to nullable and making
every existing access to `exercise.unit` handle a case study mode never
produces. The first pollutes the library; the second puts the risk on the module
that gains nothing from the change.

So drill mode has its own tables. They are also a better fit: the shape of an
exam item is fixed and known, so it can be columns that SQL can filter and
sample on, rather than JSON stuffed into a generic payload.

The two modes stay joinable where it matters — attempts on both sides carry
`learner_key`/`user_id`, so a single progress view is a UNION away.
"""
