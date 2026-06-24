// Typed fetch wrappers for the loopback backend HTTP/JSON API.
// The renderer talks to the localhost server directly (no Electron IPC bridge).

import type {
  Coins, Portfolio, Tx, CoinInfo, SetupStatus,
  AddressRow, Contact, LnInfo, LnChannel, LnTx, LnPeer, LnRequest, NetworkSettings, WalletInfo,
  PriceSource, PriceSourcesState, PriceTestResult, LoadedTx, Utxo, DexIntegrationSettings,
} from './types'

export const BASE = 'http://127.0.0.1:57100'

// The Electron preload exposes an IPC bridge; the main process holds the API token
// and attaches it (the renderer never sees the token). In dev/browser the bridge is
// absent and we fall back to a direct fetch (backend runs without a token there).
interface ApiResult {
  ok: boolean
  status: number
  data: unknown
}
declare global {
  interface Window {
    electrum?: {
      api?: {
        get: (path: string) => Promise<ApiResult>
        post: (path: string, body: unknown) => Promise<ApiResult>
      }
    }
  }
}

function bridge() {
  return typeof window !== 'undefined' ? window.electrum?.api : undefined
}

export interface Health {
  ok: boolean
  coins: string[]
}

export interface AddressResponse {
  address: string | null
}

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const b = bridge()
  if (b) {
    const r = await b.get(path)
    if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`)
    return r.data as T
  }
  const res = await fetch(`${BASE}${path}`, { headers: { Accept: 'application/json' }, signal })
  if (!res.ok) {
    throw new Error(`${path} -> HTTP ${res.status}`)
  }
  return (await res.json()) as T
}

export function getHealth(signal?: AbortSignal): Promise<Health> {
  return getJSON<Health>('/health', signal)
}

export function getCoins(signal?: AbortSignal): Promise<Coins> {
  return getJSON<Coins>('/coins', signal)
}

export function getPortfolio(signal?: AbortSignal): Promise<Portfolio> {
  return getJSON<Portfolio>('/portfolio', signal)
}

export function getAddress(coin: string, signal?: AbortSignal): Promise<AddressResponse> {
  return getJSON<AddressResponse>(`/address/${encodeURIComponent(coin)}`, signal)
}

export function getLnHistoryAll(signal?: AbortSignal): Promise<{ transactions: LnTx[] }> {
  return getJSON<{ transactions: LnTx[] }>('/lightning-history', signal)
}
export function getHistory(signal?: AbortSignal): Promise<{ transactions: Tx[] }> {
  return getJSON<{ transactions: Tx[] }>('/history', signal)
}

export function getInfo(coin: string, signal?: AbortSignal): Promise<CoinInfo> {
  return getJSON<CoinInfo>(`/getinfo/${encodeURIComponent(coin)}`, signal)
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const b = bridge()
  if (b) {
    const r = await b.post(path, body)
    const data = (r.data || {}) as Record<string, unknown>
    if (!r.ok) throw new Error((data.error as string) || `${path} -> HTTP ${r.status}`)
    return data as T
  }
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>
  if (!res.ok) throw new Error((data.error as string) || `${path} -> HTTP ${res.status}`)
  return data as T
}

export function getSetupStatus(signal?: AbortSignal): Promise<SetupStatus> {
  return getJSON<SetupStatus>('/setup/status', signal)
}

export function getDexIntegration(signal?: AbortSignal): Promise<DexIntegrationSettings> {
  return getJSON<DexIntegrationSettings>('/dex/integration', signal)
}

export function setDexIntegration(allowLocalDex: boolean): Promise<DexIntegrationSettings> {
  return postJSON<DexIntegrationSettings>('/dex/integration', { allow_local_dex: allowLocalDex })
}

export function setDexIntegrationStartup(startOnStartup: boolean): Promise<DexIntegrationSettings> {
  return postJSON<DexIntegrationSettings>('/dex/integration', { start_local_dex_on_startup: startOnStartup })
}

// Live per-coin bring-up progress for the Connecting screen. The backend serves this
// immediately (before the daemons finish), so the UI can light up each coin as it's ready.
export interface Startup {
  coins: Record<string, 'pending' | 'starting' | 'ready' | 'failed'>
  ready: number
  total: number
  all_ready: boolean
}

export function getStartup(signal?: AbortSignal): Promise<Startup> {
  return getJSON<Startup>('/startup', signal)
}

// Live unlock progress, polled while the blocking /setup/unlock POST is in flight. The coins
// are provisioned in PARALLEL, so this is a per-coin map: 'connecting' (in flight) -> 'done'
// (landed) / 'failed'. The launch screen flashes all six and settles each to solid as it lands.
export interface UnlockProgress {
  coins: Record<string, 'connecting' | 'done' | 'failed'>
  // Per-coin sub-status for the connecting screen: which server + finer phase.
  detail?: Record<string, { server: string | null; phase: string }>
  total: number
}

export function getUnlockProgress(): Promise<UnlockProgress> {
  return getJSON<UnlockProgress>('/setup/progress')
}

// First-run: create a fresh seed (returns the mnemonic ONCE for backup).
export function createWallet(password: string): Promise<{ ok: boolean; mnemonic: string }> {
  return postJSON('/setup/create', { password })
}

// First-run: restore from an existing BIP39 mnemonic.
export function restoreWallet(password: string, mnemonic: string): Promise<{ ok: boolean }> {
  return postJSON('/setup/restore', { password, mnemonic })
}

// Relaunch: unlock the existing vault (decrypts the seed + provisions the daemons).
export function unlockWallet(password: string): Promise<{ ok: boolean }> {
  return postJSON('/setup/unlock', { password })
}

// Receive: a fresh unused address for the coin (+ whether sending is currently possible).
export function getReceiveAddress(
  coin: string,
): Promise<{ address: string | null; can_send: boolean }> {
  return getJSON(`/receive/${encodeURIComponent(coin)}`)
}

// Receive: mint a brand-new receiving address (advances past the current unused one) for
// users who want a distinct address per payment. Resolves with the new address (or null).
export function newReceiveAddress(coin: string): Promise<{ address: string | null }> {
  return postJSON(`/receive/${encodeURIComponent(coin)}/new`, {})
}

// Send: build + broadcast a payment. Resolves with the txid, or rejects with a
// friendly message (insufficient funds / invalid address / no server).
export function sendCoin(coin: string, address: string, amount: string): Promise<{ txid: string }> {
  return postJSON(`/send/${encodeURIComponent(coin)}`, { address, amount })
}

// Two-step send so the user sees the fee before confirming:
//  preview -> build the tx (NOT broadcast) and return amount + miner fee + total;
//  confirm -> sign + broadcast the SAME built tx (so the previewed fee is the fee paid).
export interface SendPreview {
  ticker: string
  address: string
  amount: string // coin units, e.g. "0.50000000"
  amount_sat: number
  fee: string // coin units
  fee_sat: number
  total: string // amount + fee, coin units
  total_sat: number
  high_fee: boolean // fee is an unusually large share of the amount
}

export function previewSend(
  coin: string, address: string, amount: string, feeRate?: string, fromCoins?: string[],
): Promise<SendPreview> {
  const body: Record<string, unknown> = { address, amount }
  if (feeRate) body.fee_rate = feeRate
  if (fromCoins && fromCoins.length) body.from_coins = fromCoins
  return postJSON(`/send/${encodeURIComponent(coin)}/preview`, body)
}

export function confirmSend(coin: string): Promise<{ txid: string }> {
  return postJSON(`/send/${encodeURIComponent(coin)}/confirm`, {})
}

// ---- per-coin detail tabs ----

export function getCoinHistory(coin: string): Promise<{ transactions: Tx[] }> {
  return getJSON(`/history/${encodeURIComponent(coin)}`)
}

export function getAddresses(
  coin: string, filter: 'receiving' | 'change' | 'all' = 'receiving',
): Promise<{ addresses: AddressRow[] }> {
  return getJSON(`/addresses/${encodeURIComponent(coin)}?filter=${filter}`)
}

export function getContacts(coin?: string): Promise<{ contacts: Contact[] }> {
  return getJSON(coin ? `/contacts/${encodeURIComponent(coin)}` : '/contacts')
}

export function addContact(coin: string, address: string, label: string): Promise<{ contact: Contact }> {
  return postJSON('/contacts', { coin, address, label })
}

export function removeContact(id: string): Promise<{ ok: boolean }> {
  return postJSON('/contacts/delete', { id })
}

// Set (or clear, when label is empty) the Electrum label for an address or txid. The
// key is an address (Addresses tab) or a txid (History tab / a sent payment description).
export function setLabel(coin: string, key: string, label: string): Promise<{ ok: boolean }> {
  return postJSON(`/label/${encodeURIComponent(coin)}`, { key, label })
}

// ---- lightning ----

export function getLnStatus(coin: string): Promise<LnInfo> {
  return getJSON(`/lightning/${encodeURIComponent(coin)}/status`)
}

export function getLnChannels(coin: string): Promise<{ channels: LnChannel[] }> {
  return getJSON(`/lightning/${encodeURIComponent(coin)}/channels`)
}

export function getLnHistory(coin: string): Promise<{ transactions: LnTx[] }> {
  return getJSON(`/lightning/${encodeURIComponent(coin)}/history`)
}

export function enableLn(coin: string): Promise<{ enabled: boolean }> {
  return postJSON(`/lightning/${encodeURIComponent(coin)}/enable`, {})
}

export function openChannel(coin: string, connectionString: string, amount: string, pushAmount = ''): Promise<unknown> {
  const body: Record<string, string> = { connection_string: connectionString, amount }
  if (pushAmount.trim()) body.push_amount = pushAmount.trim()
  return postJSON(`/lightning/${encodeURIComponent(coin)}/channels/open`, body)
}

export function closeChannel(coin: string, channelPoint: string, force = false): Promise<unknown> {
  return postJSON(`/lightning/${encodeURIComponent(coin)}/channels/close`, {
    channel_point: channelPoint, force,
  })
}

export function lnPay(coin: string, invoice: string): Promise<unknown> {
  return postJSON(`/lightning/${encodeURIComponent(coin)}/pay`, { invoice })
}

// A decoded BOLT11 invoice (shape varies by daemon; the fields the Send tab reads are optional).
export interface DecodedInvoice {
  amount_msat?: number | null
  amount_sat?: number | null
  amount_BTC?: string | number | null
  description?: string
  message?: string
  [k: string]: unknown
}
export function decodeInvoice(coin: string, invoice: string): Promise<DecodedInvoice> {
  return getJSON(`/lightning/${encodeURIComponent(coin)}/decode?invoice=${encodeURIComponent(invoice)}`)
}

export function lnInvoice(coin: string, amount: string, memo: string, expiry: string): Promise<unknown> {
  return postJSON(`/lightning/${encodeURIComponent(coin)}/invoice`, { amount, memo, expiry })
}

// ---- lightning: backups / peers / recovery / requests (direct-channel features) ----

export function getLnPeers(coin: string): Promise<{ peers: LnPeer[] }> {
  return getJSON(`/lightning/${encodeURIComponent(coin)}/peers`)
}
export function getLnRequests(coin: string): Promise<{ requests: LnRequest[] }> {
  return getJSON(`/lightning/${encodeURIComponent(coin)}/requests`)
}
// Encrypted static channel backup (recover funds if state is lost). Per-coin.
export function exportChannelBackup(coin: string, channelPoint: string): Promise<{ backup: string }> {
  return postJSON(`/lightning/${encodeURIComponent(coin)}/channels/export-backup`, { channel_point: channelPoint })
}
export function importChannelBackup(coin: string, backup: string): Promise<{ result: unknown }> {
  return postJSON(`/lightning/${encodeURIComponent(coin)}/channels/import-backup`, { backup })
}
// Ask the remote peer to force-close (recover from a backup; optional connection string).
export function requestForceClose(coin: string, channelPoint: string, connectionString = ''): Promise<unknown> {
  return postJSON(`/lightning/${encodeURIComponent(coin)}/channels/request-close`, {
    channel_point: channelPoint, connection_string: connectionString,
  })
}
export function addLnPeer(coin: string, connectionString: string): Promise<unknown> {
  return postJSON(`/lightning/${encodeURIComponent(coin)}/peers/add`, { connection_string: connectionString })
}
export function deleteLnRequest(coin: string, requestId: string): Promise<{ result: unknown }> {
  return postJSON(`/lightning/${encodeURIComponent(coin)}/requests/delete`, { request_id: requestId })
}

// ---- network / server settings (per coin) ----

export function getNetworkSettings(coin: string): Promise<NetworkSettings> {
  return getJSON(`/settings/${encodeURIComponent(coin)}`)
}

// Apply a server: "auto" | "offline" | "host:port:s". The daemon restarts onto the
// new server (~5-10s) and the same NetworkSettings shape is returned.
export function setServer(coin: string, server: string): Promise<NetworkSettings> {
  return postJSON(`/settings/${encodeURIComponent(coin)}`, { server })
}

// Apply the transaction fee policy for a coin (no daemon restart). 'network' uses the server
// estimate (falling back to the fixed rate when it can't estimate); 'fixed' always uses the
// rate. Returns the updated NetworkSettings (which carries fee_mode + fee_sat_per_byte).
export function setFeePolicy(
  coin: string, feeMode: 'network' | 'fixed', satPerByte?: number,
): Promise<NetworkSettings> {
  const body: Record<string, unknown> = { fee_mode: feeMode }
  if (satPerByte != null) body.fee_sat_per_byte = satPerByte
  return postJSON(`/settings/${encodeURIComponent(coin)}`, body)
}

// ---- price sources + display currency (global; Settings → Price & currency) ----
// The whole config is global (applies to all coins). The backend never returns raw API
// keys — only hasApiKey + a masked stub. All mutating ops return the new masked state.

// A source as sent on add/update: the editable PriceSource fields plus an optional plaintext
// apiKey (empty = keep existing) and clearApiKey (drop the stored key).
export type PriceSourceSpec =
  Partial<Omit<PriceSource, 'id' | 'hasApiKey' | 'apiKeyMask'>>
  & { id?: string; apiKey?: string; clearApiKey?: boolean }

export function getPriceSources(signal?: AbortSignal): Promise<PriceSourcesState> {
  return getJSON<PriceSourcesState>('/price-sources', signal)
}

export function getFxCurrencies(signal?: AbortSignal): Promise<{ currencies: string[] }> {
  return getJSON<{ currencies: string[] }>('/fx/currencies', signal)
}

function priceOp<T = PriceSourcesState>(op: string, extra: Record<string, unknown> = {}): Promise<T> {
  return postJSON<T>('/price-sources', { op, ...extra })
}

export function setPriceEnabled(enabled: boolean): Promise<PriceSourcesState> {
  return priceOp('set_enabled', { enabled })
}
export function setAllowPrivateHosts(allow: boolean): Promise<PriceSourcesState> {
  return priceOp('set_allow_private', { allow })
}
export function setPollSeconds(seconds: number): Promise<PriceSourcesState> {
  return priceOp('set_poll', { seconds })
}
export function setPriceDisplay(fiatCurrency?: string, displayFiat?: boolean): Promise<PriceSourcesState> {
  const extra: Record<string, unknown> = {}
  if (fiatCurrency !== undefined) extra.fiatCurrency = fiatCurrency
  if (displayFiat !== undefined) extra.displayFiat = displayFiat
  return priceOp('set_display', extra)
}
export function addPriceSource(source: PriceSourceSpec): Promise<PriceSourcesState> {
  return priceOp('add', { source })
}
export function updatePriceSource(id: string, source: PriceSourceSpec): Promise<PriceSourcesState> {
  return priceOp('update', { id, source })
}
export function removePriceSource(id: string): Promise<PriceSourcesState> {
  return priceOp('remove', { id })
}
export function setPriceSourceEnabled(id: string, enabled: boolean): Promise<PriceSourcesState> {
  return priceOp('set_source_enabled', { id, enabled })
}
export function reorderPriceSources(order: string[]): Promise<PriceSourcesState> {
  return priceOp('reorder', { order })
}
export function testPriceSource(
  source: PriceSourceSpec, ticker?: string, fiat?: string,
): Promise<PriceTestResult> {
  return priceOp<PriceTestResult>('test', { source, ticker, fiat })
}

// ---- Tools (per coin): load transaction ----

// Deserialize a raw transaction (hex or PSBT base64) for inspection — offline, no broadcast.
export function loadTransaction(coin: string, tx: string): Promise<LoadedTx> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/load-tx`, { tx })
}

// Fetch a transaction from the network by txid, then deserialize it (online only).
export function fetchTransaction(coin: string, txid: string): Promise<LoadedTx> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/fetch-tx`, { txid })
}

// Broadcast a signed raw transaction to the network; resolves with the txid (online only).
export function broadcastTransaction(coin: string, tx: string): Promise<{ txid: string }> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/broadcast`, { tx })
}

// Sign a message with one of the wallet's own addresses; resolves with the base64 signature.
export function signMessage(coin: string, address: string, message: string): Promise<{ signature: string }> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/sign-message`, { address, message })
}

// Verify a signature over a message for any address; resolves with whether it's valid.
export function verifyMessage(
  coin: string, address: string, signature: string, message: string,
): Promise<{ valid: boolean }> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/verify-message`, { address, signature, message })
}

// Coin control: the wallet's UTXOs (with frozen flag).
export function getUtxos(coin: string): Promise<{ utxos: Utxo[] }> {
  return getJSON(`/tools/${encodeURIComponent(coin)}/utxos`)
}

// Coin control: freeze/unfreeze an address (frozen coins are excluded from spends).
export function setAddressFrozen(coin: string, address: string, frozen: boolean): Promise<{ ok: boolean }> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/freeze-address`, { address, frozen })
}

// Encrypt a message to a recipient (a hex public key, or one of your addresses). Offline.
export function encryptMessage(coin: string, key: string, message: string): Promise<{ encrypted: string }> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/encrypt-message`, { key, message })
}

// Decrypt a message addressed to one of your keys (key = your public key or address).
export function decryptMessage(coin: string, key: string, encrypted: string): Promise<{ message: string }> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/decrypt-message`, { key, encrypted })
}

// Advanced tx — each BUILDS (and signs) a tx and returns it as a LoadedTx for review; broadcast is a
// separate explicit step (broadcastTransaction on the returned `raw`).

// Build a multi-output transaction. outputs = [[address, amount], …]; amounts in coin units ("!" = max).
// fromCoins (optional) restricts inputs to the coins selected in Send's coin control.
export function payToMany(coin: string, outputs: [string, string][], feerate?: string, fromCoins?: string[]): Promise<LoadedTx> {
  const body: Record<string, unknown> = { outputs }
  if (feerate) body.feerate = feerate
  if (fromCoins && fromCoins.length) body.from_coins = fromCoins
  return postJSON(`/tools/${encodeURIComponent(coin)}/pay-to-many`, body)
}

// Sweep all funds controlled by a private key to a destination address (online).
export function sweepKey(coin: string, privkey: string, destination: string, feerate?: string): Promise<LoadedTx> {
  const body: Record<string, unknown> = { privkey, destination }
  if (feerate) body.feerate = feerate
  return postJSON(`/tools/${encodeURIComponent(coin)}/sweep`, body)
}

// Build a higher-fee RBF replacement of an unconfirmed wallet tx (txid or raw hex) at new_feerate (online).
export function bumpFee(coin: string, tx: string, newFeerate: string): Promise<LoadedTx> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/bump-fee`, { tx, new_feerate: newFeerate })
}

// ---- Tools (per coin): keys & seed ----

// Master public key (xpub) — PUBLIC, safe to share (watch-only import). No password.
export function getMasterPubkey(coin: string): Promise<{ mpk: string }> {
  return getJSON(`/tools/${encodeURIComponent(coin)}/master-pubkey`)
}

// Read-only wallet identity (xpub + derivation + type + fingerprint). PUBLIC, no password.
export function getWalletInfo(coin: string): Promise<WalletInfo> {
  return getJSON(`/tools/${encodeURIComponent(coin)}/wallet-info`)
}

// ---- session lock / unlock + change password ----
export function getSessionStatus(): Promise<{ locked: boolean; vault_exists: boolean }> {
  return getJSON('/session/status')
}
export function lockSession(): Promise<{ ok: boolean; locked: boolean }> {
  return postJSON('/session/lock', {})
}
export function unlockSession(password: string): Promise<{ ok: boolean; locked: boolean }> {
  return postJSON('/session/unlock', { password })
}
export function changePassword(currentPassword: string, newPassword: string): Promise<{ ok: boolean }> {
  return postJSON('/wallet/change-password', { current_password: currentPassword, new_password: newPassword })
}

// SOCKS5 proxy for a coin's daemon (restarts it). A blank password keeps the stored one.
export function setProxy(
  coin: string,
  cfg: { enable: boolean; host?: string; port?: number; user?: string; password?: string },
): Promise<NetworkSettings> {
  return postJSON(`/settings/${encodeURIComponent(coin)}/proxy`, cfg)
}

// Reveal the master recovery phrase (the seed for ALL coins). SENSITIVE: the backend re-verifies
// `password` against the vault before returning.
export function revealSeed(coin: string, password: string): Promise<{ seed: string }> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/reveal-seed`, { password })
}

// Reveal the private key (WIF) of one of your addresses. SENSITIVE: backend re-verifies `password`.
export function exportPrivkey(coin: string, password: string, address: string): Promise<{ privkey: string }> {
  return postJSON(`/tools/${encodeURIComponent(coin)}/export-privkey`, { password, address })
}
