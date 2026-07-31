import { useState } from 'react'
import { LookupProvider, SelectableText } from '../components/Lookup'

/**
 * Reading works today with no new backend: `POST /api/lookup` accepts arbitrary text with
 * no `unit_id`, taking the live-gloss path and consulting the accumulated expression
 * lexicon. So this page is a real tool, not a placeholder — paste French, select a word.
 *
 * Because the text was never ingested, expressions aren't pre-annotated, so nothing is
 * underlined up front and detection leans on lemma matching against expressions learned
 * from other lessons. That's the documented tradeoff of the unseen-text path.
 */

// Written for this page rather than quoted from anywhere: each line is built around a
// specific expression type so the lookup has something real to find.
const SAMPLES: { label: string; text: string }[] = [
  {
    label: 'idioms',
    text:
      "Quand elle a vu la facture, elle a piqué une crise. Son mari, lui, a gardé son " +
      "sang-froid : il avait déjà mis de l'argent de côté. « Ce n'est pas la fin du " +
      "monde », a-t-il dit en haussant les épaules.",
  },
  {
    label: 'same word, three expressions',
    text:
      "Le feu brûlait doucement dans la cheminée. Dehors, quelqu'un avait mis le feu à " +
      "une poubelle près du feu rouge. Plus tard, nous avons regardé un feu d'artifice " +
      'au-dessus du fleuve.',
  },
  {
    label: 'news register',
    text:
      "En revanche, les négociations ont tourné court. Le porte-parole a fait savoir que " +
      "son gouvernement comptait prendre une décision dans les jours à venir, tout en " +
      'appelant à la retenue.',
  },
  {
    label: 'science register',
    text:
      "Mis bout à bout, les brins d'ADN d'une seule cellule mesureraient près de deux " +
      "mètres. Au cours de la division cellulaire, les cellules filles reçoivent une " +
      'copie complète du génome.',
  },
]

export function ReadingPage({ language }: { language: string }) {
  const [draft, setDraft] = useState('')
  const [text, setText] = useState('')

  return (
    <LookupProvider language={language}>
      <div className="pagehead">
        <h2>Reading</h2>
        <p>
          Paste any French text and select a word to translate it in context. If the word
          belongs to a fixed expression, the popup shows the whole expression — not just the
          word.
        </p>
      </div>

      {!text ? (
        <div className="card">
          <div className="reading-input">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Collez un texte en français…"
              aria-label="Text to read"
              spellCheck={false}
            />
            <div className="sample-row">
              <span className="bar-label">or try:</span>
              {SAMPLES.map((s) => (
                <button
                  key={s.label}
                  className="sample-btn"
                  onClick={() => {
                    setDraft(s.text)
                    setText(s.text)
                  }}
                >
                  {s.label}
                </button>
              ))}
            </div>
            <div className="actions">
              <button className="btn" disabled={!draft.trim()} onClick={() => setText(draft)}>
                Read this
              </button>
            </div>
          </div>
        </div>
      ) : (
        <>
          <SelectableText text={text} className="reading-passage" lang={language} />
          <div className="actions">
            <button
              className="btn ghost"
              onClick={() => {
                setText('')
                setDraft('')
              }}
            >
              ← New text
            </button>
            <span className="bar-label">
              select any word · expressions are detected from the lesson lexicon
            </span>
          </div>
        </>
      )}
    </LookupProvider>
  )
}
