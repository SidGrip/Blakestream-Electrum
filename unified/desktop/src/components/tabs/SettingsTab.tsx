import { useEffect, useRef, useState } from 'react'
import type { NetworkSettings, WalletInfo } from '../../types'
import { resolveCoinColor, COIN_ORDER } from '../../types'
import {
  getNetworkSettings, setServer, setFeePolicy,
  getWalletInfo, changePassword, setProxy,
} from '../../api'
import { useStore } from '../../store'
import CoinIcon from '../CoinIcon'
import { lbl, input, primaryBtn, secondaryBtn, codeBox, card, errBox } from '../uiKit'
import {
  defaultExplorerBase, explorerBase, setExplorerBase,
  BASE_UNIT_OPTIONS, getBaseUnit, setBaseUnit, getThousandSep, setThousandSep, formatAmount, type BaseUnit,
} from '../../explorer'
import PriceSection from './PriceSection'
import lockImg from '../../assets/lock.svg'

// Per-coin settings sub-tabs; chosen section renders below, capped at maxW so a small setting
// isn't stretched across a wide screen.
const SETTINGS_TABS: { key: string; label: string; maxW: number }[] = [
  { key: 'wallet', label: 'Wallet information', maxW: 640 },
  { key: 'security', label: 'Password', maxW: 480 },
  { key: 'price', label: 'Price & currency', maxW: 620 },
  { key: 'fee', label: 'Transaction fee', maxW: 560 },
  { key: 'network', label: 'Network', maxW: 960 },
  { key: 'explorer', label: 'Block explorer', maxW: 640 },
  { key: 'unit', label: 'Base unit', maxW: 420 },
  { key: 'color', label: 'Coin settings', maxW: 820 },
  { key: 'dex', label: 'DEX integration', maxW: 560 },
]

export default function SettingsTab({ coin }: { coin: string }) {
  const [active, setActive] = useState('network')
  const tab = SETTINGS_TABS.find((t) => t.key === active) ?? SETTINGS_TABS[0]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {SETTINGS_TABS.map((t) => {
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
      <div style={{ width: '100%', maxWidth: tab.maxW, margin: '0 auto' }}>
        {active === 'wallet' && <WalletInfoSection coin={coin} />}
        {active === 'security' && <SecuritySection />}
        {active === 'price' && <PriceSection coin={coin} />}
        {active === 'network' && <NetworkAndProxySection coin={coin} />}
        {active === 'fee' && <FeeSection coin={coin} />}
        {active === 'explorer' && <ExplorerSection coin={coin} />}
        {active === 'unit' && <BaseUnitSection coin={coin} />}
        {active === 'color' && <CoinSettingsSection coin={coin} />}
        {active === 'dex' && <DexIntegrationSection />}
      </div>
    </div>
  )
}

const sectionTitle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 700,
  color: '#e6e6e6',
  margin: '0 0 4px',
}
const sectionHint: React.CSSProperties = {
  fontSize: 11,
  color: '#8a929b',
  margin: '0 0 8px',
}

function DexIntegrationSection() {
  const dexIntegrationAllowed = useStore((s) => s.dexIntegrationAllowed)
  const dexStartOnStartup = useStore((s) => s.dexStartOnStartup)
  const dexTrustedId = useStore((s) => s.dexTrustedId)
  const dexTrustedName = useStore((s) => s.dexTrustedName)
  const dexPendingPair = useStore((s) => s.dexPendingPair)
  const refreshDexIntegration = useStore((s) => s.refreshDexIntegration)
  const setDexIntegrationAllowed = useStore((s) => s.setDexIntegrationAllowed)
  const setDexStartOnStartup = useStore((s) => s.setDexStartOnStartup)
  const approveDexPair = useStore((s) => s.approveDexPair)
  const clearPendingDexPair = useStore((s) => s.clearPendingDexPair)
  const forgetDexPair = useStore((s) => s.forgetDexPair)
  const allowLocalDex = dexIntegrationAllowed === true
  const [loading, setLoading] = useState(dexIntegrationAllowed === null)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    if (dexIntegrationAllowed !== null) {
      setLoading(false)
      return () => { live = false }
    }
    setLoading(true)
    refreshDexIntegration()
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [dexIntegrationAllowed, refreshDexIntegration])

  const toggleAllow = (checked: boolean) => {
    setSaving(true)
    setStatus(null)
    setError(null)
    setDexIntegrationAllowed(checked)
      .then(() => {
        setStatus(checked ? 'DEX discovery is enabled.' : 'DEX discovery is disabled.')
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false))
  }

  const toggleStartup = (checked: boolean) => {
    setSaving(true)
    setStatus(null)
    setError(null)
    setDexStartOnStartup(checked)
      .then(() => {
        setStatus(checked
          ? 'DEX integration will start with the wallet.'
          : 'DEX integration will be temporary unless enabled manually.')
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false))
  }

  const approvePendingDex = () => {
    if (!dexPendingPair?.id) return
    setSaving(true)
    setStatus(null)
    setError(null)
    approveDexPair(dexPendingPair.id, dexPendingPair.name)
      .then(() => setStatus(`${dexPendingPair.name || 'Blakestream DEX'} is approved. The DEX can reconnect now.`))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false))
  }

  const clearPendingDex = () => {
    setSaving(true)
    setStatus(null)
    setError(null)
    clearPendingDexPair()
      .then(() => setStatus('Pending DEX request cleared. The next DEX connection will ask again.'))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false))
  }

  const forgetDex = () => {
    setSaving(true)
    setStatus(null)
    setError(null)
    forgetDexPair()
      .then(() => setStatus('Paired DEX was forgotten. The next DEX connection will require approval.'))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false))
  }

  return (
    <section style={card}>
      <h3 style={sectionTitle}>DEX integration</h3>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <label
          aria-label="Allow local DEX integration"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 16,
            height: 16,
            cursor: loading || saving ? 'default' : 'pointer',
            userSelect: 'none',
          }}
        >
          <input
            type="checkbox"
            checked={allowLocalDex}
            disabled={loading || saving}
            onChange={(e) => toggleAllow(e.currentTarget.checked)}
            style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }}
          />
          <span
            aria-hidden
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 16,
              height: 16,
              borderRadius: 5,
              border: allowLocalDex ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.18)',
              background: allowLocalDex ? 'rgba(var(--coin-rgb),0.22)' : 'rgba(255,255,255,0.04)',
              boxShadow: allowLocalDex ? '0 0 8px rgba(var(--coin-rgb),0.30)' : 'none',
              color: '#eef2f8',
              fontSize: 11,
              lineHeight: 1,
              transition: 'background .2s, border-color .2s, box-shadow .2s',
            }}
          >
            {allowLocalDex ? '✓' : ''}
          </span>
        </label>
        <span style={{ color: '#e6e6e6', fontWeight: 600 }}>Allow local DEX integration</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
        <label
          aria-label="Start DEX integration when wallet starts"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 16,
            height: 16,
            cursor: loading || saving ? 'default' : 'pointer',
            userSelect: 'none',
          }}
        >
          <input
            type="checkbox"
            checked={dexStartOnStartup}
            disabled={loading || saving}
            onChange={(e) => toggleStartup(e.currentTarget.checked)}
            style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }}
          />
          <span
            aria-hidden
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 16,
              height: 16,
              borderRadius: 5,
              border: dexStartOnStartup ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.18)',
              background: dexStartOnStartup ? 'rgba(var(--coin-rgb),0.22)' : 'rgba(255,255,255,0.04)',
              boxShadow: dexStartOnStartup ? '0 0 8px rgba(var(--coin-rgb),0.30)' : 'none',
              color: '#eef2f8',
              fontSize: 11,
              lineHeight: 1,
              transition: 'background .2s, border-color .2s, box-shadow .2s',
            }}
          >
            {dexStartOnStartup ? '✓' : ''}
          </span>
        </label>
        <span style={{ color: '#e6e6e6', fontWeight: 600 }}>Start DEX integration when wallet starts</span>
      </div>
      {allowLocalDex && (
        <p style={{ ...sectionHint, marginTop: 8 }}>
          {dexStartOnStartup
            ? 'The wallet will connect to a running local Blakestream DEX after each wallet start.'
            : 'Blakestream DEX can discover this multiwallet for this wallet session.'}
        </p>
      )}
      {allowLocalDex && (
        <div style={codeBox}>http://127.0.0.1:57100/ready</div>
      )}
      {allowLocalDex && dexPendingPair?.id && !dexTrustedId && (
        <div style={{
          marginTop: 10,
          padding: '10px 12px',
          borderRadius: 8,
          border: '1px solid rgba(var(--coin-rgb),0.45)',
          background: 'rgba(var(--coin-rgb),0.10)',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}>
          <div style={{ color: '#eef2f8', fontWeight: 700, fontSize: 12 }}>Approve local DEX connection?</div>
          <div style={{ color: '#aeb7c2', fontSize: 12 }}>
            {dexPendingPair.name || 'Blakestream DEX'} is asking to connect on this computer.
            This request stays here until you approve it or clear it.
          </div>
          <div style={{ ...codeBox, margin: 0, fontSize: 11 }}>{dexPendingPair.id}</div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" disabled={saving} style={secondaryBtn} onClick={clearPendingDex}>
              Clear request
            </button>
            <button type="button" disabled={saving} style={primaryBtn} onClick={approvePendingDex}>
              Approve DEX
            </button>
          </div>
        </div>
      )}
      {allowLocalDex && dexTrustedId && (
        <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ color: '#aeb7c2', fontSize: 12 }}>
            Paired with <span style={{ color: '#eef2f8' }}>{dexTrustedName || 'Blakestream DEX'}</span>
          </div>
          <button type="button" disabled={saving} style={secondaryBtn} onClick={forgetDex}>
            Forget paired DEX
          </button>
        </div>
      )}
      {status && <p style={{ margin: '8px 0 0', color: '#8fe39b', fontSize: 12 }}>{status}</p>}
      {error && <div style={{ ...errBox, marginTop: 8 }}>{error}</div>}
    </section>
  )
}

// Two-click reset: first click arms "Are you sure?"; a second click within 5s runs onConfirm.
function ConfirmResetButton({ onConfirm, style }: { onConfirm: () => void; style?: React.CSSProperties }) {
  const [armed, setArmed] = useState(false)
  const timer = useRef<number | null>(null)
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])
  const click = () => {
    if (armed) {
      if (timer.current) clearTimeout(timer.current)
      setArmed(false)
      onConfirm()
    } else {
      setArmed(true)
      timer.current = window.setTimeout(() => setArmed(false), 5000)
    }
  }
  const base: React.CSSProperties = {
    padding: '4px 16px',
    borderRadius: 8,
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'background .2s, border-color .2s, box-shadow .2s, color .2s',
    ...style,
  }
  return (
    <button
      type="button"
      onClick={click}
      style={
        armed
          ? {
              ...base,
              background: 'rgba(239,106,53,0.20)',
              color: '#ffe2d2',
              border: '1px solid #ef6a35',
              boxShadow: '0 0 14px rgba(239,106,53,0.5), inset 0 1px 0 rgba(255,255,255,0.15)',
              textShadow: '0 1px 2px rgba(0,0,0,0.4)',
            }
          : { ...base, background: 'transparent', color: '#cfd4da', border: '1px solid #2e333a' }
      }
    >
      {armed ? 'Are you sure?' : 'Reset to default'}
    </button>
  )
}

// ---- 1. Network / Server ----

// Sentinel <option> values that aren't real "host:port:s" servers.
const AUTO = '__auto__'
const CUSTOM = '__custom__'

// Network + Proxy share the one /settings/<coin> payload, cached in the store. The cache shows instantly
// (no "hung" blank) while a background refresh re-fetches to keep status/height current — a header spinner
// signals the refresh. A failed refresh keeps the cached data; only a first load with no cache shows an error.
function NetworkAndProxySection({ coin }: { coin: string }) {
  const initial = useStore((s) => s.networkSettings[coin]) ?? null
  const loadNetworkSettings = useStore((s) => s.loadNetworkSettings)
  const [refreshing, setRefreshing] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setLoadError(null)
    setRefreshing(true)
    loadNetworkSettings(coin, { force: true })
      .then(() => { if (live) setLoadError(null) })
      .catch((e) => { if (live) setLoadError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (live) setRefreshing(false) })
    return () => { live = false }
  }, [coin, loadNetworkSettings])
  // Hide the error card (and Proxy) only when there is nothing cached to fall back to.
  const hardError = loadError && !initial
  return (
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
      <div style={{ flex: '1 1 360px', minWidth: 0 }}>
        <NetworkSection coin={coin} initial={initial} loadError={hardError ? loadError : null} refreshing={refreshing} />
      </div>
      {!hardError && (
        <div style={{ flex: '1 1 360px', minWidth: 0 }}>
          <ProxySection coin={coin} initial={initial} />
        </div>
      )}
    </div>
  )
}

function NetworkSection({ coin, initial, loadError, refreshing }: { coin: string; initial: NetworkSettings | null; loadError: string | null; refreshing: boolean }) {
  const setNetworkSettings = useStore((s) => s.setNetworkSettings)
  const [settings, setSettings] = useState<NetworkSettings | null>(null)

  // Form state, derived from settings once loaded.
  const [autoConnect, setAutoConnect] = useState(true)
  const [selected, setSelected] = useState<string>(AUTO) // a known server, AUTO, or CUSTOM
  const [custom, setCustom] = useState('')

  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)

  // Map fetched NetworkSettings into the form controls.
  const hydrate = (s: NetworkSettings) => {
    setSettings(s)
    setAutoConnect(s.auto_connect)
    if (s.auto_connect || s.server === 'auto') {
      setSelected(AUTO)
    } else if (s.server === 'offline' || s.server === '' || s.known_servers.includes(s.server)) {
      setSelected(s.server)
    } else {
      // Configured server not in the known list → drive the Custom field.
      setSelected(CUSTOM)
      setCustom(s.server)
    }
  }

  // Hydrate from the parent-fetched payload (null while the fetch is in flight).
  useEffect(() => {
    setApplyError(null)
    if (initial) hydrate(initial)
    else setSettings(null)
  }, [initial])

  // Server string to POST for the current form state.
  const targetServer = (): string => {
    if (autoConnect) return 'auto'
    if (selected === CUSTOM) return custom.trim()
    if (selected === AUTO) return 'auto'
    return selected
  }

  const apply = () => {
    const server = targetServer()
    if (!autoConnect && selected === CUSTOM && !server) {
      setApplyError('Enter a server as host:port:s')
      return
    }
    setApplying(true)
    setApplyError(null)
    setServer(coin, server)
      .then((s) => { hydrate(s); setNetworkSettings(coin, s) })
      .catch((e) => setApplyError(e instanceof Error ? e.message : String(e)))
      .finally(() => setApplying(false))
  }

  // Header with a "refreshing" spinner in the corner opposite the label.
  const header = (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '0 0 4px' }}>
      <h3 style={{ ...sectionTitle, margin: 0 }}>Network</h3>
      {refreshing && <span className="mini-spinner" title="Refreshing…" />}
    </div>
  )

  if (loadError) {
    return (
      <section style={card}>
        {header}
        <div style={errBox}>{loadError}</div>
      </section>
    )
  }
  if (!settings) {
    return (
      <section style={card}>
        {header}
        <p style={{ color: '#8a929b', margin: 0 }}>Loading network settings…</p>
      </section>
    )
  }

  const showCustom = !autoConnect && selected === CUSTOM
  const live = settings.connected
  const heightText =
    settings.blockchain_height !== null ? settings.blockchain_height.toLocaleString() : '—'

  return (
    <section style={card}>
      {header}
      <p style={sectionHint}>Choose how this coin connects to its ElectrumX servers.</p>

      {/* Live status */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 10px',
          background: '#1a1d21',
          border: '1px solid #2e333a',
          borderRadius: 8,
          fontSize: 12,
        }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: live ? '#4caf50' : '#ef5350',
            flex: '0 0 auto',
          }}
        />
        <span style={{ color: live ? '#e6e6e6' : '#8a929b' }}>
          {live ? 'Connected' : 'Disconnected'}
          {settings.live_server ? ` — ${settings.live_server}` : ''}
        </span>
        <span style={{ marginLeft: 'auto', color: '#8a929b' }}>height {heightText}</span>
      </div>

      {/* Connection mode */}
      <label style={lbl}>Connection mode</label>
      <div style={{ display: 'flex', gap: 16, fontSize: 13, color: '#cfd4da' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
          <input
            type="radio"
            name={`mode-${coin}`}
            checked={autoConnect}
            onChange={() => setAutoConnect(true)}
          />
          Auto-connect
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
          <input
            type="radio"
            name={`mode-${coin}`}
            checked={!autoConnect}
            onChange={() => {
              setAutoConnect(false)
              // Pick a sensible default server when leaving auto.
              if (selected === AUTO) {
                setSelected(settings.known_servers[0] ?? CUSTOM)
              }
            }}
          />
          Single server
        </label>
      </div>

      {/* Server select (only meaningful in single-server mode) */}
      <label style={lbl}>Server</label>
      <div style={{ display: 'flex', gap: 10, alignItems: 'stretch' }}>
        <select
          style={{ ...input, appearance: 'auto', flex: 1, opacity: autoConnect ? 0.5 : 1 }}
          value={autoConnect ? AUTO : selected}
          disabled={autoConnect}
          onChange={(e) => setSelected(e.target.value)}
        >
          <option value={AUTO}>Auto</option>
          {settings.known_servers.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
          <option value="offline">Offline</option>
          <option value={CUSTOM}>Custom…</option>
        </select>
        <button style={{ ...primaryBtn, flex: 'none', minWidth: 124 }} onClick={apply} disabled={applying}>
          {applying ? 'Reconnecting…' : 'Apply'}
        </button>
      </div>

      {showCustom && (
        <>
          <label style={lbl}>Custom server (host:port:s)</label>
          <input
            style={input}
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            placeholder="electrum.example.com:50002:s"
            autoComplete="off"
            spellCheck={false}
          />
        </>
      )}

      {applyError && <div style={{ ...errBox, marginTop: 10 }}>{applyError}</div>}
      {applying && (
        <p style={{ color: '#8a929b', fontSize: 11, marginTop: 8 }}>
          Restarting {coin} daemon on the new server — this takes a few seconds.
        </p>
      )}
    </section>
  )
}

// ---- 1b. Transaction fee ----

// ~226 vbytes for a typical 1-in/2-out P2PKH payment; preview line only (real tx is sized at send time).
const TYPICAL_VBYTES = 226

function FeeSection({ coin }: { coin: string }) {
  const [mode, setMode] = useState<'network' | 'fixed'>('network')
  const [rate, setRate] = useState('10')
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let live = true
    setLoaded(false)
    setLoadError(null)
    setApplyError(null)
    setSaved(false)
    getNetworkSettings(coin)
      .then((s) => {
        if (!live) return
        setMode(s.fee_mode)
        setRate(String(s.fee_sat_per_byte))
        setLoaded(true)
      })
      .catch((e) => live && setLoadError(e instanceof Error ? e.message : String(e)))
    return () => {
      live = false
    }
  }, [coin])

  const apply = () => {
    const spb = parseInt(rate, 10)
    if (!Number.isFinite(spb) || spb < 1) {
      setApplyError('Enter a whole number of at least 1 sat/byte.')
      return
    }
    setApplying(true)
    setApplyError(null)
    setFeePolicy(coin, mode, spb)
      .then((s) => {
        setMode(s.fee_mode)
        setRate(String(s.fee_sat_per_byte))
        setSaved(true)
        setTimeout(() => setSaved(false), 1500)
      })
      .catch((e) => setApplyError(e instanceof Error ? e.message : String(e)))
      .finally(() => setApplying(false))
  }

  if (loadError) {
    return (
      <section style={card}>
        <h3 style={sectionTitle}>Transaction fee</h3>
        <div style={errBox}>{loadError}</div>
      </section>
    )
  }
  if (!loaded) {
    return (
      <section style={card}>
        <h3 style={sectionTitle}>Transaction fee</h3>
        <p style={{ color: '#8a929b', margin: 0 }}>Loading fee settings…</p>
      </section>
    )
  }

  const spb = parseInt(rate, 10)
  const previewSat = Number.isFinite(spb) && spb > 0 ? spb * TYPICAL_VBYTES : 0
  const previewCoin = formatAmount((previewSat / 1e8).toFixed(8), coin)

  return (
    <section style={card}>
      <h3 style={sectionTitle}>Transaction fee</h3>
      <p style={sectionHint}>How the fee is calculated when you send.</p>

      <label style={lbl}>Fee mode</label>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13, color: '#cfd4da' }}>
        <label style={{ display: 'flex', alignItems: 'flex-start', gap: 6, cursor: 'pointer' }}>
          <input
            type="radio"
            name={`fee-${coin}`}
            checked={mode === 'network'}
            onChange={() => setMode('network')}
          />
          <span>
            Network estimate{' '}
            <span style={{ color: '#8a929b' }}>
              — ask the server; fall back to the fixed rate.
            </span>
          </span>
        </label>
        <label style={{ display: 'flex', alignItems: 'flex-start', gap: 6, cursor: 'pointer' }}>
          <input
            type="radio"
            name={`fee-${coin}`}
            checked={mode === 'fixed'}
            onChange={() => setMode('fixed')}
          />
          <span>
            Fixed rate <span style={{ color: '#8a929b' }}>— always the sat/byte below</span>
          </span>
        </label>
      </div>

      <label style={lbl}>{mode === 'fixed' ? 'Fee rate (sat/byte)' : 'Fallback rate (sat/byte)'}</label>
      <div style={{ display: 'flex', gap: 10, alignItems: 'stretch' }}>
        <input
          style={{ ...input, flex: 1 }}
          type="number"
          min={1}
          step={1}
          value={rate}
          onChange={(e) => setRate(e.target.value)}
          inputMode="numeric"
        />
        <button style={{ ...primaryBtn, flex: 'none', minWidth: 124 }} onClick={apply} disabled={applying}>
          {applying ? 'Saving…' : saved ? 'Saved ✓' : 'Apply'}
        </button>
      </div>
      <p style={{ color: '#8a929b', fontSize: 11, margin: '6px 0 0' }}>
        {mode === 'fixed' ? 'Applied to every payment.' : 'Used only when the server returns no estimate.'}{' '}
        Estimated fee for a typical payment:{' '}
        <span style={{ color: '#cfd4da', fontFamily: 'ui-monospace, monospace' }}>{previewCoin}</span>
      </p>

      {applyError && <div style={{ ...errBox, marginTop: 10 }}>{applyError}</div>}
    </section>
  )
}

// ---- 2. Block explorer ----

function ExplorerSection({ coin }: { coin: string }) {
  const [base, setBase] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setBase(explorerBase(coin))
    setSaved(false)
  }, [coin])

  const save = () => {
    setExplorerBase(coin, base)
    setBase(explorerBase(coin))
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }

  const reset = () => {
    setExplorerBase(coin, '')
    setBase(defaultExplorerBase(coin))
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }

  return (
    <section style={card}>
      <h3 style={sectionTitle}>Block explorer</h3>
      <p style={sectionHint}>
        Coin page for the “open in explorer” links on History and Addresses. Links append{' '}
        <code style={{ color: '#cfd4da' }}>?tx=&lt;txid&gt;</code> and{' '}
        <code style={{ color: '#cfd4da' }}>?addr=&lt;addr&gt;</code> so the explorer opens the
        coin page and jumps straight to it.
      </p>
      <input
        style={input}
        value={base}
        onChange={(e) => setBase(e.target.value)}
        placeholder={defaultExplorerBase(coin)}
        autoComplete="off"
        spellCheck={false}
      />
      <div style={{ display: 'flex', gap: 10, marginTop: 14, justifyContent: 'space-between' }}>
        <button style={primaryBtn} onClick={save}>
          {saved ? 'Saved ✓' : 'Save'}
        </button>
        <ConfirmResetButton onConfirm={reset} />
      </div>
    </section>
  )
}

// ---- 3. Base unit / display ----

function BaseUnitSection({ coin }: { coin: string }) {
  const [unit, setUnit] = useState<BaseUnit>(getBaseUnit())
  const [grouped, setGrouped] = useState<boolean>(getThousandSep())

  const change = (u: BaseUnit) => {
    setUnit(u)
    setBaseUnit(u)
  }
  const toggleGroup = (on: boolean) => {
    setGrouped(on)
    setThousandSep(on)
  }

  // Preview value large enough that the thousand separators show.
  const preview = formatAmount('12345.5', coin)

  return (
    <section style={card}>
      <h3 style={sectionTitle}>Base unit</h3>
      <p style={sectionHint}>How amounts are displayed across the wallet.</p>
      <select
        style={{ ...input, appearance: 'auto' }}
        value={unit}
        onChange={(e) => change(e.target.value as BaseUnit)}
      >
        {BASE_UNIT_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, fontSize: 12, color: '#cfd4da', cursor: 'pointer', userSelect: 'none' }}>
        <input
          type="checkbox"
          checked={grouped}
          onChange={(e) => toggleGroup(e.target.checked)}
          style={{ accentColor: 'var(--coin)', width: 14, height: 14 }}
        />
        Add thousand separators
      </label>
      <p style={{ color: '#8a929b', fontSize: 11, marginTop: 10 }}>
        Preview: <span style={{ color: '#cfd4da', fontFamily: 'ui-monospace, monospace' }}>{preview}</span>
      </p>
    </section>
  )
}

// ---- 4. Coin settings (startup selection + color) ----

// Compact 16x16 themed checkbox (matches the DEX section's house style; retints per coin).
function CheckBox({ checked, disabled, onChange, ariaLabel }: {
  checked: boolean; disabled?: boolean; onChange: () => void; ariaLabel: string
}) {
  return (
    <label aria-label={ariaLabel} style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 16, height: 16,
      cursor: disabled ? 'default' : 'pointer', userSelect: 'none',
    }}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={onChange}
        style={{ position: 'absolute', opacity: 0, width: 0, height: 0 }} />
      <span aria-hidden style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 16, height: 16,
        borderRadius: 5,
        border: checked ? '1px solid var(--coin)' : '1px solid rgba(255,255,255,0.18)',
        background: checked ? 'rgba(var(--coin-rgb),0.22)' : 'rgba(255,255,255,0.04)',
        boxShadow: checked ? '0 0 8px rgba(var(--coin-rgb),0.30)' : 'none',
        color: '#eef2f8', fontSize: 11, lineHeight: 1,
        transition: 'background .2s, border-color .2s, box-shadow .2s',
      }}>{checked ? '✓' : ''}</span>
    </label>
  )
}

function CoinSettingsSection({ coin }: { coin: string }) {
  // Two columns: left = global startup selection; right = this-wallet start/stop + coin color.
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
      <div style={{ flex: '1 1 340px', minWidth: 300 }}>
        <StartupSelectionSection />
      </div>
      <div style={{ flex: '1 1 340px', minWidth: 300, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <ThisWalletSection coin={coin} />
        <ColorSection coin={coin} />
      </div>
    </div>
  )
}

// Left column — global "which coins auto-start at launch" preference (applies next launch).
function StartupSelectionSection() {
  const autostartAll = useStore((s) => s.autostartAll)
  const autostartCoins = useStore((s) => s.autostartCoins)
  const coins = useStore((s) => s.coins)
  const setAutostartAll = useStore((s) => s.setAutostartAll)
  const toggleAutostartCoin = useStore((s) => s.toggleAutostartCoin)
  const saveCurrentRunningAsDefault = useStore((s) => s.saveCurrentRunningAsDefault)
  const name = (t: string) => coins?.[t]?.coin_name ?? t

  return (
    <section style={card}>
      <h3 style={sectionTitle}>Start coins at startup</h3>
      <p style={sectionHint}>
        Choose which coins start automatically when the wallet launches. Applies on the next launch —
        start or stop coins for this session from the wallet list.
        {!autostartAll && ' At least one coin must start.'}
      </p>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <CheckBox checked={autostartAll} onChange={() => setAutostartAll(!autostartAll)}
          ariaLabel="Start all coins at startup" />
        <span style={{ color: '#e6e6e6', fontWeight: 600 }}>Start all coins at startup</span>
      </div>
      <div style={{
        marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8,
        opacity: autostartAll ? 0.5 : 1, pointerEvents: autostartAll ? 'none' : 'auto',
      }}>
        {COIN_ORDER.map((t) => (
          <div key={t} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <CheckBox checked={autostartAll || !!autostartCoins[t]} disabled={autostartAll}
              onChange={() => toggleAutostartCoin(t)} ariaLabel={`Start ${t} at startup`} />
            <CoinIcon ticker={t} size={20} />
            <span style={{ color: '#e6e6e6', fontWeight: 600 }}>{t}</span>
            <span style={{ color: '#8a929b', fontSize: 12 }}>{name(t)}</span>
          </div>
        ))}
      </div>
      <button type="button" style={{ ...secondaryBtn, marginTop: 12 }} onClick={saveCurrentRunningAsDefault}>
        Save current running set as default startup
      </button>
    </section>
  )
}

// Right column (top) — start/stop the currently-selected coin's wallet for this session.
function ThisWalletSection({ coin }: { coin: string }) {
  const coinStatus = useStore((s) => s.coinStatus)
  const coins = useStore((s) => s.coins)
  const startCoin = useStore((s) => s.startCoin)
  const stopCoin = useStore((s) => s.stopCoin)
  const [busy, setBusy] = useState(false)

  const selStatus = coinStatus[coin] ?? 'running'
  const runningCount = COIN_ORDER.filter((t) => (coinStatus[t] ?? 'running') === 'running').length
  const name = (t: string) => coins?.[t]?.coin_name ?? t

  const onStopSelected = async () => {
    setBusy(true)
    try {
      const r = await stopCoin(coin)
      if (r.blocked && window.confirm(
        `${coin} is connected to the DEX. Stopping it will cancel its DEX orders. Stop anyway?`)) {
        await stopCoin(coin, true)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <section style={card}>
      <h3 style={sectionTitle}>This wallet — {name(coin)}</h3>
      {selStatus === 'running' ? (
        <>
          <p style={sectionHint}>{coin} is running.</p>
          <button type="button" disabled={busy || runningCount <= 1}
            style={{ ...secondaryBtn, opacity: runningCount <= 1 ? 0.5 : 1 }}
            title={runningCount <= 1 ? "Can't stop your last running coin" : ''}
            onClick={onStopSelected}>
            {busy ? 'Stopping…' : 'Stop wallet'}
          </button>
        </>
      ) : (
        <>
          <p style={sectionHint}>{coin} isn't running.</p>
          <button type="button" disabled={selStatus === 'starting'} style={primaryBtn}
            onClick={() => startCoin(coin)}>
            {selStatus === 'starting' ? 'Starting…' : 'Start wallet'}
          </button>
        </>
      )}
    </section>
  )
}

function ColorSection({ coin }: { coin: string }) {
  const overrides = useStore((s) => s.coinColorOverrides)
  const setCoinColor = useStore((s) => s.setCoinColor)
  const resetCoinColor = useStore((s) => s.resetCoinColor)

  // Effective color (override wins); whole UI retints live since every consumer reads the same store override.
  const color = resolveCoinColor(overrides, coin)

  return (
    <section style={card}>
      <h3 style={sectionTitle}>Coin color</h3>
      <p style={sectionHint}>
        Pick a custom color for {coin}
        <br />
        <span style={{ display: 'inline-block', paddingLeft: 24 }}>
          — used for its accent, donut slice, buttons and rings.
        </span>
      </p>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <input
          type="color"
          value={color}
          onChange={(e) => setCoinColor(coin, e.target.value)}
          aria-label={`${coin} brand color`}
          style={{
            width: 44,
            height: 32,
            padding: 0,
            background: 'transparent',
            border: '1px solid rgba(255,255,255,0.18)',
            borderRadius: 8,
            cursor: 'pointer',
            flex: '0 0 auto',
          }}
        />
        <span style={{ color: '#cfd4da', fontFamily: 'ui-monospace, monospace', fontSize: 13 }}>
          {color}
        </span>
        <ConfirmResetButton onConfirm={() => resetCoinColor(coin)} style={{ marginLeft: 'auto' }} />
      </div>
    </section>
  )
}

// ---- 6. Wallet information (read-only) ----

function WalletInfoSection({ coin }: { coin: string }) {
  const setActiveTab = useStore((s) => s.setActiveTab)
  const setToolsSection = useStore((s) => s.setToolsSection)
  const [info, setInfo] = useState<WalletInfo | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    let live = true
    setInfo(null); setErr(null)
    getWalletInfo(coin)
      .then((r) => { if (live) setInfo(r) })
      .catch((e) => { if (live) setErr(e instanceof Error ? e.message : String(e)) })
    return () => { live = false }
  }, [coin])
  const row = (label: string, value: React.ReactNode) => (
    <div style={{ marginTop: 12 }}>
      <div style={{ ...lbl, marginTop: 0 }}>{label}</div>
      {value}
    </div>
  )
  const openKeysAndSeed = () => {
    setToolsSection('keys')
    setActiveTab('tools')
  }
  return (
    <section style={card}>
      <h3 style={sectionTitle}>Wallet information — {coin}</h3>
      <p style={sectionHint}>Public, watch-only identity for this coin — nothing here can spend funds.</p>
      <div style={{ fontSize: 11, color: '#4fc3f7', background: 'rgba(79,195,247,0.08)', border: '1px solid rgba(79,195,247,0.3)', borderRadius: 8, padding: '6px 10px' }}>
        ⓘ All six coins share the same BIP39 seed. To see the seed itself, use Tools →{' '}
        <button
          type="button"
          onClick={openKeysAndSeed}
          style={{
            appearance: 'none',
            background: 'transparent',
            border: 0,
            padding: 0,
            color: '#8be4ff',
            font: 'inherit',
            textDecoration: 'underline',
            cursor: 'pointer',
          }}
        >
          Keys &amp; seed
        </button>
        .
      </div>
      {err && <div style={errBox}>{err}</div>}
      {!info && !err && <p style={{ color: '#8a929b', fontSize: 12, marginTop: 12 }}>Loading…</p>}
      {info && (
        <>
          {row('Master public key (xpub)', (
            <div style={{ display: 'flex', alignItems: 'stretch', gap: 8 }}>
              <div style={{ ...codeBox, flex: 1, minWidth: 0, wordBreak: 'break-all' }}>{info.mpk}</div>
              <button
                type="button"
                style={{ ...secondaryBtn, flex: 'none', minWidth: 88 }}
                onClick={() => { void navigator.clipboard?.writeText(info.mpk); setCopied(true); setTimeout(() => setCopied(false), 1500) }}
              >
                {copied ? 'Copied ✓' : 'Copy'}
              </button>
            </div>
          ))}
          {row('Derivation path', <div style={codeBox}>{info.derivation_path}</div>)}
          {row('Script type', <div style={{ color: '#cfd4da', fontSize: 13 }}>Native SegWit (P2WPKH)</div>)}
          {row('Key fingerprint', <div style={codeBox}>{info.fingerprint ?? '—'}</div>)}
        </>
      )}
    </section>
  )
}

// ---- 7. Security & password (lock / unlock + change password) ----

function SecuritySection() {
  // Lock state lives in the store, shared with the footer lock chip.
  const locked = useStore((s) => s.sessionLocked)
  const unlockSessionPw = useStore((s) => s.unlockSessionPw)
  const refreshSessionStatus = useStore((s) => s.refreshSessionStatus)
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [showChange, setShowChange] = useState(false)

  useEffect(() => { void refreshSessionStatus() }, [refreshSessionStatus])

  const doUnlock = async () => {
    if (!pw) return
    setBusy(true); setErr(null)
    try { await unlockSessionPw(pw); setPw('') }
    catch (e) { setErr(e instanceof Error ? e.message : 'wrong password') } finally { setBusy(false) }
  }

  return (
    <section style={card}>
      <h3 style={sectionTitle}>Password</h3>
      <p style={sectionHint}>Change the password that encrypts your wallet. Lock/unlock from the chip in the bottom-right corner.</p>
      {locked === true ? (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#e0a23a', marginBottom: 8 }}>
            <img src={lockImg} alt="" width={14} height={14} style={{ display: 'block', flex: '0 0 auto' }} />
            Locked — enter your password to re-enable signing &amp; reveal.
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input type="password" style={{ ...input, flex: 1 }} value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void doUnlock()} placeholder="Password" autoComplete="off" />
            <button type="button" style={{ ...primaryBtn, flex: 'none' }} disabled={busy || !pw} onClick={() => void doUnlock()}>{busy ? 'Unlocking…' : 'Unlock'}</button>
          </div>
        </>
      ) : (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" style={primaryBtn} onClick={() => setShowChange(true)}>Change password…</button>
        </div>
      )}
      {err && <div style={errBox}>{err}</div>}
      {showChange && <ChangePasswordModal onClose={() => setShowChange(false)} />}
    </section>
  )
}

function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const submit = async () => {
    setErr(null)
    if (next.length < 8) return setErr('New password must be at least 8 characters.')
    if (next !== confirm) return setErr('New passwords do not match.')
    if (next === cur) return setErr('New password must differ from the current one.')
    setBusy(true)
    try { await changePassword(cur, next); setDone(true) }
    catch (e) { setErr(e instanceof Error ? e.message : 'could not change password') } finally { setBusy(false) }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 16 }}>
      <div style={{ ...card, width: 'min(440px, 92vw)', position: 'relative' }}>
        {done ? (
          <>
            <h3 style={sectionTitle}>Password changed ✓</h3>
            <p style={sectionHint}>The vault is re-encrypted under the new password. Your recovery seed is unchanged, and you stay signed in.</p>
            <button type="button" style={{ ...primaryBtn, marginTop: 10 }} onClick={onClose}>Close</button>
          </>
        ) : (
          <>
            <h3 style={sectionTitle}>Change password</h3>
            <p style={sectionHint}>This re-encrypts the vault only — your recovery seed is never changed.</p>
            <label style={lbl}>Current password</label>
            <input type="password" style={input} value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="off" />
            <label style={lbl}>New password (min 8)</label>
            <input type="password" style={input} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="off" />
            <label style={lbl}>Confirm new password</label>
            <input type="password" style={input} value={confirm} onChange={(e) => setConfirm(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void submit()} autoComplete="off" />
            {err && <div style={errBox}>{err}</div>}
            <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
              <button type="button" style={secondaryBtn} disabled={busy} onClick={onClose}>Cancel</button>
              <button type="button" style={{ ...primaryBtn, marginLeft: 'auto' }} disabled={busy || !cur || !next} onClick={() => void submit()}>{busy ? 'Changing… (a few seconds)' : 'Change password'}</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ---- 8. Proxy (Tor / SOCKS5) ----

function ProxySection({ coin, initial }: { coin: string; initial: NetworkSettings | null }) {
  const setNetworkSettings = useStore((s) => s.setNetworkSettings)
  const [enable, setEnable] = useState(false)
  const [host, setHost] = useState('127.0.0.1')
  const [port, setPort] = useState('9050')
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [hasPw, setHasPw] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [msg, setMsg] = useState('')

  // Reflect the backend (defaults when a coin has no proxy) so switching coins never strands the previous host/port.
  const hydrate = (ns: NetworkSettings) => {
    const p = ns.proxy
    if (p) {
      setEnable(p.enabled)
      setHost(p.host || '127.0.0.1')
      setPort(p.port ? String(p.port) : '9050')
      setUser(p.user || '')
      setHasPw(p.has_password)
    }
  }
  // Hydrate from the parent-fetched payload (null while the fetch is in flight).
  useEffect(() => { if (initial) hydrate(initial) }, [initial])

  const apply = async () => {
    setErr(null); setMsg('')
    setBusy(true)
    try {
      const ns = await setProxy(coin, { enable, host: host.trim(), port: Number(port), user: user.trim(), password })
      hydrate(ns)
      setNetworkSettings(coin, ns)
      setPassword('')
      setMsg(ns.connected ? 'Applied ✓' : 'Applied — daemon restarting / not yet connected')
      setTimeout(() => setMsg(''), 4000)
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <section style={card}>
      <h3 style={sectionTitle}>Proxy (Tor / SOCKS5) — {coin}</h3>
      <p style={sectionHint}>Route this coin's daemon through a SOCKS5 proxy. Applying restarts the daemon.</p>
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#cfd4da', cursor: 'pointer', userSelect: 'none' }}>
        <input type="checkbox" checked={enable} onChange={(e) => setEnable(e.target.checked)} style={{ accentColor: 'var(--coin)', width: 14, height: 14 }} />
        Use a SOCKS5 proxy
      </label>
      <div style={{ opacity: enable ? 1 : 0.5, pointerEvents: enable ? 'auto' : 'none', marginTop: 8 }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ flex: 2 }}><label style={lbl}>Host</label><input style={input} value={host} onChange={(e) => setHost(e.target.value)} placeholder="127.0.0.1" autoComplete="off" spellCheck={false} /></div>
          <div style={{ flex: 1 }}><label style={lbl}>Port</label><input style={input} value={port} onChange={(e) => setPort(e.target.value)} placeholder="9050" inputMode="numeric" autoComplete="off" /></div>
        </div>
        <label style={lbl}>Username — optional</label>
        <input style={input} value={user} onChange={(e) => setUser(e.target.value)} autoComplete="off" />
        <label style={lbl}>Password — optional</label>
        <input type="password" style={input} value={password} onChange={(e) => setPassword(e.target.value)} placeholder={hasPw ? '•••••• (stored — leave blank to keep)' : ''} autoComplete="off" />
        <p style={{ ...sectionHint, marginTop: 6 }}>Tor is usually 127.0.0.1:9050. A wrong proxy just shows the coin disconnected — turn it off here to recover.</p>
      </div>
      {err && <div style={errBox}>{err}</div>}
      {msg && <div style={{ color: '#7fe0a3', fontSize: 12, marginTop: 8 }}>{msg}</div>}
      <button type="button" style={{ ...primaryBtn, marginTop: 12 }} disabled={busy} onClick={() => void apply()}>{busy ? 'Restarting daemon…' : 'Apply'}</button>
    </section>
  )
}
