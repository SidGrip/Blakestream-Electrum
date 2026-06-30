'use strict'

// Blakestream Wallet (multiwallet) — Electron main process.
// The renderer never touches the network: it calls the loopback HTTP backend
// (http://127.0.0.1:57100) only through the contextBridge preload + the
// 'electrum:api' IPC handler below, which verifies the backend's identity and
// attaches the per-launch bearer token. nodeIntegration stays off.

const { app, BrowserWindow, Tray, Menu, nativeImage, shell, ipcMain, dialog } = require('electron')
const path = require('path')
const crypto = require('crypto')
const http = require('http')
const { spawn } = require('child_process')

const isDev = !app.isPackaged

// App icon for the window (taskbar/Alt-Tab via X11 _NET_WM_ICON under Xwayland) and the tray. Packaged
// it's an unpacked extraResource (a real file path — most reliable for the Tray); in dev it's the repo PNG.
const ICON_PATH = isDev
  ? path.join(__dirname, '..', 'build', 'icons', '256x256.png')
  : path.join(process.resourcesPath, 'icon.png')

const BACKEND_HOST = '127.0.0.1'
const BACKEND_PORT = 57100

// The UI is a static dashboard (no GPU-heavy content), and many target machines
// (VMs, mismatched drivers) fail to init the GPU process — logging "Exiting GPU
// process due to errors during initialization" and falling back to software render.
// Disable HW acceleration up front to silence that and run more reliably.
app.disableHardwareAcceleration()

// Per-launch bearer token. It stays in THIS process only; the renderer reaches the
// backend through the electrum:api IPC handler below, which attaches the token. The
// token is never put on a command line or exposed to the renderer.
const API_TOKEN = process.env.ELECTRUM_API_TOKEN || crypto.randomBytes(32).toString('hex')

function exeName(name) {
  return process.platform === 'win32' ? `${name}.exe` : name
}

// Low-level loopback request. The renderer supplies only a method + path (constrained to
// a loopback path), never a full URL, so this cannot be turned into an SSRF. The bearer
// token is attached only when `auth` is set, so the identity handshake — which must run
// BEFORE we trust the listener — can omit it.
function httpRequest({ method, path: reqPath, body, auth }) {
  return new Promise((resolve) => {
    // Bound the body in the MAIN process before allocating/serializing — legit bodies
    // are tiny (password + 24 words); a huge body from a buggy/compromised renderer must
    // not spike main's memory (the backend's 64 KiB cap only fires after this point).
    let payload = null
    if (body != null) {
      try {
        payload = Buffer.from(JSON.stringify(body))
      } catch (e) {
        resolve({ ok: false, status: 400, data: { error: 'unserializable body' } })
        return
      }
      if (payload.length > 64 * 1024) {
        resolve({ ok: false, status: 413, data: { error: 'request body too large' } })
        return
      }
    }
    const headers = { Accept: 'application/json' }
    if (auth) headers.Authorization = `Bearer ${API_TOKEN}`
    if (payload) {
      headers['Content-Type'] = 'application/json'
      headers['Content-Length'] = payload.length
    }
    const req = http.request(
      { host: BACKEND_HOST, port: BACKEND_PORT, path: reqPath, method, headers },
      (res) => {
        let data = ''
        res.on('data', (c) => { data += c })
        res.on('end', () => {
          let parsed = {}
          try { parsed = data ? JSON.parse(data) : {} } catch (e) { parsed = { error: 'invalid json from backend' } }
          const ok = res.statusCode >= 200 && res.statusCode < 300
          resolve({ ok, status: res.statusCode, data: parsed })
        })
      },
    )
    req.on('error', (e) => resolve({ ok: false, status: 0, data: { error: String((e && e.message) || e) } }))
    if (payload) req.write(payload)
    req.end()
  })
}

let backendVerified = false

// Confirm the process answering on 127.0.0.1:57100 is OUR backend before sending it the
// token or any secret. We challenge it with a random nonce; only a backend spawned with
// ELECTRUM_API_TOKEN can return HMAC(token, nonce). A local port-squatter that grabbed
// the port first holds no token and cannot, so we refuse to talk to it (the seed/password
// never leave this process). No-op in dev, where the backend is run separately.
async function verifyBackend() {
  if (isDev) return true
  const nonce = crypto.randomBytes(16).toString('hex')
  const res = await httpRequest({ method: 'GET', path: `/handshake?nonce=${nonce}`, auth: false })
  const proof = res && res.data && res.data.proof
  if (typeof proof !== 'string' || proof.length === 0) return false
  const expected = crypto.createHmac('sha256', API_TOKEN).update(nonce).digest('hex')
  const a = Buffer.from(proof)
  const b = Buffer.from(expected)
  return a.length === b.length && crypto.timingSafeEqual(a, b)
}

// Proxy a renderer API call to the loopback backend with the token attached — but only
// after the identity handshake has confirmed the listener really is our backend.
async function backendRequest({ method, path: reqPath, body }) {
  if (!backendVerified) {
    backendVerified = await verifyBackend()
    if (backendVerified) backendRestarts = 0   // healthy backend confirmed -> fresh restart budget
    if (!backendVerified) {
      return { ok: false, status: 0, data: { error: 'backend identity check failed' } }
    }
  }
  return httpRequest({ method, path: reqPath, body, auth: true })
}

ipcMain.handle('electrum:api', (_event, msg) => {
  const method = msg && msg.method
  const reqPath = msg && msg.path
  // Only GET/POST, only a loopback path (leading single slash, no scheme/authority).
  if ((method !== 'GET' && method !== 'POST') ||
      typeof reqPath !== 'string' || !reqPath.startsWith('/') ||
      reqPath.startsWith('//') || reqPath.includes('://')) {
    return Promise.resolve({ ok: false, status: 400, data: { error: 'bad request' } })
  }
  return backendRequest({ method, path: reqPath, body: msg.body })
})

ipcMain.handle('electrum:backup-save-dialog', async () => {
  const stamp = new Date().toISOString().slice(0, 10)
  const result = await dialog.showSaveDialog(mainWindow, {
    title: 'Save Blakestream wallet backup',
    defaultPath: `Blakestream-Wallet-Backup-${stamp}.bswallet`,
    filters: [
      { name: 'Blakestream wallet backup', extensions: ['bswallet'] },
      { name: 'All files', extensions: ['*'] },
    ],
  })
  return result.canceled ? null : result.filePath
})

ipcMain.handle('electrum:backup-open-dialog', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Restore Blakestream wallet backup',
    properties: ['openFile'],
    filters: [
      { name: 'Blakestream wallet backup', extensions: ['bswallet'] },
      { name: 'All files', extensions: ['*'] },
    ],
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('electrum:relaunch', () => {
  app.relaunch()
  app.exit(0)
  return true
})

let mainWindow = null
let backend = null
let tray = null // system-tray indicator; module-scoped so it isn't garbage-collected (which hides it)
// Bounded consecutive auto-restarts of the supervisor; reset to 0 once a backend verifies healthy.
let backendRestarts = 0

function startBackend() {
  // Dev: the Python backend runs separately. Packaged: spawn the bundled,
  // self-contained backend (supervisor binary + per-coin daemon binaries) that
  // electron-builder placed under resources/backend.
  if (isDev) return
  const root = path.join(process.resourcesPath, 'backend')
  const supervisor = path.join(root, 'supervisor', 'electrum-backend', exeName('electrum-backend'))
  const daemons = path.join(root, 'daemons')
  const datadirs = path.join(app.getPath('userData'), 'electrum')
  // The PyInstaller backend must NOT inherit the AppImage/Electron environment: AppRun
  // exports LD_LIBRARY_PATH=$APPDIR/usr/lib (Electron's libs), which makes the backend's
  // Python resolve libssl/libffi/etc. against Electron's libs instead of its own
  // _internal/ and crash. Hand it a minimal, clean environment instead of process.env.
  const env = {
    HOME: process.env.HOME,
    USERPROFILE: process.env.USERPROFILE,
    USER: process.env.USER || '',
    LANG: process.env.LANG || 'C.UTF-8',
    PATH: process.platform === 'win32'
      ? (process.env.PATH || '')
      : '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
    TMPDIR: process.env.TMPDIR || '/tmp',
    TEMP: process.env.TEMP,
    TMP: process.env.TMP,
    ELECTRUM_API_TOKEN: API_TOKEN,
  }
  // Keep only Electron-side lifecycle notes for support. Do not pipe raw backend
  // stdout/stderr here; test builds used verbose plaintext backend logs, but
  // production should not persist request bodies, wallet data, or RPC details.
  const logPath = path.join(app.getPath('userData'), 'backend.log')
  const note = (msg) => { try { require('fs').appendFileSync(logPath, `\n[main] ${msg}\n`) } catch (e) { /* ignore */ } }
  backend = spawn(supervisor, ['--backend-dir', daemons, '--datadirs', datadirs, 'multi', '--serve'],
    { stdio: ['ignore', 'ignore', 'ignore'], env })
  // A missing binary throws 'error'; a crash or an EADDRINUSE (port-squatted) exit fires
  // 'exit'. Without these the failure is silent and the UI just spins. Record it, and
  // force a re-handshake so a replacement backend can't be trusted on stale state.
  backend.on('error', (err) => note(`backend spawn error: ${(err && err.message) || err}`))
  backend.on('exit', (code, signal) => {
    note(`backend exited (code=${code} signal=${signal})`)
    backend = null
    backendVerified = false
    // Bounded auto-restart: a one-off crash recovers instead of hanging the UI at "Starting
    // wallets"; a genuine crash-loop gives up after the cap so it can't thrash. The backend's own
    // port-reaper already handles the common stale-supervisor case, so this is the net for
    // OOM/segfault-style deaths. Skip during an intentional quit.
    if (quitting) return
    if (backendRestarts < 3) {
      backendRestarts++
      note(`restarting backend (attempt ${backendRestarts}/3)`)
      setTimeout(startBackend, 800)
    } else {
      note('backend gave up after 3 restarts; the UI will surface the retry card')
    }
  })
}

// Hard-stop fallback (used if the graceful path is unavailable or times out).
function killBackend() {
  if (backend) {
    try { backend.kill('SIGTERM') } catch (e) { /* already gone */ }
    backend = null
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: '#1a1d21',
    title: 'Blakestream Wallet',
    icon: ICON_PATH,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.cjs'),
    },
  })

  if (isDev && process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }

  // Open external links in the system browser, never in-app — and only safe schemes
  // (an attacker-influenced url with e.g. file:/javascript: must not be opened).
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const u = new URL(url)
      if (u.protocol === 'https:' || u.protocol === 'http:' || u.protocol === 'mailto:') {
        shell.openExternal(url)
      }
    } catch (e) { /* malformed url: ignore */ }
    return { action: 'deny' }
  })

  // Never navigate the app frame away from the bundled UI (defense-in-depth).
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isDev && process.env.VITE_DEV_SERVER_URL && url.startsWith(process.env.VITE_DEV_SERVER_URL)) return
    if (!url.startsWith('file://')) event.preventDefault()
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// Bring the window up (restore if minimized, re-create if it was closed) and focus it.
function showMainWindow() {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  } else {
    createWindow()
  }
}

// System-tray indicator with the app icon, shown while the app runs. Click to surface the window;
// the menu offers Show / Quit. Best-effort: a tray failure (e.g. no StatusNotifier host) must never
// crash the app — the window icon still carries the branding.
function createTray() {
  try {
    if (tray) return
    let img = nativeImage.createFromPath(ICON_PATH)
    if (img.isEmpty()) return // icon not found at runtime — skip the tray rather than show a blank one
    // Linux tray hosts (AppIndicator) expect a small icon; downscale so it isn't clipped/oversized.
    if (process.platform === 'linux') img = img.resize({ width: 22, height: 22 })
    tray = new Tray(img)
    tray.setToolTip('Blakestream Wallet')
    tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: 'Show Blakestream Wallet', click: showMainWindow },
        { type: 'separator' },
        { label: 'Quit', click: () => app.quit() },
      ]),
    )
    tray.on('click', showMainWindow)
  } catch (e) {
    /* tray unsupported on this desktop — non-fatal */
  }
}

// Single instance: a second launch must not spawn a second backend that races the
// first for the same userData (vault + wallets) — that could mint a second seed and
// leave the vault out of sync with the on-disk wallets.
const HAS_SINGLE_INSTANCE_LOCK = app.requestSingleInstanceLock()
if (!HAS_SINGLE_INSTANCE_LOCK) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })
}

app.whenReady().then(() => {
  if (!HAS_SINGLE_INSTANCE_LOCK) return   // second instance: do not start a backend
  startBackend()
  createWindow()
  createTray()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// Graceful shutdown: ask the supervisor (over the verified loopback) to stop all six
// daemons cleanly, THEN exit — on Windows a bare SIGTERM hard-kills it and orphans the
// daemons. Bounded so a wedged backend can't block quit; falls back to a hard kill.
let quitting = false
app.on('will-quit', (event) => {
  if (quitting || !backend) return
  event.preventDefault()
  quitting = true
  const finish = () => { killBackend(); app.quit() }
  if (!backendVerified) { finish(); return }
  const timer = setTimeout(finish, 5000)
  httpRequest({ method: 'POST', path: '/shutdown', body: {}, auth: true })
    .finally(() => { clearTimeout(timer); finish() })
})
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
