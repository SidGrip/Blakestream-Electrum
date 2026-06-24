# Test price API — contract for the Blakestream Electrum wallet

This document specifies a **test price endpoint** that the unified multicoin wallet can consume as a
user-configured price source. Another AI / service can implement the endpoint to any stack (Node,
Python, Go, nginx+static JSON, …) as long as it satisfies the request/response contract below.

The 6 coins have tickers: **BLC, BBTC, ELT, LIT, PHO, UMO**. None has a real exchange price, so this
test API exists to feed the wallet deterministic numbers during development and demos.

---

## 1. How the wallet consumes a source

The wallet stores an ordered list of price **sources**. Each source has a **role**, a **URL template**
with placeholders, and a **dotted `jsonPath`** used to pull a single number out of the JSON response.
For a `http_template` source the wallet does, per coin (or once per fiat):

1. Substitute placeholders into `urlTemplate` and into `jsonPath`.
2. `GET` the URL (server-side, from the Python backend — **not** the browser).
3. Parse JSON, walk the dotted `jsonPath`, coerce the value to a decimal number.
4. Any failure (network, non-200, missing field, non-numeric) is treated as **"no price"** — the
   wallet never crashes and the coin's balance still renders in coin units.

### Placeholders the wallet substitutes
| Placeholder     | Meaning                                  | Example      |
|-----------------|------------------------------------------|--------------|
| `{coin}`        | ticker, UPPERCASE                        | `BLC`        |
| `{coin_lower}`  | ticker, lowercase                        | `blc`        |
| `{fiat}`        | chosen display fiat, UPPERCASE (ISO 4217)| `USD`        |
| `{fiat_lower}`  | chosen display fiat, lowercase           | `usd`        |
| `{ids}`         | optional per-source id (or per-coin id from a `coinIds` map) | `blakecoin` |

Placeholders may appear in **both** the URL and the `jsonPath`.

---

## 2. Roles — implement at least `coin_btc`

A source declares which **role** it fills. The wallet's price chain is:

```
coin → BTC      (coin_btc)        ← the test API should provide this for all 6 coins
BTC  → fiat     (btc_fiat)        ← wallet default uses CoinGecko; test API may also provide it
coin → fiat     (coin_fiat)       ← optional direct shortcut
```

- **`coin_btc`** — return the price of one coin in **BTC**. **Minimum requirement** for the test API.
- **`btc_fiat`** *(optional)* — return the price of **1 BTC in the chosen fiat**.
- **`coin_fiat`** *(optional)* — return the price of one coin directly in the chosen fiat.

Final per-coin fiat value the wallet computes:
`value_fiat = amount × price_btc(coin) × btc_fiat(fiat)` (or a direct `coin_fiat`).

---

## 3. Request contract

### 3a. Recommended `coin_btc` endpoint (one coin per call)
```
GET https://<host>/price?coin={coin}&vs=btc
```
- Wallet source config:
  - `role`: `coin_btc`
  - `kind`: `http_template`
  - `urlTemplate`: `https://<host>/price?coin={coin}&vs=btc`
  - `jsonPath`: `price`
- Fully-resolved example: `GET https://<host>/price?coin=BLC&vs=btc`

### 3b. Optional batch endpoint (all coins at once)
```
GET https://<host>/prices?vs=btc
```
- `jsonPath`: `prices.{coin}.btc`  → the wallet pulls `prices.BLC.btc`, `prices.PHO.btc`, …

### 3c. Optional `btc_fiat` endpoint
```
GET https://<host>/btc?vs={fiat_lower}
```
- `role`: `btc_fiat`, `jsonPath`: `rates.{fiat_lower}` (or `price`, match your shape).

### Auth (optional)
If the endpoint needs a key, the wallet sends it as an **HTTP request header** whose name is the
source's `apiKeyHeader` (e.g. `X-API-Key: <key>`). The key is **never** put in the URL/query string.
Keep the test API **keyless** unless you specifically want to exercise the key path.

---

## 4. Response contract

- **Content-Type**: `application/json`, HTTP `200`.
- The number the wallet extracts may be a JSON number **or** a decimal **string** (both accepted).
- "Unknown price" must be expressed cleanly: either return `null` at the `jsonPath` location, or a
  `404`. The wallet treats both as "no price" for that coin (no crash, balance stays in coin units).
- Keep responses small (the wallet caps reads at ~128 KB).

### 4a. Single-coin response (matches 3a, `jsonPath = price`)
```json
{ "coin": "BLC", "vs": "btc", "price": "0.00000123" }
```

### 4b. Batch response (matches 3b, `jsonPath = prices.{coin}.btc`)
```json
{
  "vs": "btc",
  "prices": {
    "BLC":  { "btc": "0.00000123" },
    "BBTC": { "btc": "0.00000045" },
    "ELT":  { "btc": "0.00000008" },
    "LIT":  { "btc": "0.00000002" },
    "PHO":  { "btc": "0.00000004" },
    "UMO":  { "btc": "0.00000039" }
  }
}
```

### 4c. BTC→fiat response (matches 3c, `jsonPath = rates.{fiat_lower}`)
```json
{ "base": "BTC", "rates": { "usd": "65000.00", "eur": "60000.00" } }
```

---

## 5. Per-coin example values (use these so output is testable)

| Coin | BTC per coin   |
|------|----------------|
| BLC  | `0.00000123`   |
| BBTC | `0.00000045`   |
| ELT  | `0.00000008`   |
| LIT  | `0.00000002`   |
| PHO  | `0.00000004`   |
| UMO  | `0.00000039`   |

These are placeholders — pick any positive numbers; the wallet just needs a parseable decimal.

---

## 6. Reachability / security (important)

The wallet fetches sources **server-side** through an SSRF guard:
- **HTTPS only.** Plain `http://` is rejected.
- **Private / LAN / loopback hosts are blocked by default** (RFC1918 `10/8`,`172.16/12`,`192.168/16`,
  loopback `127/8`/`::1`, link-local `169.254/16`/`fe80::/10`, unique-local `fc00::/7`).
- **Timeout** ~4 s; **response cap** ~128 KB; 3xx redirects are re-validated/refused.

Therefore the test API must be **either**:
1. **Public HTTPS** (a real domain + valid TLS cert) — works with the default guard; **or**
2. **On the LAN / localhost**, in which case the user must tick **Settings → Price & currency →
   "Allow LAN/private hosts"** (sets `allow_private_hosts: true`). It is still **HTTPS-only** even
   when LAN hosts are allowed — a LAN test box needs a self-signed/internal cert reachable over
   `https://`.

**Recommended test-box shape:** a small HTTPS service on the build/test LAN (valid or trusted-internal
cert) serving the batch endpoint in §3b/§4b, with the user enabling "Allow LAN/private hosts".

---

## 7. Self-verify before wiring into the wallet

```bash
# coin_btc single
curl -s "https://<host>/price?coin=BLC&vs=btc"
# expect: {"coin":"BLC","vs":"btc","price":"0.00000123"}

# batch
curl -s "https://<host>/prices?vs=btc" | python3 -m json.tool
# expect: prices.BLC.btc etc. present and numeric

# (if implemented) btc_fiat
curl -s "https://<host>/btc?vs=usd"
```

Once these return parseable numbers, add the source in the wallet (Settings → Price & currency →
Add source → role `coin_btc`, kind `http_template`, paste the `urlTemplate` + `jsonPath`), hit
**Test** on the row to confirm the wallet resolves a number, then **enable** it and toggle the
**Balance** header to fiat.

---

## 8. Live Blakestream test API (current) + what to add

A test API is up at `https://explorer.blakestream.io`. Two roles already work; two more endpoints are
needed to exercise **every** wallet function.

### Working today
| Role | Wallet `urlTemplate` | Wallet `jsonPath` | Example response |
|------|----------------------|-------------------|------------------|
| **coin_btc** | `https://explorer.blakestream.io/api/test/prices?vs=btc` | `prices.{coin}.btc` | `{"vs":"btc","prices":{"BLC":{"btc":"0.00000123"}, …}}` |
| **btc_fiat** | `https://explorer.blakestream.io/api/test/btc?vs={fiat_lower}` | `rates.{fiat_lower}` | `{"base":"BTC","rates":{"usd":"65000.00"}}` (usd + eur supported) |

These two are enough for the wallet to show fiat: `value = coin_btc × btc_fiat`. (Verified: 1 BLC →
$0.07995; 1 UMO → €0.0234.)

### Add these two to test all functions

**(a) Direct Coin → Fiat (`coin_fiat` role).** Make `/api/test/prices` also accept a fiat `vs`
(currently it errors with `unsupported vs='usd'`). Return each coin priced directly in that fiat:
```
GET https://explorer.blakestream.io/api/test/prices?vs=usd
  -> {"vs":"usd","prices":{"BLC":{"usd":"0.07995"}, "BBTC":{"usd":"0.02925"}, …}}
```
Wallet source → role `coin_fiat`, `urlTemplate` `…/api/test/prices?vs={fiat_lower}`,
`jsonPath` `prices.{coin}.{fiat_lower}`. (Lets us test the direct-price short-circuit + the
per-role priority chain when both a `coin_fiat` and a `coin_btc`+`btc_fiat` path exist.)

**(b) API-key path.** Add a route (or a flag on the existing one) that **requires a header key**, so
the wallet's API-key field + header injection + key masking can be tested:
```
GET https://explorer.blakestream.io/api/test/prices?vs=btc&auth=1
  with header  X-Test-Key: demo   -> 200 (same prices payload)
  without the header               -> 401  (wallet shows "no value returned")
```
Wallet source → same as `coin_btc` above, plus **API key header** `X-Test-Key` and **API key** `demo`.
The wallet sends the key only as a header (never in the URL) and never returns it to the UI (shown
masked as `••••demo`).

### Optional extras to stress the parser/guards
- An endpoint that returns `null` / `404` for one coin (e.g. omit UMO) → wallet must show that coin as
  **"no price"** while the others stay priced (partial-pricing path).
- A deliberately slow endpoint (>4 s) → wallet must time out to "no price" without stalling the app.
