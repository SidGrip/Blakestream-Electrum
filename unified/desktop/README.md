# Blakestream Electrum — Multiwallet desktop dashboard

XLite-style desktop UI for the six-coin family (one seed → all six), built on the
`unified/` backend. **Electron + React 18 + Vite + Recharts + Zustand.** The renderer
talks to the backend over a loopback HTTP API (`http://127.0.0.1:57100`); there is no
Electron IPC bridge (contextIsolation on, nodeIntegration off, sandbox on).

## Architecture

```
Electron renderer (this app)  ──HTTP──▶  unified.api (loopback :57100)
                                              │
                                       unified.orchestrator
                                       (one seed → 6 per-coin Electrum daemons)
```

## Run (dev)

1. Start the backend (brings up the six daemons and serves the API):

   ```bash
   ELECTRIUM_PYBIN=/path/to/venv/bin/python \
   ELECTRIUM_WSROOT=/path/to/generated/workspaces \
   python -m unified.launcher --datadirs ~/.blakestream/electrium \
       --vault ~/.blakestream/vault.enc multi --serve
   # first run: add --create (new seed) or --restore (seed on stdin)
   ```

2. Run the UI:

   ```bash
   cd unified/desktop
   npm install
   npm run dev          # Vite dev server
   # in another shell: VITE_DEV_SERVER_URL=http://localhost:5173 electron .
   ```

   Or load the built renderer directly: `npm run build && electron .`

## Build

```bash
cd unified/desktop && npm install && npm run dist   # → release/*.AppImage
```

Produces `release/Blakestream Electrum-<ver>.AppImage` (~110 MB, the Electron shell +
renderer). Validated on an amd64 Linux host with Docker.

## State / TODO

- ✅ Frontend builds + packages (AppImage); verified rendering against the live 6-coin
  backend under xvfb.
- Prices: the six coins have **no market/exchange yet**, so price/value/sparkline fields
  degrade gracefully (amount-only / "no price data yet"). Plug a real source into
  `unified/prices.py` when a market exists.
- **P6b (self-contained packaging, TODO):** bundle the Python backend (PyInstaller the six
  per-coin workspaces + the `unified` package) as electron-builder `extraResources`, and have
  the Electron main process spawn `unified.launcher … --serve` on startup. Until then the
  backend runs as a separate process. Live balance sync also needs each aux coin's own
  ElectrumX endpoint (only BLC has a public server today).
