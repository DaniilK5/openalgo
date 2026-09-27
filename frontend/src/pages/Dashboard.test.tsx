import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@/test/test-utils'

vi.mock('@/hooks/useOrderEventRefresh', () => ({ useOrderEventRefresh: () => {} }))

import Dashboard from './Dashboard'

function stubDashboardFetch(broker: string, data: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      const payload = url.includes('dashboard-data')
        ? { status: 'success', broker, data }
        : { status: 'success', total_symbols: 100 }
      return Promise.resolve({
        status: 200,
        json: () => Promise.resolve(payload),
      })
    })
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Dashboard account balance display', () => {
  it('shows Bybit USD-equivalent equity and keeps coin quantity separate', async () => {
    stubDashboardFetch('bybit', {
      availablecash: '900.25',
      collateral: '0.00',
      m2munrealized: '12.50',
      m2mrealized: '0.00',
      utiliseddebits: '100.25',
      account_equity_usd: '1100.50',
      coin_balances: [
        { coin: 'BTC', equity: '0.0123', usd_value: '750.20', currency: 'USD' },
      ],
    })

    render(<Dashboard />)

    expect(await screen.findByText('Account Equity')).toBeInTheDocument()
    expect(screen.getByText('$1,100.50')).toBeInTheDocument()
    expect(screen.getByText('0.0123 BTC')).toBeInTheDocument()
    expect(screen.getByText('$750.20')).toBeInTheDocument()
    expect(screen.queryByText('Available Balance')).not.toBeInTheDocument()
  })

  it('keeps the existing Indian-format balance display for other brokers', async () => {
    stubDashboardFetch('zerodha', {
      availablecash: '1234567',
      collateral: '2000',
      m2munrealized: '0',
      m2mrealized: '0',
      utiliseddebits: '100',
    })

    render(<Dashboard />)

    expect(await screen.findByText('Available Balance')).toBeInTheDocument()
    expect(screen.getByText('12.35L')).toBeInTheDocument()
    expect(screen.queryByText('Account Equity')).not.toBeInTheDocument()
    expect(screen.queryByText('Coin Balances')).not.toBeInTheDocument()
  })
})
