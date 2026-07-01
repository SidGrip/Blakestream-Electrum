# BlakeStream Electrum

One-seed desktop **multiwallet** and standalone single-coin wallets for the
BlakeStream Blake-256 coin family — BLC, BBTC, ELT, LIT, PHO, UMO — with a bundled
ElectrumX server and per-coin Lightning hubs.

```
Licence: MIT
Language: Python (>= 3.10) + Electron/React (multiwallet desktop UI)
Based on: Electrum by Thomas Voegtlin (https://electrum.org/)
```


## BlakeStream multicoin builder

This fork builds the six **BlakeStream Electrium wallets** — Blakecoin (BLC), BlakeBitcoin (BBTC),
Electron-ELT (ELT), Lithium (LIT), Photon (PHO), UniversalMolecule (UMO) — and the **ElectrumX server**.

`./build-electrum.sh` is the root builder; run it with no arguments to print this usage (it never
builds until you choose what to build):

```bash
./build-electrum.sh [wallets] [targets] [options]

Choose at least one:
  -blc -bbtc -elt -lit -pho -umo   selected standalone coin wallets
  -all                             all six standalone wallets
  -multi                           the unified one-seed multiwallet

Targets (optional; omit to build for the machine you're on):
  --linux      Linux AppImage     (amd64 Docker host)
  --windows    Windows .exe       (amd64 Docker host)
  --macos      macOS .dmg/.app    (on a Mac)
  --wheel      Python sdist/wheel (standalone wallets only)

Options:
  --jobs N     build up to N standalone Linux/Windows wallets in parallel
  --dry-run    print the build commands without running them
  -h, --help   show this help
```

Linux and Windows build in an amd64 Docker container; the macOS app builds natively on a Mac.
`--jobs` only parallelises standalone Linux/Windows builds (multiwallet, macOS, and wheel stay serial
because they share packaging state).

On macOS the builder bootstraps portable build tooling into the clone instead of
requiring a global Node install. The local cache lives under `.build-tools/macos`
and is used for Node 22, npm cache, Electron downloads, and electron-builder
downloads. Native macOS packaging still needs Apple's command line tools and
Homebrew. Missing Homebrew packages such as `autoconf`, `automake`, `libtool`,
`gettext`, `coreutils`, and `pkgconf` are installed only when needed. Set
`ELECTRUM_MACOS_ALLOW_BREW_INSTALL=0` to make the build fail instead of changing
the Homebrew prefix.

```bash
./build-electrum.sh -blc                               # Blakecoin, for this machine's OS
./build-electrum.sh -blc -pho --linux                  # BLC + PHO, Linux AppImage
./build-electrum.sh -all --linux --windows             # all six standalone, Linux + Windows
./build-electrum.sh -all --linux --windows --jobs 3    # ... three wallets at a time
./build-electrum.sh -multi --linux                     # unified multiwallet, Linux
./build-electrum.sh -multi --macos                     # unified multiwallet, macOS (on a Mac)
./build-electrum.sh -all -multi --linux --windows      # standalone wallets + the multiwallet
```

Lower-level helpers the root builder calls:

```bash
scripts/build-single-wallets.sh [linux|windows|macos|wheel|all] [COINS…]   # six standalone wallets (OS interactive if omitted)
scripts/build_wallet_variant.sh <COIN> <linux|windows|macos|wheel>          # one coin, one OS
build-electrumx.sh [--smoke]                                                # shared ElectrumX server image sidgrip/electrumx-blakestream
contrib/build-wine/build-base.sh                                            # prebuilt Windows build base sidgrip/electrum-wine-base
```

Each coin's 25.2 repo also ships a per-coin **`build-electrum.sh <linux|windows|macos|wheel|all>`** that
delegates here (canonical copy in `contrib/coin-repo/`, distributed by `scripts/sync-build-electrum.sh`).
The OS is chosen by flag, not auto-detected: linux/windows build in an **amd64** container, so any amd64
Docker host (Linux, Windows, or an Intel Mac) builds either; only the macOS app needs a Mac.

**Build environments:** Linux AppImage in a glibc-portable Debian-bullseye container
(`contrib/build-linux/appimage`); Windows `.exe` via the prebuilt `sidgrip/electrum-wine-base`
(`contrib/build-wine`) for standalone wallets and `sidgrip/electrium-multiwallet-wine-builder:25.2`
for the multiwallet; macOS `.dmg`/`.app` natively on a Mac (`contrib/osx/make_osx.sh`). Artifacts land
in `outputs/<COIN>/<os>/`, named `Electrium-<COIN>-<version>`, and `outputs/multiwallet/<os>/`.
Server deploy: see `server/README.md`.


## Multiwallet seed and Lightning recovery

The unified multiwallet uses one BIP39 recovery phrase for BLC, BBTC, ELT, LIT,
PHO, and UMO. Each coin is restored from a native-SegWit BIP84 account key, so
the same phrase can recover the same on-chain wallet in the multiwallet or in a
compatible standalone coin wallet when the restore settings match.

When restoring the multiwallet phrase in a standalone wallet, choose BIP39,
native SegWit / P2WPKH, and the matching derivation path:

| Coin | Standalone restore path |
|------|-------------------------|
| BLC | `m/84'/10'/0'` |
| BBTC | `m/84'/424242'/0'` |
| ELT | `m/84'/424243'/0'` |
| LIT | `m/84'/424244'/0'` |
| PHO | `m/84'/424245'/0'` |
| UMO | `m/84'/424246'/0'` |

Electrum may show its normal BIP39 warning during a standalone restore. That is
expected: upstream Electrum does not normally generate BIP39 seeds because they
do not carry Electrum's seed-version marker. This fork intentionally uses BIP39
for the multiwallet so the same phrase can restore all six on-chain wallets with
the paths above.

The phrase restores on-chain addresses and funds. It does not, by itself,
restore active Lightning channel state. Lightning channels also depend on the
wallet file/channel database and channel backups. If the wallet file is lost, a
static channel backup can request a force-close from the remote peer so funds
return on-chain, but it cannot keep using the old channel.

Before importing the multiwallet phrase into a standalone wallet or reinstalling
while Lightning channels are open:

- Prefer to close/cash out Lightning channels first.
- Keep a backup of the multiwallet data directory and the per-coin wallet files.
- Export a fresh static channel backup after creating or changing channels.
- Do not run Lightning from both the multiwallet and a standalone wallet restored
  from the same phrase at the same time. Use one wallet instance for Lightning
  until channels are closed or recovered.

### Backup and restore

Besides the seed phrase, the multiwallet can save and restore a single encrypted
backup file, which also preserves data the phrase alone cannot (Lightning channel
state, contacts, and settings).

**Back up:** use **Tools -> Backup wallet** instead of manually copying the data
directory. It writes one encrypted `.bswallet` file containing the vault, contacts,
settings, per-coin wallet files, and the Lightning channel state stored in those
wallet files. Resyncable data such as headers, logs, cache, and gossip databases is
left out so the backup stays small. The file is encrypted with the current wallet
password.

**Restore:** on first launch, choose **Restore from backup file** (next to **Create
new wallet** and **Restore from seed**), pick the `.bswallet` file, and enter the
wallet password that was active when the backup was created — that same password
both decrypts the backup and unlocks the restored wallet.

The multiwallet Lightning flow uses direct per-coin channels to the configured
BlakeStream hub peers. It is not a public cross-coin Lightning graph, and it does
not use Bitcoin-mainnet trampoline routing, LNURL, or submarine swaps for normal
wallet channel management.


## Getting started

The supported way to produce the wallets is the **BlakeStream multicoin builder**
above: `./build-electrum.sh` builds the standalone single-coin wallets, the unified
multiwallet, and the ElectrumX server, with dependencies handled in an isolated
build environment. The rest of this section is for running the underlying engine
from source during development.

### Build-from-source dependencies

The engine is mostly pure Python, but a few pieces are not. On Debian/Ubuntu a
minimal set is:

```
$ sudo apt-get install libsecp256k1-dev python3-cryptography
$ ELECTRUM_ECC_DONT_COMPILE=1 python3 -m pip install --user ".[crypto]"
```

- **libsecp256k1** — elliptic-curve operations. By default it compiles locally as
  part of `electrum-ecc`; set `ELECTRUM_ECC_DONT_COMPILE=1` to use a system
  `libsecp256k1-dev` instead (compiling it needs `automake libtool` and a C compiler).
- **cryptography** — fast symmetric ciphers (`python3-cryptography`, or via pip).
- **Qt** — only for a standalone single-coin wallet's desktop GUI (`python3-pyqt6`).
  The unified multiwallet ships its own Electron/React UI and does not need Qt.

### Run the engine from source

```
$ git submodule update --init
$ python3 -m pip install --user -e .
$ ./run_electrum
```

### Run tests

```
$ pytest tests -v
```
Parallelize with `-n auto` (via [`pytest-xdist`](https://github.com/pytest-dev/pytest-xdist)); run a single file with `pytest tests/test_bitcoin.py -v`.

### Packaging

Per-platform packaging notes live under `contrib/` — [Linux tarball](contrib/build-linux/sdist/README.md),
[Linux AppImage](contrib/build-linux/appimage/README.md), [macOS](contrib/osx/README.md),
[Windows](contrib/build-wine/README.md). For the BlakeStream wallets the multicoin
builder above is the entry point that drives these. (There is no Android build — the
builder targets Linux, Windows, and macOS only.)

## Contributing

BlakeStream Electrum is a fork of [Electrum](https://electrum.org/) (MIT, by Thomas
Voegtlin) carrying the six BlakeStream coins, the unified multiwallet, the bundled
ElectrumX server, and the BlakeStream Lightning hub flow. Bug reports, fixes, tests,
and improvements to any of these are welcome via the project repository.
