import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Electron loads the built renderer from the local filesystem, so all asset
// references must be relative.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Each coin icon must emit as a real asset file (discoverable + verifiable in dist/assets).
    // Vite otherwise inlines any asset under 4 KB as a base64 data URI, which would drop a small
    // icon from dist/assets. Disable inlining for assets/coins/* only; everything else keeps Vite's
    // default threshold (the six built-in icons are all >4 KB anyway).
    assetsInlineLimit: (filePath: string) =>
      /[\\/]assets[\\/]coins[\\/]/.test(filePath) ? false : undefined,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
})
