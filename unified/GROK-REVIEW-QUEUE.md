# Grok review queue — RESUME HERE

External reviewer **Grok** (via the local connector on `127.0.0.1:7898`) hit its usage
limit on 2026-06-02, so the back half of the build was finished solo. The limit later
cleared and the full section-by-section review was completed (see below).

## Section-by-section review COMPLETE (2026-06-02) — overall verdict: "green-light to ship"
All six `unified/` sections reviewed with Grok one at a time (code shown, opinions/
alternatives requested, fixes applied + re-verified on `.221`). Applied:
- **§1 orchestrator.py** — `flock` guard around `start()` (closes the restart TOCTOU);
  non-blocking per-coin exponential back-off in `ensure_running()` (no tight restart loop);
  crash-restart reloads only wallets loaded *this session* (`self._loaded`), so a locked
  relaunch stays locked. (wait_ready timing + leave-locked-until-unlock confirmed fine.)
- **§2 launcher.py** — `--create/--restore` now refuses to clobber an existing vault without
  `--force`; sealing a new seed requires a real password (no `None` into Argon2 headless).
  (SIGTERM→KeyboardInterrupt shutdown kept — verified; our `serve()` is stdlib, won't swallow it.)
- **§3 vault.py** — durable write (fsync file → `os.replace` → fsync dir, correct order);
  AAD bound to the per-vault salt (tamper-evident). Argon2id params + random nonce kept.
- **§4 prices.py** — `CachedProvider` TTL wrapper (future adapters need no own rate-limit);
  bad/`None` balances coerce to `Decimal(0)` so one coin can't blank the dashboard.
- **§5 api.py** — `/setup/create|restore` return **409** if a vault exists (a double-submit
  can't mint a 2nd seed); 64 KiB POST body cap (413); **optional per-launch bearer token**
  gating all routes (no-op when unset). Argon2 cost deemed sufficient anti-brute-force.
- **§6 desktop** — race analysis confirmed safe; added a **Connecting** screen (30s→error)
  to kill the dashboard-flash; token wired via **preload + `additionalArguments`** (sandbox-safe,
  not on the URL) → `window.electrium.apiToken` → `Authorization: Bearer` on every fetch.
- **Verified on `.221`:** 28/28 unit tests pass; relaunch-unlock still green; token 401/200,
  double-create 409, restore-on-existing 409, body 413 all confirmed; Connecting + Unlock
  screenshots captured. Biggest remaining risk Grok flagged: the migration path for existing
  separate-seed field wallets into the unified seed (get the coin_type/derivation sweep right).

The marker/queue below is kept for history.

External reviewer **Grok** (via the local connector on `127.0.0.1:7898`) **hit its usage
limit on 2026-06-02.** We are continuing the build solo and will return to have Grok
review everything produced after the marker below.

## Reviewed by Grok (complete, folded in)
- `blakestream-electrum.md` §1–§12 — section-by-section; corrections/code applied.
- **P0** coin_type contract + **P1** `unified/provisioning.py` (+ tests) — Grok verdict
  "production-ready, no blockers"; polish applied (`provision_for_daemon`, edge guards,
  wordlist cache).
- Grok chose **P2 provisioning approach A** (per-coin account `zprv` restore).
- Grok acknowledged the build-server validation (16/16 tests on `.221`, all 6 wheels,
  coin_type baked in) and the end-to-end zprv-restore address-match proof.
- Grok's 4 pre-P2 checks: **all DONE.** #1 (zprv restore → address match) ✓, #4 (argon2 env) ✓,
  **#2 (port/datadir isolation)** ✓ (6/6 daemons bound distinct 127.0.0.1:57101–57106),
  **#3 (`getinfo` readiness)** ✓ (online daemons ready on poll 1; offline `getinfo` errors on the
  None network — readiness uses online `getinfo`). Also learned: the generated variants have their
  bundled server lists cleared, so a `server` must be configured per coin before the online daemon
  can start.

## ┌────────────────────────────────────────────────────────────┐
## │  <<<  GROK USAGE LIMIT REACHED HERE — 2026-06-02  >>>        │
## └────────────────────────────────────────────────────────────┘

## PENDING Grok review (when the limit resets)
- [ ] **P2 orchestrator** `unified/orchestrator.py` — daemon spawn/restore/supervise/aggregate.
      Built and validated on `.221`: brings up all six daemons from ONE mnemonic, restores each from
      its per-coin account `zprv` fed via **stdin (not argv)**, aggregates balances, clean shutdown.
      `unified/p2_smoke.py` passes **6/6** (every daemon's first address == known-answer vector).
      Supervision added: `daemon_alive`/`ensure_running`/`supervise_once` (RPC-liveness check,
      restart-on-crash validated on `.221`). Review focus: back-off policy, error handling, stdin-restore path,
      per-coin `server` config source, and whether to reimplement this in Electron `main` (Node) or
      keep the Python orchestrator and have the Electron UI call it over IPC.
- [ ] `unified/p2_smoke.py` (integration smoke) — sanity of the KAVs + flow.
- [ ] `unified/launcher.py` — two products on one engine: `single <COIN>` (own seed) vs `multi`
      (one shared seed → six). Both validated on `.221`; seed read from stdin (`--restore`) or
      generated (`--create`), never argv. Review the mode split + seed handling.
- [ ] **P3 vault** `unified/vault.py` (+ `tests/test_vault.py`) — Argon2id (raw 32B via
      `hash_secret_raw`, type=ID; 64 MiB/t=3/p=4) → AES-256-GCM; stores only salt/nonce/ciphertext/kdf;
      atomic write, 0600; `change_password` re-encrypts. Wired into `launcher.py --vault` (seal a new/
      restored seed; unlock an existing one). 6 unit tests + e2e seal→unlock→bring-up validated on
      `.221`, wrong-password rejected, no plaintext at rest. Review: KDF params, AAD, wipe limits.
- [ ] **P4 price-oracle framework** `unified/prices.py` (+ `tests/test_prices.py`) — pluggable
      provider chain + `ManualPriceProvider`; per-coin BTC price × one BTC/USD rate, both optional;
      `value_portfolio` splits priced/unpriced and degrades to None (these coins have NO market/feed
      yet — confirmed by owner). Wired into `orchestrator.portfolio()`. 6 unit tests + integration
      smoke on `.221`. Review: provider interface (for a future exchange adapter), Decimal handling.
- [ ] **P5.1 API** `unified/api.py` — loopback HTTP/JSON over the orchestrator (/health, /coins,
      /portfolio, /getinfo/<c>, /address/<c>); stdlib http.server; Decimals serialized as strings.
      Validated on `.221`. Review: add a per-launch bearer token; it's loopback-only today.
- [ ] **P5.2 frontend** `unified/desktop/` — Electron + React 18 + Vite + Recharts + Zustand
      dashboard over the loopback API (CoinSidebar / BalanceHeader / AssetsTable+Sparkline /
      PortfolioDonut / TxFeed; dark theme, per-coin colors; null-safe "no price source yet"
      placeholders). Generated by a 5-agent workflow against the locked contract; **tsc + vite build
      pass clean on `.221`** (851 modules). Electron entry hardened (contextIsolation/nodeIntegration/
      sandbox). Review: component correctness, donut/allocation logic, polling, bundle size (recharts).
- [ ] `launcher --serve` (backend entrypoint) — brings up the daemons + runs `unified.api` on
      127.0.0.1:57100 in one command; validated on `.221`. Review the serve/shutdown path.
- [x] **Functional run verified** under xvfb on `.221`: dashboard renders against the live 6-coin
      backend (screenshot), graceful "no price/allocation yet" placeholders confirmed.
- [x] **P6a** electron-builder AppImage (frontend shell, ~110 MB) builds on `.221` (`npm run dist`).
- [x] **P6b.1/2** PyInstaller per-coin daemon binaries (`build-backend.sh`) — all 6 build (~90 MB each)
      and the orchestrator drives them in `binaries` mode; 6/6 addresses match KAVs on `.221`.
- [x] **P6b.3a/b** `launcher --backend-dir` + PyInstaller the supervisor → `electrium-backend` (91 MB).
      The **fully self-contained backend** (supervisor binary + 6 daemon binaries, no system Python)
      runs `--serve` and serves the API on `.221`. `provisioning.load_coins` is PyInstaller-aware
      (`sys._MEIPASS`). Review: bundle size (~540 MB of 6 daemons — dedup/onefile later), frozen paths.
- [x] **P6b.3c DONE** — Electron main spawns the bundled `electrium-backend` (from
      `process.resourcesPath/backend`) on launch; electron-builder `extraResources` bundles supervisor
      + 6 daemons. Single **`Blakestream Electrum-0.25.2.AppImage` (~225 MB)** verified on `.221` under
      xvfb: it spawns its own backend (1 supervisor + 6 daemons, **no system Python/Node**), `/health`
      returns all 6 coins, fresh launch → unprovisioned (onboarding). `launcher --serve` is headless-safe
      (no getpass block) and stops daemons on SIGTERM.
- [x] **Seed-onboarding** — backend `/setup/status|create|restore` (POST), online provisioning,
      graceful `portfolio()`/`history()` for unprovisioned coins; UI `Setup` screen (Create new /
      Restore, password, seed-shown-once for create; spellcheck off on the phrase input). Gated in
      `App` via store `onboarded`/`initialChecked`. Verified on `.221` (curl flow + xvfb screenshot of
      the onboarding card). Review: password POST over loopback (add token), KDF, seed-display UX.
- [x] **Relaunch unlock DONE** — backend `/setup/unlock` (decrypt vault → `provision_all`); orchestrator
      tracks loaded wallets per-session (`all_provisioned` via `self._loaded`, not a file check), `bring_up`
      only auto-loads when a seed is supplied (a relaunch stays locked), and `start()` clears a stale
      `daemon` lockfile when no daemon answers RPC (crash/SIGKILL recovery — the relaunch fix). UI `Setup`
      adds an Unlock card when `vault_exists && !provisioned`; store gains `unlockWallet`. Verified on `.221`:
      backend curl flow (fresh restore → clean stop → relaunch shows `provisioned:false` → wrong pw 401 →
      right pw ok → `provisioned:true` → live BLC address) and an xvfb screenshot of the Unlock screen.
      Review: password POST over loopback (token), and whether relaunch should auto-load read-only watching.
- [ ] **Remaining polish (TODO)** — app icon; per-coin ElectrumX endpoints for live sync (only BLC has a
      public server today); bundle-size dedup; encrypt per-coin wallet files at rest (vault gates the
      session, but the daemon wallet files are currently plaintext on disk).

## Migration (legacy separate-seed → unified seed) — BUILT + Grok-reviewed (2026-06-02)
Resolves `blakestream-electrum.md` §12 open-question #2 in favour of **option (a), a sweep**
(not import-into-HD, which would put non-derivable keys behind the master seed and break
seed-only restore). Implemented in `unified/migration.py` (+ `tests/test_migration.py`).

- **Discovery (net-free, exhaustive).** `discover()` enumerates every scheme a field wallet
  could hold — Electrum-native (`m/` p2pkh, `m/0'` p2wpkh), BIP39 purpose 44/49/84 at the
  **inherited coin_type 10** (where all six legacy coins' funds actually are) AND at the
  coin's own coin_type, plus raw WIF / 64-hex. Gap-limit (20) scan per scheme×change. Old
  non-BIP32 `mpk` seeds, multisig and SLIP39 are NOT scanned and are **loudly warned** about
  (never a silent "complete").
- **Sweep = the dry-run.** `Migrator.plan()` runs the online coin daemon's `sweep` (no
  broadcast) and parses the returned signed tx, so the previewed amount IS exactly what
  `execute()` broadcasts. All Blakestream tx crypto stays inside the daemon (single-SHA256
  txid / double-SHA256 BIP143 / blake256); `migration.py` never touches raw tx bytes.
- **Guards / "done".** Cross-chain refused (dest HRP must match the coin). A server outage is a
  hard `plan.error` ("could not reach the server … NOT migrated"), never a silent "no funds"
  (a bug found+fixed during break-testing). `verify()` declares done only when source
  addresses read empty AND the tx reaches N confirmations (default 1).
- **Proven on `.221`:** derivation is byte-identical to a REAL daemon restored at each scheme's
  path (`derive_at` == daemon `listaddresses[0]`: bip84→`blc1q…800`, bip44→`Bca2…`); the
  daemon's own `deserialize_privkey` (the parser `sweep` uses) ingests our WIFs to the exact
  key/address; **15/15** unit + edge/break tests pass (gap-limit, raw-key, garbage, cross-chain,
  empty, sweep-None idempotent, dust-raise, >imax warn, server-down hard-error, verify gate).
- **PRE-v1 GATE (not yet done):** a live on-chain sweep is NOT exercised — only BLC has prod
  ElectrumX (mainnet; no real-fund test) and no regtest indexer is currently deployed. Before
  v1, run a **regtest dress-rehearsal** on `.221` (`Blakestream-electrum/.../tests/regtest/
  regtest.sh` pattern + ElectrumX regtest): fund a legacy-derived address → `plan`→`execute`→
  `verify` → assert the unified wallet balance moved. Components are individually proven; this
  gates the integration.
- **UI copy to add:** "This is a one-way sweep. The old wallet will be emptied and can be
  deleted after the new balance confirms."

## How to resume the Grok review later
1. `curl -s http://127.0.0.1:7898/health` (confirm `grokReady: true`, limit cleared).
2. Continue the existing conversation (`newChat:false`) — it already has full P0/P1/build context —
   or start fresh with a short context recap.
3. Share, concisely (the connector adds a `[[grok-connector-turn:…]]` marker to *large* prompts —
   keep messages small to avoid it): `unified/orchestrator.py`, the #2/#3 results, and new tests.
