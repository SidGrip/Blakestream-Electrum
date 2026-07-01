'use strict'

// The renderer never holds the API bearer token. It calls these thin IPC methods;
// the main process attaches the token and talks to the loopback backend. This keeps
// the token out of the renderer (no XSS can read it) and off any process argv
// (/proc/<pid>/cmdline is world-readable on Linux).
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electrum', {
  api: {
    get: (path) => ipcRenderer.invoke('electrum:api', { method: 'GET', path }),
    post: (path, body) => ipcRenderer.invoke('electrum:api', { method: 'POST', path, body }),
  },
  backup: {
    pickSavePath: () => ipcRenderer.invoke('electrum:backup-save-dialog'),
    pickOpenPath: () => ipcRenderer.invoke('electrum:backup-open-dialog'),
  },
  app: {
    relaunch: () => ipcRenderer.invoke('electrum:relaunch'),
  },
})
