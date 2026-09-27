declare global {
  interface Window {
    OPENALGO_APP_TIMEZONE?: string
  }
}

export const APP_TIME_ZONE =
  typeof window !== 'undefined' && window.OPENALGO_APP_TIMEZONE
    ? window.OPENALGO_APP_TIMEZONE
    : 'Asia/Almaty'
export const APP_LOCALE = 'en-KZ'
export const LEGACY_SCHEDULE_TIME_ZONE = 'Asia/Kolkata'

type DateInput = Date | number | string | null | undefined

function parseDateInput(value: DateInput): Date | null {
  if (value == null || value === '') return null
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  if (typeof value === 'number') {
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? null : date
  }

  const dateOnly = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (dateOnly) {
    const date = new Date(
      Date.UTC(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]), 12)
    )
    return Number.isNaN(date.getTime()) ? null : date
  }

  const dateTime = value.match(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/)
  const normalized = dateTime && !/(Z|[+-]\d{2}:?\d{2})$/i.test(value)
    ? `${value.replace(' ', 'T')}Z`
    : value
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatAppDateTime(
  value: DateInput,
  options: Intl.DateTimeFormatOptions = {}
): string {
  const date = parseDateInput(value)
  if (!date) return value == null ? '—' : String(value)
  const hasExplicitDateFields = ['weekday', 'era', 'year', 'month', 'day'].some(
    (field) => options[field as keyof Intl.DateTimeFormatOptions] !== undefined
  )
  const hasExplicitTimeFields = [
    'hour',
    'minute',
    'second',
    'fractionalSecondDigits',
    'dayPeriod',
  ].some((field) => options[field as keyof Intl.DateTimeFormatOptions] !== undefined)
  return new Intl.DateTimeFormat(APP_LOCALE, {
    ...(hasExplicitDateFields ? {} : { dateStyle: 'medium' as const }),
    ...(hasExplicitTimeFields ? {} : { timeStyle: 'short' as const }),
    hour12: false,
    ...options,
    timeZone: options.timeZone ?? APP_TIME_ZONE,
  }).format(date)
}

export function formatAppDate(
  value: DateInput,
  options: Intl.DateTimeFormatOptions = {}
): string {
  const date = parseDateInput(value)
  if (!date) return value == null ? '—' : String(value)
  const hasExplicitDateFields = ['weekday', 'era', 'year', 'month', 'day'].some(
    (field) => options[field as keyof Intl.DateTimeFormatOptions] !== undefined
  )
  return new Intl.DateTimeFormat(APP_LOCALE, {
    ...(hasExplicitDateFields ? {} : { dateStyle: 'medium' as const }),
    ...options,
    timeZone: options.timeZone ?? APP_TIME_ZONE,
  }).format(date)
}

export function formatAppTime(
  value: DateInput,
  options: Intl.DateTimeFormatOptions = {}
): string {
  const date = parseDateInput(value)
  if (!date) return value == null ? '—' : String(value)
  return new Intl.DateTimeFormat(APP_LOCALE, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    ...options,
    timeZone: options.timeZone ?? APP_TIME_ZONE,
  }).format(date)
}

export function appTodayDateInput(offsetYears = 0): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: APP_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const valueFor = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? ''
  const date = new Date(
    Date.UTC(Number(valueFor('year')), Number(valueFor('month')) - 1, Number(valueFor('day')))
  )
  date.setUTCFullYear(date.getUTCFullYear() - offsetYears)
  return date.toISOString().slice(0, 10)
}

const WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']
const SCHEDULE_ANCHOR_MONDAY = Date.UTC(2026, 0, 5)

function wallClockParts(instant: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(instant)
  const valueFor = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value)
  return {
    year: valueFor('year'),
    month: valueFor('month'),
    day: valueFor('day'),
    hour: valueFor('hour'),
    minute: valueFor('minute'),
  }
}

function instantForWallClock(
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
  timeZone: string
): Date {
  const requestedWall = Date.UTC(year, month - 1, day, hour, minute)
  let instant = requestedWall
  for (let i = 0; i < 3; i += 1) {
    const actualWall = wallClockParts(new Date(instant), timeZone)
    instant +=
      requestedWall -
      Date.UTC(
        actualWall.year,
        actualWall.month - 1,
        actualWall.day,
        actualWall.hour,
        actualWall.minute
      )
  }
  return new Date(instant)
}

/** Convert an HH:mm clock value without changing the represented instant. */
export function convertScheduleTime(
  value: string,
  fromTimeZone: string,
  toTimeZone: string = APP_TIME_ZONE
): string {
  const match = value.match(/^(\d{1,2}):(\d{2})$/)
  if (!match) return value
  const instant = instantForWallClock(
    2026,
    1,
    5,
    Number(match[1]),
    Number(match[2]),
    fromTimeZone
  )
  const converted = wallClockParts(instant, toTimeZone)
  return `${String(converted.hour).padStart(2, '0')}:${String(converted.minute).padStart(2, '0')}`
}

/** Convert a local calendar date while preserving its local-midnight instant. */
export function convertScheduleDate(
  value: string,
  fromTimeZone: string,
  toTimeZone: string = APP_TIME_ZONE
): string {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return value
  const instant = instantForWallClock(
    Number(match[1]),
    Number(match[2]),
    Number(match[3]),
    0,
    0,
    fromTimeZone
  )
  const converted = wallClockParts(instant, toTimeZone)
  return `${String(converted.year).padStart(4, '0')}-${String(converted.month).padStart(2, '0')}-${String(
    converted.day
  ).padStart(2, '0')}`
}

/** Convert a weekly schedule, including weekday rollover at midnight. */
export function convertWeeklySchedule(
  value: string,
  days: string[],
  fromTimeZone: string,
  toTimeZone: string = APP_TIME_ZONE
): { time: string; days: string[] } {
  const match = value.match(/^(\d{1,2}):(\d{2})$/)
  if (!match) return { time: value, days }
  const sourceDays = days.length ? days : ['mon']
  const convertedTimes = new Set<string>()
  const convertedDays = new Set<string>()
  for (const sourceDay of sourceDays) {
    const sourceIndex = WEEKDAYS.indexOf(sourceDay.toLowerCase())
    if (sourceIndex < 0) continue
    const mondayOffset = (sourceIndex + 6) % 7
    const sourceDate = new Date(SCHEDULE_ANCHOR_MONDAY + mondayOffset * 86_400_000)
    const instant = instantForWallClock(
      sourceDate.getUTCFullYear(),
      sourceDate.getUTCMonth() + 1,
      sourceDate.getUTCDate(),
      Number(match[1]),
      Number(match[2]),
      fromTimeZone
    )
    const converted = wallClockParts(instant, toTimeZone)
    convertedTimes.add(
      `${String(converted.hour).padStart(2, '0')}:${String(converted.minute).padStart(2, '0')}`
    )
    const convertedWeekday = new Date(
      Date.UTC(converted.year, converted.month - 1, converted.day)
    ).getUTCDay()
    convertedDays.add(WEEKDAYS[convertedWeekday])
  }
  if (!convertedDays.size) return { time: value, days }
  const convertedTime =
    convertedTimes.size === 1 ? [...convertedTimes][0] : convertScheduleTime(value, fromTimeZone, toTimeZone)
  return {
    time: convertedTime,
    days: [...convertedDays].sort((a, b) => WEEKDAYS.indexOf(a) - WEEKDAYS.indexOf(b)),
  }
}
