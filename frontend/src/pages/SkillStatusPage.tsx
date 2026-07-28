import type { Skill } from '../skills'

/**
 * Status page for a module that isn't finished.
 *
 * Deliberately not a "coming soon" splash. It states what already exists that the module
 * will reuse and what is genuinely missing, because that is the information both a learner
 * and whoever builds it next actually need. Overstating readiness would send learners to a
 * dead end and hide the real work.
 */
export function SkillStatusPage({
  skill,
  onGoListening,
}: {
  skill: Skill
  onGoListening: () => void
}) {
  const label = skill.status === 'partial' ? 'Partly built' : 'Not built yet'

  return (
    <>
      <div className="pagehead">
        <h2>{skill.label}</h2>
        <p>{skill.blurb}</p>
      </div>

      <div className="statuscard">
        <div className="statushead">
          <h3>{skill.label}</h3>
          <span className="native">{skill.native}</span>
          <span className={`chip ${skill.status === 'partial' ? 'warn' : ''}`}>{label}</span>
          {skill.progress > 0 && <span className="chip">{skill.progress}% of the way</span>}
        </div>

        <div className="progressbar" role="img" aria-label={`${skill.progress}% complete`}>
          <span
            className={skill.status === 'partial' ? 'partial' : 'none'}
            style={{ width: `${Math.max(skill.progress, 2)}%` }}
          />
        </div>

        <div className="reuse-grid">
          <div className="reuse have">
            <div className="reuse-label">Already exists · reused</div>
            <ul>
              {skill.have.map((h) => (
                <li key={h}>{h}</li>
              ))}
            </ul>
          </div>
          <div className="reuse need">
            <div className="reuse-label">Still missing</div>
            <ul>
              {skill.need.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </div>
        </div>

        <div className="actions">
          <button className="btn ghost" onClick={onGoListening}>
            Practise listening instead →
          </button>
        </div>
      </div>
    </>
  )
}
