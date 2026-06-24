// Zustand store: single source of truth for the wallet. Components consume `useStore`.
// Global aggregates refresh on the 8s poll; per-coin detail is fetched on selection with a
// request-id race guard so clicking quickly between coins can't land stale data.

import { create } from 'zustand'
import type {
  Coins, Portfolio, Tx, Timeframe, SetupStatus, TabKey, AddressRow, Contact, LnInfo, LnTx, LnRequest,
  PriceSourcesState, NetworkSettings,
} from './types'
import {
  getCoins, getPortfolio, getHistory, getLnHistoryAll, getInfo, getSetupStatus, getStartup,
  getCoinHistory, getAddresses, getLnStatus, getLnHistory, getLnRequests, getContacts,
  getNetworkSettings, getSessionStatus, lockSession, unlockSession,
  getDexIntegration, setDexIntegration, setDexIntegrationStartup,
  getPriceSources, setPriceDisplay as apiSetPriceDisplay, setPriceEnabled as apiSetPriceEnabled,
  createWallet as apiCreateWallet, restoreWallet as apiRestoreWallet,
  unlockWallet as apiUnlockWallet,
} from './api'
import type { Startup } from './api'
import { getFiatCurrency, setFiatCurrency as persistFiatCurrency } from './explorer'

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

function hasConfiguredPriceApi(st: PriceSourcesState | null): boolean {
  return Boolean(st?.sources?.some((src) => (
    src.enabled !== false
    && src.urlTemplate.trim().length > 0
    && src.jsonPath.trim().length > 0
  )))
}

interface StoreState {
  coins: Coins | null
  portfolio: Portfolio | null
  history: Tx[]
  lnHistory: LnTx[]   // global cross-coin Lightning feed (mirrors `history`, fetched each poll)
  selected: string | null
  activeTab: TabKey
  lightningMode: 'simple' | 'advanced'
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
  // Price/currency: seeded from localStorage for instant first paint, reconciled from the backend.
  // priceSources holds the full masked config for the Settings UI (loaded on demand).
  coinFiatMode: Record<string, boolean>
  fiatCurrency: string
  priceSources: PriceSourcesState | null
  priceApiConfigured: boolean
  // Session-only UI collapse state (survives tab/coin navigation, resets on app restart).
  contactsAddOpen: boolean
  sendPayContactOpen: boolean
  priceSectionOpen: boolean
  // Transient toasts (e.g. health-failover "switched server ✓").
  toasts: Toast[]
  // Local DEX discovery consent and presence, shared by Settings and the footer chip. null = unknown.
  dexIntegrationAllowed: boolean | null
  dexStartOnStartup: boolean
  dexConnected: boolean
  dexLastSeen: number | null
  dexHeartbeatTtlSeconds: number

  refresh: () => Promise<void>
  setSelected: (t: string) => void
  setActiveTab: (t: TabKey) => void
  setLightningMode: (mode: 'simple' | 'advanced') => void
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
  loadPriceSources: () => Promise<void>
  applyPriceState: (st: PriceSourcesState) => void
  toggleCoinFiat: (ticker: string) => void
  toggleAllFiatFrom: (ticker: string) => void
  setFiatCurrency: (code: string) => void
  setCoinAddresses: (coin: string, rows: AddressRow[]) => void
  setContactsAddOpen: (open: boolean) => void
  setSendPayContactOpen: (open: boolean) => void
  setPriceSectionOpen: (open: boolean) => void
  refreshDexIntegration: () => Promise<void>
  setDexIntegrationAllowed: (allowed: boolean) => Promise<void>
  setDexStartOnStartup: (enabled: boolean) => Promise<void>
  pushToast: (text: string, kind?: Toast['kind']) => void
  dismissToast: (id: number) => void
}

// Module-level handle so polling survives re-renders and only one timer runs.
let pollTimer: ReturnType<typeof setTimeout> | null = null
// Coins whose history we've warmed in the background after sync, so each is fetched only once (not every poll).
const prefetchedHistory = new Set<string>()
// Last-seen balance signature per coin; a change means a new tx landed, so refresh that coin's cached history.
const lastSeenBalance = new Map<string, string>()
// When the OPEN coin's history was last force-refreshed; re-pull on a 30s cadence as a safety net.
let lastSelectedHistoryRefresh = 0
const SELECTED_HISTORY_REFRESH_MS = 30_000
// When the very first refresh started — used to fail "Connecting…" after a timeout.
let firstRefreshAt: number | null = null
// Failover toast de-dup: last seq toasted per coin, plus a flag to seed pre-existing events silently at startup.
let toastIdSeq = 0
let failoverSeeded = false
const lastFailoverSeq: Record<string, number> = {}

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
  lightningMode: 'advanced',
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
  coinFiatMode: loadCoinFiatMode(),
  fiatCurrency: getFiatCurrency(),
  priceSources: null,
  priceApiConfigured: false,
  sessionLocked: null,
  networkSettings: {},
  contactsAddOpen: false,
  sendPayContactOpen: false,
  priceSectionOpen: false,
  toasts: [],
  dexIntegrationAllowed: null,
  dexStartOnStartup: false,
  dexConnected: false,
  dexLastSeen: null,
  dexHeartbeatTtlSeconds: 45,

  refresh: async () => {
    if (get().loading) return
    set({ loading: true })
    if (firstRefreshAt === null) firstRefreshAt = Date.now()
    if (!get().initialChecked) {
      try {
        const startup = await getStartup()
        set({ startup, connectError: false })
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
          void get().fetchCoinData(t, { background: true })
        }
      }
      // Safety net: re-pull the OPEN coin's history every 30s even if its balance signature looked unchanged.
      const nowMs = Date.now()
      if (selected && portfolio?.coins?.[selected] && nowMs - lastSelectedHistoryRefresh > SELECTED_HISTORY_REFRESH_MS) {
        lastSelectedHistoryRefresh = nowMs
        void get().fetchCoinData(selected, { background: true })
      }
      if (get().contacts.length === 0) void get().loadContacts()
      // Keep the lock chip + Security panel in sync with the backend's session state.
      void get().refreshSessionStatus()
      // Keep the optional DEX chip in sync with Settings and external changes.
      void get().refreshDexIntegration()
      // Reconcile the fiat toggle/currency from the backend once (it is authoritative).
      if (!get().priceSources) void get().loadPriceSources()

      const connected: Record<string, boolean> = { ...get().connected }
      await Promise.all(
        tickers.map(async (ticker) => {
          try {
            const info = await getInfo(ticker)
            connected[ticker] = info.connected ?? false
          } catch {
            connected[ticker] = false
          }
        }),
      )
      set({ connected })
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

  // Per-coin detail fetch with a race guard: stamp a reqId, fetch in parallel, and DISCARD
  // the result if the user switched coins or a newer request for this coin already fired.
  fetchCoinData: async (coin, opts) => {
    const reqId = Date.now()
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
    set({ coinColorOverrides: next })
  },

  // Clear a coin's override, reverting it to its COIN_COLORS default.
  resetCoinColor: (ticker) => {
    const next = { ...get().coinColorOverrides }
    delete next[ticker]
    persistCoinColorOverrides(next)
    set({ coinColorOverrides: next })
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

  refreshDexIntegration: async () => {
    try {
      const settings = await getDexIntegration()
      set({
        dexIntegrationAllowed: Boolean(settings.allow_local_dex),
        dexStartOnStartup: Boolean(settings.start_local_dex_on_startup),
        dexConnected: Boolean(settings.dex_connected),
        dexLastSeen: typeof settings.dex_last_seen === 'number' ? settings.dex_last_seen : null,
        dexHeartbeatTtlSeconds: Number(settings.heartbeat_ttl_seconds) || 45,
      })
    } catch {
      set({ dexIntegrationAllowed: false, dexStartOnStartup: false, dexConnected: false, dexLastSeen: null, dexHeartbeatTtlSeconds: 45 })
    }
  },
  setDexIntegrationAllowed: async (allowed) => {
    const settings = await setDexIntegration(allowed)
    set({
      dexIntegrationAllowed: Boolean(settings.allow_local_dex),
      dexStartOnStartup: Boolean(settings.start_local_dex_on_startup),
      dexConnected: Boolean(settings.dex_connected),
      dexLastSeen: typeof settings.dex_last_seen === 'number' ? settings.dex_last_seen : null,
      dexHeartbeatTtlSeconds: Number(settings.heartbeat_ttl_seconds) || 45,
    })
  },
  setDexStartOnStartup: async (enabled) => {
    const settings = await setDexIntegrationStartup(enabled)
    set({
      dexIntegrationAllowed: Boolean(settings.allow_local_dex),
      dexStartOnStartup: Boolean(settings.start_local_dex_on_startup),
      dexConnected: Boolean(settings.dex_connected),
      dexLastSeen: typeof settings.dex_last_seen === 'number' ? settings.dex_last_seen : null,
      dexHeartbeatTtlSeconds: Number(settings.heartbeat_ttl_seconds) || 45,
    })
  },

  pushToast: (text, kind = 'info') => {
    const id = ++toastIdSeq
    set((s) => ({ toasts: [...s.toasts, { id, text, kind }] }))
    window.setTimeout(() => get().dismissToast(id), 4500)
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))
