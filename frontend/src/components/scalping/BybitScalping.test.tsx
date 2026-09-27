import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  instrumentState: {
    data: { status: 'success', data: [] as never[] } as
      | { status: string; data: never[] }
      | undefined,
    isFetching: false,
    isError: false,
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[1] === 'bybit-instruments'
      ? mocks.instrumentState
      : { data: undefined, isFetching: false, isError: false },
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

vi.mock('@/stores/authStore', () => ({
  useAuthStore: (selector: (state: { user: { broker: string } }) => unknown) =>
    selector({ user: { broker: 'bybit' } }),
}))

vi.mock('@/stores/themeStore', () => ({
  useThemeStore: (selector: (state: { appMode: string }) => unknown) =>
    selector({ appMode: 'live' }),
}))

vi.mock('@/hooks/useMarketData', () => ({
  useMarketData: () => ({
    data: new Map(),
    isAuthenticated: false,
    isFallbackMode: false,
  }),
}))

vi.mock('@/utils/toast', () => ({
  showToast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

import { BybitScalping } from './BybitScalping'

describe('Bybit instrument search states', () => {
  beforeEach(() => {
    mocks.instrumentState.data = { status: 'success', data: [] }
    mocks.instrumentState.isFetching = false
    mocks.instrumentState.isError = false
  })

  it('explains the expected search input and distinguishes no matches', async () => {
    const user = userEvent.setup()
    render(<BybitScalping />)

    expect(
      screen.getByText('Enter at least 2 characters, for example BTC, ETH, or BTCUSDT.')
    ).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Search symbol, e.g. BTCUSDT'), 'BTC')

    expect(
      await screen.findByText('No matching Bybit instruments. Try BTC, ETH, or BTCUSDT.')
    ).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Master Contract' })).not.toBeInTheDocument()
  })

  it('directs the user to refresh the master when the instrument request fails', async () => {
    mocks.instrumentState.data = undefined
    mocks.instrumentState.isError = true
    const user = userEvent.setup()
    render(<BybitScalping />)

    await user.type(screen.getByPlaceholderText('Search symbol, e.g. BTCUSDT'), 'BTC')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'choose Force Download, then search again'
    )
    expect(screen.getByRole('link', { name: 'Master Contract' })).toHaveAttribute(
      'href',
      '/master-contract'
    )
  })
})
