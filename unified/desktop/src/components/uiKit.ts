// Shared inline-style constants + tiny formatting helpers for the coin-detail tabs.
// Salvaged from SendReceiveModal (styles) and TxFeed (relTime/numValue).

export const lbl: React.CSSProperties = {
  display: 'block',
  fontSize: 13,
  color: '#8a929b',
  margin: '10px 0 4px',
}

export const input: React.CSSProperties = {
  width: '100%',
  padding: '3px 12px',
  background: 'rgba(20,23,27,0.7)',
  border: '1px solid rgba(255,255,255,0.10)',
  borderRadius: 8,
  color: '#e6e6e6',
  font: 'inherit',
  boxSizing: 'border-box',
}

// Coin-tinted glass: retints with --coin* when the focused coin changes. The fill is a
// TRANSLUCENT coin tint over dark glass (so the effective surface is dark) — the label is
// therefore a bright silver-white on every coin, not the solid-bg auto-contrast color.
export const primaryBtn: React.CSSProperties = {
  padding: '4px 16px',
  background: 'rgba(var(--coin-rgb),0.22)',
  color: '#eef2f8',
  textShadow: '0 1px 2px rgba(0,0,0,0.4)',
  border: '1px solid var(--coin)',
  borderRadius: 10,
  fontWeight: 700,
  cursor: 'pointer',
  backdropFilter: 'blur(6px)',
  WebkitBackdropFilter: 'blur(6px)',
  boxShadow:
    '0 0 12px rgba(var(--coin-rgb),0.40), 0 0 22px rgba(var(--coin-rgb),0.22), inset 0 1px 0 rgba(255,255,255,0.18)',
}

export const secondaryBtn: React.CSSProperties = {
  padding: '4px 16px',
  background: 'rgba(255,255,255,0.06)',
  color: '#e6e6e6',
  border: '1px solid rgba(255,255,255,0.12)',
  borderRadius: 10,
  fontWeight: 600,
  cursor: 'pointer',
}

export const errBox: React.CSSProperties = {
  marginTop: 10,
  color: '#ef5350',
  fontSize: 12,
  background: 'rgba(239,83,80,0.10)',
  border: '1px solid rgba(239,83,80,0.35)',
  borderRadius: 6,
  padding: '8px 10px',
}

export const feeBox: React.CSSProperties = {
  marginTop: 14,
  background: 'rgba(20,23,27,0.7)',
  border: '1px solid rgba(255,255,255,0.10)',
  borderRadius: 8,
  padding: '10px 12px',
}

// Card surface used by every tab body — frosted glass.
export const card: React.CSSProperties = {
  background: 'rgba(34,38,43,0.58)',
  backdropFilter: 'blur(20px) saturate(170%) contrast(108%)',
  WebkitBackdropFilter: 'blur(20px) saturate(170%) contrast(108%)',
  border: '1px solid rgba(255,255,255,0.13)',
  borderRadius: 12,
  boxShadow:
    '0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.15), inset 0 -2px 4px rgba(0,0,0,0.18)',
  padding: 16,
}

// Mono code box for addresses / invoices.
export const codeBox: React.CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 12,
  wordBreak: 'break-all',
  background: 'rgba(20,23,27,0.7)',
  border: '1px solid rgba(255,255,255,0.10)',
  borderRadius: 8,
  padding: '10px 12px',
  color: '#cfd4da',
}

/** Parse a tx value string to a number; NaN when unknown/invalid. */
export function numValue(v: string | undefined): number {
  if (v === undefined || v === '') return NaN
  const n = Number(v)
  return Number.isFinite(n) ? n : NaN
}

/** Human-friendly relative time from a unix-seconds timestamp. */
export function relTime(ts: number | null | undefined): string {
  if (ts === null || ts === undefined || !Number.isFinite(ts)) return '—'
  const nowSec = Date.now() / 1000
  const diff = nowSec - ts
  if (diff < 0) return 'just now'
  const mins = Math.floor(diff / 60)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  if (months < 12) return `${months}mo ago`
  return `${Math.floor(months / 12)}y ago`
}

/** Signed, colored amount string + the color to render it in. */
export function signedAmount(v: string | undefined): { text: string; color: string } {
  const n = numValue(v)
  if (!Number.isFinite(n)) return { text: '—', color: '#8a929b' }
  const received = n >= 0
  const text = `${received ? '+' : '−'}${Math.abs(n).toLocaleString(undefined, {
    maximumFractionDigits: 8,
  })}`
  return { text, color: received ? '#4caf50' : '#ef5350' }
}
