import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LanguagePicker } from './LanguagePicker'
import type { Language } from '../types'

/**
 * The dropdown is hand-rolled rather than a <select>, because a <select> can only render text and
 * the whole point of this control is that it shows a flag. That trade means the behaviour a native
 * control would have given for free — dismissing, keyboard movement, an accessible name — is code
 * here, so it is tested here.
 */

const LANGUAGES: Language[] = [
  { code: 'fr', name_en: 'French', name_native: 'Français' },
  { code: 'ru', name_en: 'Russian', name_native: 'Русский' },
  { code: 'zh', name_en: 'Chinese (Mandarin)', name_native: '中文' },
]

const setup = (value = 'fr') => {
  const onChange = vi.fn()
  render(<LanguagePicker languages={LANGUAGES} value={value} onChange={onChange} />)
  return { onChange, trigger: screen.getByRole('button', { expanded: false }) }
}

describe('LanguagePicker', () => {
  it('names the current language, since a flag alone is silent to a screen reader', () => {
    const { trigger } = setup('fr')
    expect(trigger).toHaveAttribute('aria-label', expect.stringContaining('French'))
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu')
  })

  it('opens on click and offers every language', async () => {
    const { trigger } = setup()
    await userEvent.click(trigger)

    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const items = screen.getAllByRole('menuitem')
    expect(items).toHaveLength(3)
    expect(items.map((i) => i.textContent)).toEqual([
      'FrançaisFrench',
      'РусскийRussian',
      '中文Chinese (Mandarin)',
    ])
  })

  it('reports the selection and closes', async () => {
    const { trigger, onChange } = setup()
    await userEvent.click(trigger)
    await userEvent.click(screen.getByRole('menuitem', { name: /Russian/ }))

    expect(onChange).toHaveBeenCalledWith('ru')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('marks the language already loaded, so the menu says where you are', async () => {
    const { trigger } = setup('zh')
    await userEvent.click(trigger)
    expect(screen.getByRole('menuitem', { name: /Chinese/ })).toHaveClass('on')
    expect(screen.getByRole('menuitem', { name: /French/ })).not.toHaveClass('on')
  })

  it('closes on Escape and hands focus back to the trigger', async () => {
    const { trigger, onChange } = setup()
    await userEvent.click(trigger)
    await userEvent.keyboard('{Escape}')

    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('closes on a click outside without selecting anything', async () => {
    const { trigger, onChange } = setup()
    await userEvent.click(trigger)
    await userEvent.click(document.body)

    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('focuses the first item on open and moves with the arrow keys', async () => {
    const { trigger } = setup()
    await userEvent.click(trigger)

    const [fr, ru, zh] = screen.getAllByRole('menuitem')
    expect(fr).toHaveFocus()

    await userEvent.keyboard('{ArrowDown}')
    expect(ru).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}')
    expect(zh).toHaveFocus()
    // Wraps, so holding Down never dead-ends on the last item.
    await userEvent.keyboard('{ArrowDown}')
    expect(fr).toHaveFocus()
    await userEvent.keyboard('{ArrowUp}')
    expect(zh).toHaveFocus()
    await userEvent.keyboard('{Home}')
    expect(fr).toHaveFocus()
    await userEvent.keyboard('{End}')
    expect(zh).toHaveFocus()
  })

  it('renders nothing at all before the language list has loaded', () => {
    const { container } = render(<LanguagePicker languages={[]} value="fr" onChange={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })
})
