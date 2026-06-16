#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-25.2}"
ARCH="${ARCH:-x86_64}"
APP_NAME="Blakestream-Electrium-Hub"
APPDIR="${APPDIR:-$REPO_ROOT/build/hub-appimage/AppDir}"
DIST_DIR="${DIST_DIR:-$REPO_ROOT/outputs/hub/linux}"
OUT="$DIST_DIR/$APP_NAME-$VERSION-$ARCH.AppImage"
APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"
HUB_ID="blakestream-electrium-hub"
HUB_SHARE="$APPDIR/usr/share/blakestream-electrium-hub"

TICKERS=(BLC PHO BBTC ELT LIT UMO)

coin_file() {
    case "$1" in
        BLC) printf 'Electrium-BLC-4.7.2-x86_64.AppImage' ;;
        PHO) printf 'Electrium-PHO-4.7.2-x86_64.AppImage' ;;
        BBTC) printf 'Electrium-BBTC-4.7.2-x86_64.AppImage' ;;
        ELT) printf 'Electrium-ELT-4.7.2-x86_64.AppImage' ;;
        LIT) printf 'Electrium-LIT-4.7.2-x86_64.AppImage' ;;
        UMO) printf 'Electrium-UMO-4.7.2-x86_64.AppImage' ;;
        *) return 1 ;;
    esac
}

require_file() {
    local path="$1"
    if [[ ! -f "$path" ]]; then
        printf 'required file missing: %s\n' "$path" >&2
        exit 1
    fi
}

command -v "$APPIMAGETOOL" >/dev/null 2>&1 || {
    printf 'appimagetool not found. Set APPIMAGETOOL=/path/to/appimagetool.\n' >&2
    exit 1
}

require_file "$REPO_ROOT/hub/electrium_hub.py"
require_file "$REPO_ROOT/hub/appimage/AppRun"
require_file "$REPO_ROOT/hub/appimage/$HUB_ID.desktop"
require_file "$REPO_ROOT/coin-overlays/coins.json"
require_file "$REPO_ROOT/coin-overlays/BLC/icons/Electrum_512.png"

for ticker in "${TICKERS[@]}"; do
    require_file "$REPO_ROOT/outputs/$ticker/linux/$(coin_file "$ticker")"
done

rm -rf "$APPDIR"
mkdir -p \
    "$APPDIR/usr/bin" \
    "$APPDIR/usr/coin-overlays" \
    "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/512x512/apps" \
    "$HUB_SHARE/outputs"

cp "$REPO_ROOT/hub/appimage/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

cp "$REPO_ROOT/hub/electrium_hub.py" "$APPDIR/usr/bin/electrium_hub.py"
chmod +x "$APPDIR/usr/bin/electrium_hub.py"

cp "$REPO_ROOT/coin-overlays/coins.json" "$APPDIR/usr/coin-overlays/coins.json"
cp "$REPO_ROOT/hub/README.md" "$HUB_SHARE/README.md"

for ticker in "${TICKERS[@]}"; do
    wallet_file="$(coin_file "$ticker")"
    target_dir="$HUB_SHARE/outputs/$ticker/linux"
    mkdir -p "$target_dir"
    cp "$REPO_ROOT/outputs/$ticker/linux/$wallet_file" "$target_dir/$wallet_file"
    chmod +x "$target_dir/$wallet_file"
done

ln -s share/blakestream-electrium-hub/outputs "$APPDIR/usr/outputs"

cp "$REPO_ROOT/hub/appimage/$HUB_ID.desktop" "$APPDIR/$HUB_ID.desktop"
cp "$REPO_ROOT/hub/appimage/$HUB_ID.desktop" "$APPDIR/usr/share/applications/$HUB_ID.desktop"
cp "$REPO_ROOT/coin-overlays/BLC/icons/Electrum_512.png" "$APPDIR/$HUB_ID.png"
cp "$REPO_ROOT/coin-overlays/BLC/icons/Electrum_512.png" "$APPDIR/usr/share/icons/hicolor/512x512/apps/$HUB_ID.png"

desktop-file-validate "$APPDIR/$HUB_ID.desktop"

mkdir -p "$DIST_DIR"
rm -f "$OUT"
ARCH="$ARCH" "$APPIMAGETOOL" "$APPDIR" "$OUT"
chmod +x "$OUT"

printf '%s\n' "$OUT"
