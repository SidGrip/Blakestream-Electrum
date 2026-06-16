# Blakestream ElectrumX server

The ElectrumX backend for the Blakestream six-coin family (Blake-256 R8 +
variable-length AuxPoW). This is the server the wallet (`electrum/`) connects to.

Source: copied from the AuxPoW/25.2 line (`Blakestream-Electrium-Auxpow`), which
**supersedes the deprecated 15.21 server**. Coin classes live in
`electrumx/lib/coins.py`:

| Ticker | ElectrumX `COIN` class |
|--------|------------------------|
| BLC  | `Blakecoin` |
| BBTC | `BlakeBitcoin` |
| ELT  | `Electron-ELT` |
| LIT  | `Lithium` |
| PHO  | `Photon` |
| UMO  | `UniversalMolecule` |

Each class has a `…Testnet` variant; ElectrumX selects by `COIN` + `NET`.

Entry points: `electrumx_server`, `electrumx_rpc`, `electrumx_compact_history`.
Depends on the repo-root `blake256/` C extension for header hashing.

Production endpoints are the stable hostnames `electrum1.blakestream.io` and
`electrum2.blakestream.io` (the servers behind them are being updated; the names
stay). A deploy script for these hosts will be added later.
