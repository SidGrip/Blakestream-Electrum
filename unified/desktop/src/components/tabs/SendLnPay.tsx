import { useEffect, useState } from 'react'
import { decodeInvoice, lnPay, type DecodedInvoice } from '../../api'
import { useStore } from '../../store'
import { primaryBtn, secondaryBtn, errBox, feeBox, card } from '../uiKit'

// Shown inside the Send tab when the "Pay to" field holds a Lightning (BOLT11) invoice rather than
// an on-chain address: decode it to show the amount + memo, then pay it. This is what makes Send a
// single, rail-aware action instead of two separate "send" forms.
export default function SendLnPay({
  coin,
  invoice,
  onReset,
}: {
  coin: string
  invoice: string
  onReset: () => void
}) {
  const [decoded, setDecoded] = useState<DecodedInvoice | null>(null)
  const [decodeErr, setDecodeErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [paid, setPaid] = useState(false)

  // Decode whenever the invoice changes (it lives in the parent's address field). Aborts cleanly so
  // a stale decode can't overwrite a newer one as the user keeps typing/pasting.
  useEffect(() => {
    let cancelled = false
    const inv = invoice.trim()
    setDecoded(null)
    setDecodeErr('')
    setError('')
    setPaid(false)
    if (inv.length < 20) return
    decodeInvoice(coin, inv)
      .then((d) => { if (!cancelled) setDecoded(d) })
      .catch((e) => { if (!cancelled) setDecodeErr(e instanceof Error ? e.message : 'could not decode invoice') })
    return () => { cancelled = true }
  }, [coin, invoice])

  const amountStr = decoded
    ? decoded.amount_BTC != null
      ? `${decoded.amount_BTC} ${coin}`
      : decoded.amount_sat != null
        ? `${(decoded.amount_sat / 1e8).toFixed(8)} ${coin}`
        : decoded.amount_msat != null
          ? `${(decoded.amount_msat / 1e11).toFixed(8)} ${coin}`
          : 'any amount (invoice has none)'
    : ''
  const memo = decoded ? decoded.description || decoded.message || '' : ''

  const pay = async () => {
    setBusy(true)
    setError('')
    try {
      await lnPay(coin, invoice.trim())
      setPaid(true)
      void useStore.getState().fetchCoinData(coin)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'payment failed')
    } finally {
      setBusy(false)
    }
  }

  if (paid) {
    return (
      <section style={card}>
        <p style={{ color: '#4caf50', fontWeight: 600, marginTop: 0 }}>Paid ⚡</p>
        <p style={{ color: '#8a929b', fontSize: 12, marginBottom: 14 }}>
          Your Lightning payment went through. Sending over Lightning also makes more room to receive on that channel.
        </p>
        <button style={secondaryBtn} onClick={onReset}>New payment</button>
      </section>
    )
  }

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ color: '#e0a23a', fontSize: 12, fontWeight: 600, margin: '4px 0 8px' }}>⚡ Lightning invoice</div>
      {decodeErr && <div style={errBox}>{decodeErr}</div>}
      {decoded && (
        <div style={feeBox}>
          <Row label="Amount" value={amountStr} />
          {memo && <Row label="Description" value={memo} />}
        </div>
      )}
      {!decoded && !decodeErr && <div style={{ color: '#8a929b', fontSize: 12 }}>Reading invoice…</div>}
      {error && <div style={errBox}>{error}</div>}
      <button
        style={{ ...primaryBtn, marginTop: 14 }}
        disabled={busy || !!decodeErr || !decoded}
        onClick={() => void pay()}
      >
        {busy ? 'Paying…' : 'Pay invoice'}
      </button>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 13, padding: '2px 0' }}>
      <span style={{ color: '#8a929b' }}>{label}</span>
      <span style={{ color: '#e6e6e6', fontWeight: 600, wordBreak: 'break-word', textAlign: 'right' }}>{value}</span>
    </div>
  )
}
