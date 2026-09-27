import { describe, expect, it, vi } from 'vitest'
import {
  APP_TIME_ZONE,
  convertScheduleDate,
  convertScheduleTime,
  convertWeeklySchedule,
  formatAppDate,
  formatAppDateTime,
} from './dateTime'

describe('application date and time localization', () => {
  it('uses Kazakhstan as the default display timezone', () => {
    expect(APP_TIME_ZONE).toBe('Asia/Almaty')
    expect(formatAppDateTime('2026-09-26T00:00:00Z')).toContain('05:00')
  })

  it('does not shift date-only values', () => {
    expect(formatAppDate('2026-09-26')).toContain('Sep 26, 2026')
  })

  it('converts legacy India schedule clocks to Kazakhstan time', () => {
    expect(convertScheduleTime('09:15', 'Asia/Kolkata')).toBe('08:45')
  })

  it('converts recurring schedule weekdays when a clock crosses midnight', () => {
    expect(convertWeeklySchedule('00:15', ['mon'], 'Asia/Kolkata')).toEqual({
      time: '23:45',
      days: ['sun'],
    })
  })

  it('converts date-only schedule midnight to the corresponding application date', () => {
    expect(convertScheduleDate('2026-01-05', 'Asia/Kolkata')).toBe('2026-01-04')
  })

  it('uses the timezone supplied by the server runtime config', async () => {
    const originalTimezone = window.OPENALGO_APP_TIMEZONE
    window.OPENALGO_APP_TIMEZONE = 'UTC'
    vi.resetModules()
    try {
      const { APP_TIME_ZONE } = await import('./dateTime')
      expect(APP_TIME_ZONE).toBe('UTC')
    } finally {
      window.OPENALGO_APP_TIMEZONE = originalTimezone
      vi.resetModules()
    }
  })
})
