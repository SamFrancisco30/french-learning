"""French language profile."""

from __future__ import annotations

from .base import LanguageProfile

# Articles, pronouns, prepositions, conjunctions, auxiliaries and their inflections.
# Blanking these teaches nothing about listening comprehension, so the cloze
# generator skips them (they're still fine as *distractors* in grammar drills).
FRENCH_FUNCTION_WORDS = frozenset(
    """
le la les l un une des du de d au aux a à
je tu il elle on nous vous ils elles me te se moi toi lui leur eux soi
ce cet cette ces c ça cela ceci celui celle ceux celles
mon ma mes ton ta tes son sa ses notre nos votre vos leurs
qui que quoi dont où quel quelle quels quelles lequel laquelle
et ou ni mais or car donc puis alors ainsi aussi
si comme quand lorsque pendant depuis avant après pour par sans sous sur
dans en entre vers chez avec contre selon malgré hormis
ne pas plus moins très trop peu bien mal encore déjà toujours jamais
y en là ici tout toute tous toutes même autre autres chaque
suis es est sommes êtes sont était étaient étais étions étiez
serai seras sera serons serez seront serait seraient été être
ai as a avons avez ont avais avait avions aviez avaient
aurai auras aura aurons aurez auront aurait auraient eu avoir
fais fait faisons faites font faisait faisaient
vais vas va allons allez vont allait allaient aller
peux peut pouvons pouvez peuvent pouvait pouvaient pouvoir
dois doit devons devez doivent devait devaient devoir
veux veut voulons voulez veulent voulait voulaient vouloir
qu s n m t j
""".split()
)

FRENCH = LanguageProfile(
    code="fr",
    name_en="French",
    name_native="Français",
    asr_code="fr",
    freq_code="fr",
    function_words=FRENCH_FUNCTION_WORDS,
    # French elides before vowels: l'eau, d'accord, j'ai, n'est, qu'il, s'il, m'a, t'as, jusqu'à
    filler_words=frozenset(
        """euh heu eh hé ben bein bah hein ouais mouais bof hum hm ouah""".split()
    ),
    elision_prefixes=("l", "d", "j", "n", "c", "s", "m", "t", "qu", "jusqu", "lorsqu", "puisqu"),
    needs_segmentation=False,
    # French news/documentary narration typically runs 160-200 wpm.
    baseline_wpm=175.0,
    diacritics_significant=True,
    # Measured on this corpus: "M." appears 3 times and bare initials 5, so both guards earn
    # their place; the rest are here because news and lecture material reliably produces them.
    sentence_abbreviations=frozenset(
        """m mm mme mmes mlle mlles dr pr st ste sts stes ex av apr env art
           no nos fig vol chap ch éd ed p pp t j.-c j.c av.j.-c""".split()
    ),
    terminal_abbreviations=frozenset("etc cf ibid op".split()),
    # As a French teacher reads a dictée. Longest symbols first matters for the ones that share a
    # prefix — "..." must be matched before "." — so the splicer sorts by length rather than
    # relying on this order, but keeping them grouped here makes the set easy to read.
    punctuation_names=(
        ("…", "points de suspension"),
        ("...", "points de suspension"),
        ("?", "point d'interrogation"),
        ("!", "point d'exclamation"),
        (";", "point-virgule"),
        (":", "deux points"),
        (",", "virgule"),
        (".", "point"),
        ("«", "ouvrez les guillemets"),
        ("»", "fermez les guillemets"),
        ("(", "ouvrez la parenthèse"),
        (")", "fermez la parenthèse"),
        ("—", "tiret"),
        ("–", "tiret"),
    ),
    # Ordered roughly by how often a learner trips on them. Ranked sets, not a dictionary: the
    # grader only asks "did the learner write a different member of the same set", which is
    # cheap and catches the confusions French dictée is actually testing.
    homophone_groups=(
        ("a", "à"),
        ("ou", "où"),
        ("et", "est", "es", "ai", "aie", "aies"),
        ("son", "sont"),
        ("on", "ont", "on n'"),
        ("ce", "se", "ceux"),
        ("ses", "ces", "c'est", "s'est", "sais", "sait", "s'en", "c'en"),
        ("la", "là", "l'a", "l'as"),
        ("leur", "leurs"),
        ("du", "dû", "dus"),
        ("sur", "sûr", "sûre"),
        ("mais", "mes", "met", "mets", "mai", "m'es", "m'est"),
        ("peu", "peut", "peux", "peuh"),
        ("quand", "quant", "qu'en", "camp"),
        ("si", "s'y", "ci", "scie"),
        ("ni", "n'y", "nid"),
        ("dans", "d'en", "dent"),
        ("plutôt", "plus tôt"),
        ("quelque", "quel que", "quelques"),
        ("davantage", "d'avantage", "d'avantages"),
        ("parce que", "par ce que"),
        ("tout", "tous", "toux"),
        ("voir", "voire"),
        ("qu'il", "qui"),
        ("notre", "nôtre"),
        ("votre", "vôtre"),
        # The -é / -er / -ez ending, the single most common French dictation error. Handled
        # separately in the grader because it is a suffix rule, not a fixed word set.
    ),
)
