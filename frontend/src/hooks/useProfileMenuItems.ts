import { useEffect } from 'react'
import { profileMenuItems } from '@/config/navigation'
import { useBrokerStore } from '@/stores/brokerStore'

/**
 * Profile dropdown items filtered by broker capabilities.
 *
 * Single source of truth for menu gating, used by the shared Navbar AND the
 * standalone full-screen layouts (Playground, Historify, Historify Charts,
 * Flow Editor). Holdings are available to stock brokers and Bybit's unified
 * account coin-balance view; leverage remains capability-gated.
 */
export function useProfileMenuItems() {
  const { capabilities, isLoaded, fetchCapabilities } = useBrokerStore()

  // Standalone layouts can mount without the main app shell having fetched
  // capabilities yet — fetch on demand so gating never runs on stale null.
  useEffect(() => {
    if (!isLoaded) {
      fetchCapabilities()
    }
  }, [isLoaded, fetchCapabilities])

  return profileMenuItems.filter((item) => {
    if (item.href === '/leverage') return capabilities?.leverage_config === true
    if (item.href === '/holdings') {
      return capabilities?.broker_type !== 'crypto' || capabilities?.broker_name === 'bybit'
    }
    return true
  })
}
