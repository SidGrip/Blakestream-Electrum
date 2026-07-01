import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getLnStatus, getLnChannels, getLnPeers, enableLn,
  openChannel, closeChannel,
  exportChannelBackup, importChannelBackup, requestForceClose, addLnPeer,
} from '../../api'
import { useStore } from '../../store'
import type { LnChannel, LnInfo, LnPeer } from '../../types'
import { Th, Td } from '../tableCells'
import { lbl, input, primaryBtn, secondaryBtn, errBox, codeBox, card } from '../uiKit'
import ErrorOverlay from '../ErrorOverlay'
import { formatAmount } from '../../explorer'

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
function asStr(v: unknown): string {
  if (v == null) return '—'
  return String(v)
}
// Daemon reports channel balances in sats; render as coin units.
function sats(v: unknown, coin: string): string {
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isFinite(n)) return '—'
  return formatAmount((n / 1e8).toFixed(8), coin)
}
function short(s: unknown, n = 16): string {
  const v = asStr(s)
  return v.length > n ? v.slice(0, n) + '…' : v
}
function channelPoint(ch: LnChannel): string {
  return String(ch.channel_point ?? ch.channel_id ?? ch.short_channel_id ?? '')
}
function channelIsOpening(ch: LnChannel): boolean {
  const state = String(ch.state ?? '').toUpperCase()
  return ['PREOPENING', 'OPENING', 'FUNDED'].includes(state)
}
function channelIsOpen(ch: LnChannel): boolean {
  return String(ch.state ?? '').toUpperCase() === 'OPEN'
}
function channelIsBackup(ch: LnChannel): boolean {
  return String(ch.type ?? '').toUpperCase() === 'BACKUP'
}
function openChannelErrMsg(coin: string, e: unknown): string {
  const msg = errMsg(e)
  if (/Refusing to reuse multisig_funding_keypair|Wait one block/i.test(msg)) {
    return `${coin} already has a channel opening. Wait for one ${coin} block confirmation before opening another ${coin} channel. You can still open channels on other coins.`
  }
  if (/TimeoutError/i.test(msg)) {
    return `${coin} channel open timed out while waiting for the peer/RPC. If an OPENING row appears, let it finish syncing; otherwise retry when the hub is reachable.`
  }
  return msg
}
function coinAmount(n: number): string {
  return n.toFixed(8).replace(/\.?0+$/, '')
}

type ChannelUiState = {
  connStr: string
  amount: string
  receiveCapacity: boolean
  receivePct: string
  busy: boolean
  error: string | null
  importing: boolean
  importStr: string
  showImport: boolean
}
const EMPTY_CHANNEL_UI: ChannelUiState = {
  connStr: '',
  amount: '',
  receiveCapacity: false,
  receivePct: '40',
  busy: false,
  error: null,
  importing: false,
  importStr: '',
  showImport: false,
}

type PeerUiState = {
  cs: string
  busy: boolean
  error: string | null
}
const EMPTY_PEER_UI: PeerUiState = {
  cs: '',
  busy: false,
  error: null,
}

type ConfirmDialogState = {
  title: string
  body: React.ReactNode
  confirmLabel: string
  danger?: boolean
}

// Per-coin Lightning tab: direct-channels-only (no routing/trampoline/swaps/LNURL — no LN graph).
export default function LightningTab({ coin }: { coin: string }) {
  const lnInfo = useStore((s) => s.coinStates[coin]?.lnInfo)
  const lightningMode = useStore((s) => s.lightningMode)
  const setLightningMode = useStore((s) => s.setLightningMode)
  const [info, setInfo] = useState<LnInfo | null>(lnInfo ?? null)
  const [channels, setChannels] = useState<LnChannel[]>([])
  const [peers, setPeers] = useState<LnPeer[]>([])
  const activeCoinRef = useRef(coin)

  const refresh = useCallback(async () => {
    const targetCoin = coin
    const [st, chans, prs] = await Promise.all([
      getLnStatus(targetCoin).catch(() => null),
      getLnChannels(targetCoin).then((r) => r.channels ?? []).catch(() => [] as LnChannel[]),
      getLnPeers(targetCoin).then((r) => r.peers ?? []).catch(() => [] as LnPeer[]),
    ])
    if (activeCoinRef.current !== targetCoin) return
    if (st) setInfo(st)
    setChannels(chans)
    setPeers(prs)
  }, [coin])

  useEffect(() => {
    activeCoinRef.current = coin
    setInfo(lnInfo ?? null)
    setChannels([])
    setPeers([])
    let live = true
    void (async () => {
      if (!lnInfo?.enabled) {
        try { await enableLn(coin) } catch { /* still starting; reads below degrade gracefully */ }
      }
      if (!live) return
      await refresh()
    })()
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coin])

  const [lnTab, setLnTab] = useState<'channels' | 'peers'>('channels')
  const visibleInfo = activeCoinRef.current === coin ? info : (lnInfo ?? null)
  const visibleChannels = activeCoinRef.current === coin ? channels : []
  const visiblePeers = activeCoinRef.current === coin ? peers : []
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <ModeToggle mode={lightningMode} onChange={setLightningMode} />
      {lightningMode === 'simple' ? (
        <LightningSimplePanel
          coin={coin}
          info={visibleInfo}
          channels={visibleChannels}
          hub={visibleInfo?.hub ?? null}
          onChanged={() => void refresh()}
        />
      ) : (
        <>
          <Dashboard coin={coin} info={visibleInfo} />
          {/* Sub-tabs */}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {([['channels', `Channels · ${visibleChannels.length}`], ['peers', `Peers · ${visiblePeers.length}`]] as const).map(([k, label]) => {
              const on = lnTab === k
              return (
                <button
                  key={k}
                  type="button"
                  onClick={() => setLnTab(k)}
                  style={{
                    padding: '6px 12px', fontSize: 12, fontWeight: 600, borderRadius: 8, cursor: 'pointer',
                    border: on ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.12)',
                    background: on ? 'rgba(var(--coin-rgb),0.18)' : 'rgba(255,255,255,0.04)',
                    color: on ? '#eef2f8' : '#cfd4da',
                    transition: 'background .15s, border-color .15s, color .15s',
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
          {lnTab === 'channels' ? (
            <ChannelsSection coin={coin} channels={visibleChannels} hub={visibleInfo?.hub ?? null} hubChannel={visibleInfo?.hub_channel ?? false} onChanged={() => void refresh()} />
          ) : (
            <PeersSection coin={coin} peers={visiblePeers} onChanged={() => void refresh()} />
          )}
        </>
      )}
    </div>
  )
}

function ModeToggle({
  mode, onChange,
}: {
  mode: 'simple' | 'advanced'
  onChange: (mode: 'simple' | 'advanced') => void
}) {
  return (
    <div style={{ display: 'flex', gap: 6, maxWidth: 340 }}>
      {(['simple', 'advanced'] as const).map((m) => {
        const on = mode === m
        return (
          <button
            key={m}
            type="button"
            onClick={() => onChange(m)}
            style={{
              flex: 1,
              padding: '6px 10px',
              fontSize: 12,
              fontWeight: 700,
              borderRadius: 8,
              cursor: 'pointer',
              border: on ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.12)',
              background: on ? 'rgba(var(--coin-rgb),0.18)' : 'rgba(255,255,255,0.04)',
              color: on ? '#eef2f8' : '#cfd4da',
              transition: 'background .15s, border-color .15s, color .15s',
            }}
          >
            {m === 'simple' ? 'Simple' : 'Advanced'}
          </button>
        )
      })}
    </div>
  )
}

function Dashboard({ coin, info }: { coin: string; info: LnInfo | null }) {
  const stat = (label: string, value: string) => (
    <div style={{ minWidth: 130 }}>
      <div style={{ fontSize: 11, color: '#8a929b' }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 700, color: '#e6e6e6', marginTop: 2 }}>{value}</div>
    </div>
  )
  return (
    <section style={{ ...card, background: 'rgba(79,195,247,0.06)', border: '1px solid rgba(79,195,247,0.3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <span style={{ color: '#4fc3f7', fontSize: 18 }}>⚡</span>
        <strong style={{ fontSize: 14 }}>Lightning</strong>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: '#8a929b' }}>
          {info?.enabled ? 'enabled' : 'starting…'}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
        {stat('Can send', sats(info?.can_send_sat ?? 0, coin))}
        {stat('Can receive', sats(info?.can_receive_sat ?? 0, coin))}
        {stat('Open channels', String(info?.num_channels ?? 0))}
        {info?.num_backups ? stat('Backups', String(info.num_backups)) : null}
      </div>
      {info?.node_id && (
        <div style={{ marginTop: 12 }}>
          <div style={{ ...lbl, marginTop: 0 }}>Your node id (share to receive a channel)</div>
          <div style={codeBox}>{info.node_id}</div>
        </div>
      )}
      <div style={{ marginTop: 10, fontSize: 11, color: '#8a929b' }}>
        ⓘ Direct channels only on {coin} — there is no public routing, trampoline, swap or LNURL network here.
        Open a channel to a peer you connect to, and back it up so funds are recoverable.
      </div>
    </section>
  )
}

function LightningSimplePanel({
  coin, info, channels, hub, onChanged,
}: {
  coin: string
  info: LnInfo | null
  channels: LnChannel[]
  hub: string | null
  onChanged: () => void
}) {
  const pushToast = useStore((s) => s.pushToast)
  const [amount, setAmount] = useState('')
  const [receivePct, setReceivePct] = useState('40')
  const [setupAction, setSetupAction] = useState<'send' | 'receive' | null>(null)
  const [busy, setBusy] = useState<'send' | 'receive' | 'withdraw' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialogState | null>(null)
  const [cashoutPickerOpen, setCashoutPickerOpen] = useState(false)
  const confirmResolver = useRef<((ok: boolean) => void) | null>(null)

  const askConfirm = useCallback((dialog: ConfirmDialogState) => new Promise<boolean>((resolve) => {
    confirmResolver.current = resolve
    setConfirmDialog(dialog)
  }), [])
  const settleConfirm = useCallback((ok: boolean) => {
    confirmResolver.current?.(ok)
    confirmResolver.current = null
    setConfirmDialog(null)
  }, [])

  const openingChannel = channels.some(channelIsOpening)
  const cashoutChannels = channels.filter((ch) => !channelIsBackup(ch) && channelIsOpen(ch) && channelPoint(ch))
  const amountN = /^\d+(\.\d+)?$/.test(amount.trim()) ? Number(amount.trim()) : null
  const receivePctN = Number(receivePct)
  const receivePush = amountN != null ? amountN * receivePctN / 100 : 0
  const sendAfterReceive = amountN != null ? Math.max(0, amountN - receivePush) : 0

  useEffect(() => {
    if (openingChannel && setupAction) setSetupAction(null)
  }, [openingChannel, setupAction])

  const refreshWalletState = () => {
    onChanged()
    void useStore.getState().fetchCoinData(coin, { background: true })
  }

  const openSimpleChannel = async (kind: 'send' | 'receive') => {
    setError(null)
    if (!hub) {
      return setError(`No ${coin} hub is configured. Use Advanced to connect a custom peer.`)
    }
    if (openingChannel) {
      return setError(`${coin} already has a channel opening. Wait for one ${coin} block confirmation before opening another ${coin} channel.`)
    }
    if (amountN == null || amountN <= 0) return setError('Enter a valid channel amount.')
    if (amountN < 0.5) return setError(`Use at least 0.5 ${coin} for a Lightning channel.`)
    const pushAmount = kind === 'receive' ? coinAmount(receivePush) : ''
    const sendPreview = kind === 'receive' ? coinAmount(sendAfterReceive) : coinAmount(amountN)
    const receivePreview = kind === 'receive' ? coinAmount(receivePush) : '0'
    const confirmed = await askConfirm({
      title: kind === 'receive' ? `Add ${coin} receive balance?` : `Add ${coin} send balance?`,
      body: (
        <>
          <p>You will lock <strong>{coinAmount(amountN)} {coin}</strong> into a Lightning channel.</p>
          <p>
            Estimated result: you can send about <strong>{sendPreview} {coin}</strong>
            {kind === 'receive'
              ? <> and receive about <strong>{receivePreview} {coin}</strong></>
              : null}.
          </p>
          <p>Opening a channel uses an on-chain transaction and needs confirmations before it is ready.</p>
        </>
      ),
      confirmLabel: 'Open channel',
    })
    if (!confirmed) return
    setBusy(kind)
    try {
      await openChannel(coin, hub, coinAmount(amountN), pushAmount)
      setAmount('')
      setSetupAction(null)
      pushToast(
        kind === 'receive'
          ? `Channel opening: about ${sendPreview} ${coin} send / ${receivePreview} ${coin} receive`
          : `Channel opening: about ${sendPreview} ${coin} spending room`,
        'success',
      )
      refreshWalletState()
    } catch (e) {
      setError(openChannelErrMsg(coin, e))
    } finally {
      setBusy(null)
    }
  }

  const withdrawChannel = async (ch: LnChannel) => {
    const point = channelPoint(ch)
    if (!point) return
    if (!channelIsOpen(ch)) {
      setError('Only fully open channels can be cashed out here. Use Advanced tools for pending or recovery channels.')
      return
    }
    setError(null)
    const local = sats(ch.local_balance ?? 0, coin)
    const channelName = short(ch.short_channel_id ?? point, 28)
    const ok = await askConfirm({
      title: 'Cash Out Channel to On-Chain Wallet?',
      body: (
        <>
          <p>You are permanently closing channel <strong>{channelName}</strong>.</p>
          <p>
            About <strong style={{ color: '#f7f9fc' }}>{local}</strong> returns to your on-chain wallet after the close transaction confirms.
          </p>
          <p style={{ textAlign: 'center' }}>
            <span style={{ display: 'block' }}>Small on-chain fees are deducted, the hub must be online.</span>
            <span style={{ display: 'block' }}>The channel cannot be reopened with the same state.</span>
          </p>
          <p style={{ textAlign: 'center', color: '#f3c85a', fontSize: 16, fontWeight: 700 }}>This action cannot be undone.</p>
        </>
      ),
      confirmLabel: 'Close & Withdraw',
      danger: true,
    })
    if (!ok) return
    setBusy('withdraw')
    try {
      await closeChannel(coin, point, false)
      pushToast('Channel close broadcast. Funds will arrive on-chain after confirmation.', 'success')
      refreshWalletState()
    } catch (e) {
      setError(errMsg(e))
    } finally {
      setBusy(null)
    }
  }

  const startCashout = () => {
    setError(null)
    if (cashoutChannels.length === 0) {
      setError('There are no fully open channels to cash out yet.')
      return
    }
    if (cashoutChannels.length === 1) {
      void withdrawChannel(cashoutChannels[0])
      return
    }
    setCashoutPickerOpen(true)
  }

  const selectSetupAction = (kind: 'send' | 'receive') => {
    setError(null)
    setSetupAction((current) => current === kind ? null : kind)
  }

  const capacityCard = (label: string, value: string, note: string, tone: 'send' | 'receive') => (
    <div style={{
      flex: 1,
      minWidth: 180,
      padding: '12px 14px',
      borderRadius: 10,
      border: tone === 'send' ? '1px solid rgba(95,211,138,0.26)' : '1px solid rgba(79,195,247,0.28)',
      background: tone === 'send' ? 'rgba(95,211,138,0.08)' : 'rgba(79,195,247,0.07)',
    }}>
      <div style={{ fontSize: 11, color: '#8a929b', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 800, color: '#eef2f8' }}>{value}</div>
      <div style={{ fontSize: 11, color: '#8a929b', marginTop: 6, lineHeight: 1.35 }}>{note}</div>
    </div>
  )

  return (
    <section style={{ ...card, display: 'flex', flexDirection: 'column', gap: 14, position: 'relative' }}>
      {confirmDialog && (
        <ConfirmDialog
          dialog={confirmDialog}
          onCancel={() => settleConfirm(false)}
          onConfirm={() => settleConfirm(true)}
        />
      )}
      {cashoutPickerOpen && (
        <CashoutPickerDialog
          coin={coin}
          channels={cashoutChannels}
          onCancel={() => setCashoutPickerOpen(false)}
          onPick={(ch) => {
            setCashoutPickerOpen(false)
            void withdrawChannel(ch)
          }}
        />
      )}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ color: '#fbbc04', fontSize: 18 }}>⚡</span>
            <strong style={{ fontSize: 16 }}>Lightning setup</strong>
            <span style={{ marginLeft: 'auto', color: info?.enabled ? '#7fe0a3' : '#efb15f', fontSize: 11 }}>
              {info?.enabled ? 'ready' : 'starting'}
            </span>
          </div>
          <p style={{ color: '#8a929b', fontSize: 12, lineHeight: 1.5, margin: '8px 0 0' }}>
            Use this when a Lightning trade says you need more spending or receiving room. Advanced mode keeps peer,
            backup, and force-close controls.
          </p>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {capacityCard('You can pay/spend right now', sats(info?.can_send_sat ?? 0, coin), 'Available for Lightning invoices and trades you send.', 'send')}
        {capacityCard('You can receive right now', sats(info?.can_receive_sat ?? 0, coin), 'Available for Lightning payments and trades you receive.', 'receive')}
      </div>

      <div
        className={setupAction ? 'send-flip' : undefined}
        style={{
          display: 'grid',
          gridTemplateColumns: setupAction ? '1fr' : 'repeat(3, minmax(220px, 1fr))',
          gap: 10,
        }}
      >
        {setupAction ? (
          <SetupActionPanel
            coin={coin}
            kind={setupAction}
            amount={amount}
            setAmount={setAmount}
            receivePct={receivePct}
            setReceivePct={setReceivePct}
            sendAfterReceive={sendAfterReceive}
            receivePush={receivePush}
            disabled={busy != null || openingChannel}
            busy={busy === setupAction}
            onSubmit={() => void openSimpleChannel(setupAction)}
            onCancel={() => setSetupAction(null)}
          />
        ) : (
          <>
            <div style={{ position: 'relative', display: 'grid', gridTemplateColumns: 'repeat(2, minmax(220px, 1fr))', gap: 10, gridColumn: 'span 2' }}>
              <ActionBox
                title="Fund for Sending Payments"
                text="Best when you want to pay Lightning invoices or trades."
                button="Fund sending"
                disabled={busy != null || openingChannel || !hub}
                busy={busy === 'send'}
                onClick={() => selectSetupAction('send')}
              />
              <ActionBox
                title="Enable Receiving Payments"
                text="Best when you need to accept Lightning payments or trades."
                button="Enable receiving"
                disabled={busy != null || openingChannel || !hub}
                busy={busy === 'receive'}
                onClick={() => selectSetupAction('receive')}
              />
              {openingChannel && <OpeningChannelOverlay coin={coin} />}
            </div>
            <ActionBox
              title="Cash Out to On-Chain Wallet"
              text="Cash out closes a channel and returns its funds to your on-chain wallet after network confirmation."
              button={cashoutChannels.length > 1 ? `Choose channel (${cashoutChannels.length})` : cashoutChannels.length === 1 ? 'Cash out channel' : 'No open channel yet'}
              disabled={busy != null || cashoutChannels.length === 0}
              busy={busy === 'withdraw'}
              onClick={startCashout}
            />
          </>
        )}
      </div>

      {!hub && (
        <div style={errBox}>No {coin} hub is configured. Open Advanced tools to connect a custom peer.</div>
      )}
      <ErrorOverlay message={error} onDismiss={() => setError(null)} />

      <div>
        <div style={{ fontSize: 12, fontWeight: 800, marginBottom: 8 }}>Channels</div>
        {channels.length === 0 ? (
          <div style={{ ...codeBox, color: '#8a929b' }}>No Lightning channels yet.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 314, overflowY: 'auto', paddingRight: 4 }}>
            {channels.map((ch, i) => {
              const point = channelPoint(ch)
              const state = asStr(ch.state)
              const canWithdraw = channelIsOpen(ch) && !!point && !channelIsBackup(ch)
              return (
                <div
                  key={point || i}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'minmax(140px, 1fr) minmax(180px, 1.3fr) auto',
                    gap: 10,
                    alignItems: 'center',
                    padding: '9px 10px',
                    borderRadius: 8,
                    border: '1px solid rgba(255,255,255,0.10)',
                    background: 'rgba(20,23,27,0.45)',
                    fontSize: 12,
                  }}
                >
                  <div>
                    <div style={{ color: '#cfd4da', fontFamily: 'ui-monospace, monospace' }}>{short(ch.remote_pubkey, 18)}</div>
                    <div style={{ color: channelIsOpen(ch) ? '#7fe0a3' : '#efb15f', fontSize: 11, marginTop: 2 }}>{state}</div>
                  </div>
                  <div style={{ color: '#8a929b', fontFamily: 'ui-monospace, monospace' }}>
                    send {sats(ch.local_balance ?? 0, coin)} / receive {sats(ch.remote_balance ?? 0, coin)}
                  </div>
                  <button
                    type="button"
                    style={{
                      ...secondaryBtn,
                      fontSize: 11,
                      padding: '3px 10px',
                      opacity: canWithdraw ? 1 : 0.45,
                      cursor: canWithdraw ? 'pointer' : 'not-allowed',
                    }}
                    disabled={!canWithdraw || busy != null}
                    onClick={() => void withdrawChannel(ch)}
                  >
                    {busy === 'withdraw' ? 'Cashing out…' : 'Cash out'}
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </section>
  )
}

function CashoutPickerDialog({
  coin, channels, onCancel, onPick,
}: {
  coin: string
  channels: LnChannel[]
  onCancel: () => void
  onPick: (ch: LnChannel) => void
}) {
  return (
    <div
      role="presentation"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 70,
        background: 'rgba(5,7,10,0.62)',
        backdropFilter: 'blur(8px) saturate(135%)',
        WebkitBackdropFilter: 'blur(8px) saturate(135%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 18,
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="ln-cashout-title"
        style={{
          ...card,
          width: 'min(620px, 94vw)',
          padding: 18,
          borderRadius: 12,
          background: 'rgba(24,28,33,0.94)',
          boxShadow: '0 24px 70px rgba(0,0,0,0.58), 0 0 0 1px rgba(var(--coin-rgb),0.18), inset 0 1px 0 rgba(255,255,255,0.13)',
        }}
      >
        <h3 id="ln-cashout-title" style={{ margin: '0 0 8px', color: '#eef2f8', fontSize: 16, letterSpacing: 0 }}>
          Choose a {coin} Channel to Cash Out
        </h3>
        <p style={{ color: '#8a929b', fontSize: 12, lineHeight: 1.5, margin: '0 0 12px' }}>
          Cashing out closes the selected channel and returns its funds to your on-chain wallet after network confirmation.
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: '52vh', overflowY: 'auto' }}>
          {channels.map((ch, i) => {
            const point = channelPoint(ch)
            const total = sats(Number(ch.local_balance ?? 0) + Number(ch.remote_balance ?? 0), coin)
            return (
              <button
                key={point || i}
                type="button"
                onClick={() => onPick(ch)}
                style={{
                  width: '100%',
                  textAlign: 'left',
                  padding: '10px 12px',
                  borderRadius: 8,
                  border: '1px solid rgba(255,255,255,0.12)',
                  background: 'rgba(20,23,27,0.58)',
                  color: '#cfd4da',
                  cursor: 'pointer',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
                  <span style={{ fontFamily: 'ui-monospace, monospace', color: '#eef2f8' }}>{short(ch.remote_pubkey, 24)}</span>
                  <span style={{ color: '#7fe0a3', fontSize: 11 }}>{asStr(ch.state)}</span>
                </div>
                <div style={{ fontFamily: 'ui-monospace, monospace', color: '#8a929b', fontSize: 12, marginTop: 5 }}>
                  spend {sats(ch.local_balance ?? 0, coin)} / receive {sats(ch.remote_balance ?? 0, coin)} / channel {total}
                </div>
              </button>
            )
          })}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
          <button type="button" style={secondaryBtn} onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

function OpeningChannelOverlay({ coin }: { coin: string }) {
  return (
    <div style={{
      position: 'absolute',
      inset: 0,
      zIndex: 2,
      padding: '18px 20px',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      textAlign: 'center',
      gap: 8,
      color: '#efb15f',
      background: 'rgba(24,22,14,0.78)',
      border: '1px solid rgba(251,188,4,0.38)',
      borderRadius: 10,
      boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.08), 0 12px 28px rgba(0,0,0,0.28)',
      fontSize: 15,
      lineHeight: 1.35,
      fontWeight: 700,
      pointerEvents: 'auto',
      cursor: 'default',
    }}>
      <div>Waiting for a {coin} channel to confirm.</div>
      <div>You can open another {coin} channel after one block.</div>
    </div>
  )
}

function ActionBox({
  title, text, button, disabled, busy, onClick, children,
}: {
  title: string
  text: string
  button: string
  disabled?: boolean
  busy?: boolean
  onClick: () => void
  children?: React.ReactNode
}) {
  return (
    <div style={{
      padding: 12,
      borderRadius: 10,
      border: '1px solid rgba(255,255,255,0.11)',
      background: 'rgba(20,23,27,0.42)',
      minHeight: 124,
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
    }}>
      <div style={{ fontSize: 13, fontWeight: 800, color: '#eef2f8' }}>{title}</div>
      <p style={{ color: '#8a929b', fontSize: 11, lineHeight: 1.45, margin: 0, flex: 1 }}>{text}</p>
      {children}
      <button
        type="button"
        style={{ ...primaryBtn, alignSelf: 'flex-start', opacity: disabled ? 0.48 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}
        disabled={disabled}
        onClick={onClick}
      >
        {busy ? 'Working…' : button}
      </button>
    </div>
  )
}

function SetupActionPanel({
  coin,
  kind,
  amount,
  setAmount,
  receivePct,
  setReceivePct,
  sendAfterReceive,
  receivePush,
  disabled,
  busy,
  onSubmit,
  onCancel,
}: {
  coin: string
  kind: 'send' | 'receive'
  amount: string
  setAmount: (amount: string) => void
  receivePct: string
  setReceivePct: (pct: string) => void
  sendAfterReceive?: number
  receivePush?: number
  disabled?: boolean
  busy: boolean
  onSubmit: () => void
  onCancel: () => void
}) {
  const amountN = /^\d+(\.\d+)?$/.test(amount.trim()) ? Number(amount.trim()) : null
  return (
    <div
      style={{
        ...card,
        minHeight: 140,
        padding: 16,
        display: 'grid',
        gridTemplateColumns: 'minmax(240px, 0.48fr) minmax(0, 1.52fr)',
        gap: 20,
        alignItems: 'start',
        background: 'rgba(20,23,27,0.42)',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: '#eef2f8' }}>
          {kind === 'receive' ? 'Enable Receiving Payments' : 'Fund for Sending Payments'}
        </div>
        <p style={{ color: '#8a929b', fontSize: 12, lineHeight: 1.45, margin: 0 }}>
          {kind === 'receive'
            ? 'Open a channel that gives you room to accept Lightning payments or trades.'
            : 'Open a channel that gives you room to pay Lightning invoices or trades.'}
        </p>
        <div style={{ marginTop: 'auto', color: '#8a929b', fontSize: 11, lineHeight: 1.45 }}>
          Opening a channel locks on-chain funds into Lightning until you cash out the channel.
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: '100%', maxWidth: 720, minWidth: 0 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '260px minmax(380px, 1fr)', columnGap: 20, alignItems: 'start', minWidth: 0 }}>
        <div>
          <label style={{ ...lbl, marginTop: 0 }}>Amount to lock ({coin})</label>
          <input
            style={input}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="0.0"
            inputMode="decimal"
            autoComplete="off"
          />
        </div>
        <div style={{ minWidth: 0 }}>
          <label style={{ ...lbl, marginTop: 0 }}>Preview</label>
          <div
            style={{
              ...codeBox,
              padding: '3px 12px',
              minWidth: 0,
              minHeight: 28,
              display: 'flex',
              alignItems: 'center',
              fontSize: 11,
              whiteSpace: 'nowrap',
              color: amountN != null && amountN > 0 ? '#cfd4da' : '#8a929b',
            }}
          >
            {amountN != null && amountN > 0
              ? kind === 'receive'
                ? <>send about {coinAmount(sendAfterReceive ?? 0)} {coin}; receive about {coinAmount(receivePush ?? 0)} {coin}.</>
                : <>send about {coinAmount(amountN)} {coin}; receive about 0 {coin}.</>
              : 'Enter an amount to preview the channel.'}
          </div>
        </div>
        </div>
        {kind === 'receive' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'nowrap', whiteSpace: 'nowrap', overflow: 'visible' }}>
            <span style={{ color: '#8a929b', fontSize: 11 }}>Receiving split</span>
            {(['25', '40', '60'] as const).map((pct) => (
              <button
                key={pct}
                type="button"
                onClick={() => setReceivePct(pct)}
                style={{
                  padding: '4px 10px',
                  borderRadius: 999,
                  cursor: 'pointer',
                  fontSize: 11,
                  fontWeight: 700,
                  border: receivePct === pct ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.12)',
                  background: receivePct === pct ? 'rgba(var(--coin-rgb),0.18)' : 'rgba(255,255,255,0.04)',
                  color: receivePct === pct ? '#eef2f8' : '#cfd4da',
                }}
              >
                {pct === '25' ? 'Light 25%' : pct === '40' ? 'Balanced 40%' : 'Heavy 60%'}
              </button>
            ))}
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
          <button
            type="button"
            style={{ ...primaryBtn, minWidth: 210, opacity: disabled ? 0.48 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}
            disabled={disabled}
            onClick={onSubmit}
          >
            {busy ? 'Opening…' : kind === 'receive' ? 'Open receive channel' : 'Open send channel'}
          </button>
          <button
            type="button"
            style={{ ...secondaryBtn, minWidth: 100 }}
            disabled={busy}
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

function ChannelsSection({
  coin, channels, hub, hubChannel, onChanged,
}: {
  coin: string
  channels: LnChannel[]
  hub: string | null
  hubChannel: boolean
  onChanged: () => void
}) {
  const pushToast = useStore((s) => s.pushToast)
  const [uiByCoin, setUiByCoin] = useState<Record<string, ChannelUiState>>({})
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialogState | null>(null)
  const confirmResolver = useRef<((ok: boolean) => void) | null>(null)
  const ui = uiByCoin[coin] ?? EMPTY_CHANNEL_UI
  const patchUi = useCallback((targetCoin: string, patch: Partial<ChannelUiState>) => {
    setUiByCoin((prev) => ({
      ...prev,
      [targetCoin]: { ...EMPTY_CHANNEL_UI, ...(prev[targetCoin] ?? {}), ...patch },
    }))
  }, [])
  const askConfirm = useCallback((dialog: ConfirmDialogState) => new Promise<boolean>((resolve) => {
    confirmResolver.current = resolve
    setConfirmDialog(dialog)
  }), [])
  const settleConfirm = useCallback((ok: boolean) => {
    confirmResolver.current?.(ok)
    confirmResolver.current = null
    setConfirmDialog(null)
  }, [])

  const open = async () => {
    const targetCoin = coin
    const cs = ui.connStr.trim()
    const channelAmount = ui.amount.trim()
    const channelN = Number(channelAmount)
    const receivePct = Number(ui.receivePct || '40')
    const pushN = ui.receiveCapacity ? channelN * receivePct / 100 : 0
    const pushAmount = pushN > 0 ? coinAmount(pushN) : ''
    patchUi(targetCoin, { error: null })
    if (!cs) return patchUi(targetCoin, { error: 'Enter a peer (node_id@host:port).' })
    if (!/^\d+(\.\d+)?$/.test(channelAmount) || channelN <= 0) return patchUi(targetCoin, { error: 'Enter a valid channel amount.' })
    if (ui.receiveCapacity && (![20, 40, 60].includes(receivePct))) return patchUi(targetCoin, { error: 'Choose a valid receive capacity split.' })
    if (channels.some(channelIsOpening)) {
      return patchUi(targetCoin, { error: `${targetCoin} already has a channel opening. Wait for one ${targetCoin} block confirmation before opening another ${targetCoin} channel. Other coins can still open their own channels.` })
    }
    if (channelN < 0.5 && !(await askConfirm({
      title: 'Open small channel?',
      body: <p>Small {targetCoin} channels may fail or cost too much in fees.</p>,
      confirmLabel: 'Open anyway',
    }))) return
    if (hub && cs !== hub && !(await askConfirm({
      title: `Open ${targetCoin} channel to custom peer?`,
      body: <p>Only use a custom peer if you trust it and know the exact node address.</p>,
      confirmLabel: 'Open channel',
    }))) return
    // Fat-finger guard: cap is up to max supply, so warn before an accidentally huge channel.
    if (channelN > 1_000_000 && !(await askConfirm({
      title: 'Open very large channel?',
      body: <p>Open a {targetCoin} channel of {channelAmount}? Real channels are usually tiny; please confirm this amount.</p>,
      confirmLabel: 'Open channel',
    }))) return
    if (pushN > 0 && !(await askConfirm({
      title: 'Open receive-capacity channel?',
      body: (
        <>
          <p>You will lock <strong>{channelAmount} {targetCoin}</strong> into a Lightning channel.</p>
          <p>The peer/hub gets <strong>{pushAmount} {targetCoin}</strong> starting balance. You get about <strong>{pushAmount} {targetCoin}</strong> immediate receive capacity.</p>
          <p>This cannot be undone without closing the channel later.</p>
        </>
      ),
      confirmLabel: 'Open channel',
    }))) return
    patchUi(targetCoin, { busy: true })
    try {
      await openChannel(targetCoin, cs, channelAmount, pushAmount)
      patchUi(targetCoin, { connStr: '', amount: '', receiveCapacity: false, receivePct: '40', error: null })
      if (pushN > 0) {
        pushToast(`Channel opening: about ${coinAmount(channelN - pushN)} ${targetCoin} send / ${pushAmount} ${targetCoin} receive`, 'success')
      } else {
        pushToast(`Channel opening: about ${channelAmount} ${targetCoin} send capacity`, 'success')
      }
      onChanged()
    } catch (e) {
      patchUi(targetCoin, { error: openChannelErrMsg(targetCoin, e) })
    } finally {
      patchUi(targetCoin, { busy: false })
    }
  }
  const amountPreview = /^\d+(\.\d+)?$/.test(ui.amount.trim()) ? Number(ui.amount.trim()) : null
  const receivePctPreview = Number(ui.receivePct || '40')
  const pushPreview = ui.receiveCapacity && amountPreview != null ? amountPreview * receivePctPreview / 100 : 0
  const showPushPreview = amountPreview != null && amountPreview > 0 && pushPreview >= 0 && pushPreview < amountPreview
  const previewSend = showPushPreview ? Math.max(0, amountPreview - pushPreview) : null
  const close = async (point: string, force: boolean) => {
    const targetCoin = coin
    patchUi(targetCoin, { error: null })
    if (force && !(await askConfirm({
      title: `Force-close ${targetCoin} channel?`,
      body: <p>Your funds are time-locked until the on-chain delay passes. Use cooperative close if the peer is online.</p>,
      confirmLabel: 'Force close',
      danger: true,
    }))) return
    try { await closeChannel(targetCoin, point, force); pushToast(`${force ? 'Force-' : ''}close broadcast ✓`, 'success'); onChanged() }
    catch (e) { patchUi(targetCoin, { error: errMsg(e) }) }
  }
  const requestClose = async (point: string) => {
    const targetCoin = coin
    patchUi(targetCoin, { error: null })
    const cs = window.prompt('Peer connection string (node_id@host:port) to ask it to force-close — leave blank to try with stored peer:') ?? ''
    try { await requestForceClose(targetCoin, point, cs.trim()); pushToast('Requested remote force-close ✓', 'success'); onChanged() }
    catch (e) { patchUi(targetCoin, { error: errMsg(e) }) }
  }
  const backup = async (point: string) => {
    const targetCoin = coin
    patchUi(targetCoin, { error: null })
    try {
      const { backup } = await exportChannelBackup(targetCoin, point)
      // Coin-labelled filename: one seed spans 6 coins, so backups must not be confused.
      const date = new Date().toISOString().slice(0, 10)
      const blob = new Blob([backup], { type: 'text/plain' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `channel-backup-${targetCoin}-${date}.txt`
      a.click()
      URL.revokeObjectURL(a.href)
      pushToast(`Backup saved (for ${targetCoin} only) ✓`, 'success')
    } catch (e) { patchUi(targetCoin, { error: errMsg(e) }) }
  }
  const doImport = async () => {
    const targetCoin = coin
    const backupText = ui.importStr.trim()
    patchUi(targetCoin, { error: null })
    if (!backupText) return patchUi(targetCoin, { error: 'Paste a channel backup.' })
    patchUi(targetCoin, { importing: true })
    try {
      await importChannelBackup(targetCoin, backupText)
      patchUi(targetCoin, { importStr: '', showImport: false, error: null })
      pushToast('Backup imported ✓', 'success')
      onChanged()
    } catch (e) {
      patchUi(targetCoin, { error: errMsg(e) })
    } finally {
      patchUi(targetCoin, { importing: false })
    }
  }

  return (
    <section style={{ ...card }}>
      {confirmDialog && (
        <ConfirmDialog
          dialog={confirmDialog}
          onCancel={() => settleConfirm(false)}
          onConfirm={() => settleConfirm(true)}
        />
      )}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
        <button type="button" style={{ ...secondaryBtn, marginLeft: 'auto', fontSize: 12 }} onClick={() => patchUi(coin, { showImport: !ui.showImport })}>
          Import backup
        </button>
      </div>

      {ui.showImport && (
        <div style={{ marginBottom: 12 }}>
          <label style={lbl}>Paste a channel backup (for {coin})</label>
          <textarea style={{ ...input, minHeight: 60, fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
            value={ui.importStr} onChange={(e) => patchUi(coin, { importStr: e.target.value })} placeholder="channel_backup:…" spellCheck={false} />
          <button type="button" style={{ ...secondaryBtn, marginTop: 6 }} onClick={() => void doImport()} disabled={ui.importing}>
            {ui.importing ? 'Importing…' : 'Import'}
          </button>
        </div>
      )}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: 'left' }}>
              <Th>Peer</Th>
              <Th align="right">You / Them</Th>
              <Th align="right">Capacity</Th>
              <Th>State</Th>
              <Th align="right"> </Th>
            </tr>
          </thead>
          <tbody>
            {channels.length === 0 ? (
              <tr><td colSpan={5} style={{ padding: '20px 16px', textAlign: 'center', color: '#8a929b' }}>No channels yet</td></tr>
            ) : channels.map((ch, i) => {
              const point = (ch.channel_point ?? ch.channel_id ?? ch.short_channel_id ?? '') as string
              const isBackup = channelIsBackup(ch)
              const localN = Number(ch.local_balance ?? 0)
              const remoteN = Number(ch.remote_balance ?? 0)
              return (
                <tr key={point || i} style={{ borderTop: '1px solid #2e333a' }}>
                  <Td mono>
                    {short(ch.remote_pubkey, 16)}
                    {isBackup && <span style={tag('#fbbc04')}>backup</span>}
                  </Td>
                  <Td align="right" mono>{isBackup ? '—' : `${sats(localN, coin)} / ${sats(remoteN, coin)}`}</Td>
                  <Td align="right" mono>{isBackup ? '—' : sats(localN + remoteN, coin)}</Td>
                  <Td muted>{asStr(ch.state)}</Td>
                  <Td align="right">
                    <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                      {!isBackup && <MiniBtn onClick={() => void backup(point)} disabled={!point}>Backup</MiniBtn>}
                      {isBackup ? (
                        <MiniBtn color="#ef5350" onClick={() => void requestClose(point)} disabled={!point}>Request close</MiniBtn>
                      ) : (
                        <>
                          <MiniBtn onClick={() => void close(point, false)} disabled={!point}>Close</MiniBtn>
                          <MiniBtn color="#ef5350" onClick={() => void close(point, true)} disabled={!point}>Force</MiniBtn>
                        </>
                      )}
                    </div>
                  </Td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div style={{ borderTop: '1px solid #2e333a', marginTop: 12, paddingTop: 12, position: 'relative' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <div style={{ fontSize: 12, fontWeight: 700 }}>Open a channel</div>
          {hub && (
            <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 11, color: '#8a929b' }}>
              {hubChannel ? <span>✓ hub channel</span> : null}
              <button type="button" style={{ ...secondaryBtn, fontSize: 11, padding: '3px 10px' }} onClick={() => patchUi(coin, { connStr: hub })}>Use {coin} hub</button>
            </span>
          )}
        </div>
        <label style={lbl}>Peer (node_id@host:port)</label>
        <input style={input} value={ui.connStr} onChange={(e) => patchUi(coin, { connStr: e.target.value })} placeholder="03abc…@1.2.3.4:9735" autoComplete="off" spellCheck={false} />
        <label style={lbl}>Amount to lock in channel ({coin})</label>
        <input style={input} value={ui.amount} onChange={(e) => patchUi(coin, { amount: e.target.value })} placeholder="0.0" inputMode="decimal" autoComplete="off" />
        <div style={{ marginTop: 12, padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(251,188,4,0.24)', background: 'rgba(251,188,4,0.05)' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, fontWeight: 700, color: '#e6e6e6' }}>
            <input
              type="checkbox"
              checked={ui.receiveCapacity}
              onChange={(e) => patchUi(coin, { receiveCapacity: e.target.checked })}
            />
            Create receive capacity now
          </label>
          <p style={{ color: '#8a929b', fontSize: 11, lineHeight: 1.45, margin: '6px 0 8px' }}>
            This gives the peer/hub starting balance inside the new channel. Leave it off for normal send capacity.
          </p>
          {ui.receiveCapacity && (
            <select
              style={{ ...input, appearance: 'auto', marginTop: 0 }}
              value={ui.receivePct}
              onChange={(e) => patchUi(coin, { receivePct: e.target.value })}
            >
              <option value="20">Light receive split · 20%</option>
              <option value="40">Balanced receive split · 40%</option>
              <option value="60">Receive-heavy split · 60%</option>
            </select>
          )}
        </div>
        <p style={{ color: '#efb15f', fontSize: 11, lineHeight: 1.45, margin: '8px 0 0' }}>
          Opening a channel locks these coins in Lightning until you close the channel later.
        </p>
        {showPushPreview && (
          <div style={{ ...codeBox, marginTop: 8, color: '#cfd4da' }}>
            New channel preview: send about {coinAmount(previewSend ?? 0)} {coin}; receive about {coinAmount(pushPreview)} {coin}.
          </div>
        )}
        <ErrorOverlay message={ui.error} onDismiss={() => patchUi(coin, { error: null })} />
        <button style={{ ...primaryBtn, marginTop: 14 }} disabled={ui.busy} onClick={() => void open()}>
          {ui.busy ? 'Opening…' : 'Open channel'}
        </button>
      </div>
    </section>
  )
}

function PeersSection({ coin, peers, onChanged }: { coin: string; peers: LnPeer[]; onChanged: () => void }) {
  const pushToast = useStore((s) => s.pushToast)
  const [uiByCoin, setUiByCoin] = useState<Record<string, PeerUiState>>({})
  const ui = uiByCoin[coin] ?? EMPTY_PEER_UI
  const patchUi = useCallback((targetCoin: string, patch: Partial<PeerUiState>) => {
    setUiByCoin((prev) => ({
      ...prev,
      [targetCoin]: { ...EMPTY_PEER_UI, ...(prev[targetCoin] ?? {}), ...patch },
    }))
  }, [])

  const add = async () => {
    const targetCoin = coin
    const targetPeer = ui.cs.trim()
    patchUi(targetCoin, { error: null })
    if (!targetPeer) return patchUi(targetCoin, { error: 'Enter a peer (node_id@host:port).' })
    patchUi(targetCoin, { busy: true })
    try { await addLnPeer(targetCoin, targetPeer); patchUi(targetCoin, { cs: '', error: null }); pushToast('Peer connected ✓', 'success'); onChanged() }
    catch (e) { patchUi(targetCoin, { error: errMsg(e) }) } finally { patchUi(targetCoin, { busy: false }) }
  }

  return (
    <section style={{ ...card, position: 'relative' }}>
        <div>
          {peers.length === 0 ? (
            <div style={{ fontSize: 12, color: '#8a929b' }}>No peers connected.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {peers.map((p, i) => (
                <div key={i} style={{ ...codeBox, display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 11 }}>
                  <span style={{ wordBreak: 'break-all', minWidth: 0 }}>{asStr(p.node_id)}</span>
                  <span style={{ color: p.initialized ? '#4caf50' : '#8a929b', flex: '0 0 auto', whiteSpace: 'nowrap' }}>{asStr(p.address)}</span>
                </div>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <input style={{ ...input, flex: 1 }} value={ui.cs} onChange={(e) => patchUi(coin, { cs: e.target.value })} placeholder="connect a peer: node_id@host:port" autoComplete="off" spellCheck={false} />
            <button type="button" style={secondaryBtn} onClick={() => void add()} disabled={ui.busy}>{ui.busy ? 'Connecting…' : 'Add peer'}</button>
          </div>
          <ErrorOverlay message={ui.error} onDismiss={() => patchUi(coin, { error: null })} />
        </div>
    </section>
  )
}

function MiniBtn({ children, onClick, disabled, color }: { children: React.ReactNode; onClick: () => void; disabled?: boolean; color?: string }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} style={{
      padding: '4px 10px', fontSize: 11, borderRadius: 6, border: '1px solid #2e333a',
      background: '#1a1d21', color: disabled ? '#555c64' : (color ?? '#cfd4da'), cursor: disabled ? 'not-allowed' : 'pointer',
    }}>{children}</button>
  )
}

function ConfirmDialog({
  dialog, onCancel, onConfirm,
}: {
  dialog: ConfirmDialogState
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div
      role="presentation"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 70,
        background: 'rgba(5,7,10,0.62)',
        backdropFilter: 'blur(8px) saturate(135%)',
        WebkitBackdropFilter: 'blur(8px) saturate(135%)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 18,
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="ln-confirm-title"
        style={{
          ...card,
          width: 'min(660px, 94vw)',
          padding: 18,
          borderRadius: 12,
          background: 'rgba(24,28,33,0.92)',
          boxShadow: '0 24px 70px rgba(0,0,0,0.58), 0 0 0 1px rgba(var(--coin-rgb),0.18), inset 0 1px 0 rgba(255,255,255,0.13)',
        }}
      >
        <h3 id="ln-confirm-title" style={{ margin: '0 0 10px', color: '#eef2f8', fontSize: 16, letterSpacing: 0 }}>
          {dialog.title}
        </h3>
        <div style={{ color: '#cfd4da', fontSize: 13, lineHeight: 1.55 }}>
          {dialog.body}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <button type="button" style={secondaryBtn} onClick={onCancel}>Cancel</button>
          <button
            type="button"
            style={dialog.danger
              ? {
                ...secondaryBtn,
                color: '#ffb4ae',
                border: '1px solid rgba(239,83,80,0.55)',
                background: 'rgba(239,83,80,0.14)',
              }
              : primaryBtn}
            onClick={onConfirm}
            autoFocus
          >
            {dialog.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

const tag = (c: string): React.CSSProperties => ({
  marginLeft: 6, fontSize: 10, color: c, border: `1px solid ${c}55`, borderRadius: 4, padding: '0 5px',
})
