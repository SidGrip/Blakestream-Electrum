// Zustand store: single source of truth for the wallet. Components consume `useStore`.
// Global aggregates refresh on the 8s poll; per-coin detail is fetched on selection with a
// request-id race guard so clicking quickly between coins can't land stale data.

import { create } from 'zustand'
import type {
  Coins, Portfolio, Tx, Timeframe, SetupStatus, TabKey, AddressRow, Contact, LnInfo, LnTx, LnRequest,
  PriceSourcesState, NetworkSettings, DexIntegrationSettings,
} from './types'
import {
  getCoins, getPortfolio, getHistory, getLnHistoryAll, getInfo, getSetupStatus, getStartup,
  getCoinHistory, getAddresses, getLnStatus, getLnHistory, getLnRequests, getContacts,
  getNetworkSettings, getSessionStatus, lockSession, unlockSession,
  getDexIntegration, setDexIntegration, setDexIntegrationStartup, approveDexPairing, forgetDexPairing,
  clearPendingDexPairing,
  setCoinColors as apiSetCoinColors,
  getStartupCoins, setStartupCoins as apiSetStartupCoins,
  startCoin as apiStartCoin, stopCoin as apiStopCoin,
  getPriceSources, setPriceDisplay as apiSetPriceDisplay, setPriceEnabled as apiSetPriceEnabled,
  createWallet as apiCreateWallet, restoreWallet as apiRestoreWallet,
  unlockWallet as apiUnlockWallet,
} from './api'
import type { Startup, StartupCoins } from './api'
import { COIN_COLORS, COIN_ORDER } from './types'
import {
  getFiatCurrency, setFiatCurrency as persistFiatCurrency,
  getAllocationOpen, setAllocationOpen as persistAllocationOpen,
} from './explorer'

export interface CoinDetailState {
  history: Tx[]
  lnHistory: LnTx[]
  // Outstanding Lightning invoices, cached + refreshed on the poll so the invoices panel persists across restarts.
  lnRequests: LnRequest[]
  addresses: AddressRow[]
  lnInfo: LnInfo | null
  loading: boolean
  lastReqId: number
}

const emptyCoinState = (): CoinDetailState => ({
  history: [], lnHistory: [], lnRequests: [], addresses: [], lnInfo: null, loading: false, lastReqId: 0,
})

// User per-coin color overrides, persisted in localStorage.
const COIN_COLOR_OVERRIDES_KEY = 'coinColorOverrides'

function loadCoinColorOverrides(): Record<string, string> {
  try {
    const raw = localStorage.getItem(COIN_COLOR_OVERRIDES_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') return parsed as Record<string, string>
    }
  } catch {
    /* localStorage unavailable or malformed JSON; fall through to defaults */
  }
  return {}
}

function persistCoinColorOverrides(map: Record<string, string>): void {
  try {
    localStorage.setItem(COIN_COLOR_OVERRIDES_KEY, JSON.stringify(map))
  } catch {
    /* localStorage unavailable; nothing to persist */
  }
}

function effectiveCoinColors(overrides: Record<string, string>): Record<string, string> {
  return { ...COIN_COLORS, ...overrides }
}

function syncCoinColorsToBackend(overrides: Record<string, string>): void {
  void apiSetCoinColors(effectiveCoinColors(overrides)).catch(() => {
    /* backend may still be starting; a later color edit will retry */
  })
}

// Per-coin fiat display mode (true = show this coin's balance in fiat), persisted across restarts.
const COIN_FIAT_MODE_KEY = 'coinFiatMode'

function loadCoinFiatMode(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(COIN_FIAT_MODE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') return parsed as Record<string, boolean>
    }
  } catch {
    /* localStorage unavailable or malformed; default to all-coin */
  }
  return {}
}

function persistCoinFiatMode(map: Record<string, boolean>): void {
  try {
    localStorage.setItem(COIN_FIAT_MODE_KEY, JSON.stringify(map))
  } catch {
    /* localStorage unavailable; nothing to persist */
  }
}

// Global balance display mode for the header AND the left rail: coin amount / Lightning / fiat.
// The header toggle (and a rail-balance click) cycle it; the rail follows so both stay in sync.
export type BalanceView = 'onchain' | 'lightning' | 'fiat'

// Which coins auto-start at launch. Mirrors the backend sidecar (the authoritative copy); the
// localStorage mirror only gives the Coin-settings tab an instant first paint. Default: start all.
const AUTOSTART_KEY = 'autostartCoins'

function loadAutostart(): StartupCoins {
  try {
    const raw = localStorage.getItem(AUTOSTART_KEY)
    if (raw) {
      const p = JSON.parse(raw)
      if (p && typeof p === 'object' && Array.isArray(p.coins)) {
        return { include_all: p.include_all !== false, coins: p.coins as string[] }
      }
    }
  } catch {
    /* localStorage unavailable or malformed; default to start-all */
  }
  return { include_all: true, coins: [...COIN_ORDER] }
}

function persistAutostart(pref: StartupCoins): void {
  try {
    localStorage.setItem(AUTOSTART_KEY, JSON.stringify({ include_all: pref.include_all, coins: pref.coins }))
  } catch {
    /* localStorage unavailable; nothing to persist */
  }
}

function syncAutostartToBackend(pref: StartupCoins): void {
  void apiSetStartupCoins(pref).catch(() => {
    /* backend may still be starting; a later edit retries */
  })
}

// Per-coin running state derived for the wallet list: running | starting | stopped | failed.
export type CoinRunState = 'running' | 'starting' | 'stopped' | 'failed'

function startupToCoinStatus(s: Startup['coins'][string] | undefined): CoinRunState {
  switch (s) {
    case 'ready': return 'running'
    case 'stopped': return 'stopped'
    case 'failed': return 'failed'
    default: return 'starting'   // pending | starting | undefined
  }
}

function hasConfiguredPriceApi(st: PriceSourcesState | null): boolean {
  return Boolean(st?.sources?.some((src) => (
    src.enabled !== false
    && src.urlTemplate.trim().length > 0
    && src.jsonPath.trim().length > 0
  )))
}

export type ToolsSection = 'backup' | 'load' | 'sign' | 'crypto' | 'advanced' | 'keys'
export type HistoryType = 'all' | 'onchain' | 'lightning'

interface StoreState {
  coins: Coins | null
  portfolio: Portfolio | null
  history: Tx[]
  lnHistory: LnTx[]   // global cross-coin Lightning feed (mirrors `history`, fetched each poll)
  selected: string | null
  activeTab: TabKey
  lightningMode: 'simple' | 'advanced'
  toolsSection: ToolsSection
  historyType: HistoryType
  coinStates: Record<string, CoinDetailState>
  contacts: Contact[]
  timeframe: Timeframe
  connected: Record<string, boolean>
  loading: boolean
  error: string | null
  setup: SetupStatus | null
  startup: Startup | null
  onboarded: boolean
  initialChecked: boolean
  connectError: boolean
  coinColorOverrides: Record<string, string>
  // Selective coin startup (Coin settings tab). autostartAll/autostartCoins is the saved preference
  // (applies next launch). coinStatus is the live per-coin run state for the wallet list.
  autostartAll: boolean
  autostartCoins: Record<string, boolean>
  coinStatus: Record<string, CoinRunState>
  // Price/currency: seeded from localStorage for instant first paint, reconciled from the backend.
  // priceSources holds the full masked config for the Settings UI (loaded on demand).
  coinFiatMode: Record<string, boolean>
  balanceView: BalanceView
  fiatCurrency: string
  priceSources: PriceSourcesState | null
  priceApiConfigured: boolean
  // Session-only UI collapse state (survives tab/coin navigation, resets on app restart).
  contactsAddOpen: boolean
  sendPayContactOpen: boolean
  priceSectionOpen: boolean
  // Sidebar Allocation donut collapsed/expanded; persisted (collapsed on first launch).
  allocationOpen: boolean
  // Transient toasts (e.g. health-failover "switched server ✓").
  toasts: Toast[]
  // Local DEX discovery consent and presence, shared by Settings and the footer chip. null = unknown.
  dexIntegrationAllowed: boolean | null
  dexStartOnStartup: boolean
  dexConnected: boolean
  dexLastSeen: number | null
  dexHeartbeatTtlSeconds: number
  dexTrustedId: string | null
  dexTrustedName: string | null
  dexPendingPair: DexIntegrationSettings['pending_dex_pair']

  refresh: () => Promise<void>
  setSelected: (t: string) => void
  setActiveTab: (t: TabKey) => void
  setLightningMode: (mode: 'simple' | 'advanced') => void
  setToolsSection: (section: ToolsSection) => void
  setHistoryType: (t: HistoryType) => void
  fetchCoinData: (coin: string, opts?: { background?: boolean }) => Promise<void>
  loadContacts: () => Promise<void>
  setTimeframe: (tf: Timeframe) => void
  startPolling: (ms?: number) => void
  stopPolling: () => void
  createWallet: (password: string) => Promise<string>
  restoreWallet: (password: string, mnemonic: string) => Promise<void>
  unlockWallet: (password: string) => Promise<void>
  // In-session soft lock (Settings → Security + the footer lock chip). null = unknown yet.
  sessionLocked: boolean | null
  refreshSessionStatus: () => Promise<void>
  lockWallet: () => Promise<void>
  unlockSessionPw: (password: string) => Promise<void>
  // Per-coin network/proxy settings cache: shows instantly on revisit while a background refresh
  // (loadNetworkSettings force:true) keeps status/height current; also updated on apply.
  networkSettings: Record<string, NetworkSettings>
  loadNetworkSettings: (coin: string, opts?: { force?: boolean }) => Promise<void>
  setNetworkSettings: (coin: string, ns: NetworkSettings) => void
  finishOnboarding: () => void
  finishConnecting: () => Promise<void>
  setCoinColor: (ticker: string, hex: string) => void
  resetCoinColor: (ticker: string) => void
  // Selective coin startup actions (Coin settings tab + wallet-list Start/Stop).
  setAutostartAll: (on: boolean) => void
  toggleAutostartCoin: (ticker: string) => void
  saveCurrentRunningAsDefault: () => void
  loadStartupCoins: () => Promise<void>
  startCoin: (ticker: string) => Promise<void>
  stopCoin: (ticker: string, force?: boolean) => Promise<{ blocked: boolean }>
  loadPriceSources: () => Promise<void>
  applyPriceState: (st: PriceSourcesState) => void
  toggleCoinFiat: (ticker: string) => void
  toggleAllFiatFrom: (ticker: string) => void
  cycleBalanceView: () => void
  setFiatCurrency: (code: string) => void
  setCoinAddresses: (coin: string, rows: AddressRow[]) => void
  setContactsAddOpen: (open: boolean) => void
  setSendPayContactOpen: (open: boolean) => void
  setPriceSectionOpen: (open: boolean) => void
  setAllocationOpen: (open: boolean) => void
  refreshDexIntegration: () => Promise<void>
  setDexIntegrationAllowed: (allowed: boolean) => Promise<void>
  setDexStartOnStartup: (enabled: boolean) => Promise<void>
  approveDexPair: (dexId: string, dexName?: string) => Promise<void>
  clearPendingDexPair: () => Promise<void>
  forgetDexPair: () => Promise<void>
  pushToast: (text: string, kind?: Toast['kind']) => void
  dismissToast: (id: number) => void
}

// Module-level handle so polling survives re-renders and only one timer runs.
let pollTimer: ReturnType<typeof setTimeout> | null = null
// Coins whose history we've warmed in the background after sync, so each is fetched only once (not every poll).
const prefetchedHistory = new Set<string>()
// Last-seen balance signature per coin; a change means a new tx landed, so refresh that coin's cached history.
const lastSeenBalance = new Map<string, string>()
// Per-coin history refresh safety net. Balance changes still force an immediate refresh; these
// timers keep confirmation counts moving for rows whose balance no longer changes.
const lastCoinHistoryRefresh = new Map<string, number>()
const SELECTED_HISTORY_REFRESH_MS = 60_000
const PENDING_HISTORY_REFRESH_MS = 60_000
const BACKGROUND_HISTORY_REFRESH_MS = 240_000

function hasPendingHistoryRows(rows: Tx[] | undefined): boolean {
  return Boolean(rows?.some((row) => {
    const conf = Number(row.confirmations ?? 0)
    return Number.isFinite(conf) && conf < 6
  }))
}
// When the very first refresh started — used to fail "Connecting…" after a timeout.
let firstRefreshAt: number | null = null
// Failover toast de-dup: last seq toasted per coin, plus a flag to seed pre-existing events silently at startup.
let toastIdSeq = 0
let failoverSeeded = false
const lastFailoverSeq: Record<string, number> = {}
// Reconcile the autostart preference from the backend exactly once per session.
let loadedStartupOnce = false

function dexStateFromSettings(settings: DexIntegrationSettings) {
  return {
    dexIntegrationAllowed: Boolean(settings.allow_local_dex),
    dexStartOnStartup: Boolean(settings.start_local_dex_on_startup),
    dexConnected: Boolean(settings.dex_connected),
    dexLastSeen: typeof settings.dex_last_seen === 'number' ? settings.dex_last_seen : null,
    dexHeartbeatTtlSeconds: Number(settings.heartbeat_ttl_seconds) || 45,
    dexTrustedId: typeof settings.trusted_dex_id === 'string' && settings.trusted_dex_id ? settings.trusted_dex_id : null,
    dexTrustedName: typeof settings.trusted_dex_name === 'string' && settings.trusted_dex_name ? settings.trusted_dex_name : null,
    dexPendingPair: settings.pending_dex_pair ?? null,
  }
}

export interface Toast {
  id: number
  text: string
  kind: 'info' | 'success' | 'warn'
}

// Monotonic generation for session lock/unlock: a user action bumps it, so a slower in-flight poll
// status read only applies if unchanged — a stale poll can't flip the lock chip back.
let sessionStatusGen = 0

export const useStore = create<StoreState>((set, get) => ({
  coins: null,
  portfolio: null,
  history: [],
  lnHistory: [],
  selected: null,
  activeTab: 'history',
  lightningMode: 'simple',
  toolsSection: 'load',
  historyType: 'all',
  coinStates: {},
  contacts: [],
  timeframe: '24H',
  connected: {},
  loading: false,
  error: null,
  setup: null,
  startup: null,
  onboarded: false,
  initialChecked: false,
  connectError: false,
  coinColorOverrides: loadCoinColorOverrides(),
  autostartAll: loadAutostart().include_all,
  autostartCoins: Object.fromEntries(
    COIN_ORDER.map((t) => [t, loadAutostart().include_all || loadAutostart().coins.includes(t)]),
  ),
  coinStatus: {},
  coinFiatMode: loadCoinFiatMode(),
  balanceView: 'onchain',
  fiatCurrency: getFiatCurrency(),
  priceSources: null,
  priceApiConfigured: false,
  sessionLocked: null,
  networkSettings: {},
  contactsAddOpen: false,
  sendPayContactOpen: false,
  priceSectionOpen: false,
  allocationOpen: getAllocationOpen(),
  toasts: [],
  dexIntegrationAllowed: null,
  dexStartOnStartup: false,
  dexConnected: false,
  dexLastSeen: null,
  dexHeartbeatTtlSeconds: 45,
  dexTrustedId: null,
  dexTrustedName: null,
  dexPendingPair: null,

  refresh: async () => {
    if (get().loading) return
    set({ loading: true })
    if (firstRefreshAt === null) firstRefreshAt = Date.now()
    // Reconcile the autostart preference from the backend once (it is authoritative).
    if (!loadedStartupOnce) { loadedStartupOnce = true; void get().loadStartupCoins() }
    if (!get().initialChecked) {
      try {
        const startup = await getStartup()
        // Derive per-coin run state from the startup map, but never downgrade an optimistic
        // 'starting' (a click) until the backend reports a terminal state.
        const cs: Record<string, CoinRunState> = { ...get().coinStatus }
        for (const t of Object.keys(startup.coins)) {
          if (cs[t] === 'starting' && startup.coins[t] !== 'stopped' && startup.coins[t] !== 'ready') continue
          cs[t] = startupToCoinStatus(startup.coins[t])
        }
        set({ startup, connectError: false, coinStatus: cs })
      } catch {
        if (Date.now() - (firstRefreshAt ?? Date.now()) > 120_000) set({ connectError: true })
      }
      set({ loading: false })
      return
    }
    try {
      set({ setup: await getSetupStatus() })
    } catch {
      /* transient backend hiccup */
    }
    try {
      const startup = await getStartup()
      const cs: Record<string, CoinRunState> = { ...get().coinStatus }
      for (const t of Object.keys(startup.coins)) {
        if (startup.coins[t] === 'ready' || startup.coins[t] === 'stopped' || startup.coins[t] === 'failed') {
          cs[t] = startupToCoinStatus(startup.coins[t])
        }
      }
      set({ startup, coinStatus: cs, connectError: false })
    } catch {
      /* startup status is a UI hint; the per-coin probes below are still authoritative */
    }
    try {
      const [coins, portfolio, history, lnHist] = await Promise.all([
        getCoins(),
        getPortfolio(),
        getHistory(),
        getLnHistoryAll().catch(() => ({ transactions: [] as LnTx[] })),
      ])

      // Default the selection to the first coin once metadata is known.
      const tickers = Object.keys(coins)
      const prevSelected = get().selected
      const selected =
        prevSelected && tickers.includes(prevSelected)
          ? prevSelected
          : tickers.length > 0
            ? tickers[0]
            : null

      set({
        coins,
        portfolio,
        history: history.transactions ?? [],
        lnHistory: lnHist.transactions ?? [],
        selected,
        loading: false,
        error: null,
      })

      // Surface health-failover events as toasts (each new seq once; seed silently at startup).
      const fo = portfolio.failover
      if (fo) {
        for (const [t, ev] of Object.entries(fo)) {
          if (ev.seq > (lastFailoverSeq[t] ?? 0)) {
            lastFailoverSeq[t] = ev.seq
            if (failoverSeeded) {
              if (ev.state === 'switching') get().pushToast(`Trying another server for ${t}…`, 'info')
              else if (ev.state === 'switched') get().pushToast(`${t} now on ${ev.server ?? 'a healthier server'} ✓`, 'success')
            }
          }
        }
        failoverSeeded = true
      }

      // Kick off per-coin detail + contacts the first time a coin is selected.
      if (selected && !get().coinStates[selected]) void get().fetchCoinData(selected)
      // Warm each coin's history into the in-memory cache in the BACKGROUND so it's instant when opened.
      for (const t of tickers) {
        const pc = portfolio?.coins?.[t]
        if (!pc) continue   // daemon not answering yet -> nothing to fetch
        // Pull as soon as the wallet is REACHABLE (daemon holds persisted history, so it shows on launch
        // without waiting for full sync), then refresh whenever balance or sync state changes.
        const sig = `${pc.amount}|${pc.pending ?? ''}|${pc.synced}`
        if (!prefetchedHistory.has(t) || lastSeenBalance.get(t) !== sig) {
          prefetchedHistory.add(t)
          lastSeenBalance.set(t, sig)
          lastCoinHistoryRefresh.set(t, Date.now())
          void get().fetchCoinData(t, { background: true })
        }
      }
      // Safety net: re-pull cached per-coin history even if its balance signature looked unchanged.
      // Confirmation counts advance without changing the balance, so every coin with a pending row
      // gets a 60s refresh even when it is not selected. Normal background history is much slower.
      const nowMs = Date.now()
      for (const t of tickers) {
        if (!portfolio?.coins?.[t]) continue
        const rows = get().coinStates[t]?.history
        const refreshMs = hasPendingHistoryRows(rows)
          ? PENDING_HISTORY_REFRESH_MS
          : t === selected
            ? SELECTED_HISTORY_REFRESH_MS
            : BACKGROUND_HISTORY_REFRESH_MS
        const last = lastCoinHistoryRefresh.get(t) ?? 0
        if (nowMs - last > refreshMs) {
          lastCoinHistoryRefresh.set(t, nowMs)
          void get().fetchCoinData(t, { background: true })
        }
      }
      if (get().contacts.length === 0) void get().loadContacts()
      // Keep the lock chip + Security panel in sync with the backend's session state.
      void get().refreshSessionStatus()
      // Keep the optional DEX chip in sync with Settings and external changes.
      void get().refreshDexIntegration()
      // Reconcile the fiat toggle/currency from the backend once (it is authoritative).
      if (!get().priceSources) void get().loadPriceSources()

      const connected: Record<string, boolean> = { ...get().connected }
      const status: Record<string, CoinRunState> = { ...get().coinStatus }
      await Promise.all(
        tickers.map(async (ticker) => {
          try {
            const info = await getInfo(ticker)
            connected[ticker] = info.connected ?? false
            // Backend marks a not-running daemon status:"stopped"; otherwise it answered, so it's
            // running. A successful connected getinfo is terminal too; clear any stale optimistic
            // 'starting' state if the original start response was missed/delayed.
            if (info.status === 'stopped') status[ticker] = 'stopped'
            else if (info.connected === true || status[ticker] !== 'starting') status[ticker] = 'running'
          } catch {
            connected[ticker] = false
            if (status[ticker] !== 'starting') status[ticker] = status[ticker] ?? 'running'
          }
        }),
      )
      set({ connected, coinStatus: status })
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      })
    }
  },

  setSelected: (t) => {
    // Keep the current tab when switching coins (don't bounce back to History); show cached detail instantly.
    set({ selected: t })
    const st = get().coinStates[t]
    const synced = get().portfolio?.coins?.[t]?.synced === true
    // Fetch the first time a coin is opened, or if the cache is empty but the coin is now synced
    // (opened mid-sync / prior fetch discarded). Don't refetch while a fetch is in flight.
    if (!st || (synced && !st.loading && (st.history?.length ?? 0) === 0)) {
      void get().fetchCoinData(t)
    }
  },

  setActiveTab: (t) => set({ activeTab: t }),
  setLightningMode: (mode) => set({ lightningMode: mode }),
  setToolsSection: (toolsSection) => set({ toolsSection }),
  setHistoryType: (historyType) =>
    // Selecting a History Type also drives the balance view: Lightning -> lightning,
    // On-chain -> coin amount; "All" leaves the balance view as the user last set it.
    set(historyType === 'lightning'
      ? { historyType, balanceView: 'lightning' }
      : historyType === 'onchain'
        ? { historyType, balanceView: 'onchain' }
        : { historyType }),

  // Per-coin detail fetch with a race guard: stamp a reqId, fetch in parallel, and DISCARD
  // the result if the user switched coins or a newer request for this coin already fired.
  fetchCoinData: async (coin, opts) => {
    const reqId = Date.now()
    lastCoinHistoryRefresh.set(coin, reqId)
    const background = opts?.background ?? false
    set((s) => ({
      coinStates: {
        ...s.coinStates,
        [coin]: { ...(s.coinStates[coin] ?? emptyCoinState()), loading: true, lastReqId: reqId },
      },
    }))
    const [hist, lnHist, addrs, lnInfo, reqs] = await Promise.all([
      getCoinHistory(coin).catch(() => ({ transactions: [] as Tx[] })),
      getLnHistory(coin).catch(() => ({ transactions: [] as LnTx[] })),
      getAddresses(coin).catch(() => ({ addresses: [] as AddressRow[] })),
      getLnStatus(coin).catch(() => null as LnInfo | null),
      getLnRequests(coin).catch(() => ({ requests: [] as LnRequest[] })),
    ])
    const cur = get()
    // A newer fetch for this coin supersedes this one. The selection guard applies only to FOREGROUND
    // fetches — a background prefetch must store its result even when the coin isn't on screen.
    if ((cur.coinStates[coin]?.lastReqId ?? 0) > reqId) return
    if (!background && cur.selected !== coin) return
    set((s) => {
      const prev = s.coinStates[coin] ?? emptyCoinState()
      // History/LN-history/addresses: backend returns [] when the wallet isn't ready (mid-sync), not when
      // genuinely empty — so a fresh EMPTY result keeps the cached list rather than flashing "nothing yet".
      const keep = <T,>(next: T[] | undefined, old: T[]): T[] =>
        (next && next.length ? next : old)
      return {
        coinStates: {
          ...s.coinStates,
          [coin]: {
            ...prev,
            history: keep(hist.transactions, prev.history),
            lnHistory: keep(lnHist.transactions, prev.lnHistory),
            // NOT keep()'d: requests can be deleted, so an empty result must clear the list (keep would re-show stale rows).
            lnRequests: reqs.requests ?? [],
            addresses: keep(addrs.addresses, prev.addresses),
            lnInfo,
            loading: false,
            lastReqId: reqId,
          },
        },
      }
    })
  },

  loadContacts: async () => {
    try {
      const { contacts } = await getContacts()
      set({ contacts: contacts ?? [] })
    } catch {
      /* contacts unavailable; leave as-is */
    }
  },

  setTimeframe: (tf) => set({ timeframe: tf }),

  createWallet: async (password) => {
    const { mnemonic } = await apiCreateWallet(password)
    return mnemonic
  },
  restoreWallet: async (password, mnemonic) => {
    await apiRestoreWallet(password, mnemonic)
  },
  unlockWallet: async (password) => {
    await apiUnlockWallet(password)
  },
  refreshSessionStatus: async () => {
    const gen = sessionStatusGen
    try {
      const r = await getSessionStatus()
      // Drop the result if a user lock/unlock happened while this poll was in flight.
      if (gen === sessionStatusGen) set({ sessionLocked: r.locked })
    } catch { /* leave as-is */ }
  },
  lockWallet: async () => {
    await lockSession()
    sessionStatusGen++
    set({ sessionLocked: true })
  },
  unlockSessionPw: async (password) => {
    await unlockSession(password)
    sessionStatusGen++
    set({ sessionLocked: false })
  },
  loadNetworkSettings: async (coin, opts) => {
    // Already cached and not forcing → no fetch (the whole point: visiting the tab again is free).
    if (!opts?.force && get().networkSettings[coin]) return
    const ns = await getNetworkSettings(coin)
    set((s) => ({ networkSettings: { ...s.networkSettings, [coin]: ns } }))
  },
  setNetworkSettings: (coin, ns) => set((s) => ({ networkSettings: { ...s.networkSettings, [coin]: ns } })),
  finishOnboarding: () => set({ onboarded: true }),

  finishConnecting: async () => {
    if (get().initialChecked) return
    try {
      const setup = await getSetupStatus()
      set({ setup, initialChecked: true, connectError: false, onboarded: setup.provisioned })
      void get().refresh()
    } catch {
      /* backend hiccup — stay on Connecting; the poll retries */
    }
  },

  startPolling: (ms = 8000) => {
    if (pollTimer !== null) return
    const tick = async () => {
      await get().refresh()
      pollTimer = setTimeout(tick, get().initialChecked ? ms : 700)
    }
    pollTimer = setTimeout(tick, 0)
  },

  stopPolling: () => {
    if (pollTimer !== null) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  },

  // Set a coin's custom brand color: persist the override map and update state so consumers retint live.
  setCoinColor: (ticker, hex) => {
    const next = { ...get().coinColorOverrides, [ticker]: hex }
    persistCoinColorOverrides(next)
    syncCoinColorsToBackend(next)
    set({ coinColorOverrides: next })
  },

  // Clear a coin's override, reverting it to its COIN_COLORS default.
  resetCoinColor: (ticker) => {
    const next = { ...get().coinColorOverrides }
    delete next[ticker]
    persistCoinColorOverrides(next)
    syncCoinColorsToBackend(next)
    set({ coinColorOverrides: next })
  },

  // ---- selective coin startup (preference applies NEXT launch; never touches the running session) ----
  setAutostartAll: (on) => {
    const coins = { ...get().autostartCoins }
    if (on) for (const t of COIN_ORDER) coins[t] = true
    const pref: StartupCoins = on
      ? { include_all: true, coins: [...COIN_ORDER] }
      : { include_all: false, coins: COIN_ORDER.filter((t) => coins[t]) }
    persistAutostart(pref)
    syncAutostartToBackend(pref)
    set({ autostartAll: on, autostartCoins: coins })
  },

  toggleAutostartCoin: (ticker) => {
    const coins = { ...get().autostartCoins, [ticker]: !get().autostartCoins[ticker] }
    if (COIN_ORDER.filter((t) => coins[t]).length === 0) return   // keep >=1 (no zero-daemon startup)
    const pref: StartupCoins = { include_all: false, coins: COIN_ORDER.filter((t) => coins[t]) }
    persistAutostart(pref)
    syncAutostartToBackend(pref)
    set({ autostartAll: false, autostartCoins: coins })
  },

  // Snapshot the coins running RIGHT NOW as the default startup set.
  saveCurrentRunningAsDefault: () => {
    const status = get().coinStatus
    const running = COIN_ORDER.filter((t) => (status[t] ?? 'running') === 'running')
    const all = running.length === COIN_ORDER.length || running.length === 0
    const pref: StartupCoins = all
      ? { include_all: true, coins: [...COIN_ORDER] }
      : { include_all: false, coins: running }
    persistAutostart(pref)
    syncAutostartToBackend(pref)
    set({
      autostartAll: pref.include_all,
      autostartCoins: Object.fromEntries(COIN_ORDER.map((t) => [t, pref.include_all || pref.coins.includes(t)])),
    })
    get().pushToast('Saved current coins as startup default', 'success')
  },

  // Reconcile the preference from the backend (authoritative). Called once from refresh().
  loadStartupCoins: async () => {
    try {
      const pref = await getStartupCoins()
      persistAutostart(pref)
      set({
        autostartAll: pref.include_all,
        autostartCoins: Object.fromEntries(COIN_ORDER.map((t) => [t, pref.include_all || pref.coins.includes(t)])),
      })
    } catch {
      /* backend not ready; the localStorage mirror seeded the initial values */
    }
  },

  startCoin: async (ticker) => {
    set({ coinStatus: { ...get().coinStatus, [ticker]: 'starting' } })
    try {
      const state = await apiStartCoin(ticker)
      const runState: CoinRunState = state.running || state.status === 'ready' ? 'running' : state.status === 'failed' ? 'failed' : 'stopped'
      set({ coinStatus: { ...get().coinStatus, [ticker]: runState } })
      void get().refresh()
    } catch (e) {
      set({ coinStatus: { ...get().coinStatus, [ticker]: 'failed' } })
      get().pushToast(e instanceof Error ? e.message : `Couldn't start ${ticker}`, 'warn')
    }
  },

  // Returns {blocked:true} when the DEX is connected and force wasn't given (the UI confirms).
  stopCoin: async (ticker, force = false) => {
    try {
      await apiStopCoin(ticker, force)
      set({ coinStatus: { ...get().coinStatus, [ticker]: 'stopped' } })
      void get().refresh()
      return { blocked: false }
    } catch (e) {
      const msg = e instanceof Error ? e.message : ''
      if (msg.includes('dex_orders_active') || msg.includes('409')) return { blocked: true }
      get().pushToast(msg || `Couldn't stop ${ticker}`, 'warn')
      return { blocked: false }
    }
  },

  // ---- price / currency ----
  // Apply a backend price-config snapshot: store it and mirror the currency into state + localStorage.
  applyPriceState: (st) => {
    persistFiatCurrency(st.display.fiatCurrency)
    const priceApiConfigured = hasConfiguredPriceApi(st)
    if (!priceApiConfigured) {
      persistCoinFiatMode({})
      set({
        priceSources: st,
        fiatCurrency: st.display.fiatCurrency,
        priceApiConfigured,
        coinFiatMode: {},
      })
      return
    }
    set({ priceSources: st, fiatCurrency: st.display.fiatCurrency, priceApiConfigured })
  },

  loadPriceSources: async () => {
    try {
      get().applyPriceState(await getPriceSources())
    } catch {
      /* price config unavailable; keep the localStorage-seeded values */
    }
  },

  // Flip ONE coin between its amount and fiat value (clicking its balance in the list).
  toggleCoinFiat: (ticker) => {
    if (!get().priceApiConfigured) {
      if (!get().priceSources) void get().loadPriceSources()
      return
    }
    const cur = get().coinFiatMode[ticker] ?? false
    const next = { ...get().coinFiatMode, [ticker]: !cur }
    persistCoinFiatMode(next)
    set({ coinFiatMode: next })
    // Turning fiat on anywhere must ensure the backend is fetching prices.
    if (!cur && !get().priceSources?.enabled) {
      void apiSetPriceEnabled(true).then((st) => get().applyPriceState(st)).catch(() => {})
    }
  },

  // Flip ALL coins at once (top-right balance), using `ticker`'s current state as the reference.
  toggleAllFiatFrom: (ticker) => {
    if (!get().priceApiConfigured) {
      if (!get().priceSources) void get().loadPriceSources()
      return
    }
    const target = !(get().coinFiatMode[ticker] ?? false)
    const next: Record<string, boolean> = {}
    Object.keys(get().coins ?? {}).forEach((t) => { next[t] = target })
    persistCoinFiatMode(next)
    set({ coinFiatMode: next })
    if (target && !get().priceSources?.enabled) {
      void apiSetPriceEnabled(true).then((st) => get().applyPriceState(st)).catch(() => {})
    }
  },

  // Cycle the global balance view (header + rail): coin -> Lightning -> fiat (fiat only with a price API).
  cycleBalanceView: () => {
    const order: BalanceView[] = get().priceApiConfigured
      ? ['onchain', 'lightning', 'fiat']
      : ['onchain', 'lightning']
    const cur = get().balanceView
    const next = order[(order.indexOf(cur) + 1) % order.length] ?? 'onchain'
    set({ balanceView: next })
    if (next === 'fiat' && !get().priceSources?.enabled) {
      void apiSetPriceEnabled(true).then((st) => get().applyPriceState(st)).catch(() => {})
    }
  },

  setFiatCurrency: (code) => {
    persistFiatCurrency(code)
    set({ fiatCurrency: code })
    void apiSetPriceDisplay(code, undefined).then((st) => get().applyPriceState(st)).catch(() => {})
  },

  // Warm the per-coin address cache from the Addresses tab's fetch, so re-opening shows instantly from cache.
  setCoinAddresses: (coin, rows) => set((s) => ({
    coinStates: {
      ...s.coinStates,
      [coin]: { ...(s.coinStates[coin] ?? emptyCoinState()), addresses: rows },
    },
  })),
  setContactsAddOpen: (open) => set({ contactsAddOpen: open }),
  setSendPayContactOpen: (open) => set({ sendPayContactOpen: open }),
  setPriceSectionOpen: (open) => set({ priceSectionOpen: open }),
  setAllocationOpen: (open) => { persistAllocationOpen(open); set({ allocationOpen: open }) },

  refreshDexIntegration: async () => {
    try {
      const settings = await getDexIntegration()
      set(dexStateFromSettings(settings))
    } catch {
      set({
        dexIntegrationAllowed: false,
        dexStartOnStartup: false,
        dexConnected: false,
        dexLastSeen: null,
        dexHeartbeatTtlSeconds: 45,
        dexTrustedId: null,
        dexTrustedName: null,
        dexPendingPair: null,
      })
    }
  },
  setDexIntegrationAllowed: async (allowed) => {
    const settings = await setDexIntegration(allowed)
    set(dexStateFromSettings(settings))
  },
  setDexStartOnStartup: async (enabled) => {
    const settings = await setDexIntegrationStartup(enabled)
    set(dexStateFromSettings(settings))
  },
  approveDexPair: async (dexId, dexName) => {
    const settings = await approveDexPairing(dexId, dexName)
    set(dexStateFromSettings(settings))
  },
  clearPendingDexPair: async () => {
    const settings = await clearPendingDexPairing()
    set(dexStateFromSettings(settings))
  },
  forgetDexPair: async () => {
    const settings = await forgetDexPairing()
    set(dexStateFromSettings(settings))
  },

  pushToast: (text, kind = 'info') => {
    const id = ++toastIdSeq
    set((s) => ({ toasts: [...s.toasts, { id, text, kind }] }))
    window.setTimeout(() => get().dismissToast(id), 4500)
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

if (typeof window !== 'undefined') {
  window.setTimeout(() => syncCoinColorsToBackend(useStore.getState().coinColorOverrides), 0)
}
