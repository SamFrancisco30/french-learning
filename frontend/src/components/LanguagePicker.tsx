import { useEffect, useRef, useState } from 'react'
import type { Language } from '../types'
import { starPoints } from './icons'

/**
 * The target language, as a flag you can open.
 *
 * Flags are drawn as inline SVG rather than written as emoji. The emoji route is one character per
 * flag and tempting for that reason, but regional-indicator pairs only render as a flag where the
 * platform ships flag glyphs: on Windows they fall back to the two letters, so "🇫🇷" reads as a
 * boxed "FR". Drawing them costs a few lines and looks the same everywhere.
 *
 * Proportions and colours follow each flag's own specification, including China's star geometry.
 *
 * The coordinate space is 900x600 rather than a tidy 30x20, and that is what makes the bands look
 * sharp instead of smeared. Russia is three equal horizontal thirds; at a height of 20 those land
 * on 6.67 and 13.33, and a band edge on a fractional coordinate gets antialiased into a soft grey
 * seam. 600 divides by three exactly, so every edge is a whole number. The rendered size is chosen
 * to match: 27 x 18 css px means France's vertical thirds are exactly 9px and Russia's horizontal
 * thirds exactly 6px, whole pixels at 1x and 2x alike.
 */

/** The official construction grid is 30 x 20; everything below is that grid at 30x. */
const U = 30

/** China's four small stars each point at the centre of the large one, per the flag's spec. */
const CN_SMALL: Array<[number, number]> = [
  [10, 2],
  [12, 4],
  [12, 7],
  [10, 9],
]

function Flag({ code }: { code: string }) {
  // 3:2, the ratio all three of these flags share.
  const box = { viewBox: '0 0 900 600', className: 'flag', role: 'presentation' as const }
  if (code === 'fr') {
    return (
      <svg {...box}>
        <rect width="300" height="600" fill="#002395" />
        <rect x="300" width="300" height="600" fill="#fff" />
        <rect x="600" width="300" height="600" fill="#ED2939" />
      </svg>
    )
  }
  if (code === 'ru') {
    return (
      <svg {...box}>
        <rect width="900" height="200" fill="#fff" />
        <rect y="200" width="900" height="200" fill="#0039A6" />
        <rect y="400" width="900" height="200" fill="#D52B1E" />
      </svg>
    )
  }
  if (code === 'zh') {
    return (
      <svg {...box}>
        <rect width="900" height="600" fill="#DE2910" />
        <polygon points={starPoints(5 * U, 5 * U, 3 * U)} fill="#FFDE00" />
        {CN_SMALL.map(([cx, cy]) => (
          <polygon
            key={`${cx}-${cy}`}
            points={starPoints(
              cx * U,
              cy * U,
              1 * U,
              (Math.atan2(5 - cy, 5 - cx) * 180) / Math.PI + 90,
            )}
            fill="#FFDE00"
          />
        ))}
      </svg>
    )
  }
  // An unknown language still gets a chip of the right size, showing its code.
  return (
    <svg {...box}>
      <rect width="900" height="600" fill="var(--bg-sunken)" />
      <text x="450" y="420" textAnchor="middle" fontSize="330" fill="var(--text-dim)">
        {code}
      </text>
    </svg>
  )
}

export function LanguagePicker({
  languages,
  value,
  onChange,
}: {
  languages: Language[]
  value: string
  onChange: (code: string) => void
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement | null>(null)
  const menu = useRef<HTMLDivElement | null>(null)
  const current = languages.find((l) => l.code === value)

  // A menu that cannot be dismissed is a trap, so both of the ways people expect to close one are
  // wired up: a click anywhere outside, and Escape. `mousedown` rather than `click`, so pressing
  // down outside dismisses it without the click also landing on whatever is underneath.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        root.current?.querySelector<HTMLButtonElement>('.langpick-trigger')?.focus()
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Opening with the keyboard should land on something, so focus the first item once it exists.
  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLButtonElement>('button')?.focus()
  }, [open])

  /** Up/Down/Home/End inside the menu, which is what a menu is expected to do. */
  const onMenuKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const items = [...(menu.current?.querySelectorAll<HTMLButtonElement>('button') ?? [])]
    if (!items.length) return
    const i = items.indexOf(document.activeElement as HTMLButtonElement)
    const go = (n: number) => {
      e.preventDefault()
      items[(n + items.length) % items.length].focus()
    }
    if (e.key === 'ArrowDown') go(i + 1)
    else if (e.key === 'ArrowUp') go(i - 1)
    else if (e.key === 'Home') go(0)
    else if (e.key === 'End') go(items.length - 1)
  }

  if (languages.length === 0) return null

  return (
    <div className="langpick" ref={root}>
      <button
        type="button"
        className="langpick-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        // The flag alone is decorative; the accessible name has to carry both what is selected and
        // what the control does, since neither is available from an <svg>.
        aria-label={`Language: ${current?.name_en ?? value}. Change language`}
        title={current ? `${current.name_native} — change language` : 'Change language'}
        onClick={() => setOpen((v) => !v)}
      >
        <Flag code={value} />
        <svg className="langpick-caret" viewBox="0 0 10 6" aria-hidden="true">
          <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      </button>

      {open && (
        <div className="langpick-menu" role="menu" ref={menu} onKeyDown={onMenuKey}>
          {languages.map((l) => (
            <button
              key={l.code}
              type="button"
              role="menuitem"
              className={l.code === value ? 'on' : ''}
              onClick={() => {
                onChange(l.code)
                setOpen(false)
              }}
            >
              <Flag code={l.code} />
              <span className="langpick-name">{l.name_native}</span>
              <span className="langpick-en">{l.name_en}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
