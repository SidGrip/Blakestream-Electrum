#!/bin/bash

set -e

APPDIR="$(dirname "$(readlink -e "$0")")"

export LD_LIBRARY_PATH="${APPDIR}/usr/lib/:${APPDIR}/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH+:$LD_LIBRARY_PATH}"
export PATH="${APPDIR}/usr/bin:${PATH}"
export LDFLAGS="-L${APPDIR}/usr/lib/x86_64-linux-gnu -L${APPDIR}/usr/lib"

# Run under XWayland so the window icon (the coin logo, set via setWindowIcon) drives the
# taskbar/dock entry. Under native Wayland GNOME ignores the window icon and shows a
# generic icon for an unmatched window.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

# Self-install a per-coin launcher + icon so the dock maps this wallet's window to its
# coin logo (and pins/launches cleanly). The slug is read from the bundled .desktop
# (StartupWMClass, patched per coin by the variant builder) — kept generic so this works
# for every variant. Baked into the AppImage; the user does nothing.
_DT="$(ls "$APPDIR"/*.desktop 2>/dev/null | head -1)"
if [ -n "$_DT" ]; then
    _SLUG="$(awk -F= '/^StartupWMClass=/{print $2; exit}' "$_DT")"
    _NM="$(awk -F= '/^Name=/{print $2; exit}' "$_DT")"
    [ -n "$_SLUG" ] || _SLUG="electrum"
    _SRC="$APPDIR/${_SLUG}.png"; [ -f "$_SRC" ] || _SRC="$APPDIR/electrum.png"
    if [ -f "$_SRC" ]; then
        _ICON="$HOME/.local/share/icons/hicolor/256x256/apps/${_SLUG}.png"
        _DESK="$HOME/.local/share/applications/${_SLUG}.desktop"
        mkdir -p "$(dirname "$_ICON")" "$(dirname "$_DESK")" 2>/dev/null || true
        cp -f "$_SRC" "$_ICON" 2>/dev/null || true
        cat > "$_DESK" 2>/dev/null <<_DEOF
[Desktop Entry]
Type=Application
Name=${_NM:-Electrum Wallet}
Icon=${_ICON}
Exec=${APPIMAGE:-$0} %u
Terminal=false
StartupNotify=true
Categories=Finance;Network;
StartupWMClass=${_SLUG}
_DEOF
    fi
fi

exec "${APPDIR}/usr/bin/python3" -s "${APPDIR}/usr/bin/electrum" "$@"
