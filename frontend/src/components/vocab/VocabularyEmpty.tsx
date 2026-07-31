export function VocabularyEmpty({ searching }: { searching: boolean }) {
  return (
    <div className="wordbook-empty">
      <h3>{searching ? 'No matching words' : 'No saved words yet'}</h3>
      <p>
        {searching
          ? 'Try a shorter search or a different spelling.'
          : 'Save a word while reading or listening and it will appear here.'}
      </p>
    </div>
  )
}
