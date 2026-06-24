import { useEffect, useState } from 'react'
import { getUtxos, getAddresses, setAddressFrozen } from '../../api'
import type { Utxo } from '../../types'
import { useStore } from '../../store'
import { secondaryBtn, errBox, card } from '../uiKit'

const mono = 'ui-monospace, SFMono-Regular, Menlo, monospace'
const coinKey = (u: Utxo) => `${u.txid}:${u.vout}`

// Coin control: pick which UTXOs a send may spend (Select) and freeze addresses out of every spend.
// `selected` is shared upward, so it scopes both the single "Pay to" preview and the "Pay Many" build.
export default function SendAdvanced({
  coin,
  selected,
  setSelected,
  open,
}: {
  coin: string
  selected: string[]
  setSelected: (next: string[]) => void
  open: boolean
}) {
  const pushToast = useStore((s) => s.pushToast)
  const [utxos, setUtxos] = useState<Utxo[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [busyAddr, setBusyAddr] = useState<string | null>(null)
  const [err, setErr] = useState('')
  // address -> user label
  const [labels, setLabels] = useState<Record<string, string>>({})

  const load = async () => {
    setLoading(true)
    setErr('')
    try {
      // Labels are best-effort; a label-fetch failure must not fail the coin list.
      const [u, a] = await Promise.all([
        getUtxos(coin),
        getAddresses(coin, 'all').catch(() => ({ addresses: [] })),
      ])
      setUtxos(u.utxos || [])
      const map: Record<string, string> = {}
      for (const row of a.addresses || []) if (row.label) map[row.address] = row.label
      setLabels(map)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'could not load coins')
    } finally {
      setLoading(false)
    }
  }
  // Load UTXOs lazily the first time the card is opened.
  useEffect(() => {
    if (open && utxos == null) void load()
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const sel = new Set(selected)
  const toggleSel = (u: Utxo) => {
    if (u.frozen || !u.txid) return  // can't spend a frozen coin, nor one with no outpoint
    const k = coinKey(u)
    const next = new Set(sel)
    if (next.has(k)) next.delete(k)
    else next.add(k)
    setSelected([...next])
  }
  const toggleFreeze = async (u: Utxo) => {
    if (!u.address) return
    setBusyAddr(u.address)
    try {
      await setAddressFrozen(coin, u.address, !u.frozen)
      // a newly frozen coin must not stay selected as a spend input
      if (!u.frozen) setSelected(selected.filter((k) => k !== coinKey(u)))
      pushToast(`${u.frozen ? 'Unfroze' : 'Froze'} address ✓`, 'success')
      await load()
    } catch (e) {
      pushToast(e instanceof Error ? e.message : 'freeze failed', 'warn')
    } finally {
      setBusyAddr(null)
    }
  }

  const selCount = selected.length
  const frozenCount = (utxos || []).filter((u) => u.frozen).length
  // Total of ticked coins, summed in satoshis to avoid float drift.
  const selectedSat = (utxos || [])
    .filter((u) => sel.has(coinKey(u)))
    .reduce((acc, u) => acc + Math.round((Number(u.amount) || 0) * 1e8), 0)
  const selectedTotal = (selectedSat / 1e8).toFixed(8)

  // Toggle lives on the Send card; this is just the card it controls.
  if (!open) return null

  return (
    <div style={{ ...card, margin: '12px 0 0', position: 'relative' }}>
      {/* minHeight reserves the row + nowrap/ellipsis keep the summary on one line so a growing
          " · N selected = total" never pushes the rows down. */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minHeight: 18, marginBottom: 8 }}>
        <span style={{ fontSize: 12, color: '#cfd6dd', fontWeight: 600, flex: '0 0 auto' }}>Coin control</span>
        <span style={{ fontSize: 11, color: '#8a929b', flex: '1 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {utxos == null
            ? ''
            : `${utxos.length} coin${utxos.length === 1 ? '' : 's'}${frozenCount ? ` · ${frozenCount} frozen` : ''}${selCount ? ` · ${selCount} selected = ${selectedTotal} ${coin}` : ''}`}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <span style={{ fontSize: 11, color: '#8a929b', flex: '1 1 auto', minWidth: 0 }}>
          Select coins to spend only those in this send (none selected = the wallet chooses).
        </span>
        <button type="button" style={{ ...secondaryBtn, flex: '0 0 auto' }} onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
        {selCount > 0 && (
          <button type="button" style={{ ...secondaryBtn, flex: '0 0 auto' }} onClick={() => setSelected([])}>Clear</button>
        )}
      </div>
      <div style={{ color: '#8a929b', fontSize: 11, marginBottom: 8 }}>
        Freeze keeps an address’s coins out of every spend until you unfreeze it.
      </div>
      <div style={{ ...card, padding: 0, overflow: 'hidden', overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: '#8a929b' }}>
              <th style={th('1%', 'center')}> </th>
              <th style={th('1%')}>Label</th>
              <th style={th('auto')}>Address</th>
              <th style={th('1%', 'right')}>Amount</th>
              <th style={th('1%', 'center')}> </th>
            </tr>
          </thead>
          <tbody>
            {utxos == null ? (
              <tr><td colSpan={5} style={tdEmpty}>{loading ? 'Loading…' : ''}</td></tr>
            ) : utxos.length === 0 ? (
              <tr><td colSpan={5} style={tdEmpty}>No unspent outputs</td></tr>
            ) : (
              utxos.map((u, i) => (
                <tr key={`${coinKey(u)}#${i}`} style={{ borderTop: '1px solid #2e333a', opacity: u.frozen ? 0.55 : 1 }}>
                  <td style={{ ...td, textAlign: 'center', overflow: 'visible' }}>
                    <button
                      type="button"
                      disabled={u.frozen || !u.txid}
                      onClick={() => toggleSel(u)}
                      title={u.frozen ? 'frozen — unfreeze to select' : 'spend this coin'}
                      style={{
                        // Fixed width fits "Selected ✓" so toggling never shifts the Label/Address columns.
                        ...secondaryBtn, width: 116, whiteSpace: 'nowrap',
                        fontWeight: sel.has(coinKey(u)) ? 700 : 500,
                        cursor: u.frozen || !u.txid ? 'not-allowed' : 'pointer',
                        opacity: u.frozen || !u.txid ? 0.5 : 1,
                        ...(sel.has(coinKey(u))
                          ? { color: '#0c1a12', background: '#5fd38a', borderColor: '#5fd38a' }
                          : { color: '#5fd38a', borderColor: 'rgba(95,211,138,0.45)' }),
                      }}
                    >
                      {sel.has(coinKey(u)) ? 'Selected ✓' : 'Select'}
                    </button>
                  </td>
                  {/* Label truncates with ellipsis; hover shows the full label only when it's cut off. */}
                  <td style={{ padding: '7px 10px', verticalAlign: 'middle' }}>
                    {u.address && labels[u.address] ? (
                      <div
                        data-full={labels[u.address]}
                        onMouseEnter={showTitleIfTruncated}
                        style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: '#cfd4da' }}
                      >
                        {labels[u.address]}
                      </div>
                    ) : (
                      <span style={{ color: '#5a626b' }}>—</span>
                    )}
                  </td>
                  <td style={{ padding: '7px 10px', color: '#cfd4da', whiteSpace: 'nowrap' }}>
                    <span
                      data-full={u.address ?? ''}
                      onMouseEnter={showTitleIfTruncated}
                      style={{ fontFamily: mono, display: 'inline-block', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', verticalAlign: 'bottom' }}
                    >
                      {u.address}
                    </span>
                    {u.coinbase && <span style={tag('#fbbc04')}>coinbase</span>}
                    {u.frozen && <span style={tag('#4fc3f7')}>frozen</span>}
                  </td>
                  <td style={{ ...td, textAlign: 'right', fontFamily: mono }}>{u.amount ?? '—'} {coin}</td>
                  <td style={{ ...td, textAlign: 'center' }}>
                    <button
                      type="button"
                      // Fixed width fits the longer "Unfreeze" so toggling never shifts the Amount column.
                      style={{
                        ...secondaryBtn, width: 100, whiteSpace: 'nowrap',
                        ...(u.frozen ? null : { color: '#4fc3f7', borderColor: 'rgba(79,195,247,0.4)' }),
                      }}
                      onClick={() => toggleFreeze(u)}
                      disabled={busyAddr === u.address || !u.address}
                    >
                      {busyAddr === u.address ? '…' : u.frozen ? 'Unfreeze' : 'Freeze'}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {err && <div style={errBox}>{err}</div>}
    </div>
  )
}

// Show the native tooltip (data-full) only when the cell text is actually truncated.
const showTitleIfTruncated = (e: React.MouseEvent<HTMLElement>) => {
  const el = e.currentTarget
  el.title = el.scrollWidth > el.clientWidth ? el.dataset.full ?? '' : ''
}

const th = (w: string, align: 'left' | 'right' | 'center' = 'left'): React.CSSProperties => ({
  width: w, padding: '7px 10px', textAlign: align, fontWeight: 600, fontSize: 11,
})
const td: React.CSSProperties = { padding: '7px 10px', color: '#cfd4da', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
const tdEmpty: React.CSSProperties = { padding: '14px 10px', textAlign: 'center', color: '#8a929b' }
const tag = (c: string): React.CSSProperties => ({
  marginLeft: 6, fontSize: 9, padding: '1px 5px', borderRadius: 4, color: c,
  border: `1px solid ${c}55`, background: `${c}14`, verticalAlign: 'middle',
})
