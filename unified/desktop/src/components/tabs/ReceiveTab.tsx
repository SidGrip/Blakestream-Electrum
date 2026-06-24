import { useEffect, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { getReceiveAddress, newReceiveAddress, setLabel, lnInvoice } from '../../api'
import { useStore } from '../../store'
import { lbl, input, primaryBtn, secondaryBtn, codeBox, card } from '../uiKit'
import ErrorOverlay from '../ErrorOverlay'

const EXPIRIES = [
  { value: 'never', label: 'Never' },
  { value: '3600', label: '1 hour' },
  { value: '86400', label: '1 day' },
  { value: '604800', label: '1 week' },
]

type Mode = 'onchain' | 'lightning'

// One card, both ways: On-chain | Lightning toggle swaps the right form, QR stays LEFT. On-chain shows
// a fresh address (BIP21 when an amount is requested); Lightning the BOLT11. Paying lives in the Send tab.
export default function ReceiveTab({ coin }: { coin: string }) {
  const coins = useStore((s) => s.coins)
  const lnInfo = useStore((s) => s.coinStates[coin]?.lnInfo)
  const setActiveTab = useStore((s) => s.setActiveTab)
  const setLightningMode = useStore((s) => s.setLightningMode)
  const [mode, setMode] = useState<Mode>('onchain')

  // On-chain receive
  const [address, setAddress] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [amount, setAmount] = useState('')
  const [expiry, setExpiry] = useState('never')
  const [minting, setMinting] = useState(false)
  const [label, setLabelText] = useState('')

  // Lightning receive
  const [reqAmount, setReqAmount] = useState('')
  const [memo, setMemo] = useState('')
  const [reqExpiry, setReqExpiry] = useState('3600')
  const [reqBusy, setReqBusy] = useState(false)
  const [reqError, setReqError] = useState<string | null>(null)
  const [bolt11, setBolt11] = useState<string | null>(null)
  const [lnCopied, setLnCopied] = useState(false)

  useEffect(() => {
    let live = true
    setAddress(null)
    setError(null)
    setMinting(false)
    // A coin switch invalidates any invoice made for the previous coin.
    setBolt11(null)
    setReqError(null)
    getReceiveAddress(coin)
      .then((r) => live && setAddress(r.address))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      live = false
    }
  }, [coin])

  // Mint a fresh address; QR re-renders off `address`. Reset Copied so the new one must be re-copied.
  const generateFresh = () => {
    if (minting) return
    setMinting(true)
    newReceiveAddress(coin)
      .then(async (r) => {
        if (r.address) {
          setAddress(r.address)
          setCopied(false)
          const trimmed = label.trim()
          if (trimmed) {
            // Best-effort: a failed label save must not fail address generation.
            try {
              await setLabel(coin, r.address, trimmed)
            } catch {
              /* ignore — the address still generated fine */
            }
          }
          setLabelText('')
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setMinting(false))
  }

  const requestInvoice = async () => {
    setReqError(null)
    setBolt11(null)
    if (!/^\d+(\.\d+)?$/.test(reqAmount.trim()) || Number(reqAmount) <= 0) return setReqError('Enter a valid amount.')
    setReqBusy(true)
    try {
      const r = (await lnInvoice(coin, reqAmount.trim(), memo.trim(), reqExpiry)) as Record<string, unknown>
      const inv = (r.lightning_invoice ?? r.bolt11 ?? r.invoice ?? r.payment_request) as string | undefined
      if (inv) {
        setBolt11(inv)
        setLnCopied(false)
      } else setReqError('Invoice created but no BOLT11 returned.')
    } catch (e) {
      setReqError(e instanceof Error ? e.message : 'could not create invoice')
    } finally {
      setReqBusy(false)
    }
  }

  // BIP21 URI when an amount is requested (scheme = coin HRP, fallback ticker); bare address otherwise.
  const scheme = (coins?.[coin]?.hrp ?? coin).toLowerCase()
  const amt = amount.trim()
  const validAmount = /^\d+(\.\d+)?$/.test(amt) && Number(amt) > 0
  const qrValue = address ? (validAmount ? `${scheme}:${address}?amount=${amt}` : address) : ''
  const receiveCapSat = lnInfo?.can_receive_sat ?? 0
  const invoiceSat = coinAmountToSats(reqAmount)
  const invoiceOverCapacity = invoiceSat != null && invoiceSat > receiveCapSat

  // Left QR shared by both modes: address/BIP21 on-chain, invoice once made for Lightning, else a
  // same-footprint placeholder.
  const leftQr = (() => {
    const showOnchain = mode === 'onchain' && !!qrValue
    const showLn = mode === 'lightning' && !!bolt11
    if (showOnchain || showLn) {
      return (
        <div style={{ background: '#fff', padding: 12, borderRadius: 10, display: 'inline-block' }}>
          <QRCodeSVG value={showOnchain ? qrValue : (bolt11 as string)} size={180} />
        </div>
      )
    }
    return (
      <div
        style={{
          width: 204,
          height: 204,
          borderRadius: 10,
          border: '1px dashed #3a4048',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          textAlign: 'center',
          padding: 12,
          color: '#8a929b',
          fontSize: 12,
          lineHeight: 1.5,
        }}
      >
        {mode === 'onchain'
          ? error
            ? 'Address unavailable'
            : 'Loading address…'
          : 'Create an invoice\nto show its QR'.split('\n').map((t, i) => (
              <span key={i}>
                {t}
                {i === 0 ? <br /> : null}
              </span>
            ))}
      </div>
    )
  })()

  const doCopy = (value: string, setFlag: (v: boolean) => void) => {
    void navigator.clipboard?.writeText(value)
    setFlag(true)
    setTimeout(() => setFlag(false), 1500)
  }

  // Fixed-width copy chip: "Copy" and "Copied ✓" share one box (minWidth + centred) so clicking never reflows.
  const copyChip = (isCopied: boolean, onClick: () => void) => (
    <button
      type="button"
      onClick={onClick}
      style={{
        flex: 'none',
        minWidth: 92,
        textAlign: 'center',
        fontSize: 11,
        padding: '4px 12px',
        borderRadius: 6,
        cursor: 'pointer',
        border: isCopied ? '1px solid rgba(95,211,138,0.55)' : '1px solid #2e333a',
        background: isCopied ? 'rgba(95,211,138,0.14)' : '#1a1d21',
        color: isCopied ? '#7fe0a3' : '#cfd4da',
        transition: 'color .15s, background .15s, border-color .15s',
      }}
    >
      {isCopied ? 'Copied ✓' : 'Copy'}
    </button>
  )

  // Result block: header (label + copy chip), full-width code box, one-line note. Full width = fewer lines.
  const resultBlock = (
    labelText: string,
    value: string,
    isCopied: boolean,
    onCopy: () => void,
    note: string,
  ) => (
    <div style={{ marginTop: 18 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 6 }}>
        <label style={{ ...lbl, marginTop: 0, marginBottom: 0 }}>{labelText}</label>
        {copyChip(isCopied, onCopy)}
      </div>
      <div style={{ ...codeBox, wordBreak: 'break-all' }}>{value}</div>
      <p style={{ color: '#8a929b', fontSize: 11, margin: '8px 0 0' }}>{note}</p>
    </div>
  )

  return (
    <section style={{ ...card, position: 'relative' }}>
      {/* On-chain | Lightning toggle — same segmented styling as the Send tab. */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 16, maxWidth: 320 }}>
        {(['onchain', 'lightning'] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            style={{
              flex: 1,
              padding: '6px 10px',
              fontSize: 12,
              fontWeight: 600,
              borderRadius: 8,
              cursor: 'pointer',
              border: mode === m ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.12)',
              background: mode === m ? 'rgba(var(--coin-rgb),0.18)' : 'rgba(255,255,255,0.04)',
              color: mode === m ? '#eef2f8' : '#cfd4da',
              transition: 'background .15s, border-color .15s, color .15s',
            }}
          >
            {m === 'onchain' ? 'On-chain' : 'Lightning'}
          </button>
        ))}
      </div>

      {/* QR (left) + request controls (right); the address/invoice spans full width below this row. */}
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        <div style={{ textAlign: 'center' }}>{leftQr}</div>

        <div style={{ flex: 1, minWidth: 240 }}>
          {mode === 'onchain' ? (
            <>
              <label style={{ ...lbl, marginTop: 0 }}>Generate a fresh address — optional</label>
              <div style={{ display: 'flex', alignItems: 'stretch', gap: 8 }}>
                <input
                  style={{ ...input, flex: 1, minWidth: 0 }}
                  value={label}
                  onChange={(e) => setLabelText(e.target.value)}
                  placeholder="Label for new address — optional"
                  autoComplete="off"
                />
                <button
                  type="button"
                  style={{
                    ...secondaryBtn,
                    flex: 'none',
                    minWidth: 96,
                    opacity: minting ? 0.6 : 1,
                    cursor: minting ? 'default' : 'pointer',
                  }}
                  disabled={minting}
                  onClick={generateFresh}
                >
                  {minting ? 'Generating…' : 'Generate'}
                </button>
              </div>
              <p style={{ color: '#8a929b', fontSize: 11, margin: '6px 0 0' }}>
                A new address each time improves privacy — the wallet still watches them all. Reusing the
                current one is fine too.
              </p>

              <label style={lbl}>Requested amount — optional</label>
              <input
                style={input}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="0.0"
                inputMode="decimal"
                autoComplete="off"
              />

              <label style={lbl}>Request expires</label>
              <select
                style={{ ...input, appearance: 'auto' }}
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
              >
                {EXPIRIES.map((e) => (
                  <option key={e.value} value={e.value}>
                    {e.label}
                  </option>
                ))}
              </select>
            </>
          ) : (
            <>
              {lnInfo?.enabled && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 10, margin: '0 0 12px', padding: '8px 10px',
                  border: invoiceOverCapacity ? '1px solid rgba(239,83,80,0.38)' : '1px solid rgba(251,188,4,0.24)',
                  borderRadius: 8,
                  background: invoiceOverCapacity ? 'rgba(239,83,80,0.08)' : 'rgba(251,188,4,0.06)',
                  color: '#cfd4da', fontSize: 11, lineHeight: 1.35,
                }}>
                  <span style={{ color: invoiceOverCapacity ? '#ef5350' : '#fbbc04', fontWeight: 700 }}>⚡</span>
                  <span style={{ flex: 1 }}>
                    {invoiceOverCapacity
                      ? `This invoice may not be payable. Receive capacity is ${formatSats(receiveCapSat, coin)}.`
                      : `Lightning receive capacity: ${formatSats(receiveCapSat, coin)}.`}
                  </span>
                  <button
                    type="button"
                    style={{ ...secondaryBtn, fontSize: 11, padding: '3px 9px' }}
                    onClick={() => {
                      setLightningMode('simple')
                      setActiveTab('lightning')
                    }}
                  >
                    Manage Lightning
                  </button>
                </div>
              )}
              <label style={{ ...lbl, marginTop: 0 }}>Amount ({coin})</label>
              <input
                style={input}
                value={reqAmount}
                onChange={(e) => setReqAmount(e.target.value)}
                placeholder="0.0"
                inputMode="decimal"
                autoComplete="off"
              />
              <label style={lbl}>Memo — optional</label>
              <input
                style={input}
                value={memo}
                onChange={(e) => setMemo(e.target.value)}
                placeholder="What's it for?"
                autoComplete="off"
              />
              <label style={lbl}>Expires</label>
              <select style={{ ...input, appearance: 'auto' }} value={reqExpiry} onChange={(e) => setReqExpiry(e.target.value)}>
                <option value="3600">1 hour</option>
                <option value="86400">1 day</option>
                <option value="604800">1 week</option>
              </select>
              <button style={{ ...primaryBtn, marginTop: 14 }} disabled={reqBusy} onClick={() => void requestInvoice()}>
                {reqBusy ? 'Creating…' : 'Create invoice'}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Full-width result under QR + controls: address (on-chain) or invoice (Lightning). */}
      {mode === 'onchain'
        ? error
          ? <p style={{ color: '#ef5350', margin: '16px 0 0' }}>{error}</p>
          : address
            ? resultBlock(`Your ${coin} address`, address, copied, () => doCopy(address, setCopied), `Only send ${coin} to this address.`)
            : <p style={{ color: '#8a929b', margin: '16px 0 0' }}>Loading address…</p>
        : bolt11
          ? resultBlock(
              'Invoice — scan the QR or copy',
              bolt11,
              lnCopied,
              () => doCopy(bolt11, setLnCopied),
              'A Lightning invoice can be paid once, by anyone who has it, before it expires.',
            )
          : null}

      {/* Self-dismissing error overlay (e.g. "Enter a valid amount."). */}
      <ErrorOverlay message={reqError} onDismiss={() => setReqError(null)} />
    </section>
  )
}

function coinAmountToSats(value: string): number | null {
  const v = value.trim()
  if (!/^\d+(\.\d+)?$/.test(v)) return null
  const n = Number(v)
  if (!Number.isFinite(n) || n <= 0) return null
  return Math.round(n * 1e8)
}

function formatSats(value: number | undefined, coin: string): string {
  const n = Number(value ?? 0)
  if (!Number.isFinite(n)) return `0.00000000 ${coin}`
  return `${(n / 1e8).toFixed(8)} ${coin}`
}
