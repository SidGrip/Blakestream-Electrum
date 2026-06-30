import { useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../store'
import { resolveCoinColor } from '../types'
import type { CoinMeta } from '../types'
import { formatAmount, formatFiat } from '../explorer'
import CoinIcon from './CoinIcon'

const MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace'

// Selects the coin; the decorative "Aurora Pulse" blooms the body gradient from the click point.
// No-ops if window/document are missing.
function triggerAuroraPulse(
  e: MouseEvent<HTMLButtonElement>,
  ticker: string,
  setSelected: (t: string) => void,
) {
  setSelected(ticker)
  if (typeof window === 'undefined' || typeof document === 'undefined') return
  const w = window.innerWidth || 1
  const h = window.innerHeight || 1
  const x = (e.clientX / w) * 100
  const y = (e.clientY / h) * 100
  document.documentElement.style.setProperty('--pulse-origin', `${x}% ${y}%`)
  document.body.classList.add('coin-switching')
  window.setTimeout(() => document.body.classList.remove('coin-switching'), 450)
}

// Fit number+suffix into maxLen by dropping trailing decimals (keeping >=2); returns text, full, trimmed.
function fitNumber(numStr: string, suffix: string, maxLen: number) {
  const sfx = suffix ? ` ${suffix}` : ''
  const full = `${numStr}${sfx}`
  if (full.length <= maxLen) return { text: full, full, trimmed: false }
  const dot = numStr.indexOf('.')
  if (dot < 0) return { text: full, full, trimmed: true } // integer too long; nothing to trim
  const intp = numStr.slice(0, dot)
  const frac = numStr.slice(dot + 1)
  for (let d = frac.length - 1; d >= 2; d--) {
    const cand = `${intp}.${frac.slice(0, d)}${sfx}`
    if (cand.length <= maxLen) return { text: cand, full, trimmed: true }
  }
  return { text: `${intp}.${frac.slice(0, 2)}${sfx}`, full, trimmed: true }
}

// Per-coin row balance — follows the global balanceView (coin amount / fiat / Lightning).
// Click cycles the view (same as the header toggle). Lightning shows send/receive stacked.
function RailBalance({ ticker }: { ticker: string }) {
  const portfolio = useStore((s) => s.portfolio)
  const balanceView = useStore((s) => s.balanceView)
  const priceApiConfigured = useStore((s) => s.priceApiConfigured)
  const fiatCurrency = useStore((s) => s.fiatCurrency)
  const cycleBalanceView = useStore((s) => s.cycleBalanceView)
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null)

  const c = portfolio?.coins?.[ticker]
  if (!c) return null
  const fiat = balanceView === 'fiat' && priceApiConfigured
  const click = (e: MouseEvent<HTMLSpanElement>) => { e.stopPropagation(); cycleBalanceView() }

  // Lightning: send/receive stacked vertically with in/out arrows, decimals trimmed to fit.
  if (balanceView === 'lightning') {
    const send = fitNumber(formatAmount(((c.ln_can_send_sat ?? 0) / 1e8).toFixed(8), ticker, false), '', 11)
    const recv = fitNumber(formatAmount(((c.ln_can_receive_sat ?? 0) / 1e8).toFixed(8), ticker, false), '', 11)
    return (
      <span
        role="button"
        title="Lightning can send / receive — click to cycle view"
        onClick={click}
        style={{
          flex: '0 0 auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 1,
          fontFamily: MONO, fontSize: 11, fontVariantNumeric: 'tabular-nums', maxWidth: 120, cursor: 'pointer',
        }}
      >
        <span title={`Can send ${send.full}`} style={{ color: '#cfd4da', whiteSpace: 'nowrap' }}>
          <span style={{ color: '#7fe0a3' }}>↑</span> {send.text}
        </span>
        <span title={`Can receive ${recv.full}`} style={{ color: '#8a929b', whiteSpace: 'nowrap' }}>
          <span style={{ color: '#e0a23a' }}>↓</span> {recv.text}
        </span>
      </span>
    )
  }

  const hasPending = Number(c.pending ?? 0) > 0
  const syncing = c.synced === false
  let display: string
  let full = ''
  let trimmed = false
  if (fiat) {
    display = c.value_fiat != null ? formatFiat(c.value_fiat, fiatCurrency) : 'no price'
  } else {
    const fit = fitNumber(formatAmount(c.amount, ticker, false), '', 13)
    display = fit.text
    full = fit.full
    trimmed = fit.trimmed
  }
  const onEnter = (e: MouseEvent<HTMLSpanElement>) => {
    if (!trimmed) return
    const r = e.currentTarget.getBoundingClientRect()
    setTip({ x: r.right + 8, y: r.top - 2, text: full })
  }
  return (
    <span
      role="button"
      title={fiat ? 'Fiat value — click to cycle view' : 'Click to cycle view: coin / Lightning / fiat'}
      onClick={click}
      onMouseEnter={onEnter}
      onMouseLeave={() => setTip(null)}
      style={{
        flex: '0 0 auto', marginLeft: 'auto', maxWidth: 120, fontFamily: MONO, fontSize: 11, fontVariantNumeric: 'tabular-nums',
        color: fiat ? '#cfd4da' : '#8a929b', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        cursor: 'pointer',
      }}
    >
      {syncing
        ? <span aria-hidden title="syncing…" style={{ color: '#8a929b', marginRight: 4 }}>•</span>
        : hasPending
          ? <span aria-hidden title="pending (unconfirmed)" style={{ color: '#e0a23a', marginRight: 4 }}>•</span>
          : null}
      {display}
      {tip && createPortal(
        <div
          style={{
            position: 'fixed', left: tip.x, top: tip.y, zIndex: 1000,
            background: 'rgba(20,23,27,0.92)',
            backdropFilter: 'blur(10px)', WebkitBackdropFilter: 'blur(10px)',
            border: '1px solid rgba(255,255,255,0.12)', borderRadius: 8,
            padding: '5px 9px', fontFamily: MONO, fontSize: 11, color: '#e6e6e6',
            whiteSpace: 'nowrap', boxShadow: '0 8px 24px rgba(0,0,0,0.45)', pointerEvents: 'none',
          }}
        >
          {tip.text}
        </div>,
        document.body,
      )}
    </span>
  )
}

// Left nav rail of coins; row click selects, balance click flips that coin to/from fiat.
export default function CoinSidebar() {
  const coins = useStore((s) => s.coins)
  const selected = useStore((s) => s.selected)
  const setSelected = useStore((s) => s.setSelected)
  const overrides = useStore((s) => s.coinColorOverrides)
  const coinStatus = useStore((s) => s.coinStatus)
  const startCoin = useStore((s) => s.startCoin)
  const balanceView = useStore((s) => s.balanceView)

  const entries: CoinMeta[] = coins ? Object.values(coins) : []

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        overflowY: 'auto',
      }}
    >
      <nav style={{ padding: '8px 0', flex: 1 }}>
        {entries.length === 0 ? (
          <div style={{ padding: '12px 16px', color: '#8a929b', fontSize: 12 }}>
            Loading coins…
          </div>
        ) : (
          entries.map((c) => {
            const color = resolveCoinColor(overrides, c.ticker)
            const active = selected === c.ticker
            const status = coinStatus[c.ticker] ?? 'running'
            const running = status === 'running'
            const dot = status === 'starting' ? color : status === 'failed' ? '#e0a23a' : '#6b7280'
            return (
              <button
                key={c.ticker}
                type="button"
                className={active ? 'rail-row is-active' : 'rail-row'}
                onClick={(e) => triggerAuroraPulse(e, c.ticker, setSelected)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  width: '100%',
                  padding: '10px 16px',
                  background: active
                    ? `linear-gradient(90deg, ${color}24, transparent)`
                    : 'transparent',
                  border: 'none',
                  borderLeft: active
                    ? `3px solid ${color}`
                    : '3px solid transparent',
                  boxShadow: active ? 'inset 4px 0 8px -4px var(--coin)' : 'none',
                  cursor: 'pointer',
                  textAlign: 'left',
                  color: '#e6e6e6',
                  font: 'inherit',
                  opacity: running ? 1 : 0.62,
                }}
              >
                <span style={{ display: 'inline-flex', filter: status === 'stopped' ? 'grayscale(1)' : undefined }}>
                  <CoinIcon ticker={c.ticker} size={26} />
                </span>
                <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                  <span style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
                    <span style={{ fontSize: 13, fontWeight: 600 }}>{c.ticker}</span>
                    {running && balanceView !== 'lightning' && <RailBalance ticker={c.ticker} />}
                  </span>
                  <span
                    style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      fontSize: 11,
                      color: '#8a929b',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {!running && (
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: dot, flex: '0 0 auto' }} />
                    )}
                    {c.coin_name ?? c.ticker}
                  </span>
                </span>
                {running && balanceView === 'lightning' && <RailBalance ticker={c.ticker} />}
                {/* Start affordance pinned to the right of the row so all the buttons line up vertically. */}
                {!running && (
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label={`Start ${c.ticker} wallet`}
                    onClick={(e) => { e.stopPropagation(); if (status !== 'starting') void startCoin(c.ticker) }}
                    style={{
                      flex: '0 0 auto',
                      fontSize: 11, fontWeight: 600, padding: '2px 10px', borderRadius: 6,
                      cursor: status === 'starting' ? 'default' : 'pointer',
                      border: '1px solid rgba(255,255,255,0.18)', background: 'rgba(255,255,255,0.05)',
                      color: '#cfd4da',
                    }}
                  >
                    {status === 'starting' ? 'Starting…' : status === 'failed' ? 'Retry' : 'Start'}
                  </span>
                )}
              </button>
            )
          })
        )}
      </nav>
    </div>
  )
}
