// App-level display preferences persisted in localStorage: per-coin explorer base URL
// and global base-unit/amount formatting. Settings edits; History/Addresses/CoinDetail read.

// ---- block explorer ----

// localStorage key for a coin's explorer base URL override.
function explorerKey(coin: string): string {
  return `blakestream.explorer.${coin}`
}

/** The default explorer base for a coin: https://explorer.blakestream.io/<ticker>/. */
export function defaultExplorerBase(coin: string): string {
  return `https://explorer.blakestream.io/${coin.toLowerCase()}/`
}

/** The effective explorer base for a coin (stored override or the default). */
export function explorerBase(coin: string): string {
  try {
    const stored = localStorage.getItem(explorerKey(coin))
    if (stored && stored.trim()) return stored.trim()
  } catch {
    /* localStorage unavailable; fall through to default */
  }
  return defaultExplorerBase(coin)
}

/** Persist a coin's explorer base override. Empty/whitespace clears it (back to default). */
export function setExplorerBase(coin: string, base: string): void {
  try {
    const v = base.trim()
    if (v) localStorage.setItem(explorerKey(coin), v)
    else localStorage.removeItem(explorerKey(coin))
  } catch {
    /* localStorage unavailable; nothing to persist */
  }
}

// The coin page reads ?tx=/?addr= on load, injects the detail view, then strips the query.
// Append the id as a query param (not a /tx/<id> path, which opens the standalone full page).
function explorerQuery(base: string, key: string, value: string): string {
  const root = base.replace(/\/+$/, '') // drop trailing slash so we never emit "/<coin>/?tx="
  const sep = root.includes('?') ? '&' : '?'
  return `${root}${sep}${key}=${encodeURIComponent(value)}`
}

/** Explorer URL for a transaction id — coin page + ?tx=, injected on load. */
export function explorerTxUrl(coin: string, txid: string): string {
  return explorerQuery(explorerBase(coin), 'tx', txid)
}

/** Explorer URL for an address — coin page + ?addr=, injected on load. */
export function explorerAddrUrl(coin: string, addr: string): string {
  return explorerQuery(explorerBase(coin), 'addr', addr)
}

// ---- base unit / amount formatting ----

export type BaseUnit = 'coin' | 'milli' | 'sat'

const BASEUNIT_KEY = 'blakestream.baseunit'

export const BASE_UNIT_OPTIONS: { value: BaseUnit; label: string }[] = [
  { value: 'coin', label: 'Whole coin (8 decimals)' },
  { value: 'milli', label: 'milli (3 decimals)' },
  { value: 'sat', label: 'sat (0 decimals)' },
]

/** The chosen base unit (defaults to whole coin). */
export function getBaseUnit(): BaseUnit {
  try {
    const v = localStorage.getItem(BASEUNIT_KEY)
    if (v === 'milli' || v === 'sat') return v
  } catch {
    /* localStorage unavailable */
  }
  return 'coin'
}

/** Persist the chosen base unit. */
export function setBaseUnit(unit: BaseUnit): void {
  try {
    localStorage.setItem(BASEUNIT_KEY, unit)
  } catch {
    /* localStorage unavailable */
  }
}

// Optional thousand separators for large balances; off by default.
const THOUSANDSEP_KEY = 'blakestream.thousandsep'
export function getThousandSep(): boolean {
  try {
    return localStorage.getItem(THOUSANDSEP_KEY) === '1'
  } catch {
    return false
  }
}
export function setThousandSep(on: boolean): void {
  try {
    localStorage.setItem(THOUSANDSEP_KEY, on ? '1' : '0')
  } catch {
    /* localStorage unavailable */
  }
}

// Render a fixed-decimal string from a whole-coin amount, scaled by the unit, with optional grouping.
function scaled(amount: number, unit: BaseUnit): string {
  const useGrouping = getThousandSep()
  if (unit === 'sat') return Math.round(amount * 1e8).toLocaleString('en-US', { useGrouping })
  if (unit === 'milli') return (amount * 1e3).toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3, useGrouping })
  return amount.toLocaleString('en-US', { minimumFractionDigits: 8, maximumFractionDigits: 8, useGrouping })
}

// ---- fiat display preference ----
// Backend (price_sources.json) is authoritative; mirrored to localStorage so the Balance header
// formats synchronously on first paint and the toggle flips without a /portfolio round-trip.

const FIAT_CURRENCY_KEY = 'blakestream.fiatCurrency'

/** The chosen display fiat (ISO 4217), defaulting to USD. */
export function getFiatCurrency(): string {
  try {
    const v = localStorage.getItem(FIAT_CURRENCY_KEY)
    if (v && /^[A-Z]{3}$/.test(v)) return v
  } catch {
    /* localStorage unavailable */
  }
  return 'USD'
}

export function setFiatCurrency(code: string): void {
  try {
    localStorage.setItem(FIAT_CURRENCY_KEY, code)
  } catch {
    /* nothing to persist */
  }
}

/** Format a fiat amount in the given currency; non-numeric/empty input returns "—" (no price). */
export function formatFiat(value: string | number | null | undefined, currency: string): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return '—'
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: n !== 0 && Math.abs(n) < 1 ? 6 : 2,
    }).format(n)
  } catch {
    // Unknown currency code: fall back to a plain number + code suffix.
    return `${n.toLocaleString(undefined, { maximumFractionDigits: 6 })} ${currency}`
  }
}

/** Suffix (ticker) for the chosen unit, e.g. BLC / mBLC / sat. */
export function unitSuffix(coin: string, unit: BaseUnit = getBaseUnit()): string {
  if (unit === 'sat') return 'sat'
  if (unit === 'milli') return `m${coin}`
  return coin
}

/** Format a whole-coin amount under the chosen base unit + suffix; non-numeric input passes through. */
export function formatAmount(
  amountStr: string | undefined | null, coin: string, withUnit = true,
): string {
  if (amountStr === undefined || amountStr === null || amountStr === '') return '—'
  const n = Number(amountStr)
  if (!Number.isFinite(n)) return amountStr
  const unit = getBaseUnit()
  const num = scaled(n, unit)
  return withUnit ? `${num} ${unitSuffix(coin, unit)}` : num
}
