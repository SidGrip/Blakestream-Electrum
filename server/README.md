# Blakestream ElectrumX Server

The ElectrumX backend for the Blakestream. It serves the
Electrum protocol for the wallets in this repo and uses the Blake-256 R8 +
variable-length AuxPoW coin classes under `electrumx/lib/coins.py`.

This server source supersedes the deprecated 15.21 server line. The packaged
entry points are `electrumx_server`, `electrumx_rpc`, and
`electrumx_compact_history`. The Docker image also installs the repo-root
`blake256/` C extension for header hashing.

## Install paths

There are two ways to stand up this server tier:

1. **Automated** — `./deploy.sh --fresh` (recommended). Detects the host's
   state, prints a decision matrix, then pulls or builds the image, generates
   the compose, starts the six services, and installs a Let's Encrypt
   renewal hook. Idempotent. On a clean host, run
   `./deploy-daemons.sh --fresh` first. See
   [Automated install](#automated-install).
2. **Manual** — follow the [Manual Deployment](#manual-deployment) steps.
   Useful when you want to inspect every artifact before it lands, or when
   you cannot run shell scripts on the target host.

Both paths produce the same on-disk layout: `${DEPLOY_DIR}/docker-compose.yml`,
`${DEPLOY_DIR}/db/<coin>/`, `${DEPLOY_DIR}/ssl/`, and a redacted
`${DEPLOY_DIR}/deploy-summary.md`.

## Coin And Port Map

| Ticker | ElectrumX `COIN` | TCP | SSL | Daemon RPC default |
|--------|------------------|-----|-----|--------------------|
| BLC  | `Blakecoin` | `50001` | `50002` | `8772` |
| BBTC | `BlakeBitcoin` | `50011` | `50012` | `8243` |
| ELT  | `Electron-ELT` | `50021` | `50022` | `6852` |
| LIT  | `Lithium` | `50031` | `50032` | `12000` |
| PHO  | `Photon` | `50041` | `50042` | `8984` |
| UMO  | `UniversalMolecule` | `50051` | `50052` | `5921` |

Each class has a testnet variant; ElectrumX selects by `COIN` plus `NET`.
Production mainnet endpoints should keep the stable hostnames
`electrum1.blakestream.io` and `electrum2.blakestream.io`.

## Daemon Requirements

Each full node must be fully synced and must have transaction indexing enabled.
Set this in each coin daemon config before starting or reindexing the daemon:

```ini
server=1
txindex=1
rpcuser=<strong unique user>
rpcpassword=<strong unique password>
rpcport=<coin rpc port>
```

For a daemon that previously ran with `txindex=0`, enable `txindex=1` and reindex
or let the node build the txindex before deploying ElectrumX. Check readiness:

```bash
<coin-cli> getblockchaininfo
<coin-cli> getindexinfo
```

`getblockchaininfo` should show local `blocks` caught up to `headers` or the
peer tip. Some low-activity 25.2 chains can still report
`initialblockdownload: true` even when caught up to peer tip, so the deploy
scripts treat tip alignment as authoritative. `getindexinfo` must show the
`txindex` entry as synced.

On a clean server you can create the six daemon containers and ElectrumX-safe
configs with:

```bash
cd server
./deploy-daemons.sh --fresh --install-system-deps
```

This pulls the six daemon Docker images, writes configs under the daemon data
root, and starts host-network containers. It deliberately sets `txindex=1` and
`prune=0`;

To build daemon images locally from source and import verified 25.2 bootstrap
files before steady-state start:

```bash
cd server
./deploy-daemons.sh --fresh --install-system-deps --build-images --bootstrap
```

`--build-images` clones the coin daemon repositories and tags local runtime
images as `local/<coin>:latest-local` by default. `--bootstrap` downloads the
latest `*-bootstrap-<height>.dat.xz` files from `BOOTSTRAP_URL/BOOTSTRAP_SERIES`,
verifies SHA256 sidecars, imports them one coin at a time with `-loadblock`, and
then starts all daemons normally. The script waits for RPC, chain catch-up, and
`txindex`. A 25.2 daemon must expose `getindexinfo` and report
`txindex.synced=true`.

If you intentionally want the daemons to keep catching up in the background, add
`--no-wait-sync`. For normal mainnet deployment, leave the default wait enabled.
You can adjust the per-coin sync wait with `--sync-timeout <seconds>`.

After the daemon step completes, deploy ElectrumX:

```bash
./deploy.sh --fresh --build-local
```

### Optional: SPV / compact block filters (BIP 157/158)

To let SPV (Neutrino-family) wallets sync privately against these daemons,
pass `--enable-blockfilters` on a fresh deploy:

```bash
./deploy-daemons.sh --fresh --enable-blockfilters
```

This appends `blockfilterindex=1` and `peerblockfilters=1` to each generated
`.conf`. On first daemon start the compact filter index is built
synchronously — the RPC for that coin is unavailable until the index
catches up (roughly minutes to hours per coin depending on chain size;
worst case for ELT is a few hours). Chain data, txindex, and coinstatsindex
are untouched.

The disk cost is small: roughly 500 MB – 1 GB per coin, so a 6-coin host
gains ~3–6 GB in `~/.<coin>/indexes/blockfilter/`.

### Rolling out on already-deployed hosts

`deploy-daemons.sh --update` rewrites the `.conf` from a template, which
would erase per-host tweaks made after the initial deploy (docker-bridge
`rpcallowip` ranges, `coinstatsindex=1`, etc.). To enable block filters on
an existing fleet without losing those tweaks, use `enable-blockfilters.sh`
instead. It's an idempotent per-coin state machine that:

1. Discovers the coin's running container on the target host.
2. Backs up the existing `.conf`.
3. Appends the two settings (skips if already present).
4. `docker stop -t 600 <container>` (clean shutdown).
5. `docker start <container>`.
6. Polls `<coin>-cli getindexinfo` until `basic block filter index.synced=true`.
7. Moves to the next coin on the same host.

It runs per-host workers in parallel and per-coin sequential within each
host, ordered smallest → largest (BLC → LIT → PHO → BBTC → UMO → ELT).
Only one coin per host is offline at any moment, so the peer ElectrumX
host keeps serving that coin to wallet clients while the affected host
rebuilds.

```bash
./enable-blockfilters.sh \
  --hosts root@host-a.example.com,root@host-b.example.com,root@host-c.example.com \
  --dry-run

./enable-blockfilters.sh \
  --hosts root@host-a.example.com,root@host-b.example.com,root@host-c.example.com
```

Per-host progress lands in `server/logs/enable-blockfilters-<host>-<ts>.log`.
Rollback for a single coin: restore the `<coin>.conf.bak-<ts>` sibling and
`docker restart` the container. Fleet-wide rollback: pass `--disable` to
strip the two lines and restart.

## Build The ElectrumX Image

From the repository root:

```bash
./build-electrumx.sh --smoke --no-push
```

This builds `sidgrip/electrumx-blakestream:25.2` from only `server/` and
`blake256/`, then verifies the six coin classes and `electrumx_server` import.
To push a release image, run the same command without `--no-push` after Docker
login.

## Manual Deployment

1. Build or pull the ElectrumX image.

2. Prepare a deployment directory on the server:

   ```bash
   mkdir -p electrumx-blakestream/{db,ssl}
   cp server/docker-compose.yml electrumx-blakestream/docker-compose.yml
   cd electrumx-blakestream
   ```

3. Edit `docker-compose.yml`.

   For every service, set:

   - `DAEMON_URL` to that coin daemon's RPC URL.
   - `REPORT_SERVICES` to the public hostname and the correct TCP/SSL ports.
   - `SERVICES` to the correct local bind ports.
   - `volumes` so each coin has its own database directory.

   Keep RPC credentials private. The compose file contains secrets.

4. Add TLS certificates for public SSL service:

   ```text
   ssl/fullchain.pem
   ssl/privkey.pem
   ```

   Let's Encrypt certificates are fine. If SSL is not configured, run only TCP
   internally until certificates are available.

5. Start the services:

   ```bash
   docker compose up -d
   docker compose logs -f blc
   ```

6. Confirm each service reaches the daemon height, then query it:

   ```bash
   printf '{"id":1,"method":"server.features","params":[]}\n' | nc 127.0.0.1 50001
   ```

7. Open firewall ports only for the public Electrum services:

   - TCP ports: `50001`, `50011`, `50021`, `50031`, `50041`, `50051`
   - SSL ports: `50002`, `50012`, `50022`, `50032`, `50042`, `50052`

   Do not expose daemon RPC ports or ElectrumX admin RPC ports publicly.

## Scripted Deployment

`server/deploy-electrumx.py` discovers local daemons, generates a private compose
deployment, and can start the ElectrumX services.

It checks native default data folders first, then attempts to inspect running
Docker containers if a native config is not usable. For Dockerized daemons it
uses a published RPC port when one exists, otherwise it tries the container IP.

Default native config locations:

| Ticker | Config |
|--------|--------|
| BLC | `~/.blakecoin/blakecoin.conf` |
| BBTC | `~/.blakebitcoin/blakebitcoin.conf` |
| ELT | `~/.electron/electron.conf` |
| LIT | `~/.lithium/lithium.conf` |
| PHO | `~/.photon/photon.conf` |
| UMO | `~/.universalmolecule/universalmolecule.conf` |

Dry-run discovery without writing files:

```bash
server/deploy-electrumx.py --dry-run
```

Generate a deployment:

```bash
server/deploy-electrumx.py \
  --deploy-dir electrumx-blakestream \
  --report-host electrum1.blakestream.io
```

Build the image first, generate compose, start services, and wait until each
Electrum TCP service answers `server.version`:

```bash
server/deploy-electrumx.py \
  --build-image \
  --deploy-dir electrumx-blakestream \
  --report-host electrum1.blakestream.io \
  --ssl on \
  --start \
  --wait-ready 1800
```

For local tests where production ports must not be touched, offset the ElectrumX
ports. For example, BLC TCP becomes `62001` when using `--port-offset 12000`:

```bash
server/deploy-electrumx.py \
  --deploy-dir /tmp/electrumx-test \
  --report-host electrum1.blakestream.io \
  --ssl off \
  --port-offset 12000 \
  --start \
  --wait-ready 300
```

The generated compose uses `network_mode: host` by default. This is deliberate:
native daemons usually bind RPC to `127.0.0.1`, and a normal bridge-network
container cannot reach the host's loopback daemon RPC reliably.

Generated files:

```text
<deploy-dir>/docker-compose.yml   private; contains daemon RPC credentials
<deploy-dir>/deploy-summary.md    redacted operator summary
<deploy-dir>/db/<coin>/           ElectrumX database per coin
<deploy-dir>/ssl/                 optional TLS certificates
```

Keep `docker-compose.yml` private and readable only by the deployment user.

## Operational Notes

- ElectrumX needs a clean stop. Use `docker compose stop -t 600`.
- Cold indexing can take several minutes per coin even when the daemon is fully
  synced.
- ElectrumX rejects loopback addresses in `REPORT_SERVICES`; use the real public
  hostname there.
- If a coin reports txindex missing or unsynced, fix the daemon first. Do not
  deploy that coin until `getindexinfo` reports txindex synced.
- Use separate DB folders per coin. Never point two ElectrumX services at the
  same DB directory.

## Troubleshooting

`bad IP address for REPORT_SERVICES: 127.0.0.1`

: Use the public hostname in `REPORT_SERVICES`. Binding locally in `SERVICES` is
  fine for tests; reporting loopback is not.

`daemon RPC probe failed`

: Confirm the daemon is running, `server=1` is set, RPC credentials are correct,
  and the RPC port is reachable from the ElectrumX host/container.

`txindex is not synced/enabled`

: Set `txindex=1`, restart or reindex the daemon, then wait until
  `getindexinfo` reports the txindex as synced.

`daemon is still in initial block download`

: Wait until `getblockchaininfo` reports `initialblockdownload: false`.
  ElectrumX should not be deployed against a daemon that is still catching up
  unless you are doing an explicit staging test with `--allow-ibd`.

## Automated install

`./deploy.sh` is the friendly shell wrapper around `deploy-electrumx.py`. It
adds state detection (so you cannot accidentally clobber a running install),
build-vs-pull selection, and Let's Encrypt deploy-hook installation.

```bash
cp .env.example .env
# edit .env: at minimum set REPORT_HOST
./deploy.sh --fresh --dry-run        # show the planned actions
./deploy.sh --fresh                  # actually deploy
```

### Decision matrix

Every run inspects the host before doing anything, prints the matrix row it
matches, and either proceeds or aborts:

| Upstream daemons | ElectrumX state                         | `--fresh`                                            | `--update`                  |
|------------------|-----------------------------------------|------------------------------------------------------|-----------------------------|
| Missing          | (any)                                   | abort — "set up coin daemons first"                  | abort                       |
| Present          | None running                            | install (pull, write compose, start, hook)           | (treated as fresh)          |
| Present          | Containers running, image current       | abort — use `--update`                               | skip (use `--pull` to force)|
| Present          | Containers running, image drift         | abort — refuse to clobber                            | pull + recreate             |
| Present          | Native `electrumx_server` running       | abort — "stop native first" (data-loss risk)         | abort                       |

### Common flags

| Flag                  | Effect                                                                                  |
|-----------------------|-----------------------------------------------------------------------------------------|
| `--fresh`             | First-time install. Refuses if ElectrumX is already present.                            |
| `--update`            | Pull (or rebuild), recreate in place, and wait for `server.version` readiness. Idempotent when image is current. |
| `--build-local`       | Build via `../build-electrumx.sh --smoke --no-push` instead of pulling.                 |
| `--pull`              | Force `docker pull` even if the local digest matches the remote manifest.               |
| `--dry-run`           | Print every action; touch nothing.                                                      |
| `--no-cert-hook`      | Skip writing the Let's Encrypt renewal hook (use if TLS terminates upstream).           |
| `--env-file PATH`     | Source this `.env` first.                                                               |
| `--port-offset N`     | Add `N` to Electrum ports for staging tests on a shared host.                           |

The wrapper delegates compose generation, daemon discovery, and
`docker compose up -d` to `deploy-electrumx.py`; it only handles the gates
the Python script does not.

## `.env.example` reference

`.env.example` lives next to this README. Copy to `.env`, edit, and the
wrapper picks it up from `$PWD`, `${DEPLOY_DIR}`, or this directory.

| Variable                          | Default                                | Notes                                                         |
|-----------------------------------|----------------------------------------|---------------------------------------------------------------|
| `REPORT_HOST`                     | _required_                             | Public hostname clients see; drives `REPORT_SERVICES` + cert. |
| `DAEMON_HOST`                     | `127.0.0.1`                            | Used only inside explicit `DAEMON_URL_<COIN>` values.          |
| `DEPLOY_DIR`                      | `./deploy`                             | Where compose + `db/` + `ssl/` live.                          |
| `PORT_OFFSET`                     | `0`                                    | Adds to Electrum ports for staging tests.                      |
| `IMAGE_TAG`                       | `sidgrip/electrumx-blakestream:25.2`   | Override only when pinning a fork or a non-default tag.       |
| `BUILD_LOCAL`                     | `0`                                    | `1` builds locally instead of pulling.                        |
| `ELECTRUMX_DEFAULT_FEE_BTC_KVB`   | `0.00002`                              | Fee fallback — see next section.                              |
| `ELECTRUMX_UID` / `ELECTRUMX_GID` | `0` / `0`                              | TLS key owner for the stock root-running image.                |
| `CERT_DIR`                        | `/etc/letsencrypt/live/${REPORT_HOST}` | Source for `fullchain.pem` + `privkey.pem`.                   |
| `DAEMON_URL_<COIN>`               | _auto-discover_                        | Set per-coin only when discovery cannot find a daemon.        |

## Fee fallback (`ELECTRUMX_DEFAULT_FEE_BTC_KVB`)

The Blakestream chains have no fee market — `<coin-cli> estimatefee N` returns
`-1` at every confirmation target. ElectrumX 25.x propagates that `-1` to
wallets as a 500 error, which breaks fee estimation in the Electrum client.

The `:25.2` image patches ElectrumX to honour `ELECTRUMX_DEFAULT_FEE_BTC_KVB`
as a fallback when the daemon returns no estimate. The value is in BTC/kvB
(so `0.00002` ≈ 2 sat/vB). Forks that don't carry the patch ignore the env
var, so setting it is always harmless.

Set via `.env`:

```ini
ELECTRUMX_DEFAULT_FEE_BTC_KVB=0.00002
```

…or as a per-service `environment:` entry in `docker-compose.yml` if you
prefer not to use `.env`.

## TLS via Let's Encrypt

The Electrum protocol's SSL port is **raw TLS**, not HTTPS. That means the
ElectrumX container must terminate TLS directly — there is no HTTP layer for
a CDN like Cloudflare to proxy. The wallet client opens a TLS socket straight
to your origin host on the SSL port and verifies the cert against the
hostname in `REPORT_SERVICES`.

### Cloudflare must be DNS-only (no proxy)

If your hostname is fronted by Cloudflare, set the DNS record to **DNS only
(grey cloud)**, not Proxied (orange cloud). The orange-cloud proxy:

- only handles HTTP/HTTPS on the standard web ports, not arbitrary TCP/SSL
  ports like `50002` / `50012` / `50022` / `50032` / `50042` / `50052`;
- would terminate TLS at Cloudflare's edge using its own cert, which the
  Electrum client will reject because the server cert chain doesn't match.

DNS-only mode resolves the hostname straight to your origin IP, so the
Electrum client's TLS handshake reaches your ElectrumX directly.

You can still use Cloudflare for the rest of your project — just not as a
proxy for the Electrum SSL ports.

### First-time certificate

Pick whichever certbot flow suits the host:

**Standalone (no web server on port 80):**

```bash
sudo certbot certonly --standalone -d <your-host.example.com> \
  --agree-tos -m <your-email>
```

certbot temporarily binds port 80 to answer the ACME HTTP-01 challenge, then
writes the cert to `/etc/letsencrypt/live/<your-host.example.com>/`.

**Webroot (an existing web server is on port 80):**

```bash
sudo certbot certonly --webroot -w /var/www/acme \
  -d <your-host.example.com> --agree-tos -m <your-email>
```

Your web server must serve `/.well-known/acme-challenge/` from `/var/www/acme`.
For nginx, that's a small `location` block on the port-80 server that
matches your hostname.

After certbot succeeds you should have:

```
/etc/letsencrypt/live/<your-host.example.com>/fullchain.pem
/etc/letsencrypt/live/<your-host.example.com>/privkey.pem
```

`deploy.sh` reads from this directory (override with `CERT_DIR` in `.env`)
and copies the files into `${DEPLOY_DIR}/ssl/` with the correct ownership.

> **Worked example: Blakestream production.** The two live hosts
> (`electrum1.blakestream.io` + `electrum2.blakestream.io`) each obtain their
> own cert via webroot on the host's nginx, which serves
> `/.well-known/acme-challenge/` from a project-local directory. Cloudflare
> DNS for those hostnames is grey-cloud (DNS-only). The cert lineage on each
> host matches its `REPORT_HOST`, so the renewal hook installed by
> `deploy.sh` matches correctly without any per-host tweaks.

### Firewall

Open ports `80` (for ACME challenges) and the Electrum SSL ports
(`50002 / 50012 / 50022 / 50032 / 50042 / 50052`) inbound. The plain TCP
ports (`50001 / 50011 / 50021 / 50031 / 50041 / 50051`) are optional — open
them only if you want to serve unencrypted clients.

To let the deploy script open the generated public Electrum ports in `ufw`, add
`--open-firewall` or set `OPEN_FIREWALL=1` in `.env`. The script reads the
generated compose, opens only `tcp://0.0.0.0:<port>` and
`ssl://0.0.0.0:<port>` service bindings, and does not expose daemon RPC or
ElectrumX admin RPC ports.

## Cert renewal automation

`deploy.sh` installs a Let's Encrypt deploy hook unless `--no-cert-hook` is
passed. The hook lives at `/etc/letsencrypt/renewal-hooks/deploy/${REPORT_HOST}-electrumx.sh`
and runs after every successful renewal of the `${REPORT_HOST}` lineage. It:

1. Copies the renewed `fullchain.pem` + `privkey.pem` from `$RENEWED_LINEAGE`
   into `${DEPLOY_DIR}/ssl/`.
2. Chowns them `${ELECTRUMX_UID}:${ELECTRUMX_GID}` and chmods `640` (privkey)
   + `644` (fullchain). The stock 25.2 image runs as root, so the default
   owner is `0:0`; set these variables only if you build a non-root image.
3. Runs `docker compose restart` for the six ElectrumX services. The image's
   `stop_grace_period: 10m` covers a clean shutdown.

The template lives at `server/templates/electrumx-renewal-hook.sh.tmpl` and is
rendered via `envsubst` at install time. On a host where the ElectrumX image is
already current, `--update` refreshes the hook and exits without recreating the
services:

```bash
./deploy.sh --update --dry-run     # confirms what the hook would say
sudo ./deploy.sh --update          # writes the hook when no image update is needed
```

If the image has changed, `--update` pulls, recreates the services, waits until
each public Electrum TCP service answers `server.version`, then installs the
hook.

### Manual cert renewal (no hook)

If you prefer to manage renewals yourself — e.g. TLS terminates at a load
balancer that handles renewal upstream — pass `--no-cert-hook` to `deploy.sh`
and skip the hook installation entirely.
