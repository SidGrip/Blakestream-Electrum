import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { previewSend, confirmSend, setLabel, type SendPreview } from '../../api'
import type { Contact } from '../../types'
import { useStore } from '../../store'
import { lbl, input, primaryBtn, secondaryBtn, feeBox, card } from '../uiKit'
import SendAdvanced from './SendAdvanced'
import SendPayMany from './SendPayMany'
import SendLnPay from './SendLnPay'
import ErrorOverlay from '../ErrorOverlay'

// TEST DATA — 40 fake contacts to exercise the stretching list; set false before ship.
const TEST_FAKE_CONTACTS = true
const fakeContacts = (coin: string): Contact[] =>
  Array.from({ length: 40 }, (_, i) => ({
    id: `fake-${coin}-${i}`,
    coin,
    address: `${coin.toLowerCase()}1q${String(i).padStart(2, '0')}a7p3n2k9m4w8s6r5t0u1v2y3z4d5e6f7g8h9`.slice(0, 42),
    // every 4th has no label to exercise address-fallback rendering
    label: i % 4 === 0 ? '' : `Test contact ${i + 1}`,
  }))

// Send flow for one coin: form -> preview (build, don't broadcast) -> review -> confirm (broadcast).
// `prefill` lets the Contacts tab hand an address over via CoinDetail.
export default function SendTab({
  coin,
  canSend,
  prefill,
}: {
  coin: string
  canSend: boolean
  prefill?: string
}) {
  const coins = useStore((s) => s.coins)
  const contacts = useStore((s) => s.contacts)
  const lnInfo = useStore((s) => s.coinStates[coin]?.lnInfo)
  const setActiveTab = useStore((s) => s.setActiveTab)
  const setLightningMode = useStore((s) => s.setLightningMode)
  const coinContacts = useMemo(() => {
    const real = contacts.filter((c) => c.coin === coin)
    return TEST_FAKE_CONTACTS ? [...real, ...fakeContacts(coin)] : real
  }, [contacts, coin])

  const [address, setAddress] = useState('')
  const [amount, setAmount] = useState('')
  // Blank ("auto") = daemon asks ElectrumX for the feerate (2 sat/vByte floor); a typed value is used verbatim.
  const [feeRate, setFeeRate] = useState('')
  const [description, setDescription] = useState('')
  const [stage, setStage] = useState<'form' | 'review'>('form')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [preview, setPreview] = useState<SendPreview | null>(null)
  // Coin control: UTXOs picked as the only allowed inputs ("txid:vout"); empty = wallet chooses.
  const [selectedCoins, setSelectedCoins] = useState<string[]>([])
  // Lifted here (not local to the card) so the gutter math knows the wider Coin Control card's visible width.
  const [advOpen, setAdvOpen] = useState(false)
  useEffect(() => { setAdvOpen(false) }, [coin])
  // Send mode: single recipient ("Pay to") or several ("Pay Many").
  const [payMode, setPayMode] = useState<'single' | 'many'>('single')

  // Measure width + contacts card so the form stays CENTRED, shifting right only when contacts come within
  // 15px. The same observer caps the contacts list height at the Coin Control card's top when it's open.
  const wrapRef = useRef<HTMLDivElement>(null)
  const contactsRef = useRef<HTMLDivElement>(null)
  const formRef = useRef<HTMLElement>(null)
  const advRef = useRef<HTMLDivElement>(null)
  const [wrapW, setWrapW] = useState(0)
  const [panelMaxH, setPanelMaxH] = useState(420)
  useLayoutEffect(() => {
    const wrap = wrapRef.current
    if (!wrap) return
    const measure = () => {
      setWrapW(wrap.clientWidth)
      // Cap contacts height at the Pay-to card's BORDER-box bottom (offsetTop+offsetHeight) when Coin Control
      // is open — NOT advRef.offsetTop, which sits a margin-bottom lower; else fill down to the viewport.
      const adv = advRef.current
      const form = formRef.current
      const contactsTop = contactsRef.current?.offsetTop ?? 0
      const capBottom = adv && adv.offsetHeight > 0 && form
        ? form.offsetTop + form.offsetHeight
        : wrap.clientHeight - 8
      setPanelMaxH(Math.max(140, capBottom - contactsTop))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(wrap)
    if (contactsRef.current) ro.observe(contactsRef.current)
    if (formRef.current) ro.observe(formRef.current)
    if (advRef.current) ro.observe(advRef.current)
    return () => ro.disconnect()
    // isLnInvoice/wrapW resize the observed form/wrap, so the observer re-measures without them as deps.
  }, [coinContacts.length, advOpen, payMode, stage])

  // A "Pay" click from the Contacts tab fills the address and resets any review.
  useEffect(() => {
    if (prefill) {
      setAddress(prefill)
      setStage('form')
    }
  }, [prefill])

  // A coin selection only makes sense for the coin it was made on — drop it when the tab switches.
  useEffect(() => {
    setSelectedCoins([])
  }, [coin])

  // BOLT11 in the "Pay to" field flips to Lightning mode: on-chain addrs never start "ln" + invoices are
  // long, so "ln" + length is a clean tell. Exclude lnurl (unsupported on these chains; never a bolt11).
  const isLnInvoice = (() => {
    const a = address.trim().toLowerCase()
    return a.startsWith('ln') && !a.startsWith('lnurl') && a.length > 20
  })()

  if (!canSend) {
    return (
      <section style={card}>
        <p style={{ color: '#8a929b', fontSize: 13, lineHeight: 1.5, margin: 0 }}>
          {coin} isn’t connected to a server yet, so sending is unavailable. Receiving still
          works. Sending will turn on once {coin} has an ElectrumX server.
        </p>
      </section>
    )
  }

  const review = async () => {
    setError(null)
    setSuccess(null)
    const addr = address.trim()
    if (!addr) return setError('Enter a destination address.')
    // Catch an obvious wrong-coin bech32 paste (e.g. umo1… into a BLC send); the daemon does full
    // checksum + network validation (incl. legacy base58) before anything is signed.
    const lower = addr.toLowerCase()
    const wrong =
      coins &&
      Object.values(coins).find(
        (c) => c.ticker !== coin && c.hrp && lower.startsWith(c.hrp.toLowerCase() + '1'),
      )
    if (wrong) return setError(`That looks like a ${wrong.ticker} address, not ${coin}.`)
    if (!/^\d+(\.\d+)?$/.test(amount.trim()) || Number(amount) <= 0) {
      return setError('Enter a valid amount.')
    }
    const fee = feeRate.trim()
    if (fee && (!/^\d+(\.\d+)?$/.test(fee) || Number(fee) <= 0)) {
      return setError('Enter a valid fee rate (sat/byte).')
    }
    setBusy(true)
    try {
      const p = await previewSend(coin, addr, amount.trim(), fee || undefined,
        selectedCoins.length ? selectedCoins : undefined)
      setPreview(p)
      setStage('review')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    setBusy(true)
    setError(null)
    setSuccess(null)
    try {
      const { txid } = await confirmSend(coin) // broadcasts the exact previewed tx
      // Best-effort label = History description; never let a labeling error fail the already-broadcast send.
      const desc = description.trim()
      if (desc) {
        try {
          await setLabel(coin, txid, desc)
        } catch {
          /* tx is already broadcast; a missing label is non-fatal */
        }
      }
      // Success: clear the form and pop a self-dismissing confirmation overlay.
      setAddress('')
      setAmount('')
      setFeeRate('')
      setDescription('')
      setSelectedCoins([])
      setPreview(null)
      setStage('form')
      setSuccess('Payment sent ✓')
      void useStore.getState().fetchCoinData(coin)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setStage('form')
    } finally {
      setBusy(false)
    }
  }

  const reviewAmt = preview ? preview.amount : amount

  const FORM_W = 480
  const GAP = 15
  // Left-gutter width beside the always-centred form, less the gap: contacts grow to fit names up to
  // here (ellipsis only when a name reaches the form), and hide when the window is too narrow.
  const contactsMaxW = Math.max(0, Math.floor((wrapW - FORM_W) / 2) - GAP)
  const showContacts = coinContacts.length > 0 && contactsMaxW >= 120
  const onChainForm = stage === 'form' && (payMode === 'many' || !isLnInvoice)
  // Coin Control toggle, reused in both send modes.
  const coinControlToggle = (
    <button
      type="button"
      onClick={() => setAdvOpen((v) => !v)}
      style={{
        background: 'transparent', border: 'none', color: '#cfd6dd',
        cursor: 'pointer', fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 6,
      }}
    >
      {advOpen ? '▾' : '▸'} Coin Control{selectedCoins.length ? ` · ${selectedCoins.length}` : ''}
    </button>
  )
  return (
    <div ref={wrapRef} style={{ position: 'relative', perspective: 1400, flex: 1, minHeight: 0 }}>
      {/* Contacts: absolute-left so it never shifts the centred form; list scrolls within panelMaxH. */}
      {showContacts && (
        <div ref={contactsRef} style={{ position: 'absolute', left: 0, top: 0, zIndex: 2 }}>
            <div style={{ ...card, width: 'fit-content', maxWidth: contactsMaxW, boxSizing: 'border-box', padding: 12, display: 'flex', flexDirection: 'column', gap: 8, maxHeight: panelMaxH }}>
              <label style={{ ...lbl, marginTop: 0, marginBottom: 0, flex: '0 0 auto' }}>Pay to a contact</label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, overflowY: 'auto', flex: '1 1 auto', minHeight: 0 }}>
                {coinContacts.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    style={{
                      flex: '0 0 auto', width: '100%', boxSizing: 'border-box',
                      padding: '7px 10px', fontSize: 12, borderRadius: 6, border: '1px solid #2e333a',
                      background: '#1a1d21', color: '#cfd4da', textAlign: 'left', cursor: 'pointer',
                      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                    }}
                    onClick={() => setAddress(c.address)}
                    title={c.address}
                  >
                    {c.label || c.address}
                  </button>
                ))}
              </div>
            </div>
        </div>
      )}

      {/* Form/review card, capped at the bech32 width (~44 chars; LN invoices scroll) and centred in the
          padded wrap. key={payMode+stage} replays the flip animation on each form<->review swap. */}
      <section
        ref={formRef}
        key={payMode + ':' + stage}
        className="send-flip"
        style={{ ...card, maxWidth: FORM_W, margin: '0 auto', position: 'relative' }}
      >
        {stage === 'review' ? (
          <>
            <p style={{ color: '#8a929b', fontSize: 12, marginTop: 0 }}>You’re about to send</p>
            <div style={{ fontSize: 22, fontWeight: 700, margin: '4px 0 2px' }}>
              {reviewAmt} {coin}
            </div>
            <p style={{ color: '#8a929b', fontSize: 11, marginBottom: 2 }}>to</p>
            <div style={{ fontFamily: 'monospace', fontSize: 12, wordBreak: 'break-all', color: '#cfd4da' }}>
              {address.trim()}
            </div>
            {preview && (
              <div style={feeBox}>
                <Row label="Amount" value={`${preview.amount} ${coin}`} />
                <Row label="Network fee" value={`${preview.fee} ${coin}`} warn={preview.high_fee} />
                <div style={{ height: 1, background: '#2e333a', margin: '8px 0' }} />
                <Row label="Total" value={`${preview.total} ${coin}`} bold />
              </div>
            )}
            {preview?.high_fee && (
              <p style={{ color: '#e0a23a', fontSize: 11, marginTop: 8 }}>
                ⚠ The network fee is unusually large relative to the amount. Double-check before sending.
              </p>
            )}
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button style={secondaryBtn} disabled={busy} onClick={() => setStage('form')}>
                Back
              </button>
              <button style={primaryBtn} disabled={busy} onClick={() => void confirm()}>
                {busy ? 'Sending…' : 'Confirm & send'}
              </button>
            </div>
          </>
        ) : (
          <>
            {/* Pay to / Pay Many tabs flip the card between send modes. */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
              {(['single', 'many'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setPayMode(m)}
                  style={{
                    flex: 1, padding: '6px 10px', fontSize: 12, fontWeight: 600, borderRadius: 8, cursor: 'pointer',
                    border: payMode === m ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.12)',
                    background: payMode === m ? 'rgba(var(--coin-rgb),0.18)' : 'rgba(255,255,255,0.04)',
                    color: payMode === m ? '#eef2f8' : '#cfd4da',
                    transition: 'background .15s, border-color .15s, color .15s',
                  }}
                >
                  {m === 'single' ? 'Pay to' : 'Pay Many'}
                </button>
              ))}
            </div>
            {lnInfo?.enabled && (
              <LightningCapacityStrip
                canSendSat={lnInfo.can_send_sat ?? 0}
                canReceiveSat={lnInfo.can_receive_sat ?? 0}
                onManage={() => {
                  setLightningMode('simple')
                  setActiveTab('lightning')
                }}
              />
            )}

            {payMode === 'single' ? (
              <>
                <label style={{ ...lbl, marginTop: 0 }}>Pay to — address or Lightning invoice</label>
                {/* Strip whitespace on input: neither an address nor an invoice ever contains spaces. */}
                <input
                  style={input}
                  value={address}
                  onChange={(e) => setAddress(e.target.value.replace(/\s/g, ''))}
                  placeholder={`${coin} address (${coin.toLowerCase()}1…)  or  Lightning invoice (ln…)`}
                  autoComplete="off"
                  spellCheck={false}
                />
                {isLnInvoice ? (
                  // Field holds a BOLT11 invoice → pay over Lightning, not on-chain.
                  <SendLnPay coin={coin} invoice={address} onReset={() => setAddress('')} />
                ) : (
                  <>
                    <label style={lbl}>Amount</label>
                    <input
                      style={input}
                      value={amount}
                      onChange={(e) => setAmount(e.target.value)}
                      placeholder="0.0"
                      inputMode="decimal"
                      autoComplete="off"
                    />
                    <label style={lbl}>Fee (sat/byte) — optional</label>
                    <input
                      style={input}
                      value={feeRate}
                      onChange={(e) => setFeeRate(e.target.value)}
                      placeholder="auto"
                      inputMode="decimal"
                      autoComplete="off"
                    />
                    <label style={lbl}>Description — optional</label>
                    <input
                      style={input}
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="e.g. loot, gas, skins…"
                      autoComplete="off"
                    />
                    {selectedCoins.length > 0 && (
                      <div style={{ color: '#4fc3f7', fontSize: 11, marginTop: 10 }}>
                        Coin control: spending only {selectedCoins.length} selected coin{selectedCoins.length === 1 ? '' : 's'}
                      </div>
                    )}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 14 }}>
                      <button style={primaryBtn} disabled={busy} onClick={() => void review()}>
                        {busy ? 'Calculating fee…' : 'Review'}
                      </button>
                      <span style={{ marginLeft: 'auto' }}>{coinControlToggle}</span>
                    </div>
                  </>
                )}
              </>
            ) : (
              <>
                <SendPayMany coin={coin} connected={canSend} selected={selectedCoins} />
                <div style={{ display: 'flex', marginTop: 14 }}>
                  <span style={{ marginLeft: 'auto' }}>{coinControlToggle}</span>
                </div>
              </>
            )}
          </>
        )}

        {/* Self-dismissing error / success overlays float over this card. */}
        <ErrorOverlay message={error} onDismiss={() => setError(null)} />
        <ErrorOverlay tone="success" message={success} onDismiss={() => setSuccess(null)} />
      </section>

      {/* Coin Control: full-width block below the form, under the contacts. */}
      {onChainForm && (
        <div ref={advRef}>
          <SendAdvanced
            open={advOpen}
            coin={coin}
            selected={selectedCoins}
            setSelected={setSelectedCoins}
          />
        </div>
      )}
    </div>
  )
}

function Row({ label, value, bold, warn }: { label: string; value: string; bold?: boolean; warn?: boolean }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '2px 0' }}>
      <span style={{ color: '#8a929b' }}>{label}</span>
      <span style={{ color: warn ? '#e0a23a' : '#e6e6e6', fontWeight: bold ? 700 : 500, fontFamily: 'monospace' }}>
        {value}
      </span>
    </div>
  )
}

function LightningCapacityStrip({
  canSendSat,
  canReceiveSat,
  onManage,
}: {
  canSendSat: number
  canReceiveSat: number
  onManage: () => void
}) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10, margin: '-6px 0 14px', padding: '8px 10px',
      border: '1px solid rgba(251,188,4,0.24)', borderRadius: 8, background: 'rgba(251,188,4,0.06)',
      color: '#cfd4da', fontSize: 11, lineHeight: 1.35,
    }}>
      <span style={{ color: '#fbbc04', fontWeight: 700 }}>⚡</span>
      <span style={{ flex: 1 }}>
        Lightning send {formatSats(canSendSat)} · receive {formatSats(canReceiveSat)}
      </span>
      <button type="button" style={{ ...secondaryBtn, fontSize: 11, padding: '3px 9px' }} onClick={onManage}>
        Manage Lightning
      </button>
    </div>
  )
}

function formatSats(value: number | undefined): string {
  const n = Number(value ?? 0)
  if (!Number.isFinite(n)) return '0.00000000'
  return (n / 1e8).toFixed(8)
}
