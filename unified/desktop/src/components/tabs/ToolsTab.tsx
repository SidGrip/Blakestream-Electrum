import { useEffect, useRef, useState } from 'react'
import { useStore, type ToolsSection } from '../../store'
import {
  loadTransaction, fetchTransaction, broadcastTransaction,
  signMessage, verifyMessage, getAddresses,
  encryptMessage, decryptMessage,
  sweepKey, bumpFee,
  getMasterPubkey, revealSeed, exportPrivkey,
  exportWalletBackup, pickBackupSavePath,
} from '../../api'
import type { LoadedTx } from '../../types'
import { card, lbl, input, primaryBtn, secondaryBtn, errBox, codeBox } from '../uiKit'
import ErrorOverlay from '../ErrorOverlay'
import { formatAmount, explorerTxUrl } from '../../explorer'

// Satoshi int -> whole-coin amount string for formatAmount (which expects coin units).
function satsToAmount(sats: number): string {
  return (sats / 1e8).toFixed(8)
}

// Tools sub-tabs; only the active tool mounts, so switching doesn't fire every tool's fetches.
const TOOL_TABS: { key: ToolsSection; label: string; subtitle: string }[] = [
  { key: 'backup', label: 'Backup wallet', subtitle: 'full wallet · encrypted' },
  { key: 'load', label: 'Load transaction', subtitle: 'inspect · broadcast' },
  { key: 'sign', label: 'Sign / verify message', subtitle: 'prove address ownership' },
  { key: 'crypto', label: 'Encrypt / decrypt message', subtitle: 'ECIES' },
  { key: 'advanced', label: 'Advanced transactions', subtitle: 'sweep · bump fee' },
  { key: 'keys', label: 'Keys & seed', subtitle: 'sensitive · reveal · import' },
]

function FullWalletBackup() {
  const pushToast = useStore((s) => s.pushToast)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState<{ path: string; bytes: number; files: number } | null>(null)

  const run = async () => {
    setErr('')
    setDone(null)
    if (!password) {
      setErr('Enter your current wallet password.')
      return
    }
    const path = await pickBackupSavePath()
    if (!path) return
    setBusy(true)
    try {
      const result = await exportWalletBackup(password, path)
      setDone({ path: result.path, bytes: result.bytes, files: result.files })
      pushToast('Wallet backup saved.', 'success')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'backup failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 10, maxWidth: 1040 }}>
        <div style={{ color: '#cfd4da', fontSize: 13, lineHeight: 1.5 }}>
          Create one portable <span style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}>.bswallet</span> backup file.
          It includes the encrypted vault, contacts, settings, per-coin wallet files, and Lightning channel state stored in those wallet files.
        </div>
        <div style={{ color: '#fbbc04', fontSize: 12, lineHeight: 1.45 }}>
          The backup is encrypted with your current wallet password. To restore this file later,
          use the wallet password that was active when the backup was created.
        </div>
        <label style={lbl}>Current wallet password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="wallet password"
          autoComplete="off"
          style={{ ...input, maxWidth: 360 }}
          onKeyDown={(e) => { if (e.key === 'Enter' && !busy) void run() }}
        />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            type="button"
            style={{
              ...primaryBtn, display: 'inline-flex', alignItems: 'center', gap: 8,
              // Greyed + inert until a password is entered (and while saving). Cursor stays the
              // normal arrow when disabled — no pointer, no not-allowed.
              ...((busy || !password) ? { opacity: 0.45, boxShadow: 'none', cursor: 'default' } : {}),
            }}
            onClick={run}
            disabled={busy || !password}
          >
            {busy ? 'Saving backup...' : 'Save backup...'}
          </button>
          <span style={{ color: '#8a929b', fontSize: 11 }}>
            Close the wallet before copying the same backup to another active PC.
          </span>
        </div>
        {busy && (
          <div
            role="status"
            aria-live="polite"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 9,
              color: '#cfd4da',
              fontSize: 12,
              marginTop: 2,
            }}
          >
            <span className="mini-spinner" aria-hidden="true" />
            Creating encrypted backup file. This can take a moment for wallet files and Lightning channel state.
          </div>
        )}
      </div>
      <ErrorOverlay message={err} onDismiss={() => setErr('')} />
      {done && (
        <div style={{ ...codeBox, marginTop: 12 }}>
          Saved {done.files} files, {(done.bytes / (1024 * 1024)).toFixed(2)} MB<br />
          {done.path}
        </div>
      )}
    </div>
  )
}

// "Load transaction": paste/load/fetch a raw tx (hex/PSBT), inspect it, broadcast if fully signed.
function LoadTransaction({ coin }: { coin: string }) {
  const connected = useStore((s) => s.connected[coin] ?? false)
  const pushToast = useStore((s) => s.pushToast)
  const [raw, setRaw] = useState('')
  const [txid, setTxid] = useState('')
  const [result, setResult] = useState<LoadedTx | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const reset = () => { setResult(null); setErr('') }

  const doLoad = async () => {
    reset(); setBusy(true)
    try {
      setResult(await loadTransaction(coin, raw.trim()))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'could not load transaction')
    } finally { setBusy(false) }
  }

  const doFetch = async () => {
    reset(); setBusy(true)
    try {
      const r = await fetchTransaction(coin, txid.trim())
      setResult(r)
      setRaw(r.raw)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'could not fetch transaction')
    } finally { setBusy(false) }
  }

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    const reader = new FileReader()
    reader.onload = () => { setRaw(String(reader.result || '').trim()); reset() }
    reader.readAsText(f)
    e.target.value = ''   // allow re-selecting the same file
  }

  const doBroadcast = async () => {
    if (!result?.raw) return
    if (!window.confirm(`Broadcast this ${coin} transaction to the network? This cannot be undone.`)) return
    setBusy(true); setErr('')
    try {
      const { txid: id } = await broadcastTransaction(coin, result.raw)
      pushToast(`Broadcast ${coin} ✓ ${id.slice(0, 12)}…`, 'success')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'broadcast failed')
    } finally { setBusy(false) }
  }

  return (
    <div>
      <label style={lbl}>Raw transaction (hex or PSBT)</label>
      <textarea
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        placeholder="0100000001…"
        spellCheck={false}
        style={{ ...input, minHeight: 96, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12, resize: 'vertical' }}
      />
      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
        <button type="button" style={primaryBtn} onClick={doLoad} disabled={busy || !raw.trim()}>
          Load
        </button>
        <button type="button" style={secondaryBtn} onClick={() => fileRef.current?.click()} disabled={busy}>
          From file…
        </button>
        <button type="button" style={secondaryBtn} onClick={() => { setRaw(''); reset() }} disabled={busy || (!raw && !result)}>
          Clear
        </button>
        <input ref={fileRef} type="file" accept=".txn,.psbt,.txt,text/plain" onChange={onFile} style={{ display: 'none' }} />
      </div>

      <label style={{ ...lbl, marginTop: 16 }}>…or fetch from the blockchain by transaction ID</label>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          value={txid}
          onChange={(e) => setTxid(e.target.value)}
          placeholder="transaction id (64 hex chars)"
          spellCheck={false}
          style={{ ...input, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
        />
        <button type="button" style={secondaryBtn} onClick={doFetch} disabled={busy || !txid.trim() || !connected} title={connected ? '' : `${coin} is offline`}>
          Fetch
        </button>
      </div>
      {!connected && <div style={{ color: '#8a929b', fontSize: 11, marginTop: 4 }}>Fetching needs a network connection — {coin} is offline.</div>}

      <ErrorOverlay message={err} onDismiss={() => setErr('')} />

      {result && <TxView coin={coin} tx={result} connected={connected} busy={busy} onBroadcast={doBroadcast} />}
    </div>
  )
}

function TxView({
  coin, tx, connected, busy, onBroadcast,
}: {
  coin: string
  tx: LoadedTx
  connected: boolean
  busy: boolean
  onBroadcast: () => void
}) {
  const canBroadcast = tx.complete !== false && connected
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 14px', fontSize: 12, marginBottom: 12 }}>
        <Meta label="Transaction ID" value={tx.txid ?? '—'} mono />
        <Meta label="Size" value={tx.size != null ? `${tx.size} bytes` : '—'} />
        <Meta label="Version" value={tx.version != null ? String(tx.version) : '—'} />
        <Meta label="Locktime" value={tx.locktime != null ? String(tx.locktime) : '—'} />
        <Meta label="Status" value={tx.complete === false ? 'Incomplete (unsigned / partial)' : tx.complete ? 'Signed' : 'Unknown'} />
        <Meta label="Total out" value={formatAmount(satsToAmount(tx.total_out_sats), coin)} />
      </div>

      <div style={{ fontSize: 11, color: '#8a929b', margin: '8px 0 4px' }}>INPUTS ({tx.inputs.length})</div>
      <div style={{ ...codeBox, padding: '8px 10px' }}>
        {tx.inputs.length === 0 ? '—' : tx.inputs.map((i, k) => (
          <div key={k} style={{ marginBottom: 2 }}>
            {i.coinbase ? <span style={{ color: '#fbbc04' }}>coinbase</span> : `${i.prevout_hash}:${i.prevout_n}`}
          </div>
        ))}
      </div>

      <div style={{ fontSize: 11, color: '#8a929b', margin: '12px 0 4px' }}>OUTPUTS ({tx.outputs.length})</div>
      <div style={{ ...codeBox, padding: '8px 10px' }}>
        {tx.outputs.length === 0 ? '—' : tx.outputs.map((o, k) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 2 }}>
            <span>{o.address ?? '(non-standard script)'}</span>
            <span style={{ color: '#cfd4da', whiteSpace: 'nowrap' }}>{formatAmount(satsToAmount(o.value_sats), coin)}</span>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 14 }}>
        <button type="button" style={primaryBtn} onClick={onBroadcast} disabled={busy || !canBroadcast}>
          Broadcast
        </button>
        {tx.complete === false && <span style={{ color: '#8a929b', fontSize: 11 }}>Transaction is not fully signed.</span>}
        {tx.complete !== false && !connected && <span style={{ color: '#8a929b', fontSize: 11 }}>{coin} is offline.</span>}
        {tx.txid && (
          <a href={explorerTxUrl(coin, tx.txid)} target="_blank" rel="noreferrer" style={{ color: '#4fc3f7', fontSize: 12, marginLeft: 'auto', textDecoration: 'none' }}>
            View in explorer ↗
          </a>
        )}
      </div>
    </div>
  )
}

// A built (signed, un-broadcast) tx shown for review, with its own Broadcast control.
function BroadcastableResult({ coin, tx }: { coin: string; tx: LoadedTx }) {
  const connected = useStore((s) => s.connected[coin] ?? false)
  const pushToast = useStore((s) => s.pushToast)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState<string | null>(null)

  const broadcast = async () => {
    if (!tx.raw) return
    if (!window.confirm(`Broadcast this ${coin} transaction to the network? This cannot be undone.`)) return
    setBusy(true); setErr('')
    try {
      const { txid } = await broadcastTransaction(coin, tx.raw)
      setDone(txid)
      pushToast(`Broadcast ${coin} ✓ ${txid.slice(0, 12)}…`, 'success')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'broadcast failed')
    } finally { setBusy(false) }
  }

  return (
    <div>
      <TxView coin={coin} tx={tx} connected={connected} busy={busy} onBroadcast={broadcast} />
      <ErrorOverlay message={err} onDismiss={() => setErr('')} />
      {done && <div style={{ marginTop: 8, color: '#4caf50', fontSize: 12 }}>Broadcast — txid {done}</div>}
    </div>
  )
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <span style={{ color: '#8a929b' }}>{label}</span>
      <span style={{ color: '#e6e6e6', wordBreak: 'break-all', fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined }}>{value}</span>
    </>
  )
}

// "Sign / verify message": sign a message with an address's key, or verify a signature against an address.
function SignVerifyMessage({ coin }: { coin: string }) {
  // Sign side
  const [addr, setAddr] = useState('')
  const [msg, setMsg] = useState('')
  const [sig, setSig] = useState('')
  const [signing, setSigning] = useState(false)
  const [signErr, setSignErr] = useState('')
  const [myAddrs, setMyAddrs] = useState<string[]>([])
  const pushToast = useStore((s) => s.pushToast)

  // Verify side
  const [vAddr, setVAddr] = useState('')
  const [vMsg, setVMsg] = useState('')
  const [vSig, setVSig] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [vResult, setVResult] = useState<null | boolean>(null)
  const [vErr, setVErr] = useState('')

  // Offer the wallet's own receiving addresses as suggestions for the signing address.
  useEffect(() => {
    let live = true
    getAddresses(coin, 'receiving')
      .then((r) => { if (live) setMyAddrs((r.addresses || []).map((a) => a.address)) })
      .catch(() => { /* non-fatal: free-text entry still works */ })
    return () => { live = false }
  }, [coin])

  const doSign = async () => {
    setSignErr(''); setSig(''); setSigning(true)
    try {
      const { signature } = await signMessage(coin, addr.trim(), msg)
      setSig(signature)
    } catch (e) {
      setSignErr(e instanceof Error ? e.message : 'could not sign message')
    } finally { setSigning(false) }
  }

  const doVerify = async () => {
    setVErr(''); setVResult(null); setVerifying(true)
    try {
      const { valid } = await verifyMessage(coin, vAddr.trim(), vSig.trim(), vMsg)
      setVResult(valid)
    } catch (e) {
      setVErr(e instanceof Error ? e.message : 'could not verify message')
    } finally { setVerifying(false) }
  }

  const copySig = async () => {
    try { await navigator.clipboard.writeText(sig); pushToast('Signature copied ✓', 'success') } catch { /* ignore */ }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
      {/* Sign */}
      <div>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>Sign</div>
        <label style={lbl}>Address (one of your {coin} addresses)</label>
        <input
          value={addr} onChange={(e) => setAddr(e.target.value)}
          list={`addrs-${coin}`} placeholder="your address" spellCheck={false}
          style={{ ...input, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
        />
        <datalist id={`addrs-${coin}`}>
          {myAddrs.map((a) => <option key={a} value={a} />)}
        </datalist>
        <label style={lbl}>Message</label>
        <textarea
          value={msg} onChange={(e) => setMsg(e.target.value)} placeholder="message to sign"
          style={{ ...input, minHeight: 72, resize: 'vertical' }}
        />
        <div style={{ marginTop: 8 }}>
          <button type="button" style={primaryBtn} onClick={doSign} disabled={signing || !addr.trim()}>Sign</button>
        </div>
        <ErrorOverlay message={signErr} onDismiss={() => setSignErr('')} />
        {sig && (
          <div style={{ marginTop: 10 }}>
            <label style={lbl}>Signature</label>
            <div style={codeBox}>{sig}</div>
            <button type="button" style={{ ...secondaryBtn, marginTop: 6 }} onClick={copySig}>Copy</button>
          </div>
        )}
      </div>

      {/* Verify */}
      <div>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>Verify</div>
        <label style={lbl}>Address</label>
        <input
          value={vAddr} onChange={(e) => { setVAddr(e.target.value); setVResult(null) }}
          placeholder="signer's address" spellCheck={false}
          style={{ ...input, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
        />
        <label style={lbl}>Message</label>
        <textarea
          value={vMsg} onChange={(e) => { setVMsg(e.target.value); setVResult(null) }} placeholder="signed message"
          style={{ ...input, minHeight: 72, resize: 'vertical' }}
        />
        <label style={lbl}>Signature</label>
        <textarea
          value={vSig} onChange={(e) => { setVSig(e.target.value); setVResult(null) }} placeholder="base64 signature"
          spellCheck={false}
          style={{ ...input, minHeight: 48, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12, resize: 'vertical' }}
        />
        <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" style={primaryBtn} onClick={doVerify} disabled={verifying || !vAddr.trim() || !vSig.trim()}>Verify</button>
          {vResult === true && <span style={{ color: '#4caf50', fontWeight: 700, fontSize: 13 }}>✓ Valid signature</span>}
          {vResult === false && <span style={{ color: '#ef5350', fontWeight: 700, fontSize: 13 }}>✗ Invalid signature</span>}
        </div>
        <ErrorOverlay message={vErr} onDismiss={() => setVErr('')} />
      </div>
    </div>
  )
}

// "Encrypt / decrypt message" (ECIES): encrypt to a recipient pubkey/address; decrypt to your own key.
function EncryptDecryptMessage({ coin }: { coin: string }) {
  const [myAddrs, setMyAddrs] = useState<string[]>([])
  const pushToast = useStore((s) => s.pushToast)

  // Encrypt side
  const [eKey, setEKey] = useState('')
  const [eMsg, setEMsg] = useState('')
  const [eOut, setEOut] = useState('')
  const [eBusy, setEBusy] = useState(false)
  const [eErr, setEErr] = useState('')

  // Decrypt side
  const [dKey, setDKey] = useState('')
  const [dCipher, setDCipher] = useState('')
  const [dOut, setDOut] = useState('')
  const [dBusy, setDBusy] = useState(false)
  const [dErr, setDErr] = useState('')

  useEffect(() => {
    let live = true
    getAddresses(coin, 'receiving')
      .then((r) => { if (live) setMyAddrs((r.addresses || []).map((a) => a.address)) })
      .catch(() => { /* free-text still works */ })
    return () => { live = false }
  }, [coin])

  const doEncrypt = async () => {
    setEErr(''); setEOut(''); setEBusy(true)
    try { setEOut((await encryptMessage(coin, eKey.trim(), eMsg)).encrypted) }
    catch (e) { setEErr(e instanceof Error ? e.message : 'could not encrypt') }
    finally { setEBusy(false) }
  }
  const doDecrypt = async () => {
    setDErr(''); setDOut(''); setDBusy(true)
    try { setDOut((await decryptMessage(coin, dKey.trim(), dCipher.trim())).message) }
    catch (e) { setDErr(e instanceof Error ? e.message : 'could not decrypt') }
    finally { setDBusy(false) }
  }
  const copyEnc = async () => {
    try { await navigator.clipboard.writeText(eOut); pushToast('Encrypted message copied ✓', 'success') } catch { /* ignore */ }
  }
  const copyDec = async () => {
    try { await navigator.clipboard.writeText(dOut); pushToast('Decrypted message copied ✓', 'success') } catch { /* ignore */ }
  }

  const keyInput = (val: string, set: (v: string) => void, ph: string) => (
    <>
      <input
        value={val} onChange={(e) => set(e.target.value)} list={`encaddrs-${coin}`} placeholder={ph}
        spellCheck={false}
        style={{ ...input, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
      />
      <datalist id={`encaddrs-${coin}`}>{myAddrs.map((a) => <option key={a} value={a} />)}</datalist>
    </>
  )

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
      {/* Encrypt */}
      <div>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>Encrypt</div>
        <label style={lbl}>Recipient public key (or one of your addresses)</label>
        {keyInput(eKey, setEKey, 'public key or address')}
        <label style={lbl}>Message</label>
        <textarea value={eMsg} onChange={(e) => setEMsg(e.target.value)} placeholder="message to encrypt"
          style={{ ...input, minHeight: 72, resize: 'vertical' }} />
        <div style={{ marginTop: 8 }}>
          <button type="button" style={primaryBtn} onClick={doEncrypt} disabled={eBusy || !eKey.trim()}>Encrypt</button>
        </div>
        <ErrorOverlay message={eErr} onDismiss={() => setEErr('')} />
        {eOut && (
          <div style={{ marginTop: 10 }}>
            <label style={lbl}>Encrypted</label>
            <div style={codeBox}>{eOut}</div>
            <button type="button" style={{ ...secondaryBtn, marginTop: 6 }} onClick={copyEnc}>Copy</button>
          </div>
        )}
      </div>

      {/* Decrypt */}
      <div>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 2 }}>Decrypt</div>
        <label style={lbl}>Your address (or public key) the message was sent to</label>
        {keyInput(dKey, setDKey, 'your address or public key')}
        <label style={lbl}>Encrypted message</label>
        <textarea value={dCipher} onChange={(e) => setDCipher(e.target.value)} placeholder="encrypted message"
          spellCheck={false}
          style={{ ...input, minHeight: 72, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12, resize: 'vertical' }} />
        <div style={{ marginTop: 8 }}>
          <button type="button" style={primaryBtn} onClick={doDecrypt} disabled={dBusy || !dKey.trim() || !dCipher.trim()}>Decrypt</button>
        </div>
        <ErrorOverlay message={dErr} onDismiss={() => setDErr('')} />
        {dOut && (
          <div style={{ marginTop: 10 }}>
            <label style={lbl}>Decrypted</label>
            <div style={{ ...codeBox, whiteSpace: 'pre-wrap' }}>{dOut}</div>
            <button type="button" style={{ ...secondaryBtn, marginTop: 6 }} onClick={copyDec}>Copy</button>
          </div>
        )}
      </div>
    </div>
  )
}

// "Advanced transactions": sweep a private key or fee-bump (RBF) a tx; each builds a signed tx to review before broadcast.
type AdvMode = 'sweep' | 'bumpfee'
function AdvancedTx({ coin }: { coin: string }) {
  const connected = useStore((s) => s.connected[coin] ?? false)
  const [mode, setMode] = useState<AdvMode>('sweep')
  const [built, setBuilt] = useState<LoadedTx | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // sweep
  const [privkey, setPrivkey] = useState('')
  const [dest, setDest] = useState('')
  const [sweepFee, setSweepFee] = useState('')
  // bump fee
  const [bumpTx, setBumpTx] = useState('')
  const [bumpRate, setBumpRate] = useState('')

  const reset = () => { setBuilt(null); setErr('') }
  const switchMode = (m: AdvMode) => { setMode(m); reset() }

  const runSweep = async () => {
    reset(); setBusy(true)
    try { setBuilt(await sweepKey(coin, privkey.trim(), dest.trim(), sweepFee.trim() || undefined)) }
    catch (e) { setErr(e instanceof Error ? e.message : 'could not build sweep') }
    finally { setBusy(false) }
  }
  const runBump = async () => {
    reset(); setBusy(true)
    try { setBuilt(await bumpFee(coin, bumpTx.trim(), bumpRate.trim())) }
    catch (e) { setErr(e instanceof Error ? e.message : 'could not bump fee') }
    finally { setBusy(false) }
  }

  const seg = (m: AdvMode, label: string) => (
    <button
      type="button" onClick={() => switchMode(m)}
      style={{
        padding: '5px 12px', fontSize: 12, fontWeight: 600, borderRadius: 8, cursor: 'pointer',
        border: mode === m ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.10)',
        background: mode === m ? 'rgba(var(--coin-rgb),0.22)' : 'rgba(255,255,255,0.04)',
        color: mode === m ? '#f2f5f9' : '#cfd6dd',
      }}
    >{label}</button>
  )

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
        {seg('sweep', 'Sweep key')}
        {seg('bumpfee', 'Bump fee')}
      </div>

      {mode === 'sweep' && (
        <div>
          <label style={lbl}>Private key to sweep (WIF)</label>
          <input
            type="password" value={privkey} onChange={(e) => setPrivkey(e.target.value)} placeholder="private key"
            spellCheck={false} autoComplete="off"
            style={{ ...input, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
          />
          <label style={lbl}>Destination address</label>
          <input value={dest} onChange={(e) => setDest(e.target.value)} placeholder={`${coin} address to receive the swept funds`} spellCheck={false}
            style={{ ...input, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
            <input value={sweepFee} onChange={(e) => setSweepFee(e.target.value)} placeholder="fee sat/vB (optional)" style={{ ...input, width: 170 }} />
            <button type="button" style={{ ...primaryBtn, marginLeft: 'auto' }} onClick={runSweep} disabled={busy || !privkey.trim() || !dest.trim() || !connected} title={connected ? '' : `${coin} is offline`}>Create sweep</button>
          </div>
          {!connected && <div style={{ color: '#8a929b', fontSize: 11, marginTop: 6 }}>Sweeping needs a network connection — {coin} is offline.</div>}
        </div>
      )}

      {mode === 'bumpfee' && (
        <div>
          <label style={lbl}>Unconfirmed transaction (txid in your history, or raw hex)</label>
          <input value={bumpTx} onChange={(e) => setBumpTx(e.target.value)} placeholder="txid or raw hex" spellCheck={false}
            style={{ ...input, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
            <input value={bumpRate} onChange={(e) => setBumpRate(e.target.value)} placeholder="new fee sat/vB" style={{ ...input, width: 170 }} />
            <button type="button" style={{ ...primaryBtn, marginLeft: 'auto' }} onClick={runBump} disabled={busy || !bumpTx.trim() || !bumpRate.trim() || !connected} title={connected ? '' : `${coin} is offline`}>Bump fee</button>
          </div>
          <div style={{ color: '#8a929b', fontSize: 11, marginTop: 6 }}>
            Replace-By-Fee: the original transaction must be unconfirmed and have signalled RBF, and the {coin} network must accept the replacement. If it doesn't, the broadcast will say so.
          </div>
        </div>
      )}

      <ErrorOverlay message={err} onDismiss={() => setErr('')} />
      {built && <div style={{ marginTop: 14 }}><BroadcastableResult coin={coin} tx={built} /></div>}
    </div>
  )
}

// A masked secret box (blurred until "Show") for revealed seeds/keys, so they aren't left legible on screen.
function SecretBox({ value, label }: { value: string; label: string }) {
  const [shown, setShown] = useState(false)
  const [copied, setCopied] = useState(false)
  const pushToast = useStore((s) => s.pushToast)

  // Auto re-blur after 30s so a revealed secret isn't left legible if the user walks away.
  useEffect(() => {
    if (!shown) return
    const t = setTimeout(() => setShown(false), 30_000)
    return () => clearTimeout(t)
  }, [shown])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      pushToast(`${label} copied — clears in 60s; clipboard managers may still retain it`, 'warn')
      // Auto-clear after 60s, but only if the clipboard still holds OUR value (don't clobber a later copy).
      window.setTimeout(async () => {
        try {
          if ((await navigator.clipboard.readText()) === value) await navigator.clipboard.writeText('')
        } catch { /* clipboard read may be denied; ignore */ }
      }, 60_000)
    } catch { /* ignore */ }
  }
  const clearClip = async () => {
    try { await navigator.clipboard.writeText(''); setCopied(false); pushToast('Clipboard cleared ✓', 'success') } catch { /* ignore */ }
  }
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ ...codeBox, whiteSpace: 'pre-wrap', filter: shown ? 'none' : 'blur(6px)', userSelect: shown ? 'text' : 'none', transition: 'filter .1s' }}>
        {value}
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
        <button type="button" style={secondaryBtn} onClick={() => setShown((s) => !s)}>{shown ? 'Hide' : 'Show'}</button>
        <button type="button" style={secondaryBtn} onClick={copy}>Copy</button>
        {copied && <button type="button" style={secondaryBtn} onClick={clearClip}>Clear clipboard</button>}
      </div>
    </div>
  )
}

// Hard gate for revealing a secret: warning modal + wallet-password re-prompt (verified server-side) +
// explicit "I understand" before fetch; the secret is shown in-modal and cleared on close.
function RevealGate({
  title, warning, secretLabel, onReveal, onClose,
}: {
  title: string
  warning: string
  secretLabel: string
  onReveal: (password: string) => Promise<string>
  onClose: () => void
}) {
  const [password, setPassword] = useState('')
  const [understood, setUnderstood] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [secret, setSecret] = useState<string | null>(null)

  const reveal = async () => {
    setErr(''); setBusy(true)
    try { setSecret(await onReveal(password)) }
    catch (e) { setErr(e instanceof Error ? e.message : 'could not reveal') }
    finally { setBusy(false) }
  }

  return (
    <div
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, backdropFilter: 'blur(2px)' }}
    >
      <div onClick={(e) => e.stopPropagation()} style={{ ...card, width: 'min(560px, 92vw)', maxHeight: '86vh', overflow: 'auto' }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 8, overflowWrap: 'anywhere' }}>{title}</div>
        <div style={{ background: 'rgba(239,83,80,0.10)', border: '1px solid rgba(239,83,80,0.4)', borderRadius: 8, padding: '10px 12px', color: '#ffb4b1', fontSize: 15.5, lineHeight: 1.5, textAlign: 'center' }}>
          ⚠ {warning}
        </div>

        {secret == null ? (
          <>
            <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, margin: '14px 0 10px', fontSize: 12.5, color: '#cfd4da', cursor: 'pointer' }}>
              <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} style={{ marginTop: 2 }} />
              <span>I understand the risk and want to reveal this secret.</span>
            </label>
            <label style={lbl}>Re-enter your wallet password</label>
            <input
              type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus autoComplete="off"
              onKeyDown={(e) => { if (e.key === 'Enter' && understood && password) void reveal() }}
              placeholder="wallet password" style={input}
            />
            {err && <div style={errBox}>{err}</div>}
            <div style={{ display: 'flex', gap: 8, marginTop: 14, justifyContent: 'flex-end' }}>
              <button type="button" style={secondaryBtn} onClick={onClose}>Cancel</button>
              <button type="button" style={primaryBtn} onClick={reveal} disabled={busy || !understood || !password}>
                {busy ? 'Verifying…' : 'Reveal'}
              </button>
            </div>
          </>
        ) : (
          <>
            <SecretBox value={secret} label={secretLabel} />
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
              <button type="button" style={primaryBtn} onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// "Keys & seed" — master pubkey shown freely; recovery phrase / private key are hard-gated via RevealGate.
function KeysAndSeed({ coin }: { coin: string }) {
  const [mpk, setMpk] = useState('')
  const [mpkBusy, setMpkBusy] = useState(false)
  const [mpkErr, setMpkErr] = useState('')
  const [myAddrs, setMyAddrs] = useState<string[]>([])
  const [pkAddr, setPkAddr] = useState('')
  const [gate, setGate] = useState<null | 'seed' | 'privkey'>(null)

  useEffect(() => {
    let live = true
    getAddresses(coin, 'receiving')
      .then((r) => { if (live) setMyAddrs((r.addresses || []).map((a) => a.address)) })
      .catch(() => { /* free-text */ })
    return () => { live = false }
  }, [coin])

  const showMpk = async () => {
    setMpkErr(''); setMpkBusy(true)
    try { setMpk((await getMasterPubkey(coin)).mpk) }
    catch (e) { setMpkErr(e instanceof Error ? e.message : 'could not read master public key') }
    finally { setMpkBusy(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {/* Master public key (public) */}
      <div>
        <div style={{ fontWeight: 700, fontSize: 13 }}>Master public key (xpub)</div>
        <div style={{ color: '#8a929b', fontSize: 11, margin: '2px 0 6px' }}>Public — safe to share for watch-only imports.</div>
        <button type="button" style={secondaryBtn} onClick={showMpk} disabled={mpkBusy}>{mpkBusy ? 'Loading…' : 'Show master public key'}</button>
        <ErrorOverlay message={mpkErr} onDismiss={() => setMpkErr('')} />
        {mpk && <div style={{ ...codeBox, marginTop: 8 }}>{mpk}</div>}
      </div>

      {/* Recovery phrase (sensitive) */}
      <div>
        <div style={{ fontWeight: 700, fontSize: 13 }}>Recovery phrase (master seed)</div>
        <div style={{ color: '#8a929b', fontSize: 11, margin: '2px 0 6px' }}>The single backup phrase for ALL your coins. Never share it.</div>
        <button type="button" style={{ ...secondaryBtn, color: '#ef5350', borderColor: 'rgba(239,83,80,0.4)' }} onClick={() => setGate('seed')}>Reveal recovery phrase…</button>
      </div>

      {/* Export a private key (sensitive) */}
      <div>
        <div style={{ fontWeight: 700, fontSize: 13 }}>Export private key</div>
        <div style={{ color: '#8a929b', fontSize: 11, margin: '2px 0 6px' }}>The secret key for one of your addresses. Anyone with it can spend that address's coins.</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            value={pkAddr} onChange={(e) => setPkAddr(e.target.value)} list={`pkaddrs-${coin}`} placeholder="your address" spellCheck={false}
            style={{ ...input, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
          />
          <datalist id={`pkaddrs-${coin}`}>{myAddrs.map((a) => <option key={a} value={a} />)}</datalist>
          <button type="button" style={{ ...secondaryBtn, color: '#ef5350', borderColor: 'rgba(239,83,80,0.4)', whiteSpace: 'nowrap' }} onClick={() => setGate('privkey')} disabled={!pkAddr.trim()}>Reveal key…</button>
        </div>
      </div>

      {/* HD wallets can't import standalone keys; bring outside funds in via Sweep instead. */}
      <div style={{ color: '#8a929b', fontSize: 11 }}>
        To bring funds from an outside private key into this wallet, use <b>Advanced transactions → Sweep</b>.
        (A seed-based wallet can't import standalone keys.)
      </div>

      {gate === 'seed' && (
        <RevealGate
          title="Reveal recovery phrase"
          warning="This shows your master recovery phrase — the backup for every coin in this wallet."
          secretLabel="Recovery phrase"
          onReveal={async (pw) => (await revealSeed(coin, pw)).seed}
          onClose={() => setGate(null)}
        />
      )}
      {gate === 'privkey' && (
        <RevealGate
          title={`Reveal private key for ${pkAddr}`}
          warning="This shows the private key for this address."
          secretLabel="Private key"
          onReveal={async (pw) => (await exportPrivkey(coin, pw, pkAddr.trim())).privkey}
          onClose={() => setGate(null)}
        />
      )}
    </div>
  )
}

// Per-coin Tools tab: Electrum's "Tools" menu features, one section per group.
export default function ToolsTab({ coin }: { coin: string }) {
  const active = useStore((s) => s.toolsSection)
  const setActive = useStore((s) => s.setToolsSection)
  const tab = TOOL_TABS.find((t) => t.key === active) ?? TOOL_TABS[0]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Sub-tab bar — clicking a tool swaps the section below. */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {TOOL_TABS.map((t) => {
          const on = t.key === active
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setActive(t.key)}
              style={{
                padding: '6px 12px', fontSize: 12, fontWeight: 600, borderRadius: 8, cursor: 'pointer',
                border: on ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.12)',
                background: on ? 'rgba(var(--coin-rgb),0.18)' : 'rgba(255,255,255,0.04)',
                color: on ? '#eef2f8' : '#cfd4da',
                transition: 'background .15s, border-color .15s, color .15s',
              }}
            >
              {t.label}
            </button>
          )
        })}
      </div>

      {/* Active tool — header over content; only this tool is mounted. */}
      <section style={{ ...card, position: 'relative' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 14 }}>
          <span style={{ fontWeight: 700, fontSize: 14 }}>{tab.label}</span>
          <span style={{ color: '#8a929b', fontSize: 12, marginLeft: 'auto' }}>{tab.subtitle}</span>
        </div>
        {active === 'load' && <LoadTransaction coin={coin} />}
        {active === 'sign' && <SignVerifyMessage coin={coin} />}
        {active === 'crypto' && <EncryptDecryptMessage coin={coin} />}
        {active === 'advanced' && <AdvancedTx coin={coin} />}
        {active === 'keys' && <KeysAndSeed coin={coin} />}
        {active === 'backup' && <FullWalletBackup />}
      </section>
    </div>
  )
}
