// Shared types for the Blakestream Wallet multiwallet dashboard.
// Keep in sync with the loopback backend API contract (http://127.0.0.1:57100).

export interface CoinMeta {
  ticker: string
  coin_name: string | null
  coin_type: number | null
  hrp: string | null
  rpc_port: number
}

export type Coins = Record<string, CoinMeta>

export interface PortfolioCoin {
  amount: string
  // The not-yet-confirmed part of `amount` (unconfirmed + unmatured), as a decimal string.
  // > 0 means some of the balance is still pending confirmation.
  pending?: string
  // is_synchronized for this coin: false = still syncing (balance not yet reliable → show
  // "syncing", not "pending"); true = up to date; null = unknown.
  synced?: boolean | null
  // Fiat value of this coin's balance in the chosen display currency, when the user has
  // enabled a price source and a price chain completes; null when unpriced.
  value_fiat?: string | null
  // Legacy, may be absent.
  price_btc?: string | null
  value_btc?: string | null
  value_usd?: string | null
}

export interface FailoverEvent {
  state: 'switching' | 'switched' | 'reverted'
  server: string | null
  seq: number
}

export interface Portfolio {
  coins: Record<string, PortfolioCoin>
  total: { value_btc?: string | null; value_usd?: string | null; value_fiat?: string | null }
  // The display fiat the values are in, and whether the user has the fiat view on.
  fiat?: string
  display_fiat?: boolean
  priced: string[]
  unpriced: string[]
  // Latest health-aware-failover event per coin (the UI toasts each new seq once).
  failover?: Record<string, FailoverEvent>
}

// ---- user-configurable price sources (Settings → Price & currency) ----
// Every source is a user-supplied named API link; nothing is shipped/hardwired.
export type PriceRole = 'coin_btc' | 'btc_fiat' | 'coin_fiat'
export type PriceKind = 'http_template'

// One price source as returned by the backend (raw API key NEVER included — only hasApiKey
// + a masked stub). The same shape (plus an optional apiKey/clearApiKey) is sent on add/update.
export interface PriceSource {
  id: string
  role: PriceRole
  kind: PriceKind
  enabled: boolean
  label: string
  urlTemplate: string
  jsonPath: string
  coinIds: Record<string, string>
  ids: string
  apiKeyHeader: string
  hasApiKey: boolean
  apiKeyMask: string
  ttl: number
}

export interface PriceSourcesState {
  enabled: boolean
  allow_private_hosts: boolean
  poll_seconds: number
  display: { fiatCurrency: string; displayFiat: boolean }
  sources: PriceSource[]
  tickers: string[]
}

export interface PriceTestResult {
  ok: boolean
  value: string | null
  role: PriceRole
  ticker: string
  fiat?: string
}

export interface Tx {
  coin: string
  txid?: string
  timestamp?: number | null
  date?: string
  value?: string
  balance?: string
  height?: number
  confirmations?: number | null
  label?: string
}

export interface CoinInfo {
  connected?: boolean
  server?: string
  blockchain_height?: number
  version?: string
}

// Address-book + per-coin detail shapes (new backend endpoints).
export interface AddressRow {
  address: string
  balance: string
  label: string
  used: boolean
  // True for change addresses (the wallet's internal chain), false for receiving.
  change?: boolean
}

export interface Contact {
  id: string
  coin: string
  address: string
  label: string
}

export interface LnInfo {
  enabled: boolean
  num_channels: number
  num_backups?: number
  node_id: string | null
  can_send_sat?: number
  can_receive_sat?: number
  // The coin's configured LN hub (node_id@host:port) + whether you have a channel to it.
  hub?: string | null
  hub_channel?: boolean
}

// A Lightning peer (list_peers) and a created payment request (list_requests).
export interface LnPeer {
  node_id?: string
  address?: string
  initialized?: boolean
  [k: string]: unknown
}
export interface LnRequest {
  request_id?: string
  amount_BTC?: string | number
  amount_sat?: number
  status?: string
  status_str?: string
  message?: string
  timestamp?: number
  lightning_invoice?: string
  [k: string]: unknown
}

export interface LnChannel {
  channel_id?: string
  short_channel_id?: string
  channel_point?: string
  state?: string
  remote_pubkey?: string
  local_balance?: number | string
  remote_balance?: number | string
  [k: string]: unknown
}

export interface LnTx {
  type?: string
  amount_msat?: number
  amount?: string
  timestamp?: number | null
  date?: string
  label?: string
  direction?: string
  [k: string]: unknown
}

export type TabKey =
  | 'history' | 'send' | 'receive' | 'addresses' | 'contacts' | 'lightning' | 'tools' | 'settings'

// ---- Tools: load transaction (the deserialized view of a raw tx) ----
export interface LoadedTxInput {
  prevout_hash: string
  prevout_n: number
  coinbase: boolean
  nsequence: number
  scriptSig?: string
  witness?: string[]
}
export interface LoadedTxOutput {
  scriptpubkey: string
  address: string | null
  value_sats: number
}
export interface LoadedTx {
  raw: string
  version: number | null
  locktime: number | null
  inputs: LoadedTxInput[]
  outputs: LoadedTxOutput[]
  total_out_sats: number
  txid: string | null
  size: number | null
  // true = fully signed (broadcastable), false = incomplete (PSBT), null = unknown
  complete: boolean | null
}

// ---- Tools: coin control (one wallet UTXO) ----
export interface Utxo {
  address: string | null
  amount: string | null   // coin-unit amount string (already formatted by the daemon)
  txid: string | null
  vout: number | null
  height: number | null
  confirmations?: number | null
  coinbase: boolean
  // true when this UTXO's address is frozen (excluded from spends)
  frozen: boolean
}

// Per-coin network/server settings (GET/POST /settings/<COIN>). `server` is the
// configured target ("auto" | "offline" | "host:port:s"); the live_* / connected /
// blockchain_height fields reflect the daemon's current connection.
export interface NetworkSettings {
  server: string
  auto_connect: boolean
  connected: boolean
  live_server: string | null
  blockchain_height: number | null
  known_servers: string[]
  // Transaction fee policy: 'network' = server estimate (falls back to the fixed rate when
  // the server can't estimate); 'fixed' = always fee_sat_per_byte. Rate is in sat/byte.
  fee_mode: 'network' | 'fixed'
  fee_sat_per_byte: number
  // SOCKS5 proxy (Tor/privacy). has_password only signals whether one is stored — never the secret.
  proxy?: { enabled: boolean; host: string; port: number | string; user: string; has_password: boolean }
}

// Read-only wallet identity for Settings → Wallet information (per coin).
export interface WalletInfo {
  mpk: string
  coin_type: number
  derivation_path: string
  script_type: string
  fingerprint: string | null
}

// Per-coin brand colors. Used for donut slices, row dots and tags. Tickers not present
// here fall back to the accent color. Matched to the Blakestream pool's per-ticker chip
// colors (Blakestream-MPOS-25.2-GO, EditAccountPage.vue) so the wallet and pool stay consistent.
export const COIN_COLORS: Record<string, string> = {
  BLC: '#ff9800',  // orange
  BBTC: '#ea4335', // red
  ELT: '#34a853',  // green
  LIT: '#fbbc04',  // amber
  PHO: '#4285f4',  // blue
  UMO: '#7b61ff',  // purple
}

// Effective brand color for a coin: a user override (from the store) wins, then the
// COIN_COLORS default, then the neutral cyan accent. Consumers (rail, donut, --coin vars,
// launch rings) read through this so a Settings color pick retints the whole app live.
export function resolveCoinColor(
  overrides: Record<string, string>,
  ticker: string,
): string {
  return overrides[ticker] || COIN_COLORS[ticker] || '#4fc3f7'
}

export type Timeframe = '24H' | '1W' | '1M' | '6M' | '1Y'

export interface SetupStatus {
  provisioned: boolean
  vault_exists: boolean
}

export interface DexIntegrationSettings {
  allow_local_dex: boolean
  start_local_dex_on_startup: boolean
  dex_connected: boolean
  dex_last_seen: number | null
  heartbeat_ttl_seconds: number
}
