import { useEffect, useRef, useState } from 'react'
import { useStore } from '../../store'
import { setLabel, deleteLnRequest } from '../../api'
import type { Tx, LnTx } from '../../types'
import { Th, Td } from '../tableCells'
import EditableLabel from '../EditableLabel'
import { relTime, signedAmount, numValue, card } from '../uiKit'
import { explorerTxUrl, formatAmount } from '../../explorer'

const asStr = (v: unknown): string => (v == null ? '—' : String(v))
// Daemon LN invoice amounts are sats -> coin-unit display.
const satsToCoin = (v: unknown, coin: string): string => {
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? formatAmount((n / 1e8).toFixed(8), coin) : '—'
}

// target="_blank" routes through Electron's setWindowOpenHandler -> system browser for http(s) urls.
function ExplorerLink({ url }: { url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      title="Open in block explorer"
      style={{ color: '#4fc3f7', textDecoration: 'none', fontSize: 13 }}
    >
      ↗
    </a>
  )
}

// Segmented filter control (Type / Show), generic over the option value type.
function Seg<T extends string>({
  label, value, options, onChange,
}: {
  label: string
  value: T
  options: [T, string][]
  onChange: (v: T) => void
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <span style={{ fontSize: 11, color: '#8a929b' }}>{label}</span>
      <div style={{ display: 'inline-flex', border: '1px solid #2e333a', borderRadius: 8, overflow: 'hidden' }}>
        {options.map(([v, lbl]) => (
          <button
            key={v}
            type="button"
            onClick={() => onChange(v)}
            style={{
              padding: '4px 10px', fontSize: 12, border: 'none', cursor: 'pointer',
              background: value === v ? 'rgba(79,195,247,0.18)' : 'transparent',
              color: value === v ? '#e6e6e6' : '#8a929b', fontWeight: value === v ? 600 : 400,
            }}
          >
            {lbl}
          </button>
        ))}
      </div>
    </div>
  )
}

type DirF = 'all' | 'received' | 'sent'
type SortBy = 'date' | 'amount'
type Row = { ts: number; key: string; amountNum: number; kind: 'onchain' | 'lightning'; tx?: Tx; ln?: LnTx }
const REQUIRED_CONFIRMATIONS = 6

function txConfirmations(tx: Tx): number | null {
  if (typeof tx.confirmations === 'number' && Number.isFinite(tx.confirmations)) {
    return Math.max(0, Math.floor(tx.confirmations))
  }
  if (typeof tx.height === 'number' && tx.height <= 0) return 0
  return null
}

function txStatusText(tx: Tx): 'pending' | 'confirmed' {
  const conf = txConfirmations(tx)
  return conf !== null && conf >= REQUIRED_CONFIRMATIONS ? 'confirmed' : 'pending'
}

function txIsPending(tx: Tx): boolean {
  return txStatusText(tx) === 'pending'
}

function txDisplayDate(tx: Tx): string {
  if (tx.date) return tx.date
  if (typeof tx.timestamp === 'number' && Number.isFinite(tx.timestamp)) return relTime(tx.timestamp)
  return txIsPending(tx) ? 'Pending' : '—'
}

function rowDateSortValue(row: Row): number {
  if (row.tx && txIsPending(row.tx)) {
    const ts = typeof row.tx.timestamp === 'number' && Number.isFinite(row.tx.timestamp)
      ? row.tx.timestamp
      : Number.POSITIVE_INFINITY
    return ts
  }
  return Number.isFinite(row.ts) ? row.ts : Number.NEGATIVE_INFINITY
}

function TxStatusChip({ tx }: { tx: Tx }) {
  const conf = txConfirmations(tx)
  const confirmed = conf !== null && conf >= REQUIRED_CONFIRMATIONS
  const text = confirmed
    ? 'confirmed'
    : conf === null
      ? 'pending'
      : `pending ${Math.min(conf, REQUIRED_CONFIRMATIONS)}/${REQUIRED_CONFIRMATIONS}`
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: confirmed ? 76 : 96,
        height: 20,
        padding: '0 8px',
        borderRadius: 6,
        fontSize: 11,
        fontWeight: 700,
        lineHeight: '20px',
        color: confirmed ? '#8fe8a1' : '#f2c45b',
        background: confirmed ? 'rgba(46,204,113,0.12)' : 'rgba(224,162,58,0.14)',
        border: confirmed ? '1px solid rgba(46,204,113,0.42)' : '1px solid rgba(224,162,58,0.48)',
        textTransform: 'uppercase',
        letterSpacing: 0,
        whiteSpace: 'nowrap',
      }}
    >
      {text}
    </span>
  )
}

// Per-coin ledger (DATE | DESCRIPTION | AMOUNT | BALANCE) interleaving on-chain + Lightning, with
// type/direction filters and date/amount sort. Balance only reads right newest-first, else hidden.
export default function HistoryTab({ coin }: { coin: string }) {
  const perCoin = useStore((s) => s.coinStates[coin]?.history)
  const perCoinLn = useStore((s) => s.coinStates[coin]?.lnHistory)
  const globalLn = useStore((s) => s.lnHistory)
  const global = useStore((s) => s.history)
  // While syncing/fetching, an empty list means "not loaded yet" not "no transactions" — show loading.
  const synced = useStore((s) => s.portfolio?.coins?.[coin]?.synced)
  const loadingHistory = useStore((s) => s.coinStates[coin]?.loading)
  // Optimistic per-txid description overrides; bridge the gap until a refresh re-pulls authoritative labels.
  const [labelOverrides, setLabelOverrides] = useState<Record<string, string>>({})

  // Lifted to the store so the header balance can echo the selected Type (on-chain / Lightning / all).
  const typeFilter = useStore((s) => s.historyType)
  const setTypeFilter = useStore((s) => s.setHistoryType)
  const [dirFilter, setDirFilter] = useState<DirF>('all')
  const [sortBy, setSortBy] = useState<SortBy>('date')
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc')
  const [search, setSearch] = useState('')

  // Outstanding LN invoices (requests to receive), shown collapsible above the table. Cached + refreshed
  // by the store (fetchCoinData) on the normal poll, so they persist across restarts. Filter to LN ones.
  const storeReqs = useStore((s) => s.coinStates[coin]?.lnRequests)
  const lnRequests = (storeReqs ?? []).filter((r) => r.lightning_invoice || r.type === 'lightning' || r.status_str)
  const [reqOpen, setReqOpen] = useState(false)
  const pushToast = useStore((s) => s.pushToast)
  // Per-row delete confirm: the first click arms "Are you sure?", a second confirms; auto-disarms after 3s.
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null)
  useEffect(() => {
    if (!confirmDelId) return
    const t = setTimeout(() => setConfirmDelId(null), 3000)
    return () => clearTimeout(t)
  }, [confirmDelId])
  // Drag-resizable height for the expanded invoices panel (drag the bottom bar down to make it taller).
  const [reqHeight, setReqHeight] = useState(200)
  // Active drag's teardown, so an unmount mid-drag still removes window listeners and restores userSelect.
  const dragCleanup = useRef<(() => void) | null>(null)
  const startReqDrag = (e: React.MouseEvent) => {
    e.preventDefault()
    const startY = e.clientY
    const startH = reqHeight
    document.body.style.userSelect = 'none'
    const onMove = (m: MouseEvent) => setReqHeight(Math.max(96, Math.min(startH + (m.clientY - startY), 560)))
    const stop = () => {
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', stop)
      dragCleanup.current = null
    }
    dragCleanup.current = stop
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', stop)
  }
  useEffect(() => () => dragCleanup.current?.(), [])
  const delRequest = async (id?: string) => {
    if (!id) return
    setConfirmDelId(null)
    try {
      await deleteLnRequest(coin, id)
      // Optimistic: drop from the cached list now so the row disappears immediately...
      useStore.setState((s) => {
        const cs = s.coinStates[coin]
        return cs
          ? { coinStates: { ...s.coinStates, [coin]: { ...cs, lnRequests: cs.lnRequests.filter((r) => r.request_id !== id) } } }
          : {}
      })
      // ...then re-pull to confirm — if the daemon did NOT delete it, the row comes back (no silent swallow).
      void useStore.getState().fetchCoinData(coin, { background: true })
    } catch (e) {
      pushToast(e instanceof Error ? e.message : 'could not delete invoice', 'warn')
    }
  }

  const saveLabel = async (txid: string, next: string) => {
    await setLabel(coin, txid, next)
    setLabelOverrides((m) => ({ ...m, [txid]: next }))
  }

  // Prefer detailed per-coin history; else fall back to the GLOBAL merged feed so persisted history shows
  // the instant the wallet loads, even if the per-coin prefetch cached an empty result a moment too early.
  const onchain: Tx[] = perCoin && perCoin.length ? perCoin : global.filter((tx) => tx.coin === coin)
  // LN history mirrors on-chain: prefer the per-coin detail, else the always-cached global feed.
  const lnSource: LnTx[] = perCoinLn && perCoinLn.length
    ? perCoinLn
    : globalLn.filter((t) => (t as { coin?: string }).coin === coin)
  const fin = (n: number) => (Number.isFinite(n) ? n : 0)
  const allRows: Row[] = [
    ...onchain.map((tx, i): Row => ({
      ts: tx.timestamp ?? -Infinity,
      key: tx.txid ?? `oc-${tx.timestamp ?? i}-${i}`,
      amountNum: fin(numValue(tx.value)),
      kind: 'onchain',
      tx,
    })),
    ...lnSource.map((ln, i): Row => ({
      ts: ln.timestamp ?? -Infinity,
      key: `ln-${(ln.payment_hash as string | undefined) ?? ln.timestamp ?? i}-${i}`,
      amountNum: fin(typeof ln.amount_msat === 'number' ? ln.amount_msat / 1e11 : numValue(ln.amount)),
      kind: 'lightning',
      ln,
    })),
  ]

  const q = search.trim().toLowerCase()
  // Row description honouring optimistic on-chain label edits; used for search + CSV.
  const rowDesc = (r: Row): string =>
    r.tx
      ? ((r.tx.txid && labelOverrides[r.tx.txid] !== undefined ? labelOverrides[r.tx.txid] : r.tx.label) || '')
      : (r.ln?.label || '')
  const filtered = allRows.filter((r) => {
    if (typeFilter !== 'all' && r.kind !== typeFilter) return false
    if (dirFilter === 'received' && !(r.amountNum > 0)) return false
    if (dirFilter === 'sent' && !(r.amountNum < 0)) return false
    if (q) {
      const hay = [
        r.kind, rowDesc(r), r.tx?.txid, r.tx?.date, r.ln?.date,
        r.amountNum.toFixed(8),
      ].filter(Boolean).join(' ').toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
  filtered.sort((a, b) =>
    sortBy === 'amount'
      ? (sortDir === 'desc' ? b.amountNum - a.amountNum : a.amountNum - b.amountNum)
      : (sortDir === 'desc' ? rowDateSortValue(b) - rowDateSortValue(a) : rowDateSortValue(a) - rowDateSortValue(b)),
  )
  // A running balance only reads correctly newest-first; once re-sorted it would look like noise.
  const showBalance = sortBy === 'date' && sortDir === 'desc'

  // Client-side paging of the already-fetched/cached history (no extra server calls).
  const PAGE_SIZE = 50
  const [page, setPage] = useState(0)
  useEffect(() => { setPage(0) }, [coin, typeFilter, dirFilter, sortBy, sortDir, search])
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount - 1)
  const shown = filtered.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE)

  const toggleSort = (col: SortBy) => {
    if (sortBy === col) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    else { setSortBy(col); setSortDir('desc') }
  }
  const arrow = (col: SortBy) => (sortBy === col ? (sortDir === 'desc' ? ' ↓' : ' ↑') : '')
  const sortable = { cursor: 'pointer', userSelect: 'none' as const }
  const thSticky: React.CSSProperties = { position: 'sticky', top: 0, zIndex: 1, background: '#23272c' }

  // Export the filtered+sorted view as CSV; signed amount, on-chain rows carry balance+txid, LN ones blank.
  const exportCsv = () => {
    const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
    const head = ['Date', 'Type', 'Description', 'Status', `Amount (${coin})`, `Balance (${coin})`, 'Txid']
    const body = filtered.map((r) =>
      r.tx
        ? [r.tx.date || '', 'on-chain', rowDesc(r), txStatusText(r.tx), r.amountNum.toFixed(8), r.tx.balance ?? '', r.tx.txid ?? '']
        : [r.ln?.date || '', 'lightning', rowDesc(r), '', r.amountNum.toFixed(8), '', (r.ln?.payment_hash as string | undefined) ?? ''],
    )
    const csv = [head, ...body].map((row) => row.map(esc).join(',')).join('\r\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${coin}-history.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const emptyMsg =
    allRows.length === 0
      ? (loadingHistory || synced === false ? 'Loading history…' : 'No transactions yet')
      : 'No transactions match this filter'

  return (
    <section style={{ ...card, padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', flex: '0 1 auto', minHeight: 0, maxHeight: '100%' }}>
      <div style={{ flex: '0 0 auto', display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', padding: '10px 14px', borderBottom: '1px solid #2e333a', background: '#23272c' }}>
        <Seg label="Type" value={typeFilter} onChange={setTypeFilter} options={[['all', 'All'], ['onchain', 'On-chain'], ['lightning', '⚡ Lightning']]} />
        <Seg label="Show" value={dirFilter} onChange={setDirFilter} options={[['all', 'All'], ['received', 'Received'], ['sent', 'Sent']]} />
        <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search description, amount, txid…"
            autoComplete="off"
            spellCheck={false}
            style={{
              width: 230, padding: '5px 26px 5px 10px', fontSize: 12, color: '#e6e6e6',
              background: '#1a1d21', border: '1px solid #2e333a', borderRadius: 8, outline: 'none',
            }}
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch('')}
              title="Clear"
              style={{ position: 'absolute', right: 6, background: 'transparent', border: 'none', color: '#8a929b', cursor: 'pointer', fontSize: 14, lineHeight: 1, padding: 0 }}
            >
              ×
            </button>
          )}
        </div>
        <span style={{ marginLeft: 'auto', fontSize: 12, color: '#8a929b' }}>
          {filtered.length} {filtered.length === 1 ? 'entry' : 'entries'}
        </span>
        <button
          type="button"
          onClick={exportCsv}
          disabled={filtered.length === 0}
          title="Download the current filtered view as CSV"
          style={{
            padding: '5px 12px', fontSize: 12, borderRadius: 8, cursor: filtered.length === 0 ? 'default' : 'pointer',
            border: '1px solid #2e333a', background: '#1a1d21',
            color: filtered.length === 0 ? '#555c64' : '#cfd4da',
          }}
        >
          ↓ Export CSV
        </button>
      </div>

      {/* Outstanding LN invoices — collapsible panel above the table, own scroll; not in the CSV export. */}
      {lnRequests.length > 0 && (
        <div style={{ flex: '0 0 auto', borderBottom: '1px solid #2e333a' }}>
          <button
            type="button"
            onClick={() => setReqOpen((v) => !v)}
            style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', background: 'rgba(79,195,247,0.05)', border: 'none', padding: '8px 14px', cursor: 'pointer', textAlign: 'left' }}
          >
            <span style={{ color: '#8a929b', fontSize: 12 }}>{reqOpen ? '▾' : '▸'}</span>
            <span style={{ fontSize: 12, fontWeight: 700, color: '#cfd6dd' }}>Your invoices (requests)</span>
            <span style={{ fontSize: 11, color: '#8a929b' }}>— {lnRequests.length}</span>
          </button>
          {reqOpen && (
            <>
            <div style={{ height: reqHeight, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: 'left' }}>
                    <Th>Memo</Th>
                    <Th align="right">Amount</Th>
                    <Th>Status</Th>
                    <Th align="right"> </Th>
                  </tr>
                </thead>
                <tbody>
                  {lnRequests.map((r, i) => (
                    <tr key={r.request_id ?? i} style={{ borderTop: '1px solid #2e333a' }}>
                      <Td muted>{asStr(r.message)}</Td>
                      <Td align="right" mono>{r.amount_sat != null ? satsToCoin(r.amount_sat, coin) : asStr(r.amount_BTC)}</Td>
                      <Td muted>{asStr(r.status_str ?? r.status)}</Td>
                      <Td align="right">
                        {(() => {
                          const armed = confirmDelId != null && confirmDelId === r.request_id
                          return (
                            <button
                              type="button"
                              onClick={() => (armed ? void delRequest(r.request_id) : setConfirmDelId(r.request_id ?? null))}
                              // Fixed width fits "Are you sure?" so arming never widens the button or shifts columns.
                              style={{
                                width: 104, textAlign: 'center', boxSizing: 'border-box',
                                padding: '4px 8px', fontSize: 11, borderRadius: 6, cursor: 'pointer',
                                border: armed ? '1px solid rgba(239,83,80,0.6)' : '1px solid #2e333a',
                                background: armed ? 'rgba(239,83,80,0.15)' : '#1a1d21',
                                color: armed ? '#ffb4ab' : '#ef5350',
                                transition: 'color .12s, background .12s, border-color .12s',
                              }}
                            >
                              {armed ? 'Are you sure?' : 'Delete'}
                            </button>
                          )
                        })()}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* Drag this bar down to make the invoices panel taller (the history table below shrinks). */}
            <div
              onMouseDown={startReqDrag}
              title="Drag to resize"
              style={{ height: 9, cursor: 'ns-resize', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,255,255,0.03)' }}
            >
              <div style={{ width: 34, height: 3, borderRadius: 2, background: 'rgba(255,255,255,0.22)' }} />
            </div>
            </>
          )}
        </div>
      )}

      <div style={{ flex: '0 1 auto', minHeight: 0, overflowY: 'auto' }}>
      <table style={{ width: '100%', tableLayout: 'fixed', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ textAlign: 'left' }}>
            <Th width="14%" style={thSticky}><span style={sortable} onClick={() => toggleSort('date')}>Date{arrow('date')}</span></Th>
            <Th width="30%" style={thSticky}>Description</Th>
            <Th width="11%" align="center" style={thSticky}>Status</Th>
            <Th width="14%" align="right" style={thSticky}><span style={sortable} onClick={() => toggleSort('amount')}>Amount{arrow('amount')}</span></Th>
            <Th width="24%" align="right" style={thSticky}>
              <span title={showBalance ? '' : 'Running balance is shown only when sorted by date, newest first'}>
                Balance{showBalance ? '' : ' *'}
              </span>
            </Th>
            <Th width="7%" align="center" style={thSticky}> </Th>
          </tr>
        </thead>
        <tbody>
          {filtered.length === 0 ? (
            <tr>
              <td colSpan={6} style={{ padding: '32px 16px', textAlign: 'center', color: '#8a929b' }}>{emptyMsg}</td>
            </tr>
          ) : (
            shown.map((row) => {
              // Lightning payment row: no on-chain txid/balance, so it's read-only with a ⚡ marker.
              if (row.ln) {
                const ln = row.ln
                const amt = signedAmount(
                  typeof ln.amount_msat === 'number' ? (ln.amount_msat / 1e11).toFixed(8) : ln.amount,
                )
                return (
                  <tr key={row.key} style={{ borderTop: '1px solid #2e333a' }}>
                    <Td muted>{ln.date || relTime(ln.timestamp)}</Td>
                    <Td>{ln.label || '—'}</Td>
                    <Td align="center"> </Td>
                    <Td align="right" mono>
                      <span style={{ color: amt.color, fontWeight: 600 }}>{amt.text}</span>
                    </Td>
                    <Td align="right" mono muted>
                      <span title="Lightning payment (off-chain)" style={{ color: '#e0a23a' }}>⚡ Lightning</span>
                    </Td>
                    <Td align="center"> </Td>
                  </tr>
                )
              }
              const tx = row.tx as Tx
              const amt = signedAmount(tx.value)
              const desc = (tx.txid && labelOverrides[tx.txid] !== undefined
                ? labelOverrides[tx.txid]
                : tx.label) || ''
              return (
                <tr key={row.key} style={{ borderTop: '1px solid #2e333a' }}>
                  <Td muted>{txDisplayDate(tx)}</Td>
                  <Td>
                    {tx.txid ? (
                      <EditableLabel
                        value={desc}
                        placeholder="add description"
                        onSave={(next) => saveLabel(tx.txid as string, next)}
                      />
                    ) : (
                      desc || '—'
                    )}
                  </Td>
                  <Td align="center">
                    <TxStatusChip tx={tx} />
                  </Td>
                  <Td align="right" mono>
                    <span style={{ color: amt.color, fontWeight: 600 }}>{amt.text}</span>
                  </Td>
                  <Td align="right" mono muted>
                    {showBalance ? formatAmount(tx.balance, coin) : '—'}
                  </Td>
                  <Td align="center">
                    {tx.txid ? <ExplorerLink url={explorerTxUrl(coin, tx.txid)} /> : null}
                  </Td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
      </div>
      {pageCount > 1 && (
        <div
          style={{
            flex: '0 0 auto',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '10px 16px',
            borderTop: '1px solid #2e333a',
            fontSize: 12,
            color: '#8a929b',
          }}
        >
          <span>{filtered.length} entries</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button type="button" disabled={safePage === 0} onClick={() => setPage(safePage - 1)} style={pageBtn(safePage === 0)}>
              ← Previous
            </button>
            <span>Page {safePage + 1} of {pageCount}</span>
            <button type="button" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)} style={pageBtn(safePage >= pageCount - 1)}>
              Next →
            </button>
          </span>
        </div>
      )}
    </section>
  )
}

const pageBtn = (disabled: boolean): React.CSSProperties => ({
  padding: '4px 12px',
  fontSize: 12,
  borderRadius: 6,
  border: '1px solid #2e333a',
  background: '#1a1d21',
  color: disabled ? '#555c64' : '#cfd4da',
  cursor: disabled ? 'default' : 'pointer',
})
