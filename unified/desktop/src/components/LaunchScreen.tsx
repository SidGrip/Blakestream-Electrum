import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { getSetupStatus, getUnlockProgress, type UnlockProgress } from '../api'
import CoinIcon from './CoinIcon'
import { resolveCoinColor } from '../types'
import './setup.css'

// Fixed display order so the row is stable while coins light up.
const COIN_ORDER = ['BLC', 'BBTC', 'ELT', 'LIT', 'PHO', 'UMO']
const REVEAL_MS = 150 // pace between coins lighting up
const HOLD_MS = 1000 // hold once all lit before onboarding (first-run only)

type ProgressDetail = NonNullable<UnlockProgress['detail']>[string]

function orderOf(coins: Record<string, string>): string[] {
  return [
    ...COIN_ORDER.filter((t) => t in coins),
    ...Object.keys(coins).filter((t) => !COIN_ORDER.includes(t)),
  ]
}

function shortServer(server: string | null | undefined): string {
  if (!server) return ''
  return server.split(':')[0] || server
}

function progressLabel(detail: ProgressDetail | undefined): string {
  const phase = detail?.phase ?? 'connecting'
  const server = shortServer(detail?.server)
  if (phase === 'ready') return 'Ready'
  if (phase === 'failed') return server ? `Retrying ${server}` : 'Retrying'
  if (phase === 'syncing') return server ? `Syncing ${server}` : 'Syncing'
  return 'Connecting'
}

function progressColor(detail: ProgressDetail | undefined): string {
  const phase = detail?.phase ?? 'connecting'
  if (phase === 'ready') return '#7fe0a3'
  if (phase === 'failed') return '#e0a23a'
  if (phase === 'syncing') return '#4fc3f7'
  return '#cfd4da'
}

function slowestProgress(order: string[], progress: UnlockProgress | null): ProgressDetail | undefined {
  const details = progress?.detail ?? {}
  const active = order.map((t) => details[t]).filter((d): d is ProgressDetail => !!d)
  return (
    active.find((d) => (d.phase ?? 'connecting') === 'connecting')
    ?? active.find((d) => (d.phase ?? 'connecting') === 'syncing')
    ?? active.find((d) => (d.phase ?? 'connecting') === 'failed')
    ?? active.find((d) => (d.phase ?? 'connecting') === 'ready')
    ?? { phase: 'connecting', server: null }
  )
}

// The launch screen: six coin icons cascade dim → lit as each daemon reports ready.
//  • Vault exists: password field + Unlock (enables once all ready); icons stay lit, pulse on unlock.
//  • First-run (no vault): icons cascade, hold a beat, then hand off to onboarding.
//  • Backend unreachable (failed): the retry card.
export default function LaunchScreen({ failed }: { failed: boolean }) {
  const startup = useStore((s) => s.startup)
  const finishConnecting = useStore((s) => s.finishConnecting)
  const finishOnboarding = useStore((s) => s.finishOnboarding)
  const unlockWallet = useStore((s) => s.unlockWallet)
  const refresh = useStore((s) => s.refresh)
  const overrides = useStore((s) => s.coinColorOverrides)

  const [revealed, setRevealed] = useState<string[]>([])
  const [vaultExists, setVaultExists] = useState<boolean | null>(null)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<UnlockProgress | null>(null)
  const finishScheduled = useRef(false)
  const pollerRef = useRef<number | null>(null)

  const coins = startup?.coins ?? {}
  const order = startup ? orderOf(coins) : COIN_ORDER

  // Probe whether a vault exists. RETRY on failure, else one failed probe leaves vaultExists
  // null forever and the screen hangs at "6/6 ready" with no password and no advance.
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const probe = () => {
      void getSetupStatus()
        .then((s) => {
          if (!cancelled) setVaultExists(s.vault_exists)
        })
        .catch(() => {
          if (!cancelled) timer = setTimeout(probe, 800)
        })
    }
    probe()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  // Cascade: every REVEAL_MS reveal the next ready-but-unrevealed coin. Reads the store
  // directly so the fast poll never resets the timing.
  useEffect(() => {
    if (failed) return
    const id = setInterval(() => {
      setRevealed((prev) => {
        const cur = useStore.getState().startup?.coins ?? {}
        const next = orderOf(cur).find((t) => cur[t] === 'ready' && !prev.includes(t))
        return next ? [...prev, next] : prev
      })
    }, REVEAL_MS)
    return () => clearInterval(id)
  }, [failed])

  // First-run only: once every ready coin is lit, hold then hand off to onboarding. One-shot
  // (ref-guarded), NO cleanup so poll re-renders can't cancel it. Unlock path waits for password.
  useEffect(() => {
    if (failed || vaultExists !== false || finishScheduled.current || !startup?.all_ready) return
    const allReadyLit = order.filter((t) => coins[t] === 'ready').every((t) => revealed.includes(t))
    if (!allReadyLit) return
    finishScheduled.current = true
    setTimeout(() => void finishConnecting(), HOLD_MS)
  }, [startup, revealed, failed, vaultExists, finishConnecting])

  const stopPoller = () => {
    if (pollerRef.current !== null) {
      clearInterval(pollerRef.current)
      pollerRef.current = null
    }
  }
  const startPoller = () => {
    stopPoller()
    const tick = async () => {
      try {
        setProgress(await getUnlockProgress())
      } catch {
        /* transient — keep the last shown progress */
      }
    }
    void tick()
    pollerRef.current = window.setInterval(() => void tick(), 450)
  }
  // Stop polling if the screen unmounts mid-unlock.
  useEffect(() => stopPoller, [])

  // Unlock errors show as a transient toast (auto-clears 4s) so the login form doesn't shift.
  useEffect(() => {
    if (!error) return
    const id = window.setTimeout(() => setError(null), 4000)
    return () => window.clearTimeout(id)
  }, [error])

  const onUnlock = async () => {
    setError(null)
    if (!password) return setError('Enter your wallet password.')
    setBusy(true)
    setProgress(null)
    startPoller() // poll /setup/progress while the blocking unlock runs
    try {
      await unlockWallet(password)
      // Hand off to the dashboard. This screen renders before App's initialChecked gate, so
      // finishConnecting() flips it (reads provisioned status, sets onboarded, kicks refresh).
      // finishOnboarding() is belt-and-braces; refresh() preloads the dashboard data.
      finishOnboarding()
      await finishConnecting()
      await refresh()
    } catch (e) {
      const raw = e instanceof Error ? e.message : String(e)
      // Map decrypt/password errors to a clean "Wrong Password"; surface anything else verbatim.
      setError(/password|decrypt|mac check|invalid|wrong/i.test(raw) ? 'Wrong Password' : raw)
    } finally {
      stopPoller()
      setBusy(false)
    }
  }

  const allReady = !!startup?.all_ready

  // Live unlock progress: coins landed (connected + synced) vs total. Drives the early
  // "Enter wallet" escape so one slow coin can't trap the user — the rest sync on the dashboard.
  const cstates = progress?.coins ?? {}
  const total = order.length
  const doneCount = order.filter((t) => cstates[t] === 'done').length
  // Time-based fallback: if EVERY coin is slow (doneCount stays 0) the escape never appears,
  // so offer it after a grace too. Normal syncs finish in ~5s, so this only fires when stuck.
  const [graceElapsed, setGraceElapsed] = useState(false)
  useEffect(() => {
    if (!busy) { setGraceElapsed(false); return }
    const id = window.setTimeout(() => setGraceElapsed(true), 12000)
    return () => window.clearTimeout(id)
  }, [busy])
  const canEnterEarly = busy && doneCount < total && (doneCount >= 1 || graceElapsed)
  const enterNow = async () => {
    stopPoller()
    finishOnboarding()
    await finishConnecting()   // flips the App gate -> this screen unmounts; the unlock POST
    await refresh()            // finishes server-side, coins settle live on the dashboard
  }

  // Once all six are up and a vault exists, prompt for the password (heading carries the state).
  const awaitingPassword = !busy && allReady && !!vaultExists
  // No subtitle — the per-coin list below already shows each coin's live progress.
  const statusLine = ''
  const slowest = busy ? slowestProgress(order, progress) : undefined

  return (
    <div className="setup-shell">
      {error && (
        <div className="setup-toast" role="alert">
          {error}
        </div>
      )}
      <div className="setup-card">
        <h1 className="setup-title">BLAKESTREAM WALLET</h1>
        {failed ? (
          <>
            <h2 className="setup-h2">Can&rsquo;t reach the wallet backend</h2>
            <p className="setup-muted">
              The wallet service didn&rsquo;t start. Reopen the app to try again; if this
              keeps happening your install may be incomplete.
            </p>
            <button className="setup-btn" onClick={() => location.reload()}>
              Retry
            </button>
          </>
        ) : (
          <>
            <h2 className="setup-h2">
              {busy ? 'Connecting to your wallets' : awaitingPassword ? 'Enter your password' : 'Starting wallets'}
              {!awaitingPassword && (
                <span className="loading-dots">
                  <span>.</span>
                  <span>.</span>
                  <span>.</span>
                </span>
              )}
            </h2>
            {statusLine && (
              <p className="setup-muted" style={{ textAlign: 'center' }}>
                {statusLine}
              </p>
            )}

            <div
              style={{
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                gap: 16,
                marginTop: 22,
                marginBottom: vaultExists && !busy && allReady ? 22 : 0,
              }}
            >
              {order.map((t) => {
                const lit = revealed.includes(t)
                const failedCoin = coins[t] === 'failed'
                const color = resolveCoinColor(overrides, t) // per-coin brand color for the ring
                // While busy every coin flashes at once (they connect in PARALLEL), then settles
                // to a solid brand-color ring on 'done' or red on failure.
                const connState = busy ? progress?.coins?.[t] : undefined
                const connecting = busy && connState !== 'done' && connState !== 'failed'
                return (
                  <div
                    key={t}
                    style={{
                      transition: 'opacity .45s ease, filter .45s ease, transform .45s ease, box-shadow .25s ease',
                      opacity: lit ? 1 : 0.28,
                      transform: lit ? 'scale(1)' : 'scale(0.9)',
                      filter: lit ? 'none' : failedCoin ? 'grayscale(1) sepia(1) hue-rotate(-30deg)' : 'grayscale(1)',
                      display: 'flex',
                      borderRadius: '50%',
                      // per-coin pulse color for the launch-pulse keyframe (8-digit hex = color + alpha)
                      '--ring': `${color}7e`,
                      '--ring-weak': `${color}29`,
                      boxShadow:
                        connState === 'done'
                          ? `0 0 0 2px ${color}da` // landed
                          : connState === 'failed'
                            ? '0 0 0 2px rgba(224, 162, 58, 0.63)' // timed out — retrying in background
                            : connecting
                              ? `0 0 0 2px ${color}7e` // in flight — base of the pulse
                              : 'none',
                      animation: connecting ? 'launch-pulse 1.1s ease-in-out infinite' : 'none',
                    } as React.CSSProperties}
                  >
                    <CoinIcon ticker={t} size={34} />
                  </div>
                )
              })}
            </div>

            {busy && (
              <details
                className="launch-progress-details"
                style={{
                  marginTop: 16,
                  width: 300,
                  marginLeft: 'auto',
                  marginRight: 'auto',
                  border: '1px solid rgba(255,255,255,0.09)',
                  borderRadius: 8,
                  background: 'rgba(255,255,255,0.025)',
                  overflow: 'hidden',
                }}
              >
                <summary
                  className="launch-progress-summary"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'flex-start',
                    gap: 10,
                    padding: '8px 11px',
                    cursor: 'pointer',
                    listStyle: 'none',
                    color: '#cfd4da',
                    fontSize: 12,
                    fontWeight: 700,
                  }}
                >
                  <span style={{ flex: '1 1 auto', minWidth: 0 }}>{progressLabel(slowest)}</span>
                  <span
                    style={{
                      marginLeft: 'auto',
                      color: progressColor(slowest),
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                      fontSize: 11,
                      fontWeight: 600,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {doneCount}/{total} ready
                  </span>
                </summary>
                <div
                  style={{
                    borderTop: '1px solid rgba(255,255,255,0.08)',
                    padding: '8px 11px 10px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 4,
                    fontSize: 11,
                  }}
                >
                  {order.map((t) => {
                    const detail = progress?.detail?.[t]
                    return (
                      <div key={t} style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                        <span style={{ color: '#cfd4da', fontWeight: 700 }}>{t}</span>
                        <span
                          style={{
                            color: progressColor(detail),
                            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {progressLabel(detail)}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </details>
            )}

            {canEnterEarly && (
              <button
                className="setup-btn"
                style={{ marginTop: 18 }}
                onClick={() => void enterNow()}
              >
                {doneCount >= 1 ? `Enter wallet (${doneCount}/${total} ready) ` : 'Enter wallet (coins still syncing) '}&rarr;
              </button>
            )}

            {vaultExists && !busy && allReady && (
              <>
                <input
                  className="setup-input"
                  type="password"
                  placeholder="Wallet password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && allReady && !busy) void onUnlock()
                  }}
                  autoComplete="off"
                  autoFocus
                />
                <button className="setup-btn" onClick={onUnlock}>
                  Unlock
                </button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
