# Electrium Hub

Electrium Hub is the selected all-six wallet shape: a single wallet surface that
coordinates six generated Electrium variants while keeping each coin runtime,
network constants, wallet directory, and server connection isolated.

It does not merge six `electrum.constants.net` contexts into one Python process.
Both supported user shapes are explicit:

- **Single Hub wallet/server surface**: one launcher/status UI for all six coins.
- **Six per-coin wallet/server lanes**: the existing generated Electrium variants
  and one ElectrumX instance per coin remain available for direct use.

Examples:

```bash
python hub/electrium_hub.py list
python hub/electrium_hub.py status
python hub/electrium_hub.py config BLC
python hub/electrium_hub.py ensure-config --include-password
python hub/electrium_hub.py build BLC --target wheel
python hub/electrium_hub.py launch --network testnet BLC -- --offline version
python hub/electrium_hub.py launch --network regnet --detach BLC
python hub/electrium_hub.py stop BLC
```

`list` and `status` intentionally redact `rpcPassword` and only report
`hasRpcPassword`. Use `config --include-password` or
`ensure-config --include-password` only for explicit wallet import/setup flows
that need the RPC credential.

Build the all-six Hub AppImage after the per-coin AppImages exist:

```bash
./hub/build_hub_appimage.sh
```

The generated artifact is written to:

```text
outputs/hub/linux/Blakestream-Electrium-Hub-25.2-x86_64.AppImage
```

Running the Hub AppImage with no arguments opens a small chooser for the six
embedded wallets. CLI usage is also supported:

```bash
./outputs/hub/linux/Blakestream-Electrium-Hub-25.2-x86_64.AppImage hub status
./outputs/hub/linux/Blakestream-Electrium-Hub-25.2-x86_64.AppImage hub ensure-config --include-password
./outputs/hub/linux/Blakestream-Electrium-Hub-25.2-x86_64.AppImage BLC --offline version
```

The Hub AppImage contains the six coin AppImages and copies them to
`$XDG_CACHE_HOME/blakestream-electrium-hub/25.2` on first launch so the embedded
wallets execute as normal AppImages. Wallet launches default to testnet; pass
`--regtest` explicitly for regnet.

`ensure-config` creates per-coin RPC credentials and fixed Hub RPC ports inside
each coin's own Electrium data directory, including the testnet and regtest
network config directories used by Electrium. It does not merge wallet files or
network constants. It also writes a DEX-import manifest at
`~/.blakestream/electrium-hub/wallets.json` with mode `0600`.

The first desktop implementation calls this coordinator from the DEX main
process and populates the existing DEX wallet store from the returned JSON.
Users can import one single-coin Electrium variant, a few variants, or the full
six-coin Hub set. DEX swap execution is testnet/regnet only until the 25.2
mainnet BIP activation gates are complete.

Asset/NFT actions stay disabled until the selected coin daemon or ElectrumX
server reports active asset support through `getassetconfig` or
`blockchain.blakeasset.get_config`.
