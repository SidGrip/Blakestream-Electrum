import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import lockImg from '../assets/lock.svg'
import unlockImg from '../assets/unlock.svg'
import dexImg from '../assets/dex.png'
import { useStore } from '../store'
import { card, input, primaryBtn, secondaryBtn } from './uiKit'
import CoinIcon from './CoinIcon'
import TabBar from './TabBar'
import HistoryTab from './tabs/HistoryTab'
import SendTab from './tabs/SendTab'
import ReceiveTab from './tabs/ReceiveTab'
import AddressesTab from './tabs/AddressesTab'
import ContactsTab from './tabs/ContactsTab'
import LightningTab from './tabs/LightningTab'
import ToolsTab from './tabs/ToolsTab'
import SettingsTab from './tabs/SettingsTab'
import { formatAmount, formatFiat } from '../explorer'

// Right-hand panel for one coin: header, tab bar, active tab, Lightning footer.
export default function CoinDetail() {
  const coins = useStore((s) => s.coins)
  const selected = useStore((s) => s.selected)
  const portfolio = useStore((s) => s.portfolio)
  const activeTab = useStore((s) => s.activeTab)
  const connected = useStore((s) => s.connected)
  const setActiveTab = useStore((s) => s.setActiveTab)
  const setLightningMode = useStore((s) => s.setLightningMode)
  const fiatCurrency = useStore((s) => s.fiatCurrency)
  const priceApiConfigured = useStore((s) => s.priceApiConfigured)
  const coinStatus = useStore((s) => s.coinStatus)
  const startCoin = useStore((s) => s.startCoin)

  // A "Pay" click from Contacts stashes the address here, switches to Send.
  const [payPrefill, setPayPrefill] = useState<string | undefined>(undefined)
  // Global balance view (coin / Lightning / fiat) shared with the left rail; the toggle cycles it.
  const balanceView = useStore((s) => s.balanceView)
  const cycleBalanceView = useStore((s) => s.cycleBalanceView)

  const tickers = Object.keys(coins ?? {})
  const coin = selected && coins?.[selected] ? selected : tickers[0]

  // Clear a stale prefill when the user navigates away from Send or changes coin.
  useEffect(() => {
    if (activeTab !== 'send') setPayPrefill(undefined)
  }, [activeTab])
  useEffect(() => {
    setPayPrefill(undefined)
  }, [coin])
  useEffect(() => {
    if (activeTab === 'lightning') setLightningMode('simple')
  }, [activeTab, setLightningMode])

  if (!coin || !coins) {
    return (
      <div style={{ padding: 24, color: '#8a929b' }}>Loading…</div>
    )
  }

  const meta = coins[coin]

  // A coin the user didn't start (or stopped) has no running daemon — show a Start call-to-action
  // instead of the balance header/tabs (which would query a dead daemon and spam errors).
  const runState = coinStatus[coin] ?? 'running'
  if (runState !== 'running') {
    const starting = runState === 'starting'
    return (
      <div style={{ display: 'flex', flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 }}>
        <div style={{ ...card, maxWidth: 420, textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
          <span style={{ filter: runState === 'stopped' ? 'grayscale(1)' : undefined }}>
            <CoinIcon ticker={coin} size={48} />
          </span>
          <div style={{ fontSize: 18, fontWeight: 700, color: '#e6e6e6' }}>{meta?.coin_name ?? coin}</div>
          <div style={{ fontSize: 13, color: '#8a929b' }}>
            {runState === 'failed' ? `Couldn't start ${coin}.` : `This wallet isn't started.`}
          </div>
          <button
            type="button"
            style={{ ...primaryBtn, opacity: starting ? 0.7 : 1 }}
            disabled={starting}
            onClick={() => void startCoin(coin)}
          >
            {starting ? 'Starting…' : runState === 'failed' ? 'Retry' : 'Start wallet'}
          </button>
        </div>
      </div>
    )
  }

  const canSend = !!connected[coin]
  const balance = portfolio?.coins[coin]?.amount ?? '—'
  const valueFiat = portfolio?.coins[coin]?.value_fiat ?? null
  // While the daemon is still syncing, the balance isn't final.
  const syncing = portfolio?.coins[coin]?.synced === false

  // Header echoes the History "Type" while on History; on-chain everywhere else. onchain/lightning
  // flip coin<->fiat on click; "all" cycles on-chain -> Lightning -> fiat (fiat only with a price API).
  const pc = portfolio?.coins[coin]
  const lnSendSat = pc?.ln_can_send_sat ?? 0
  const lnRecvSat = pc?.ln_can_receive_sat ?? 0
  const unitPrice = valueFiat != null && Number(balance) > 0 ? Number(valueFiat) / Number(balance) : null
  const cat: 'onchain' | 'lightning' = balanceView === 'lightning' ? 'lightning' : 'onchain'
  const fiatNow = balanceView === 'fiat' && priceApiConfigured
  const lnText = (sat: number) =>
    fiatNow && unitPrice != null
      ? formatFiat(unitPrice * (sat / 1e8), fiatCurrency)
      : formatAmount((sat / 1e8).toFixed(8), coin, false)

  const onPay = (address: string) => {
    setPayPrefill(address)
    setActiveTab('send')
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0 }}>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '16px 22px 4px',
          background: 'rgba(34,38,43,0.58)',
          backdropFilter: 'blur(20px) saturate(170%) contrast(108%)',
          WebkitBackdropFilter: 'blur(20px) saturate(170%) contrast(108%)',
          borderBottom: '1px solid rgba(255,255,255,0.13)',
          boxShadow:
            '0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.15), inset 0 -2px 4px rgba(0,0,0,0.18)',
          flex: '0 0 auto',
        }}
      >
        <CoinIcon ticker={coin} size={38} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 26, fontWeight: 700, color: '#e6e6e6' }}>{meta.coin_name ?? coin}</div>
        </div>
        <div
          style={{
            flex: '1 1 auto',
            minWidth: 0,
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            pointerEvents: 'none',
          }}
        >
          {syncing ? (
            <span
              title="Still syncing with the server - balance is not final yet"
              style={{
                height: 20,
                display: 'inline-flex',
                alignItems: 'center',
                maxWidth: 'min(260px, 40vw)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                fontSize: 12,
                lineHeight: '20px',
                color: '#8a929b',
                background: 'rgba(255,255,255,0.06)',
                border: '1px solid rgba(255,255,255,0.14)',
                borderRadius: 7,
                padding: '0 10px',
                whiteSpace: 'nowrap',
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              }}
            >
              Syncing...
            </span>
          ) : null}
        </div>
        {/* Flips all coins between amount and fiat; fixed label+amount heights so the header doesn't jump on toggle. */}
        <button
          type="button"
          onClick={cycleBalanceView}
          title="Click to cycle coin amount / Lightning / fiat"
          style={{
            flex: '0 0 auto',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-end',
            justifyContent: 'center',
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            outline: 'none',
          }}
        >
          <div
            style={{
              minHeight: 18,
              fontSize: 11,
              lineHeight: '14px',
              color: '#8a929b',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontFamily: fiatNow ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
              textTransform: fiatNow ? 'none' : 'uppercase',
              letterSpacing: fiatNow ? 0 : 1,
            }}
          >
            {cat === 'lightning' ? '⚡ Lightning' : fiatNow ? formatAmount(balance, coin, true) : 'Balance'}
            <span style={{ fontSize: 12, opacity: 0.65 }} aria-hidden>⇄</span>
          </div>
          <div style={{ height: 30, display: 'flex', alignItems: 'center', gap: 8 }}>
            {cat === 'lightning' ? (
              <div style={{ display: 'inline-flex', alignItems: 'baseline', gap: 14, fontSize: 17, lineHeight: '30px', fontWeight: 700, color: '#e6e6e6', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontVariantNumeric: 'tabular-nums' }}>
                <span title="Can send"><span style={{ color: '#7fe0a3' }}>↑</span> {lnText(lnSendSat)}</span>
                <span title="Can receive"><span style={{ color: '#e0a23a' }}>↓</span> {lnText(lnRecvSat)}</span>
              </div>
            ) : (
              <div
                style={{
                  fontSize: 26,
                  lineHeight: '30px',
                  fontWeight: 700,
                  color: '#e6e6e6',
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                  fontVariantNumeric: 'tabular-nums',
                  textShadow: '0 0 12px color-mix(in srgb, var(--coin), transparent 70%)',
                }}
              >
                {fiatNow ? `≈ ${formatFiat(valueFiat, fiatCurrency)}` : formatAmount(balance, coin, false)}
              </div>
            )}
            {fiatNow && valueFiat == null && (
              <span
                style={{
                  fontSize: 10,
                  color: '#8a929b',
                  background: 'rgba(255,255,255,0.06)',
                  border: '1px solid rgba(255,255,255,0.12)',
                  borderRadius: 6,
                  padding: '1px 6px',
                }}
              >
                no price
              </span>
            )}
          </div>
        </button>
      </div>

      {/* Tab bar stays fixed; only the body below it scrolls. */}
      <div style={{ flex: '0 0 auto', padding: '14px 22px 0' }}>
        <TabBar />
      </div>
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 22px 22px', display: 'flex', flexDirection: 'column' }}>
        {/* Per-tab wrapper: remounts for the cross-fade, flex-fills so a tab can scroll internally. */}
        <div key={activeTab} className="tab-enter" style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          {activeTab === 'history' && <HistoryTab coin={coin} />}
          {/* key={coin} remounts on coin switch so in-progress send/invoice state doesn't carry to another coin. */}
          {activeTab === 'send' && <SendTab key={coin} coin={coin} canSend={canSend} prefill={payPrefill} />}
          {activeTab === 'receive' && <ReceiveTab key={coin} coin={coin} />}
          {activeTab === 'addresses' && <AddressesTab coin={coin} />}
          {activeTab === 'contacts' && <ContactsTab coin={coin} onPay={onPay} />}
          {activeTab === 'lightning' && <LightningTab coin={coin} />}
          {activeTab === 'tools' && <ToolsTab coin={coin} />}
          {activeTab === 'settings' && <SettingsTab coin={coin} />}
        </div>
      </div>

      <LNStatusFooter coin={coin} />
    </div>
  )
}

function LNStatusFooter({ coin }: { coin: string }) {
  const lnInfo = useStore((s) => s.coinStates[coin]?.lnInfo)
  const setActiveTab = useStore((s) => s.setActiveTab)
  const sessionLocked = useStore((s) => s.sessionLocked)
  const lockWallet = useStore((s) => s.lockWallet)
  const unlockSessionPw = useStore((s) => s.unlockSessionPw)
  const dexIntegrationAllowed = useStore((s) => s.dexIntegrationAllowed)
  const dexConnected = useStore((s) => s.dexConnected)
  const refreshDexIntegration = useStore((s) => s.refreshDexIntegration)
  const setDexIntegrationAllowed = useStore((s) => s.setDexIntegrationAllowed)
  const pushToast = useStore((s) => s.pushToast)
  const [unlockOpen, setUnlockOpen] = useState(false)
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)

  const numChannels = lnInfo?.num_channels ?? 0
  const live = numChannels > 0
  const text = live ? `Lightning: ${numChannels} channel${numChannels === 1 ? '' : 's'}` : 'Lightning: setup'

  useEffect(() => {
    if (dexIntegrationAllowed === null) void refreshDexIntegration()
  }, [dexIntegrationAllowed, refreshDexIntegration])
  useEffect(() => {
    if (dexIntegrationAllowed !== true) return undefined
    const id = window.setInterval(() => { void refreshDexIntegration() }, 5000)
    return () => window.clearInterval(id)
  }, [dexIntegrationAllowed, refreshDexIntegration])

  const onLockClick = async () => {
    if (sessionLocked) { setUnlockOpen(true); return }
    // No success toast — the chip icon flipping to the closed padlock is feedback enough.
    try { await lockWallet() }
    catch (e) { pushToast(e instanceof Error ? e.message : 'lock failed', 'warn') }
  }
  const doUnlock = async () => {
    if (!pw) return
    setBusy(true)
    try { await unlockSessionPw(pw); setPw(''); setUnlockOpen(false) }
    catch (e) { pushToast(e instanceof Error ? e.message : 'wrong password', 'warn') } finally { setBusy(false) }
  }
  const disableDex = async () => {
    try {
      await setDexIntegrationAllowed(false)
      pushToast('DEX integration disabled', 'success')
    } catch (e) {
      pushToast(e instanceof Error ? e.message : 'failed to disable DEX integration', 'warn')
    }
  }

  return (
    <div
      style={{
        display: 'flex', alignItems: 'center', width: '100%',
        background: 'rgba(20,23,27,0.6)',
        backdropFilter: 'blur(13px) saturate(140%)', WebkitBackdropFilter: 'blur(13px) saturate(140%)',
        borderTop: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      {/* Left: Lightning status — click opens the Lightning tab. */}
      <button
        type="button"
        onClick={() => setActiveTab('lightning')}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, flex: 1, padding: '8px 22px',
          background: 'transparent', border: 'none', color: live ? '#e6e6e6' : '#8a929b',
          fontSize: 12, textAlign: 'left', cursor: 'pointer',
        }}
      >
        <span style={{ color: live ? 'var(--coin)' : '#8a929b', fontSize: 14 }}>⚡</span>
        {text}
      </button>
      {/* Shows only while local DEX discovery is enabled; disabling hides the chip. */}
      {dexIntegrationAllowed === true && (
        <button
          type="button"
          onClick={() => void disableDex()}
          title={dexConnected ? 'Disconnect from DEX' : 'Waiting for DEX connection'}
          style={{
            display: 'flex', alignItems: 'center', gap: 7, padding: '8px 18px',
            background: dexConnected ? 'rgba(70,190,110,0.11)' : 'rgba(224,162,58,0.12)',
            border: 'none', borderLeft: '1px solid rgba(255,255,255,0.06)',
            color: dexConnected ? '#9ccfae' : '#e6c17a',
            fontSize: 12, cursor: 'pointer',
          }}
        >
          <img src={dexImg} alt="" width={16} height={16} style={{ display: 'block', flex: '0 0 auto', borderRadius: 4 }} />
          {dexConnected ? 'Disconnect from DEX' : 'Pending DEX'}
        </button>
      )}
      {/* Right: lock/unlock chip — toggles the session lock. */}
      {sessionLocked != null && (
        <button
          type="button"
          onClick={() => void onLockClick()}
          title={sessionLocked ? 'Locked — click to unlock' : 'Unlocked — click to lock'}
          style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '8px 22px',
            // Subtle red wash when locked, subtle green when unlocked.
            background: sessionLocked ? 'rgba(220,72,60,0.12)' : 'rgba(70,190,110,0.11)',
            border: 'none', borderLeft: '1px solid rgba(255,255,255,0.06)',
            color: sessionLocked ? '#e3a39a' : '#9ccfae', fontSize: 12, cursor: 'pointer',
          }}
        >
          <img src={sessionLocked ? lockImg : unlockImg} alt="" width={16} height={16} style={{ display: 'block', flex: '0 0 auto' }} />
          {sessionLocked ? 'Locked' : 'Unlocked'}
        </button>
      )}
      {/* Portal to <body>: the footer's backdrop-filter is a containing block, which would otherwise pin this fixed overlay to the footer strip. */}
      {unlockOpen && createPortal(
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 16 }}>
          <div style={{ ...card, width: 'min(380px, 92vw)' }}>
            <h3 style={{ fontSize: 13, fontWeight: 700, margin: '0 0 4px', color: '#e6e6e6' }}>Unlock wallet</h3>
            <p style={{ fontSize: 11, color: '#8a929b', margin: '0 0 8px' }}>Enter your password to re-enable signing &amp; reveal.</p>
            <input type="password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void doUnlock()} placeholder="Password" autoComplete="off" style={input} />
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button type="button" style={secondaryBtn} disabled={busy} onClick={() => { setUnlockOpen(false); setPw('') }}>Cancel</button>
              <button type="button" style={{ ...primaryBtn, marginLeft: 'auto' }} disabled={busy || !pw} onClick={() => void doUnlock()}>{busy ? 'Unlocking…' : 'Unlock'}</button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
