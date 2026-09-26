import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { scalpingApi } from '@/api/scalping'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useMarketData } from '@/hooks/useMarketData'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import type { ScalpingOrderRequest } from '@/types/scalping'
import { showToast } from '@/utils/toast'

const CATEGORIES = ['spot', 'linear', 'inverse', 'option'] as const
type BybitCategory = (typeof CATEGORIES)[number]
type BybitInstrument = {
  symbol: string
  exchange: string
  category: BybitCategory
  name?: string
  expiry?: string
  strike?: number
  instrumenttype?: string
  tick_size?: number
  qty_step?: number
  min_qty?: number
  min_order_amt?: number
  base_coin?: string
  quote_coin?: string
  settle_coin?: string
}

const CATEGORY_LABELS: Record<BybitCategory, string> = {
  spot: 'Spot',
  linear: 'Linear futures',
  inverse: 'Inverse futures',
  option: 'Options',
}

export function BybitScalping() {
  const broker = useAuthStore((state) => state.user?.broker?.toLowerCase())
  const appMode = useThemeStore((state) => state.appMode)
  const queryClient = useQueryClient()
  const [category, setCategory] = useState<BybitCategory>('spot')
  const [query, setQuery] = useState('')
  const [instrument, setInstrument] = useState<BybitInstrument | null>(null)
  const [amount, setAmount] = useState('')
  const [armed, setArmed] = useState(false)
  const [orderError, setOrderError] = useState<string | null>(null)

  const { data: instrumentsResponse, isFetching } = useQuery({
    queryKey: ['scalping', 'bybit-instruments', category, query],
    queryFn: () => scalpingApi.getBybitInstruments(category, query.trim()),
    enabled: broker === 'bybit' && query.trim().length >= 2,
  })
  const instruments = instrumentsResponse?.data ?? []
  const { data: inventoryResponse } = useQuery({
    queryKey: ['scalping', 'bybit-inventory', instrument?.symbol, appMode],
    queryFn: () => scalpingApi.getBybitInventory(instrument?.symbol ?? ''),
    enabled: broker === 'bybit' && category === 'spot' && !!instrument && appMode === 'live',
    refetchInterval: 5000,
  })
  const symbols = useMemo(
    () => (instrument ? [{ symbol: instrument.symbol, exchange: 'CRYPTO' }] : []),
    [instrument]
  )
  const { data: marketData, isAuthenticated, isFallbackMode } = useMarketData({
    symbols,
    mode: 'Depth',
    enabled: broker === 'bybit' && !!instrument,
  })
  const tick = instrument ? marketData.get(`CRYPTO:${instrument.symbol}`)?.data : undefined
  const isSpot = category === 'spot'
  const amountLabel = isSpot ? 'Buy budget / sell quantity' : 'Order quantity'

  async function placeOrder(action: 'BUY' | 'SELL') {
    if (!instrument || !armed) return
    const quantity = Number(amount)
    if (!Number.isFinite(quantity) || quantity <= 0) {
      showToast.error('Enter a positive order size', 'orders')
      return
    }

    setOrderError(null)
    const order: ScalpingOrderRequest = {
      symbol: instrument.symbol,
      exchange: 'CRYPTO',
      category,
      action,
      quantity,
      product: isSpot ? 'CNC' : 'NRML',
      market_unit: isSpot ? (action === 'BUY' ? 'quoteCoin' : 'baseCoin') : undefined,
      ltp: tick?.ltp,
    }
    try {
      const response = await scalpingApi.placeOrder(order)
      if (response.status !== 'success') {
        const message = response.message ?? 'Order was not accepted'
        setOrderError(message)
        showToast.error(message, 'orders')
        return
      }
      setAmount('')
      await queryClient.invalidateQueries({
        queryKey: ['scalping', 'bybit-inventory', instrument.symbol],
      })
      if (response.inventory_warning) {
        setOrderError(response.inventory_warning)
        showToast.warning(response.inventory_warning, 'orders')
      } else if (response.mode === 'semi_auto') {
        showToast.warning(response.message ?? 'Order queued for approval', 'orders')
      } else {
        showToast.success(`${action} order accepted`, 'orders')
      }
    } catch (error) {
      const err = error as {
        response?: { data?: { message?: string; inventory_warning?: string } }
        message?: string
      }
      const message = [
        err.response?.data?.message ?? err.message ?? 'Order failed',
        err.response?.data?.inventory_warning,
      ]
        .filter(Boolean)
        .join(' ')
      setOrderError(message)
      showToast.error(message, 'orders')
    }
  }

  if (broker !== 'bybit') {
    return (
      <Card>
        <CardContent className="pt-6">
          Connect Bybit to use this market selector.
        </CardContent>
      </Card>
    )
  }

  const depth = tick?.depth
  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-2xl font-bold">Bybit Scalping</h2>
        <div className="flex items-center gap-3">
          <Badge variant={isAuthenticated ? 'default' : isFallbackMode ? 'secondary' : 'destructive'}>
            {isAuthenticated ? 'Live feed' : isFallbackMode ? 'REST fallback' : 'Feed disconnected'}
          </Badge>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">One-Click</span>
            <Switch checked={armed} onCheckedChange={setArmed} aria-label="Arm Bybit trading" />
            <Badge variant={armed ? 'destructive' : 'secondary'}>{armed ? 'ARMED' : 'OFF'}</Badge>
          </div>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Market selection</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="space-y-1">
            <label className="text-sm text-muted-foreground">Bybit market</label>
            <Select
              value={category}
              onValueChange={(value) => {
                setCategory(value as BybitCategory)
                setInstrument(null)
                setQuery('')
              }}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {CATEGORIES.map((value) => (
                  <SelectItem key={value} value={value}>{CATEGORY_LABELS[value]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="relative space-y-1 md:col-span-2">
            <label className="text-sm text-muted-foreground">Instrument</label>
            <Input
              value={instrument ? instrument.symbol : query}
              placeholder={category === 'option' ? 'Search option contract' : 'Search symbol, e.g. BTCUSDT'}
              onChange={(event) => {
                setInstrument(null)
                setQuery(event.target.value)
              }}
            />
            {!instrument && query.trim().length >= 2 && (
              <div className="absolute z-10 mt-1 max-h-72 w-full overflow-auto rounded-md border bg-popover shadow-md">
                {instruments.map((item) => (
                  <button
                    type="button"
                    key={`${item.category}:${item.symbol}`}
                    className="block w-full px-3 py-2 text-left font-mono text-sm hover:bg-muted"
                    onClick={() => {
                      setInstrument(item)
                      setQuery('')
                    }}
                  >
                    {item.symbol}
                    <span className="ml-2 text-muted-foreground">
                      {item.base_coin && item.quote_coin
                        ? `${item.base_coin}/${item.quote_coin}`
                        : item.expiry
                          ? `${item.expiry} ${item.strike ?? ''} ${item.instrumenttype ?? ''}`
                          : item.name ?? ''}
                    </span>
                  </button>
                ))}
                {!isFetching && instruments.length === 0 && (
                  <div className="px-3 py-2 text-sm text-muted-foreground">No matching Bybit instruments</div>
                )}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {instrument && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center justify-between gap-2">
                <span>{instrument.symbol}</span>
                <Badge variant="outline">{CATEGORY_LABELS[category]}</Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <div><div className="text-xs text-muted-foreground">Last price</div><div className="font-mono text-xl">{tick?.ltp ?? '—'}</div></div>
                <div><div className="text-xs text-muted-foreground">Bid</div><div className="font-mono">{tick?.bid_price ?? '—'}</div></div>
                <div><div className="text-xs text-muted-foreground">Ask</div><div className="font-mono">{tick?.ask_price ?? '—'}</div></div>
                <div><div className="text-xs text-muted-foreground">Quote / settle</div><div className="font-mono">{instrument.quote_coin ?? instrument.settle_coin ?? '—'}</div></div>
              </div>
              <div className="grid gap-3 sm:grid-cols-[minmax(12rem,1fr)_auto_auto] sm:items-end">
                <div className="space-y-1">
                  <label className="text-sm text-muted-foreground">{amountLabel}</label>
                  <Input
                    inputMode="decimal"
                    step={instrument.qty_step ?? 'any'}
                    value={amount}
                    onChange={(event) => setAmount(event.target.value)}
                    placeholder={isSpot ? `Buy in ${instrument.quote_coin ?? 'quote coin'}` : 'Quantity'}
                  />
                  {isSpot && <p className="text-xs text-muted-foreground">Market Buy spends quote currency; Sell uses base-coin quantity.</p>}
                  {isSpot && appMode === 'live' && inventoryResponse?.data && (
                    <p className="text-xs text-muted-foreground">
                      Scalping-owned available: {inventoryResponse.data.available_quantity}{' '}
                      {inventoryResponse.data.base_coin}
                    </p>
                  )}
                </div>
                <Button disabled={!armed} onClick={() => placeOrder('BUY')}>Buy</Button>
                <Button variant="destructive" disabled={!armed} onClick={() => placeOrder('SELL')}>Sell</Button>
              </div>
              {orderError && <p role="alert" className="text-sm text-destructive">{orderError}</p>}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-base">Order book</CardTitle></CardHeader>
            <CardContent className="space-y-1 font-mono text-sm">
              <div className="grid grid-cols-2 text-xs text-muted-foreground"><span>Bid size / price</span><span className="text-right">Ask price / size</span></div>
              {Array.from({ length: 5 }, (_, index) => {
                const bid = depth?.buy?.[index]
                const ask = depth?.sell?.[index]
                return (
                  <div key={index} className="grid grid-cols-2">
                    <span className="text-green-600">{bid ? `${bid.quantity} / ${bid.price}` : '—'}</span>
                    <span className="text-right text-red-600">{ask ? `${ask.price} / ${ask.quantity}` : '—'}</span>
                  </div>
                )
              })}
              <p className="pt-2 text-xs text-muted-foreground">Quotes and depth update from the shared market-data feed.</p>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
