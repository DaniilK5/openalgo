import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@/test/test-utils'

const mocks = vi.hoisted(() => ({
  getHoldings: vi.fn(),
}))

vi.mock('@/api/trading', () => ({
  tradingApi: { getHoldings: mocks.getHoldings },
}))

vi.mock('@/stores/authStore', () => ({
  useAuthStore: () => ({ apiKey: 'test-api-key', user: { broker: 'bybit' } }),
}))

vi.mock('@/stores/themeStore', () => ({ onModeChange: () => () => {} }))
vi.mock('@/hooks/useOrderEventRefresh', () => ({ useOrderEventRefresh: () => {} }))
vi.mock('@/hooks/usePageVisibility', () => ({
  usePageVisibility: () => ({ isVisible: true, wasHidden: false, timeSinceHidden: 0 }),
}))
vi.mock('@/hooks/useLivePrice', () => ({
  useLivePrice: (items: unknown[]) => ({
    data: items,
    isLive: false,
    isPaused: false,
  }),
}))
vi.mock('@/components/trading', () => ({
  PlaceOrderDialog: () => null,
}))

import Holdings from './Holdings'

describe('Bybit Holdings coin balances', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.getHoldings.mockResolvedValue({
      status: 'success',
      data: {
        holdings: [
          {
            symbol: 'BTC',
            exchange: 'CRYPTO',
            asset_type: 'account_coin_balance',
            quantity: 0.0123,
            usd_value: 750.2,
            currency: 'USD',
          },
        ],
        statistics: { total_value: 750.2, total_positions: 1, currency: 'USD' },
      },
    })
  })

  it('shows USD value separately and does not offer trade actions for balances', async () => {
    render(<Holdings />)

    expect(await screen.findByText('Account Coin Balances')).toBeInTheDocument()
    expect(screen.getByText('0.0123')).toBeInTheDocument()
    expect(screen.getAllByText('$750.20').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Add' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Exit' })).not.toBeInTheDocument()
  })
})
