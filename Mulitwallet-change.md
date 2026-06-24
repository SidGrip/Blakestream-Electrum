# Multiwallet changes for DEX integration

Spec for the multiwallet side of a new **Blakestream DEX** feature: a "Multiwallet" button in
DEX Settings that **connects to a running multiwallet, or launches it if it isn't running**, then
registers the six per-coin Electrum wallets so the DEX can trade against them.

This file lists only what the **multiwallet** must add or guarantee. No DEX code is described here.
All citations are `file:line` in this repo, verified against source on 2026-06-20.

> Companion (DEX side, separate repo): `/home/sid/Blakestream-Dex-25.2` — the button, the
> per-wallet auto-register, the headless-launch/unlock driver, and the network-agnostic changes
> all live there and are NOT this repo's concern.

---

## 1. How the DEX connects (two paths)

The DEX already speaks **Electrum JSON-RPC** (it has electrum-type wallets today). So its primary,
natural integration is the **direct per-coin daemon path**, not the unified HTTP API:

- **Path 1 — direct per-coin daemon RPC (token-free, primary).** The DEX reads each coin's
  `config` file for `rpcuser`/`rpcpassword`/`rpcport`, then talks BasicAuth JSON-RPC to
  `127.0.0.1:<port>` (57101–57106). This is how the DEX reads balances/addresses and sends.
  It needs **no API token**, so it works against a multiwallet the user launched from its own GUI.
- **Path 2 — unified HTTP API (token-gated, only when the DEX launched the backend).** The
  `127.0.0.1:57100` API is used only for the orchestration the daemons can't do themselves:
  **headless launch + vault unlock**. The DEX can only use it when it spawned the backend itself
  (and thus owns `ELECTRUM_API_TOKEN`).

This split drives everything below. Path 1 already works; the gaps are (a) a **token-free way to
detect + read lock state + discover the datadir**, and (b) a **stable, documented headless
launch + unlock contract** for Path 2.

---

## 2. The blocker: the API token is unreachable to a foreign process

- Every endpoint **except `/handshake`** requires the bearer token — including `/health`,
  `/coins`, `/startup`, `/setup/status` (`unified/api.py:130-138`, `:188-192`, `:202-208`).
- The token is generated fresh per launch (`secrets.token_urlsafe(32)` / Electron
  `crypto.randomBytes`) and is **never written to disk** — it lives only in the launching
  process and the `ELECTRUM_API_TOKEN` env it passes to the backend
  (`unified/launcher.py:220-221`, `unified/desktop/electron/main.js:29,155`).
- `/handshake?nonce=` is the one token-free route, but it returns only an HMAC **proof** for a
  caller that *already* has the token — it is identity verification, **not** detection
  (`unified/api.py:121-129`).

**Consequence:** a DEX that did not launch the backend (e.g. the user opened the multiwallet GUI)
has no documented way to reach the unified API. It must fall back to Path 1 (config files +
daemon RPC). That fallback works for data, but there is **no token-free way to detect a running
instance or tell locked-vs-unlocked** today. That is the #1 change below.

---

## 3. Required changes

### MUST-HAVE (blocks the DEX feature)

#### 3.1 Add a token-free `GET /ready` (detection + lock state + discovery)
The DEX needs to answer three questions about an instance it did **not** launch, without a token:
is it running, is the vault unlocked, and where are the per-coin configs.

Add **one** unauthenticated, loopback-only endpoint returning **non-secret** discovery data:

```jsonc
GET /ready            // no Authorization header required; loopback Host check still applies
200 {
  "running": true,
  "locked": false,                 // true => vault exists but not unlocked this session
  "daemons_up": true,              // all six daemon processes answering RPC
  "datadir_root": "/home/sid/.config/blakestream-electrum/electrum",
  "rpc_ports": { "BLC":57101,"BBTC":57102,"ELT":57103,"LIT":57104,"PHO":57105,"UMO":57106 },
  "coins_online": { "BLC":true, ... }   // per-coin server connectivity (offline coins => false)
}
```

- **Why:** lets the DEX show "connect" vs "launch" vs "unlock your multiwallet", and find the
  config files, with no token. This single endpoint also solves datadir discovery (§3.3) and
  confirms the port map (§4.2).
- **Must NOT** return any secret: no seed, no balance, no address, and **no `rpcpassword`**.
  Rationale: a token-free endpoint is reachable by any local process; the `rpcpassword` stays in
  the 0600 config file (same-uid only). Widening it to a loopback endpoint would weaken it.
- **Current state:** does not exist. The building blocks do: `all_provisioned()`
  (`unified/orchestrator.py:1522-1525`), `daemon_alive()` (`:1754-1759`), `startup_status()`
  (`:230-239`). `locked` = `not all_provisioned()`.
- **Effort:** small.

#### 3.2 Stable, documented headless launch the DEX can locate and invoke
For "launch if not running" (headless, the DEX's default), the DEX must spawn the backend as a
child process.

- **Exact command (confirmed):**
  `electrum-backend --backend-dir <daemons> --datadirs <datadirs> multi --serve [--api-port 57100]`
  (`unified/desktop/electron/main.js:160`, handler `unified/launcher.py:144-153`). The backend
  runs **headless by default** under `--serve`; the GUI is a separate Electron process — there is
  no `--headless` flag (`unified/launcher.py:144`).
- **Provide a stable invocation.** Today the binary is a PyInstaller onedir buried at
  `resources/backend/supervisor/electrum-backend/electrum-backend` inside the AppImage
  (`unified/desktop/main.js:136-137`, `scripts/build-multiwallet.sh:154-162`). The DEX should not
  have to reverse-engineer AppImage internals. Pick one and document it:
  - an AppImage `--serve`/`--headless` passthrough that runs backend-only, **or**
  - a documented stable path to the `electrum-backend` binary (and the `--backend-dir` daemons dir).
- **Document the env contract.** The backend must be spawned with a **minimal** env —
  `HOME, USER, LANG, PATH, TMPDIR, TEMP, TMP, ELECTRUM_API_TOKEN` — and must **not** inherit the
  AppImage's `LD_LIBRARY_PATH`, which crashes the PyInstaller daemons
  (`unified/desktop/electron/main.js:144-156`).
- **Effort:** small (mostly: expose a stable entry + write the contract down).

#### 3.3 Discoverable datadir root (where the config files are)
The DEX reads the per-coin `config` files, but the root is **not fixed**:

- GUI launches with `--datadirs ${userData}/electrum` → on Linux `~/.config/blakestream-electrum/electrum`.
- A bare CLI headless launch (no `--datadirs`) defaults to `~/.blakestream/electrum`
  (`unified/launcher.py:175-176`), overridable by `ELECTRUM_DDROOT`/`--datadirs`.

So the DEX cannot hardcode the root. Expose it via the `/ready` response (`datadir_root`, §3.1).
That is the cleanest fix and avoids a second mechanism. **Effort:** folded into §3.1.

### SHOULD-HAVE (robustness / clean UX)

#### 3.4 Declare the per-coin `config` file a stable contract
Path 1 depends on this file. It already exists and is correct; just commit to it as a public
contract and document the caveats:

- Path: `<datadir_root>/<ticker lowercased>/config`, JSON (`unified/orchestrator.py:181,279`).
- Fields: `rpcuser` (default `"electrum"`), `rpcpassword`, `rpcport`, `rpchost` (`127.0.0.1`),
  `rpcsock` (`tcp`) (`:287-289`, `:123`). Parsers must **ignore unknown fields** (the file also
  holds `config_version`, `use_gossip`, `server`, …).
- Mode `0600`, parent dir `0700`, written atomically before the daemon starts
  (`:309`, `:276`, `:265-310`).
- **Caveat to document:** `rpcpassword` is **regenerated every session**
  (`secrets.token_hex(16)`, `:124`). The DEX must re-read it after each multiwallet start and
  never cache it across restarts.
- **Effort:** documentation only.

#### 3.5 Distinguish `/setup/unlock` failure modes
For the headless-launch mode the DEX collects the vault password and POSTs it. It needs to tell
the user "wrong password, retry" vs "vault problem".

- Today: no vault → `400`; wrong password **and** corrupt vault both → `401` via `BadPassword`
  (`unified/api.py:282-302`). Corrupt vault is indistinguishable from a typo.
- Add a distinct code/message for a corrupt/unreadable vault. **Effort:** small.

#### 3.6 Document the unlock → ready flow (and what "provisioned" means)
- Flow: `POST /setup/unlock {password}` → poll `GET /setup/progress` until done. Response shape:
  `{coins:{ticker:'connecting'|'done'|'failed'}, detail:{ticker:{phase,server}}, total}`
  (`unified/orchestrator.py:1561-1566`, `unified/api.py:193-194`).
- `GET /startup` (cold daemon bring-up) is a **separate** signal from `/setup/progress`
  (post-unlock provisioning) — don't conflate them (`unified/api.py:188-189` vs `:193-194`).
- `/setup/status` `provisioned:true` means "all six wallets **loaded** this session", **not**
  synced/has-funds (`unified/orchestrator.py:1522-1525` vs `:1604-1648`). Document so the DEX
  doesn't treat "loaded" as "ready to trade".
- **Effort:** documentation.

### OPTIONAL (nice to have, not blocking)

- **`POST /setup/cancel`** to abort an in-flight unlock if the DEX dialog closes; today
  `provision_all()` is fire-and-forget once `/setup/unlock` is accepted
  (`unified/api.py:282-302`). **Medium.**
- **Document graceful shutdown** for a DEX-launched backend: `POST /shutdown` (token) stops all
  six daemons; SIGTERM works on Linux but hard-kills on Windows
  (`unified/api.py:227-237`, `unified/launcher.py:209-247`). **Doc.**
- **Optional unattended unlock** via `ELECTRUM_VAULT_PASSWORD` already supported for no-TTY
  headless (`unified/launcher.py:73-89`); note the lower security in docs. **Doc.**

---

## 4. Already works — do NOT rebuild

These are confirmed correct; the DEX can rely on them as-is.

### 4.1 Headless backend, no TTY
`--serve` runs headless; with no password/TTY, `bring_up_all(None)` starts the six daemons
**unprovisioned**, API live immediately, waiting for `/setup/unlock`
(`unified/launcher.py:73-89,144-153`). This is exactly the DEX headless-launch mode.

### 4.2 Fixed six-coin ticker → RPC port map (canonical, immutable)
`unified/orchestrator.py:55-58` and `coin-overlays/coins.json`:

| Coin | Ticker | RPC port | coin_type |
|------|--------|----------|-----------|
| Blakecoin | BLC | 57101 | 10 |
| BlakeBitcoin | BBTC | 57102 | 424242 |
| Electron | ELT | 57103 | 424243 |
| Lithium | LIT | 57104 | 424244 |
| Photon | PHO | 57105 | 424245 |
| UniversalMolecule | UMO | 57106 | 424246 |

No off-by-one; order matches `coins.json`. Ports are loopback-only.

### 4.3 Daemons need no special mode for the DEX
Daemons start identically whether or not the DEX connects; the DEX just uses loopback BasicAuth
JSON-RPC (`unified/orchestrator.py:227-228,287-305`). Each coin is fully isolated (own process,
datadir, RPC port, server) — the orchestrator is a thin supervisor + aggregator.

### 4.4 Stale-instance reaper + single-instance lock
`_reap_foreign_supervisor()` kills a zombie holding 57100 (`unified/api.py:533-567`); the Electron
GUI enforces a single instance (`unified/desktop/electron/main.js:244-254`).

---

## 5. Gotchas to surface in docs

1. **Datadir root is not fixed** — GUI uses `~/.config/blakestream-electrum/electrum`, bare CLI
   uses `~/.blakestream/electrum`. Hence §3.3.
2. **`rpcpassword` rotates every session** — re-read after each start (§3.4).
3. **`provisioned` ≠ synced** — a loaded wallet may not have history yet (§3.6).
4. **Offline coins** (`ELECTRUM_SERVER_<TICKER>=''`) are marked done immediately and report
   `connected:false`; the wallet is still usable for addresses/balance/offline-signing
   (`unified/orchestrator.py:1629-1632`).
5. **No daemon-level "is_locked"** — lock state must come from `all_provisioned()` (exposed via
   `/ready`, §3.1); a per-coin RPC only reveals it by erroring on a wallet call.
6. **`LD_LIBRARY_PATH` crashes the PyInstaller daemons** — spawn with the minimal env (§3.2).
7. **Token is per-launch and never persisted** — a foreign process cannot use the unified API of a
   GUI-launched instance (§2).

---

## 6. Minimal set to unblock the DEX

If only the smallest change is wanted, ship these three:

1. **§3.1** token-free `GET /ready` (running / locked / `datadir_root` / `rpc_ports`).
2. **§3.2** a stable, documented headless launch entry + env contract.
3. **§3.4** declare the per-coin `config` file a stable contract (doc + per-session-password note).

With those, the DEX can: detect a running instance, find and read the six configs, register six
electrum wallets (Path 1), and launch+unlock headless when nothing is running (Path 2).
