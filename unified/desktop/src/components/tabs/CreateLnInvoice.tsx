import { useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { lnInvoice } from '../../api'
import { lbl, input, primaryBtn, codeBox, card } from '../uiKit'
import ErrorOverlay from '../ErrorOverlay'

// Create a Lightning invoice to receive a payment. Lives in the Receive tab — paying an invoice is
// in the Send tab (it detects a BOLT11 invoice in its "Pay to" field), so receiving and paying each
// have one home. onCreated lets a parent refresh any request list after a new invoice is made.
export default function CreateLnInvoice({ coin, onCreated }: { coin: string; onCreated?: () => void }) {
  const [reqAmount, setReqAmount] = useState('')
  const [memo, setMemo] = useState('')
  const [reqExpiry, setReqExpiry] = useState('3600')
  const [reqBusy, setReqBusy] = useState(false)
  const [reqError, setReqError] = useState<string | null>(null)
  const [bolt11, setBolt11] = useState<string | null>(null)

  const request = async () => {
    setReqError(null)
    setBolt11(null)
    if (!/^\d+(\.\d+)?$/.test(reqAmount.trim()) || Number(reqAmount) <= 0) return setReqError('Enter a valid amount.')
    setReqBusy(true)
    try {
      const r = (await lnInvoice(coin, reqAmount.trim(), memo.trim(), reqExpiry)) as Record<string, unknown>
      const inv = (r.lightning_invoice ?? r.bolt11 ?? r.invoice ?? r.payment_request) as string | undefined
      if (inv) setBolt11(inv)
      else setReqError('Invoice created but no BOLT11 returned.')
      onCreated?.()
    } catch (e) {
      setReqError(e instanceof Error ? e.message : 'could not create invoice')
    } finally {
      setReqBusy(false)
    }
  }

  return (
    <section style={{ ...card, position: 'relative' }}>
      <div style={{ maxWidth: 440 }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>Receive over Lightning</div>
        <div style={{ color: '#8a929b', fontSize: 12, marginBottom: 6 }}>Create a Lightning invoice for someone to pay.</div>
        <label style={lbl}>Amount ({coin})</label>
        <input style={input} value={reqAmount} onChange={(e) => setReqAmount(e.target.value)} placeholder="0.0" inputMode="decimal" autoComplete="off" />
        <label style={lbl}>Memo — optional</label>
        <input style={input} value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="What's it for?" autoComplete="off" />
        <label style={lbl}>Expires</label>
        <select style={{ ...input, appearance: 'auto' }} value={reqExpiry} onChange={(e) => setReqExpiry(e.target.value)}>
          <option value="3600">1 hour</option>
          <option value="86400">1 day</option>
          <option value="604800">1 week</option>
        </select>
        <button style={{ ...primaryBtn, marginTop: 14 }} disabled={reqBusy} onClick={() => void request()}>
          {reqBusy ? 'Creating…' : 'Create invoice'}
        </button>
        {bolt11 && (
          <div style={{ marginTop: 14, textAlign: 'center' }}>
            <div style={{ background: '#fff', padding: 10, borderRadius: 8, display: 'inline-block' }}>
              <QRCodeSVG value={bolt11} size={150} />
            </div>
            <div style={{ ...codeBox, marginTop: 10, textAlign: 'left' }}>{bolt11}</div>
          </div>
        )}
      </div>
      {/* Centered, self-dismissing error overlay (e.g. "Enter a valid amount."). */}
      <ErrorOverlay message={reqError} onDismiss={() => setReqError(null)} />
    </section>
  )
}
