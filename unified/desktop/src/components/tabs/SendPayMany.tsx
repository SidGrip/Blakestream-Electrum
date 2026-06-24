import { useRef, useState } from 'react'
import { payToMany, broadcastTransaction } from '../../api'
import type { LoadedTx } from '../../types'
import { useStore } from '../../store'
import { input, primaryBtn, secondaryBtn, errBox, card } from '../uiKit'

const mono = 'ui-monospace, SFMono-Regular, Menlo, monospace'

// "Pay Many" — one on-chain tx to several recipients; honours coin control via `selected` (UTXOs ticked in Advanced).
export default function SendPayMany({
  coin,
  connected,
  selected,
}: {
  coin: string
  connected: boolean
  selected: string[]
}) {
  // Each row carries a stable id so React keeps input focus when a middle row is removed.
  const rowId = useRef(0)
  const newRow = () => ({ id: rowId.current++, address: '', amount: '' })
  const [rows, setRows] = useState<{ id: number; address: string; amount: string }[]>(() => [newRow()])
  const [ptmFee, setPtmFee] = useState('')
  const [built, setBuilt] = useState<LoadedTx | null>(null)
  const [sent, setSent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const fromCoins = selected.length ? selected : undefined
  const selCount = selected.length

  const runMany = async () => {
    setErr('')
    setBuilt(null)
    setSent(null)
    setBusy(true)
    try {
      const outputs = rows
        .map((r) => [r.address.trim(), r.amount.trim()] as [string, string])
        .filter(([a, v]) => a && v)
      if (!outputs.length) throw new Error('Add at least one recipient.')
      setBuilt(await payToMany(coin, outputs, ptmFee.trim() || undefined, fromCoins))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'could not build transaction')
    } finally {
      setBusy(false)
    }
  }
  const broadcast = async () => {
    if (!built?.raw) return
    setBusy(true)
    setErr('')
    try {
      const { txid } = await broadcastTransaction(coin, built.raw)
      setSent(txid)
      setBuilt(null)
      void useStore.getState().fetchCoinData(coin)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'broadcast failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {rows.map((r, i) => (
        <div key={r.id} style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
          <input
            value={r.address}
            placeholder="address"
            spellCheck={false}
            onChange={(e) => setRows((rs) => rs.map((x, k) => (k === i ? { ...x, address: e.target.value.replace(/\s/g, '') } : x)))}
            style={{ ...input, flex: 3, fontFamily: mono, fontSize: 12 }}
          />
          <input
            value={r.amount}
            placeholder="amount"
            spellCheck={false}
            onChange={(e) => setRows((rs) => rs.map((x, k) => (k === i ? { ...x, amount: e.target.value } : x)))}
            style={{ ...input, flex: 1 }}
          />
          <button
            type="button"
            style={secondaryBtn}
            onClick={() => setRows((rs) => (rs.length > 1 ? rs.filter((_, k) => k !== i) : rs))}
            disabled={rows.length === 1}
            title="Remove"
          >
            −
          </button>
        </div>
      ))}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4 }}>
        <button type="button" style={secondaryBtn} onClick={() => setRows((rs) => [...rs, newRow()])}>
          + Add recipient
        </button>
        <input value={ptmFee} onChange={(e) => setPtmFee(e.target.value)} placeholder="fee sat/vB (optional)" style={{ ...input, width: 150 }} />
        <button type="button" style={{ ...primaryBtn, marginLeft: 'auto' }} onClick={runMany} disabled={busy || !connected} title={connected ? '' : `${coin} is offline`}>
          {busy ? 'Working…' : 'Create transaction'}
        </button>
      </div>
      <div style={{ color: '#8a929b', fontSize: 11, marginTop: 6 }}>
        Amounts are in {coin}. Use "!" as an amount to send the maximum. {selCount ? `Spends only your ${selCount} selected coin${selCount === 1 ? '' : 's'} (see Advanced).` : 'Leave fee blank to use the coin’s fee policy.'}
      </div>

      {err && <div style={errBox}>{err}</div>}
      {built && (
        <div style={{ ...card, marginTop: 12 }}>
          <div style={{ fontSize: 12, color: '#cfd6dd', marginBottom: 6 }}>
            Built — {built.outputs.length} output{built.outputs.length === 1 ? '' : 's'}
            {built.size != null ? `, ${built.size} bytes` : ''}. Review and broadcast:
          </div>
          {built.outputs.map((o, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, fontFamily: mono, color: '#cfd4da', padding: '1px 0' }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.address ?? o.scriptpubkey}</span>
              <span style={{ marginLeft: 8 }}>{(o.value_sats / 1e8).toFixed(8)} {coin}</span>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <button type="button" style={secondaryBtn} onClick={() => setBuilt(null)} disabled={busy}>Discard</button>
            <button type="button" style={{ ...primaryBtn, marginLeft: 'auto' }} onClick={broadcast} disabled={busy}>
              {busy ? 'Broadcasting…' : 'Broadcast'}
            </button>
          </div>
        </div>
      )}
      {sent && (
        <div style={{ marginTop: 12 }}>
          <p style={{ color: '#4caf50', fontWeight: 600, margin: '0 0 4px' }}>Sent ✓</p>
          <div style={{ fontFamily: mono, fontSize: 11, wordBreak: 'break-all', color: '#cfd4da' }}>{sent}</div>
        </div>
      )}
    </>
  )
}
