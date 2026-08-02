import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LockChip, QuotaBar, UnlockGate } from './Entitlement'
import type { Entitlement, Tier } from '../types'

/**
 * What the allowance looks like to a learner.
 *
 * The auth context is stubbed so each tier can be posed directly; the context's own behaviour is
 * covered in auth/AuthContext.test.tsx. What is asserted here is the product promise — a locked
 * recording is never opened by accident, an "unlock" button is never offered when it would fail, and
 * word lookup is stated as free at the moment a limit gets in the way.
 */

const authMock = vi.hoisted(() => ({ useAuth: vi.fn() }))
// Only `useAuth` is stubbed. `formatPrice` comes through for real, so these tests exercise
// the actual Intl formatting rather than asserting against a stub of it.
vi.mock('../auth/AuthContext', async (importActual) => ({
  ...(await importActual<typeof import('../auth/AuthContext')>()),
  useAuth: authMock.useAuth,
}))

function entitlement(tier: Tier, limit: number | null, unlocked: number[] = []): Entitlement {
  return {
    tier,
    unit_limit: limit,
    remaining: limit === null ? null : Math.max(0, limit - unlocked.length),
    unlocked_unit_ids: unlocked,
    premium_until: tier === 'premium' ? '2027-01-01T00:00:00Z' : null,
  }
}

function poseAuth({
  tier = 'anon' as Tier,
  limit = 2 as number | null,
  unlocked = [] as number[],
  signedIn = false,
  ready = true,
  enabled = true,
  billingEnabled = true,
  unlock = vi.fn().mockResolvedValue({ ok: true }),
  startCheckout = vi.fn().mockResolvedValue({}),
} = {}) {
  const ent = entitlement(tier, limit, unlocked)
  authMock.useAuth.mockReturnValue({
    ready,
    enabled,
    billingEnabled,
    signedIn,
    tier,
    entitlement: ent,
    config: {
      anon_unit_limit: 2,
      member_unit_limit: 5,
      price: { amount_cents: 999, currency: 'CAD', interval: 'month' },
    },
    isUnlocked: (id: number) => limit === null || unlocked.includes(id),
    unlock,
    startCheckout,
  })
  return { unlock, startCheckout }
}

describe('QuotaBar', () => {
  it('says how many recordings are left', () => {
    poseAuth({ limit: 2, unlocked: [1] })

    render(<QuotaBar onSignIn={vi.fn()} />)

    expect(screen.getByText(/1 of 2 recordings left/)).toBeInTheDocument()
  })

  it('offers the next tier up when the allowance is spent', () => {
    poseAuth({ limit: 2, unlocked: [1, 2], signedIn: false })

    render(<QuotaBar onSignIn={vi.fn()} />)

    expect(screen.getByText(/unlocked all 2/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Sign in for 5/ })).toBeInTheDocument()
  })

  it('is absent for premium, who have no allowance to report', () => {
    poseAuth({ tier: 'premium', limit: null })

    const { container } = render(<QuotaBar onSignIn={vi.fn()} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('is absent until the tier is actually known', () => {
    // Otherwise the bar flashes a wrong number, or worse a zero, on every page load.
    poseAuth({ ready: false })

    const { container } = render(<QuotaBar onSignIn={vi.fn()} />)

    expect(container).toBeEmptyDOMElement()
  })
})

describe('LockChip', () => {
  it('marks a locked recording', () => {
    poseAuth({ limit: 2, unlocked: [3] })

    render(<LockChip unitId={7} />)

    expect(screen.getByText(/Locked/)).toBeInTheDocument()
  })

  it('says nothing about a recording the learner already opened', () => {
    poseAuth({ limit: 2, unlocked: [7] })

    const { container } = render(<LockChip unitId={7} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('says nothing at all for premium', () => {
    poseAuth({ tier: 'premium', limit: null })

    const { container } = render(<LockChip unitId={7} />)

    expect(container).toBeEmptyDOMElement()
  })
})

describe('UnlockGate', () => {
  const props = {
    unitId: 7,
    unitLabel: 'World news · Unit 3',
    onUnlocked: vi.fn(),
    onClose: vi.fn(),
    onSignIn: vi.fn(),
  }

  it('asks before spending a slot, and says what it costs', async () => {
    poseAuth({ limit: 2, unlocked: [] })

    render(<UnlockGate {...props} />)

    // The whole reason this dialog exists: with an allowance of two, a misclick is a quarter of
    // everything an anonymous learner gets to try.
    expect(screen.getByText(/uses 1 of your 2 recordings/)).toBeInTheDocument()
    expect(screen.getByText(/will have 1 left/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unlock this recording' })).toBeInTheDocument()
  })

  it('opens the recording once the slot is spent', async () => {
    const onUnlocked = vi.fn()
    const { unlock } = poseAuth({ limit: 2 })

    render(<UnlockGate {...props} onUnlocked={onUnlocked} />)
    await userEvent.click(screen.getByRole('button', { name: 'Unlock this recording' }))

    expect(unlock).toHaveBeenCalledWith(7)
    expect(onUnlocked).toHaveBeenCalled()
  })

  it('offers an account, not an unlock, once an anonymous allowance is spent', () => {
    poseAuth({ limit: 2, unlocked: [1, 2], signedIn: false })

    render(<UnlockGate {...props} />)

    // Offering "unlock" here would send the learner into a 409.
    expect(screen.queryByRole('button', { name: 'Unlock this recording' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create a free account' })).toBeInTheDocument()
    expect(screen.getByText(/raises that to 5/)).toBeInTheDocument()
  })

  it('offers premium once a signed-in allowance is spent', () => {
    poseAuth({ tier: 'free', limit: 5, unlocked: [1, 2, 3, 4, 5], signedIn: true })

    render(<UnlockGate {...props} />)

    expect(screen.queryByRole('button', { name: 'Unlock this recording' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Go premium' })).toBeInTheDocument()
  })

  it('names the price in the paywall itself', () => {
    poseAuth({ tier: 'free', limit: 5, unlocked: [1, 2, 3, 4, 5], signedIn: true })

    render(<UnlockGate {...props} />)

    // Said at the moment the limit actually bites, not only on the account page.
    expect(screen.getByText(/\$9\.99/)).toBeInTheDocument()
  })

  it('offers nothing to buy when billing is not configured', () => {
    poseAuth({
      tier: 'free',
      limit: 5,
      unlocked: [1, 2, 3, 4, 5],
      signedIn: true,
      billingEnabled: false,
    })

    render(<UnlockGate {...props} />)

    expect(screen.queryByRole('button', { name: 'Go premium' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Not now' })).toBeInTheDocument()
  })

  it('states that looking up words stays free, on every tier', () => {
    poseAuth({ limit: 2, unlocked: [1, 2] })

    render(<UnlockGate {...props} />)

    // The explicit product promise, and worth saying exactly where a limit is in the way.
    expect(screen.getByText(/Looking up and saving words stays free/)).toBeInTheDocument()
  })

  it('reports a refused unlock instead of pretending it worked', async () => {
    const onUnlocked = vi.fn()
    poseAuth({
      limit: 2,
      unlock: vi.fn().mockResolvedValue({ ok: false, reason: 'quota' }),
    })

    render(<UnlockGate {...props} onUnlocked={onUnlocked} />)
    await userEvent.click(screen.getByRole('button', { name: 'Unlock this recording' }))

    expect(onUnlocked).not.toHaveBeenCalled()
    expect(await screen.findByText(/still locked/)).toBeInTheDocument()
  })

  it('can be dismissed with Escape', async () => {
    const onClose = vi.fn()
    poseAuth({ limit: 2 })

    render(<UnlockGate {...props} onClose={onClose} />)
    await userEvent.keyboard('{Escape}')

    expect(onClose).toHaveBeenCalled()
  })

  it('is a modal dialog, and puts focus inside itself', () => {
    poseAuth({ limit: 2 })

    render(<UnlockGate {...props} />)

    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    // Focus starts on a control in the dialog rather than being left behind the veil.
    expect(dialog.contains(document.activeElement)).toBe(true)
  })
})
