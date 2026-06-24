import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useStore } from '../../store'
import { getAddresses, setLabel } from '../../api'
import type { AddressRow } from '../../types'
import { Th, Td } from '../tableCells'
import { card } from '../uiKit'
import EditableLabel from '../EditableLabel'
import { explorerAddrUrl, formatAmount } from '../../explorer'

// Pins column headers while the list scrolls beneath.
const thSticky: CSSProperties = { position: 'sticky', top: 0, zIndex: 1, background: '#23272c' }

type Filter = 'receiving' | 'change' | 'all'

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'receiving', label: 'Receiving' },
  { value: 'change', label: 'Change' },
  { value: 'all', label: 'All' },
]

// Segmented-control pill; active segment is coin-tinted.
function Segment({
  value, active, onClick,
}: { value: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '5px 14px',
        fontSize: 12,
        fontWeight: 600,
        cursor: active ? 'default' : 'pointer',
        borderRadius: 8,
        border: active ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.12)',
        background: active ? 'rgba(var(--coin-rgb),0.20)' : 'rgba(255,255,255,0.04)',
        color: active ? '#eef2f8' : '#cfd4da',
        boxShadow: active ? '0 0 10px rgba(var(--coin-rgb),0.30)' : 'none',
        transition: 'background .2s, border-color .2s, box-shadow .2s, color .2s',
      }}
    >
      {value}
    </button>
  )
}

// Per-coin address list (Address|Label|Balance|Used) with copy; Receiving|Change|All filter.
// In Receiving the first unused row is flagged as the recommended next address.
export default function AddressesTab({ coin }: { coin: string }) {
  const storeAddresses = useStore((s) => s.coinStates[coin]?.addresses)
  const setCoinAddresses = useStore((s) => s.setCoinAddresses)
  // Backend returns [] for all filters mid-sync; re-fetch on synced so un-cached change/all self-heal.
  const synced = useStore((s) => s.portfolio?.coins?.[coin]?.synced === true)
  const [filter, setFilter] = useState<Filter>('receiving')
  const [rows, setRows] = useState<AddressRow[]>(storeAddresses ?? [])
  const [loading, setLoading] = useState(false)
  const [hideUsed, setHideUsed] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  // null = natural derivation order; balance starts high→low, label A→Z. Address/Used aren't sortable.
  const [sort, setSort] = useState<{ key: 'balance' | 'label'; dir: 'asc' | 'desc' } | null>(null)

  // 'receiving' mirrors the warm store cache for instant paint + self-heal on poll refresh.
  // Only receiving is cached; change/all load their own scoped list in the fetch below.
  useEffect(() => {
    if (filter === 'receiving') setRows(storeAddresses ?? [])
  }, [coin, filter, storeAddresses])

  // Refresh from the daemon on coin/filter change. Backend returns [] mid-sync (not when truly empty),
  // so for 'receiving' an empty result keeps the cache and a non-empty one warms it for instant reopen.
  useEffect(() => {
    let live = true
    setLoading(true)
    // change/all aren't store-backed: clear stale rows up front so they don't linger under the new
    // header until the fetch resolves. (Receiving repaints via the mirror effect's cache.)
    if (filter !== 'receiving') setRows([])
    getAddresses(coin, filter)
      .then((r) => {
        if (!live) return
        const next = r.addresses ?? []
        if (filter === 'receiving') {
          if (next.length) setCoinAddresses(coin, next)
        } else {
          setRows(next)
        }
      })
      .catch(() => { /* keep the current rows on a transient error */ })
      .finally(() => { if (live) setLoading(false) })
    return () => {
      live = false
    }
    // storeAddresses is handled by the mirror effect; setCoinAddresses is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coin, filter, synced])

  const copy = (addr: string) => {
    void navigator.clipboard?.writeText(addr)
    setCopied(addr)
    setTimeout(() => setCopied((c) => (c === addr ? null : c)), 1500)
  }

  // Persist a label, then patch local rows and the receiving cache so a store repaint keeps it.
  // Read the LATEST cache (not the render snapshot) so a concurrent poll refresh isn't clobbered.
  const saveLabel = async (address: string, next: string) => {
    await setLabel(coin, address, next)
    const apply = (rs: AddressRow[]) => rs.map((r) => (r.address === address ? { ...r, label: next } : r))
    setRows(apply)
    const cached = useStore.getState().coinStates[coin]?.addresses
    if (cached?.some((r) => r.address === address)) setCoinAddresses(coin, apply(cached))
  }

  // Recommended next receive address: first unused row, Receiving view only.
  const nextReceive =
    filter === 'receiving' ? rows.find((a) => !a.used)?.address ?? null : null

  // Hide-used filter then sort on a copy (never mutate `rows`); empty labels always sink to the bottom.
  const visible = useMemo(() => {
    const base = hideUsed ? rows.filter((a) => !a.used) : rows
    if (!sort) return base
    const dir = sort.dir
    return [...base].sort((a, b) => {
      if (sort.key === 'balance') {
        const c = (Number(a.balance) || 0) - (Number(b.balance) || 0)
        return dir === 'asc' ? c : -c
      }
      const la = a.label || '', lb = b.label || ''
      if (!la && !lb) return 0
      if (!la) return 1
      if (!lb) return -1
      const c = la.localeCompare(lb)
      return dir === 'asc' ? c : -c
    })
  }, [rows, hideUsed, sort])

  // Header click cycles: natural dir (balance high→low, label A→Z) → reverse → off.
  const cycleSort = (key: 'balance' | 'label') => {
    const primary = key === 'balance' ? 'desc' : 'asc'
    setSort((s) => {
      if (!s || s.key !== key) return { key, dir: primary }
      if (s.dir === primary) return { key, dir: primary === 'desc' ? 'asc' : 'desc' }
      return null
    })
  }
  const sortInd = (key: 'balance' | 'label') => (
    <span style={{ marginLeft: 4, fontSize: 9, opacity: sort?.key === key ? 1 : 0.4, color: sort?.key === key ? 'var(--coin)' : 'inherit' }}>
      {sort?.key === key ? (sort.dir === 'asc' ? '▲' : '▼') : '↕'}
    </span>
  )

  return (
    <section
      style={{
        ...card,
        padding: 0,
        overflow: 'hidden',
        // Bounded flex column: toolbar fixed, only the list below scrolls.
        flex: '0 1 auto',
        minHeight: 0,
        maxHeight: '100%',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div
        style={{
          flex: '0 0 auto',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 10,
          padding: '12px 16px',
          borderBottom: '1px solid #2e333a',
        }}
      >
        <div style={{ display: 'flex', gap: 6 }}>
          {FILTERS.map((f) => (
            <Segment
              key={f.value}
              value={f.label}
              active={filter === f.value}
              onClick={() => setFilter(f.value)}
            />
          ))}
        </div>
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 12,
            color: '#cfd4da',
            cursor: 'pointer',
            userSelect: 'none',
          }}
        >
          {/* Custom glass checkbox: native input is hidden but still drives state + a11y,
              the coin-tinted span is the visual. */}
          <input
            type="checkbox"
            checked={hideUsed}
            onChange={(e) => setHideUsed(e.target.checked)}
            style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }}
          />
          <span
            aria-hidden
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 16,
              height: 16,
              borderRadius: 5,
              border: hideUsed ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.18)',
              background: hideUsed ? 'rgba(var(--coin-rgb),0.22)' : 'rgba(255,255,255,0.04)',
              boxShadow: hideUsed ? '0 0 8px rgba(var(--coin-rgb),0.30)' : 'none',
              color: '#eef2f8',
              fontSize: 11,
              lineHeight: 1,
              transition: 'background .2s, border-color .2s, box-shadow .2s',
            }}
          >
            {hideUsed ? '✓' : ''}
          </span>
          Hide used
        </label>
      </div>

      <div style={{ flex: '0 1 auto', minHeight: 0, overflowY: 'auto' }}>
      <table
        style={{ width: '100%', tableLayout: 'fixed', borderCollapse: 'collapse', fontSize: 13 }}
      >
        <thead>
          <tr style={{ textAlign: 'left' }}>
            <Th width="38%" style={thSticky}>Address</Th>
            <Th width="22%" style={thSticky} onClick={() => cycleSort('label')}>Label{sortInd('label')}</Th>
            <Th width="18%" align="right" style={thSticky} onClick={() => cycleSort('balance')}>Balance{sortInd('balance')}</Th>
            <Th width="8%" align="center" style={thSticky}>Used</Th>
            <Th width="14%" align="right" style={thSticky}> </Th>
          </tr>
        </thead>
        <tbody>
          {visible.length === 0 ? (
            <tr>
              <td colSpan={5} style={{ padding: '32px 16px', textAlign: 'center', color: '#8a929b' }}>
                {loading ? 'Loading…' : 'No addresses yet'}
              </td>
            </tr>
          ) : (
            visible.map((a) => {
              const isNext = a.address === nextReceive
              return (
                <tr
                  key={a.address}
                  style={{
                    borderTop: '1px solid #2e333a',
                    // Coin-tinted left rail marks the recommended next address.
                    borderLeft: isNext ? '3px solid var(--coin)' : '3px solid transparent',
                    background: isNext ? 'rgba(var(--coin-rgb),0.07)' : 'transparent',
                  }}
                >
                  <Td mono>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      {a.address}
                      {isNext && (
                        <span
                          style={{
                            fontFamily: 'inherit',
                            fontSize: 10,
                            fontWeight: 700,
                            letterSpacing: 0.3,
                            color: '#eef2f8',
                            background: 'rgba(var(--coin-rgb),0.22)',
                            border: '1px solid var(--coin)',
                            borderRadius: 6,
                            padding: '1px 6px',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          next receive
                        </span>
                      )}
                    </span>
                  </Td>
                  <Td>
                    <EditableLabel
                      value={a.label}
                      placeholder="add label"
                      onSave={(next) => saveLabel(a.address, next)}
                    />
                  </Td>
                  <Td align="right" mono>
                    {formatAmount(a.balance, coin)}
                  </Td>
                  <Td align="center">
                    <span style={{ color: a.used ? '#8a929b' : '#4caf50' }}>
                      {a.used ? 'yes' : 'no'}
                    </span>
                  </Td>
                  <Td align="right">
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, whiteSpace: 'nowrap' }}>
                      <button
                        type="button"
                        onClick={() => copy(a.address)}
                        style={{
                          // Fixed-size box so the "Copied ✓" text/glyph swap never shifts the row.
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          height: 26,
                          minWidth: 72,
                          padding: '0 10px',
                          boxSizing: 'border-box',
                          fontSize: 11,
                          borderRadius: 6,
                          border: copied === a.address ? '1px solid rgba(95,211,138,0.55)' : '1px solid #2e333a',
                          background: copied === a.address ? 'rgba(95,211,138,0.14)' : '#1a1d21',
                          color: copied === a.address ? '#7fe0a3' : '#cfd4da',
                          outline: 'none',
                          transition: 'color .15s, background .15s, border-color .15s',
                        }}
                      >
                        {copied === a.address ? 'Copied ✓' : 'Copy'}
                      </button>
                      {/* Reserve the arrow slot to keep Copy aligned across rows; explorer link
                          shows only for USED addresses (unused ones have no on-chain history). */}
                      <span style={{ width: 16, flex: '0 0 auto', display: 'inline-flex', justifyContent: 'center' }}>
                        {a.used && (
                          <a
                            href={explorerAddrUrl(coin, a.address)}
                            target="_blank"
                            rel="noreferrer"
                            title="Open in block explorer"
                            style={{ color: '#4fc3f7', textDecoration: 'none', fontSize: 15 }}
                          >
                            ↗
                          </a>
                        )}
                      </span>
                    </span>
                  </Td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
      </div>
    </section>
  )
}
