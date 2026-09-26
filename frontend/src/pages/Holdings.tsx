import {
  AlertTriangle,
  Download,
  Loader2,
  Pause,
  Radio,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  Wallet,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { tradingApi } from '@/api/trading'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { PlaceOrderDialog } from '@/components/trading'
import { calculateLiveStats, useLivePrice } from '@/hooks/useLivePrice'
import { useOrderEventRefresh } from '@/hooks/useOrderEventRefresh'
import { usePageVisibility } from '@/hooks/usePageVisibility'
import { cn, makeFormatCurrency, sanitizeCSV } from '@/lib/utils'
import { useAuthStore } from '@/stores/authStore'
import { onModeChange } from '@/stores/themeStore'
import type { Holding, HoldingsStats, StandardHolding } from '@/types/trading'
import { showToast } from '@/utils/toast'
import { EmptyState } from '@/components/ui/empty-state'

function isAccountCoinBalance(
  holding: Holding
): holding is Extract<Holding, { asset_type: 'account_coin_balance' }> {
  return 'asset_type' in holding && holding.asset_type === 'account_coin_balance'
}

function isStandardHolding(holding: Holding): holding is StandardHolding {
  return !isAccountCoinBalance(holding)
}

function formatPercent(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

// Which holding the order dialog is currently acting on, and in which
// direction. Add buys more of the same scrip; Exit sells the full holding.
interface HoldingOrderIntent {
  symbol: string
  exchange: string
  action: 'BUY' | 'SELL'
  quantity: number
}

export default function Holdings() {
  const { apiKey, user } = useAuthStore()
  const formatCurrency = useMemo(() => makeFormatCurrency(user?.broker), [user?.broker])
  const [holdings, setHoldings] = useState<Holding[]>([])
  const [stats, setStats] = useState<HoldingsStats | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showStaleWarning, setShowStaleWarning] = useState(false)
  const [orderIntent, setOrderIntent] = useState<HoldingOrderIntent | null>(null)
  const hasAccountCoinBalances = holdings.some(isAccountCoinBalance)

  // Page visibility tracking for resource optimization
  const { isVisible, wasHidden, timeSinceHidden } = usePageVisibility()
  const lastFetchRef = useRef<number>(Date.now())

  // Centralized real-time price hook with WebSocket + MultiQuotes fallback
  // Automatically pauses when tab is hidden
  const {
    data: enhancedHoldings,
    isLive,
    isPaused,
  } = useLivePrice(holdings, {
    enabled: holdings.length > 0 && !hasAccountCoinBalances,
    useMultiQuotesFallback: true,
    staleThreshold: 5000,
    multiQuotesRefreshInterval: 30000,
    pauseWhenHidden: true,
  })

  // Calculate enhanced stats based on real-time data
  const enhancedStats = useMemo(() => {
    if (!stats || hasAccountCoinBalances) return stats

    // Check if any holding has live data
    const hasAnyLiveData = enhancedHoldings.some(
      (h) => (h as Holding & { _dataSource?: string })._dataSource !== 'rest'
    )

    // If no live data, return original REST stats
    if (!hasAnyLiveData) return stats

    // Recalculate stats with real-time data
    return calculateLiveStats(enhancedHoldings, stats)
  }, [stats, enhancedHoldings, hasAccountCoinBalances])

  const fetchHoldings = useCallback(
    async (showRefresh = false) => {
      if (!apiKey) {
        setIsLoading(false)
        return
      }

      if (showRefresh) setIsRefreshing(true)

      try {
        const response = await tradingApi.getHoldings(apiKey)
        if (response.status === 'success' && response.data) {
          setHoldings(response.data.holdings || [])
          setStats(response.data.statistics)
          setError(null)
        } else {
          setError(response.message || 'Failed to fetch holdings')
        }
      } catch {
        setError('Failed to fetch holdings')
      } finally {
        setIsLoading(false)
        setIsRefreshing(false)
      }
    },
    [apiKey]
  )

  // Initial fetch and visibility-aware polling
  // Pauses polling when tab is hidden to save resources
  useEffect(() => {
    // Don't poll when tab is hidden
    if (!isVisible) return

    fetchHoldings()
    lastFetchRef.current = Date.now()
  }, [fetchHoldings, isVisible])

  // Refresh on order events instead of polling
  useOrderEventRefresh(fetchHoldings, {
    events: ['order_event', 'analyzer_update'],
  })

  // Refresh data when tab becomes visible after being hidden
  useEffect(() => {
    if (!wasHidden || !isVisible) return

    const timeSinceLastFetch = Date.now() - lastFetchRef.current

    // If hidden for more than 30 seconds, show stale warning and refresh
    if (timeSinceHidden > 30000 || timeSinceLastFetch > 30000) {
      setShowStaleWarning(true)
      fetchHoldings()
      lastFetchRef.current = Date.now()
    }
  }, [wasHidden, isVisible, timeSinceHidden, fetchHoldings])

  // Auto-dismiss stale data warning after 5 seconds
  useEffect(() => {
    if (!showStaleWarning) return
    const timeout = setTimeout(() => setShowStaleWarning(false), 5000)
    return () => clearTimeout(timeout)
  }, [showStaleWarning])

  // Listen for mode changes (live/analyze) and refresh data
  useEffect(() => {
    const unsubscribe = onModeChange(() => {
      fetchHoldings()
    })
    return () => unsubscribe()
  }, [fetchHoldings])

  const exportToCSV = () => {
    if (enhancedHoldings.length === 0) {
      showToast.error('No data to export', 'system')
      return
    }

    try {
      const headers = hasAccountCoinBalances
        ? ['Coin', 'Exchange', 'Quantity', 'USD Value', 'Currency']
        : ['Symbol', 'Exchange', 'Quantity', 'Avg Price', 'LTP', 'Product', 'P&L', 'P&L %']
      const rows = hasAccountCoinBalances
        ? enhancedHoldings
            .filter(isAccountCoinBalance)
            .map((h) => [
              sanitizeCSV(h.symbol),
              sanitizeCSV(h.exchange),
              sanitizeCSV(h.quantity),
              sanitizeCSV(h.usd_value),
              sanitizeCSV(h.currency),
            ])
        : enhancedHoldings
            .filter(isStandardHolding)
            .map((h) => [
              sanitizeCSV(h.symbol),
              sanitizeCSV(h.exchange),
              sanitizeCSV(h.quantity),
              sanitizeCSV(h.average_price),
              sanitizeCSV(h.ltp),
              sanitizeCSV(h.product),
              sanitizeCSV(h.pnl),
              sanitizeCSV(h.pnlpercent),
            ])

      const csv = [headers, ...rows].map((row) => row.join(',')).join('\n')
      const blob = new Blob([csv], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const filename = `holdings_${new Date().toISOString().split('T')[0]}.csv`
      a.download = filename
      a.click()
      // Revoke the object URL to free memory
      URL.revokeObjectURL(url)
      showToast.success(`Downloaded ${filename}`, 'clipboard')
    } catch {
      showToast.error('Failed to export CSV', 'system')
    }
  }

  const isProfit = (value: number) => value >= 0

  return (
    <div className="space-y-6">
      {/* Stale Data Warning */}
      {showStaleWarning && (
        <Alert variant="default" className="bg-amber-500/10 border-amber-500/30">
          <AlertTriangle className="h-4 w-4 text-amber-600" />
          <AlertDescription className="text-amber-700 dark:text-amber-400">
            Data is being refreshed after tab was inactive...
          </AlertDescription>
        </Alert>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight">
              {hasAccountCoinBalances ? 'Account Coin Balances' : 'Investor Summary'}
            </h1>
            {!hasAccountCoinBalances && isPaused ? (
              <Badge
                variant="outline"
                className="bg-amber-500/10 text-amber-600 border-amber-500/30 gap-1"
              >
                <Pause className="h-3 w-3" />
                Paused
              </Badge>
            ) : !hasAccountCoinBalances && isLive ? (
              <Badge
                variant="outline"
                className="bg-emerald-500/10 text-emerald-600 border-emerald-500/30 gap-1"
              >
                <Radio className="h-3 w-3 animate-pulse" />
                Live
              </Badge>
            ) : null}
          </div>
          <p className="text-muted-foreground">
            {hasAccountCoinBalances
              ? 'Unified Account coin quantities and USD values. Derivative positions are shown separately.'
              : 'View your holdings portfolio'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchHoldings(true)}
            disabled={isRefreshing}
          >
            <RefreshCw className={cn('h-4 w-4 mr-2', isRefreshing && 'animate-spin')} />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={exportToCSV}>
            <Download className="h-4 w-4 mr-2" />
            Export
          </Button>
        </div>
      </div>

      {/* Stats Cards */}
      {hasAccountCoinBalances ? (
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Total Coin Balance Value</CardDescription>
              <CardTitle className="text-2xl text-primary">
                {stats?.total_value !== undefined ? formatCurrency(stats.total_value) : '---'}
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Assets</CardDescription>
              <CardTitle className="text-2xl">
                {stats?.total_positions ?? holdings.length}
              </CardTitle>
            </CardHeader>
          </Card>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Total Holding Value</CardDescription>
              <CardTitle className="text-2xl text-primary">
                {enhancedStats ? formatCurrency(enhancedStats.totalholdingvalue ?? 0) : '---'}
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Total Investment Value</CardDescription>
              <CardTitle className="text-2xl">
                {enhancedStats ? formatCurrency(enhancedStats.totalinvvalue ?? 0) : '---'}
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Total Profit and Loss</CardDescription>
              <CardTitle
                className={cn(
                  'text-2xl',
                  enhancedStats && isProfit(enhancedStats.totalprofitandloss ?? 0)
                    ? 'text-green-600'
                    : 'text-red-600'
                )}
              >
                {enhancedStats ? (
                  <div className="flex items-center gap-1">
                    {isProfit(enhancedStats.totalprofitandloss ?? 0) ? (
                      <TrendingUp className="h-5 w-5" />
                    ) : (
                      <TrendingDown className="h-5 w-5" />
                    )}
                    {formatCurrency(enhancedStats.totalprofitandloss ?? 0)}
                  </div>
                ) : (
                  '---'
                )}
              </CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardDescription>Total PnL Percentage</CardDescription>
              <CardTitle
                className={cn(
                  'text-2xl',
                  enhancedStats && isProfit(enhancedStats.totalpnlpercentage ?? 0)
                    ? 'text-green-600'
                    : 'text-red-600'
                )}
              >
                {enhancedStats ? formatPercent(enhancedStats.totalpnlpercentage ?? 0) : '---'}
              </CardTitle>
            </CardHeader>
          </Card>
        </div>
      )}

      {/* Holdings Table */}
      <Card>
        <CardContent className="py-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin" />
            </div>
          ) : error ? (
            <div className="text-center py-12 text-muted-foreground">{error}</div>
          ) : holdings.length === 0 ? (
            <EmptyState
              icon={Wallet}
              title="No holdings found"
              description="Connect a broker to start tracking your portfolio."
            />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    {hasAccountCoinBalances ? (
                      <>
                        <TableHead>Coin</TableHead>
                        <TableHead>Account</TableHead>
                        <TableHead className="text-right">Quantity</TableHead>
                        <TableHead className="text-right">USD Value</TableHead>
                        <TableHead>Balance Type</TableHead>
                      </>
                    ) : (
                      <>
                        <TableHead>Trading Symbol</TableHead>
                        <TableHead>Exchange</TableHead>
                        <TableHead className="text-right">Quantity</TableHead>
                        <TableHead className="text-right">Avg Price</TableHead>
                        <TableHead className="text-right">LTP</TableHead>
                        <TableHead>Product</TableHead>
                        <TableHead className="text-right">Profit and Loss</TableHead>
                        <TableHead className="text-right">PnL %</TableHead>
                        <TableHead className="text-center">Actions</TableHead>
                      </>
                    )}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {enhancedHoldings.map((holding, index) =>
                    isAccountCoinBalance(holding) ? (
                      <TableRow key={`${holding.symbol}-${holding.exchange}-${index}`}>
                        <TableCell className="font-medium">{holding.symbol}</TableCell>
                        <TableCell>
                          <Badge variant="outline">Unified Account</Badge>
                        </TableCell>
                        <TableCell className="text-right font-mono">{holding.quantity}</TableCell>
                        <TableCell className="text-right font-mono">
                          {formatCurrency(holding.usd_value)}
                        </TableCell>
                        <TableCell>Coin balance</TableCell>
                      </TableRow>
                    ) : (
                      <TableRow key={`${holding.symbol}-${holding.exchange}-${index}`}>
                        <TableCell className="font-medium">{holding.symbol}</TableCell>
                        <TableCell>
                          <Badge variant="outline">{holding.exchange}</Badge>
                        </TableCell>
                        <TableCell className="text-right font-mono">{holding.quantity}</TableCell>
                        <TableCell className="text-right font-mono">
                          {holding.average_price !== undefined
                            ? formatCurrency(holding.average_price)
                            : '-'}
                        </TableCell>
                        <TableCell className="text-right font-mono">
                          {holding.ltp !== undefined ? formatCurrency(holding.ltp) : '-'}
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary">{holding.product}</Badge>
                        </TableCell>
                        <TableCell
                          className={cn(
                            'text-right font-medium',
                            isProfit(holding.pnl) ? 'text-green-600' : 'text-red-600'
                          )}
                        >
                          <div className="flex items-center justify-end gap-1">
                            {isProfit(holding.pnl) ? (
                              <TrendingUp className="h-4 w-4" />
                            ) : (
                              <TrendingDown className="h-4 w-4" />
                            )}
                            {formatCurrency(holding.pnl)}
                          </div>
                        </TableCell>
                        <TableCell
                          className={cn(
                            'text-right',
                            isProfit(holding.pnlpercent) ? 'text-green-600' : 'text-red-600'
                          )}
                        >
                          {formatPercent(holding.pnlpercent)}
                        </TableCell>
                        <TableCell className="text-center">
                          <div className="flex items-center justify-center gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 px-3 border-green-600/40 text-green-600 hover:bg-green-600/10"
                              onClick={() =>
                                setOrderIntent({
                                  symbol: holding.symbol,
                                  exchange: holding.exchange,
                                  action: 'BUY',
                                  quantity: holding.quantity,
                                })
                              }
                            >
                              Add
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-7 px-3 border-red-600/40 text-red-600 hover:bg-red-600/10"
                              onClick={() =>
                                setOrderIntent({
                                  symbol: holding.symbol,
                                  exchange: holding.exchange,
                                  action: 'SELL',
                                  quantity: holding.quantity,
                                })
                              }
                            >
                              Exit
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  )}
                </TableBody>
                {hasAccountCoinBalances ? (
                  <TableFooter>
                    <TableRow className="bg-muted/50">
                      <TableCell colSpan={3} className="text-right text-muted-foreground">
                        Total coin balance value:
                      </TableCell>
                      <TableCell className="text-right font-bold">
                        {formatCurrency(
                          stats?.total_value ??
                            enhancedHoldings
                              .filter(isAccountCoinBalance)
                              .reduce((total, balance) => total + balance.usd_value, 0)
                        )}
                      </TableCell>
                      <TableCell />
                    </TableRow>
                  </TableFooter>
                ) : (
                  <TableFooter>
                    <TableRow className="bg-muted/50">
                      <TableCell colSpan={6} className="text-right text-muted-foreground">
                        Total P&L:
                      </TableCell>
                      <TableCell
                        className={cn(
                          'text-right font-bold',
                          enhancedStats && isProfit(enhancedStats.totalprofitandloss ?? 0)
                            ? 'text-green-600'
                            : 'text-red-600'
                        )}
                      >
                        {enhancedStats
                          ? `${(enhancedStats.totalprofitandloss ?? 0) >= 0 ? '+' : ''}${formatCurrency(enhancedStats.totalprofitandloss ?? 0)}`
                          : '-'}
                      </TableCell>
                      <TableCell
                        className={cn(
                          'text-right font-bold',
                          enhancedStats && isProfit(enhancedStats.totalpnlpercentage ?? 0)
                            ? 'text-green-600'
                            : 'text-red-600'
                        )}
                      >
                        {enhancedStats ? formatPercent(enhancedStats.totalpnlpercentage ?? 0) : '-'}
                      </TableCell>
                      <TableCell />
                    </TableRow>
                  </TableFooter>
                )}
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* The order dialog is only opened for stock holdings; account coin balances
          are display-only until category-aware spot order placement is enabled. */}
      <PlaceOrderDialog
        open={orderIntent !== null}
        onOpenChange={(open) => {
          if (!open) setOrderIntent(null)
        }}
        symbol={orderIntent?.symbol ?? ''}
        exchange={orderIntent?.exchange ?? ''}
        action={orderIntent?.action ?? 'BUY'}
        quantity={orderIntent?.quantity}
        product="CNC"
        priceType="MARKET"
        strategy="Holdings"
        onSuccess={(orderId) => {
          showToast.success(`Order ${orderId} placed`, 'orders')
          setOrderIntent(null)
          fetchHoldings(true)
        }}
        onError={(message) => showToast.error(message, 'orders')}
      />
    </div>
  )
}
