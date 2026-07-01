#!/usr/bin/env bash
# Bake --no-sandbox into a built AppImage's AppRun so it launches on a plain
# double-click / `./App.AppImage`.
#
# Why: modern Ubuntu (23.10+/24.04) restricts unprivileged user namespaces, so
# Electron falls back to the SUID sandbox — which an AppImage's user-owned FUSE
# mount can't satisfy (chrome-sandbox can't be setuid-root there). The result is
# the "The SUID sandbox helper binary was found, but is not configured correctly"
# abort. electron-builder only adds --no-sandbox to the generated .desktop Exec
# line, NOT to AppRun's own exec, so running the AppImage directly still aborts.
# This patches AppRun's two exec lines and repacks. The renderer only loads local
# files and talks to loopback, so dropping the Chromium sandbox is an acceptable
# trade here (contextIsolation + nodeIntegration:false still apply).
#
# Run AFTER `npm run dist`:
#   ./fix-appimage-sandbox.sh "release/Blakestream Wallet-0.25.2.AppImage"
#
#   fix-appimage-sandbox.sh <input.AppImage> [output.AppImage]
set -euo pipefail

IN="${1:?usage: fix-appimage-sandbox.sh <input.AppImage> [output.AppImage]}"
OUT="${2:-$IN}"
RT="${ELECTRON_BUILDER_APPIMAGE_RUNTIME:-$HOME/.cache/electron-builder/appimage/appimage-12.0.1/runtime-x64}"
[ -f "$RT" ] || { echo "AppImage runtime not found at $RT (run 'npm run dist' once to populate the cache, or set ELECTRON_BUILDER_APPIMAGE_RUNTIME)"; exit 1; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
INABS="$(readlink -f "$IN")"
( cd "$WORK" && "$INABS" --appimage-extract >/dev/null )

if ! grep -q 'no-sandbox' "$WORK/squashfs-root/AppRun"; then
  sed -i 's|exec "$BIN"|exec "$BIN" --no-sandbox|' "$WORK/squashfs-root/AppRun"
fi
mksquashfs "$WORK/squashfs-root" "$WORK/app.sqfs" -root-owned -noappend -comp gzip -b 1048576 >/dev/null
cat "$RT" "$WORK/app.sqfs" > "$OUT.tmp"
chmod +x "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
echo "patched (--no-sandbox baked into AppRun): $OUT"
