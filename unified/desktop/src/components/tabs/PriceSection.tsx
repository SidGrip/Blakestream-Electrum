import { useEffect, useState } from 'react'
import { useStore } from '../../store'
import {
  getFxCurrencies, setPriceEnabled, setPollSeconds,
  addPriceSource, updatePriceSource, removePriceSource, setPriceSourceEnabled,
  reorderPriceSources, testPriceSource, type PriceSourceSpec,
} from '../../api'
import type { PriceRole, PriceSource, PriceSourcesState } from '../../types'
import { card, input, primaryBtn, secondaryBtn, lbl, errBox } from '../uiKit'

const sectionTitle: React.CSSProperties = { fontSize: 13, fontWeight: 700, color: '#e6e6e6', margin: '0 0 4px' }
const sectionHint: React.CSSProperties = { fontSize: 11, color: '#8a929b', margin: '0 0 10px' }
const groupTitle: React.CSSProperties = { fontSize: 12, fontWeight: 700, color: '#cfd4da', margin: '14px 0 2px' }
const divider = '1px solid rgba(255,255,255,0.08)'

const ROLE_GROUPS: { role: PriceRole; title: string; hint: string }[] = [
  { role: 'coin_btc', title: 'Coin → BTC', hint: 'An API that returns a coin’s price in BTC.' },
  { role: 'btc_fiat', title: 'BTC → Fiat', hint: 'An API that returns the price of 1 BTC in your currency.' },
  { role: 'coin_fiat', title: 'Direct Coin → Fiat', hint: 'An API that prices a coin directly in your currency.' },
]

// Glass checkbox: only the box toggles (label is inert); fixed border-box + always-rendered ✓ so toggling never reflows the row.
function Check({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#cfd4da', userSelect: 'none' }}>
      <button
        type="button"
        role="checkbox"
        aria-checked={on}
        aria-label={label || 'toggle'}
        onClick={() => onChange(!on)}
        style={{
          flex: '0 0 auto', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          width: 16, height: 16, padding: 0, borderRadius: 5, cursor: 'pointer', boxSizing: 'border-box',
          border: on ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.18)',
          background: on ? 'rgba(var(--coin-rgb),0.22)' : 'rgba(255,255,255,0.04)',
          boxShadow: on ? '0 0 8px rgba(var(--coin-rgb),0.30)' : 'none',
          color: '#eef2f8', fontSize: 11, lineHeight: 1,
        }}
      >
        <span aria-hidden style={{ visibility: on ? 'visible' : 'hidden' }}>✓</span>
      </button>
      {label && <span>{label}</span>}
    </span>
  )
}

const miniBtn: React.CSSProperties = {
  padding: '3px 9px', fontSize: 11, borderRadius: 6, border: '1px solid #2e333a',
  background: '#1a1d21', color: '#cfd4da', cursor: 'pointer',
}

// One source row: enable, reorder within role, Test, inline Edit, Remove. A blank source starts open so it can be filled in.
function SourceRow({
  src, isFirst, isLast, coin, fiat, onState,
}: {
  src: PriceSource
  isFirst: boolean
  isLast: boolean
  coin: string
  fiat: string
  onState: (st: PriceSourcesState) => void
}) {
  const blank = !src.label && !src.urlTemplate
  const [open, setOpen] = useState(blank)
  const [draft, setDraft] = useState<PriceSourceSpec>({
    role: src.role, kind: src.kind, label: src.label, urlTemplate: src.urlTemplate,
    jsonPath: src.jsonPath, ids: src.ids, apiKeyHeader: src.apiKeyHeader,
  })
  const [apiKey, setApiKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const beginEdit = () => {
    setDraft({
      role: src.role, kind: src.kind, label: src.label, urlTemplate: src.urlTemplate,
      jsonPath: src.jsonPath, ids: src.ids, apiKeyHeader: src.apiKeyHeader,
    })
    setApiKey('')
    setClearKey(false)
    setErr(null)
    setOpen(true)
  }

  const run = async (p: Promise<PriceSourcesState>) => {
    setBusy(true)
    setErr(null)
    try {
      onState(await p)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const save = () =>
    run(updatePriceSource(src.id, { ...draft, id: src.id, apiKey: apiKey || undefined, clearApiKey: clearKey || undefined }))
      .then(() => setOpen(false))

  const doTest = async () => {
    setBusy(true)
    setErr(null)
    setTest(null)
    try {
      const spec: PriceSourceSpec = open
        ? { ...draft, id: src.id, apiKey: apiKey || undefined }
        : { role: src.role, kind: src.kind, urlTemplate: src.urlTemplate, jsonPath: src.jsonPath, ids: src.ids, apiKeyHeader: src.apiKeyHeader, id: src.id }
      const r = await testPriceSource(spec, coin, fiat)
      setTest(r.ok
        ? { ok: true, text: `${r.role === 'btc_fiat' ? 'BTC' : r.ticker} → ${r.value}` }
        : { ok: false, text: 'No value returned (check link / JSON path / key / host).' })
    } catch (e) {
      setTest({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ borderTop: divider, padding: '8px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Check on={src.enabled} onChange={(v) => run(setPriceSourceEnabled(src.id, v))} label="" />
        <span style={{ fontSize: 12, color: '#e6e6e6', fontWeight: 600, minWidth: 0, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {src.label || <span style={{ color: '#8a929b', fontWeight: 400, fontStyle: 'italic' }}>(unnamed)</span>}
          {src.hasApiKey && <span style={{ color: '#8a929b', fontWeight: 400, marginLeft: 6 }}>· key {src.apiKeyMask}</span>}
        </span>
        <button type="button" style={{ ...miniBtn, opacity: isFirst ? 0.4 : 1 }} disabled={isFirst || busy} title="Higher priority" onClick={() => run(reorderMove(src.id, -1))}>▲</button>
        <button type="button" style={{ ...miniBtn, opacity: isLast ? 0.4 : 1 }} disabled={isLast || busy} title="Lower priority" onClick={() => run(reorderMove(src.id, +1))}>▼</button>
        <button type="button" style={miniBtn} disabled={busy} onClick={doTest}>Test</button>
        <button type="button" style={miniBtn} disabled={busy} onClick={open ? () => setOpen(false) : beginEdit}>{open ? 'Close' : 'Edit'}</button>
        <RemoveBtn onConfirm={() => run(removePriceSource(src.id))} />
      </div>

      {test && (
        <div style={{ fontSize: 11, marginTop: 6, color: test.ok ? '#7fe0a3' : '#e0a23a' }}>
          {test.ok ? '✓ ' : '⚠ '}{test.text}
        </div>
      )}

      {open && (
        <div style={{ marginTop: 8, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 14px', alignItems: 'start' }}>
          <Field label="Name" value={draft.label ?? ''} onChange={(v) => setDraft((d) => ({ ...d, label: v }))} placeholder="A short name for this API" />
          <Field label="Link (URL)" value={draft.urlTemplate ?? ''} onChange={(v) => setDraft((d) => ({ ...d, urlTemplate: v }))} placeholder="https://…/{coin}?vs={fiat_lower}" mono />
          <Field label="JSON path" value={draft.jsonPath ?? ''} onChange={(v) => setDraft((d) => ({ ...d, jsonPath: v }))} placeholder="data.{coin}.price" mono />
          <Field label="ids (optional, fills {ids})" value={draft.ids ?? ''} onChange={(v) => setDraft((d) => ({ ...d, ids: v }))} placeholder="" mono />
          <Field label="API key header (optional)" value={draft.apiKeyHeader ?? ''} onChange={(v) => setDraft((d) => ({ ...d, apiKeyHeader: v }))} placeholder="X-API-Key" mono />
          <div>
            <label style={lbl}>API key{src.hasApiKey ? ` (stored ${src.apiKeyMask} — leave blank to keep)` : ''}</label>
            <input type="password" style={input} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={src.hasApiKey ? '••••••••' : 'paste key (optional)'} autoComplete="off" disabled={clearKey} />
          </div>
          {src.hasApiKey && (
            <div style={{ gridColumn: '1 / -1' }}>
              <Check on={clearKey} onChange={setClearKey} label="Clear stored key" />
            </div>
          )}
          <div style={{ gridColumn: '1 / -1', fontSize: 10, color: '#8a929b' }}>
            Placeholders: {'{coin} {coin_lower} {fiat} {fiat_lower} {ids}'} — substituted into the link and JSON path.
          </div>
          <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8, marginTop: 4 }}>
            <button type="button" style={{ ...primaryBtn, flex: 'none' }} disabled={busy} onClick={() => void save()}>Save</button>
            <button type="button" style={secondaryBtn} disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </div>
      )}

      {err && <div style={{ ...errBox, marginTop: 6 }}>{err}</div>}
    </div>
  )
}

// Move a source one slot up/down among its same-role siblings, returning the API call.
function reorderMove(id: string, dir: -1 | 1): Promise<PriceSourcesState> {
  const ps = useStore.getState().priceSources
  const srcs = ps?.sources ?? []
  const i = srcs.findIndex((s) => s.id === id)
  if (i < 0) return Promise.resolve(ps as PriceSourcesState)
  const role = srcs[i].role
  let j = i + dir
  while (j >= 0 && j < srcs.length && srcs[j].role !== role) j += dir
  if (j < 0 || j >= srcs.length) return Promise.resolve(ps as PriceSourcesState)
  const order = srcs.map((s) => s.id)
  ;[order[i], order[j]] = [order[j], order[i]]
  return reorderPriceSources(order)
}

function Field({ label, value, onChange, placeholder, mono, full }: {
  label: string; value: string; onChange: (v: string) => void
  placeholder?: string; mono?: boolean; full?: boolean
}) {
  return (
    <div style={full ? { gridColumn: '1 / -1' } : undefined}>
      <label style={lbl}>{label}</label>
      <input
        style={{ ...input, fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined, fontSize: mono ? 12 : undefined }}
        value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} autoComplete="off" spellCheck={false}
      />
    </div>
  )
}

// Two-click remove (✕ then confirm), per the app's destructive-confirm pattern.
function RemoveBtn({ onConfirm }: { onConfirm: () => void }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = window.setTimeout(() => setArmed(false), 4000)
    return () => clearTimeout(t)
  }, [armed])
  return (
    <button
      type="button"
      onClick={() => (armed ? onConfirm() : setArmed(true))}
      title={armed ? 'Click again to remove' : 'Remove source'}
      style={{
        ...miniBtn,
        color: armed ? '#ffd7c7' : '#ef5350',
        border: armed ? '1px solid #ef6a35' : '1px solid #2e333a',
        background: armed ? 'rgba(239,106,53,0.18)' : '#1a1d21',
      }}
    >
      {armed ? 'Sure?' : '✕'}
    </button>
  )
}

// Settings → Price & currency: global config for all coins. User-supplied price APIs only (none hardwired); first enabled source per role wins.
export default function PriceSection({ coin }: { coin: string }) {
  const ps = useStore((s) => s.priceSources)
  const fiatCurrency = useStore((s) => s.fiatCurrency)
  const applyPriceState = useStore((s) => s.applyPriceState)
  const loadPriceSources = useStore((s) => s.loadPriceSources)
  const setFiat = useStore((s) => s.setFiatCurrency)

  const [currencies, setCurrencies] = useState<string[]>([])
  const [applyMsg, setApplyMsg] = useState('')
  const [poll, setPoll] = useState('30')
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    void loadPriceSources()
    getFxCurrencies().then((r) => setCurrencies(r.currencies ?? [])).catch(() => {})
  }, [loadPriceSources])

  // Seed the poll field from the stored value when config loads/changes.
  useEffect(() => {
    if (ps?.poll_seconds) setPoll(String(ps.poll_seconds))
  }, [ps?.poll_seconds])

  const savePoll = () => {
    const n = parseInt(poll, 10)
    if (Number.isFinite(n) && n >= 5) void run(setPollSeconds(n))
  }

  const run = async (p: Promise<PriceSourcesState>, after?: () => void) => {
    setErr(null)
    try {
      applyPriceState(await p)
      after?.()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const addSource = (role: PriceRole) =>
    run(addPriceSource({ role, kind: 'http_template', enabled: true, label: '', urlTemplate: '', jsonPath: '' }))

  const applyToAll = async () => {
    await run(setPriceEnabled(true))
    setApplyMsg('Applied to all 6 coins ✓')
    window.setTimeout(() => setApplyMsg(''), 2500)
  }

  const fiatOptions = currencies.length ? currencies : [fiatCurrency, 'USD', 'EUR', 'GBP']

  return (
    <section style={{ ...card }}>
      <h3 style={{ ...sectionTitle, margin: '0 0 8px' }}>Price &amp; currency</h3>
      <div>
      <p style={sectionHint}>
        Applies to all 6 coins. Add your own price APIs below. Prices fetch in the background, so
        balances never wait on them.
      </p>
      {err && <div style={errBox}>{err}</div>}

      {/* Currency + fiat toggle + apply-to-all in one center-aligned row so the checkbox lines up with the dropdown */}
      <div style={{ marginBottom: 12 }}>
        <label style={lbl}>Display currency</label>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
          <select style={{ ...input, appearance: 'auto', width: 160, flex: 'none' }} value={fiatCurrency} onChange={(e) => setFiat(e.target.value)}>
            {Array.from(new Set(fiatOptions)).sort().map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#cfd4da' }} title="How often prices are re-fetched from your APIs (applies to all coins)">
            Refresh API every
            <input
              type="number" min={5} step={5} value={poll}
              onChange={(e) => setPoll(e.target.value)}
              onBlur={savePoll}
              onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }}
              style={{ ...input, width: 64, textAlign: 'center', flex: 'none' }}
            />
            s
          </label>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
            {applyMsg && <span style={{ fontSize: 11, color: '#7fe0a3' }}>{applyMsg}</span>}
            <button type="button" style={{ ...secondaryBtn }} onClick={() => void applyToAll()}>Apply to all coins</button>
          </div>
        </div>
      </div>

      {/* Source groups — each lets the user add their own API for that role */}
      {ROLE_GROUPS.map((g) => {
        const groupSources = (ps?.sources ?? []).filter((s) => s.role === g.role)
        return (
          <div key={g.role}>
            <div style={groupTitle}>{g.title}</div>
            <p style={{ ...sectionHint, margin: '0 0 4px' }}>{g.hint}</p>
            {groupSources.map((s, i) => (
              <SourceRow
                key={s.id} src={s} coin={coin} fiat={fiatCurrency}
                isFirst={i === 0} isLast={i === groupSources.length - 1}
                onState={applyPriceState}
              />
            ))}
            <div style={{ marginTop: 6 }}>
              <button type="button" style={miniBtn} onClick={() => void addSource(g.role)}>+ Add price API</button>
            </div>
          </div>
        )
      })}
      </div>
    </section>
  )
}
