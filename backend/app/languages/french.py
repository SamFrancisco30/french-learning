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
)
