import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AccountPage } from './AccountPage'
import type { Entitlement, Tier } from '../types'

/**
 * The email login page, and the account it becomes once you are signed in.
 *
 * The auth context is stubbed, so what is asserted here is the page's own contract: that the form
 * collects an email and password and hands them over, that a failure is reported in words a learner
 * can act on rather than Supabase's developer text, and that nothing claims a subscription is active
 * on the strength of a URL.
 */

const authMock = vi.hoisted(() => ({ useAuth: vi.fn() }))
// Only `useAuth` is stubbed. `formatPrice` comes through for real, so these tests exercise
// the actual Intl formatting rather than asserting against a stub of it.
vi.mock('../auth/AuthContext', async (importActual) => ({
  ...(await importActual<typeof import('../auth/AuthContext')>()),
  useAuth: authMock.useAuth,
}))

function poseAuth({
  ready = true,
  enabled = true,
  signedIn = false,
  tier = 'anon' as Tier,
  billingEnabled = true,
  email = null as string | null,
  signIn = vi.fn().mockResolvedValue({}),
  googleEnabled = true,
  signInWithGoogle = vi.fn().mockResolvedValue({}),
  signUp = vi.fn().mockResolvedValue({}),
  signOut = vi.fn().mockResolvedValue(undefined),
  sendPasswordReset = vi.fn().mockResolvedValue({}),
  startCheckout = vi.fn().mockResolvedValue({}),
  openBillingPortal = vi.fn().mockResolvedValue({}),
  refreshMe = vi.fn().mockResolvedValue(undefined),
  price = { amount_cents: 999, currency: 'CAD', interval: 'month' } as {
    amount_cents: number | null
    currency: string
    interval: string | null
  } | null,
} = {}) {
  const entitlement: Entitlement = {
    tier,
    unit_limit: tier === 'premium' ? null : tier === 'free' ? 5 : 2,
    remaining: tier === 'premium' ? null : 1,
    unlocked_unit_ids: [],
    premium_until: tier === 'premium' ? '2027-03-01T00:00:00Z' : null,
  }
  authMock.useAuth.mockReturnValue({
    ready,
    enabled,
    billingEnabled,
    signedIn,
    tier,
    email,
    entitlement,
    config: { anon_unit_limit: 2, member_unit_limit: 5, price },
    signIn,
    googleEnabled,
    signInWithGoogle,
    signUp,
    signOut,
    sendPasswordReset,
    startCheckout,
    openBillingPortal,
    refreshMe,
  })
  return {
    signIn,
    signUp,
    signOut,
    sendPasswordReset,
    startCheckout,
    openBillingPortal,
    signInWithGoogle,
  }
}

describe('AccountPage — signed out', () => {
  it('shows an email and password form', () => {
    poseAuth()

    render(<AccountPage navigate={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toHaveAttribute('type', 'email')
    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password')
  })

  it('signs in with what was typed', async () => {
    const { signIn } = poseAuth()

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.type(screen.getByLabelText('Email'), '  learner@example.com  ')
    await userEvent.type(screen.getByLabelText('Password'), 'correct-horse')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in', hidden: false }))

    // Trimmed: a trailing space copied out of an email client must not cause a mismatch.
    expect(signIn).toHaveBeenCalledWith('learner@example.com', 'correct-horse')
  })

  it('reports a failed sign-in instead of appearing to do nothing', async () => {
    poseAuth({ signIn: vi.fn().mockResolvedValue({ error: 'That email and password do not match.' }) })

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.type(screen.getByLabelText('Email'), 'learner@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in', hidden: false }))

    expect(await screen.findByText('That email and password do not match.')).toBeInTheDocument()
  })

  it('switches to creating an account, and asks the password manager for a new password', async () => {
    const { signUp } = poseAuth()

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.click(screen.getByRole('tab', { name: 'Create account' }))

    // autocomplete decides whether a manager offers a saved password or generates one. Getting it
    // wrong is why managers sometimes save the wrong value.
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'new-password')
    expect(screen.getByText(/raises your unlocked recordings from 2 to 5/)).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Email'), 'new@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-password')
    await userEvent.click(screen.getByRole('button', { name: 'Create account', hidden: false }))

    expect(signUp).toHaveBeenCalledWith('new@example.com', 'a-long-password')
  })

  it('says to check the inbox when confirmation is required', async () => {
    poseAuth({ signUp: vi.fn().mockResolvedValue({ confirmEmail: true }) })

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.click(screen.getByRole('tab', { name: 'Create account' }))
    await userEvent.type(screen.getByLabelText('Email'), 'new@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-password')
    await userEvent.click(screen.getByRole('button', { name: 'Create account', hidden: false }))

    // Otherwise a successful signup looks like nothing happened.
    expect(await screen.findByText(/confirm your address from the email/)).toBeInTheDocument()
  })

  it('sends a reset link without revealing whether the address has an account', async () => {
    const { sendPasswordReset } = poseAuth()

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Forgotten your password/ }))
    await userEvent.type(screen.getByLabelText('Email'), 'learner@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Send reset link' }))

    expect(sendPasswordReset).toHaveBeenCalledWith('learner@example.com')
    // Phrased as a conditional: confirming which addresses are registered would leak the user list.
    expect(await screen.findByText(/If learner@example.com has an account/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })

  it('offers a way to keep practising without an account', async () => {
    const navigate = vi.fn()
    poseAuth()

    render(<AccountPage navigate={navigate} />)
    await userEvent.click(screen.getByRole('button', { name: /Keep practising without an account/ }))

    expect(navigate).toHaveBeenCalledWith('/listening')
  })

  it('explains itself rather than offering a dead form when accounts are not configured', () => {
    poseAuth({ enabled: false })

    render(<AccountPage navigate={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Accounts are not set up' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })
})

describe('AccountPage — Google', () => {
  it('offers Google above the email form', () => {
    poseAuth()

    render(<AccountPage navigate={vi.fn()} />)

    // Above, not below: someone who signed up with Google has no password to remember, and
    // burying the button under a password field is how duplicate accounts get created.
    const card = screen.getByRole('button', { name: /Continue with Google/ })
    const emailField = screen.getByLabelText('Email')
    expect(card.compareDocumentPosition(emailField) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('starts the Google redirect', async () => {
    const { signInWithGoogle } = poseAuth()

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Continue with Google/ }))

    expect(signInWithGoogle).toHaveBeenCalled()
  })

  it('offers the same button when creating an account', async () => {
    poseAuth()

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.click(screen.getByRole('tab', { name: 'Create account' }))

    // Google makes no distinction between signing up and signing in, so one button serves both.
    expect(screen.getByRole('button', { name: /Continue with Google/ })).toBeInTheDocument()
  })

  it('hides Google on the password-reset step', async () => {
    poseAuth()

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Forgotten your password/ }))

    // A Google account has no password here to reset.
    expect(screen.queryByRole('button', { name: /Continue with Google/ })).not.toBeInTheDocument()
  })

  it('hides Google when the project does not have the provider switched on', () => {
    poseAuth({ googleEnabled: false })

    render(<AccountPage navigate={vi.fn()} />)

    // A visible button that always errors reads as the app being broken.
    expect(screen.queryByRole('button', { name: /Continue with Google/ })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
  })

  it('reports a failed redirect rather than hanging on "Working…"', async () => {
    poseAuth({
      signInWithGoogle: vi
        .fn()
        .mockResolvedValue({ error: 'Google sign-in is not switched on for this app yet.' }),
    })

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Continue with Google/ }))

    expect(
      await screen.findByText('Google sign-in is not switched on for this app yet.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Continue with Google/ })).toBeEnabled()
  })
})

describe('AccountPage — signed in', () => {
  it('states the price before sending anyone to Stripe', () => {
    poseAuth({ signedIn: true, tier: 'free', email: 'a@b.c' })

    render(<AccountPage navigate={vi.fn()} />)

    // A paywall that names no price makes people click through to Stripe to find out. The figure
    // comes from Stripe itself, so it cannot drift from what is actually charged.
    expect(screen.getByText(/\$9\.99/)).toBeInTheDocument()
    expect(screen.getByText(/month/)).toBeInTheDocument()
  })

  it('still offers the upgrade when the price could not be fetched', () => {
    poseAuth({ signedIn: true, tier: 'free', email: 'a@b.c', price: null })

    render(<AccountPage navigate={vi.fn()} />)

    // Stripe being briefly unreachable must not remove the ability to subscribe.
    expect(screen.getByRole('button', { name: 'Go premium' })).toBeInTheDocument()
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument()
  })

  it('shows the free tier and what is left', () => {
    poseAuth({ signedIn: true, tier: 'free', email: 'learner@example.com' })

    render(<AccountPage navigate={vi.fn()} />)

    expect(screen.getByText('learner@example.com')).toBeInTheDocument()
    expect(screen.getByText('Free account')).toBeInTheDocument()
    expect(screen.getByText(/1 of 5 recordings left/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Go premium' })).toBeInTheDocument()
  })

  it('starts checkout through the server, never with a key in the browser', async () => {
    const { startCheckout } = poseAuth({ signedIn: true, tier: 'free', email: 'a@b.c' })

    render(<AccountPage navigate={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Go premium' }))

    expect(startCheckout).toHaveBeenCalled()
  })

  it('shows premium with its renewal date and a way to manage billing', () => {
    poseAuth({ signedIn: true, tier: 'premium', email: 'learner@example.com' })

    render(<AccountPage navigate={vi.fn()} />)

    expect(screen.getByText('Premium')).toBeInTheDocument()
    expect(screen.getByText(/Everything unlocked/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Manage billing' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Go premium' })).not.toBeInTheDocument()
  })

  it('hides the upgrade offer when billing is not configured', () => {
    poseAuth({ signedIn: true, tier: 'free', email: 'a@b.c', billingEnabled: false })

    render(<AccountPage navigate={vi.fn()} />)

    expect(screen.queryByRole('button', { name: 'Go premium' })).not.toBeInTheDocument()
  })

  it('signs out and returns to the library', async () => {
    const navigate = vi.fn()
    const { signOut } = poseAuth({ signedIn: true, tier: 'free', email: 'a@b.c' })

    render(<AccountPage navigate={navigate} />)
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))

    expect(signOut).toHaveBeenCalled()
    expect(navigate).toHaveBeenCalledWith('/listening')
  })
})
