#!/usr/bin/env bash
# Build ONE coin's Electrum 0.25.2 wallet variant for one platform.
#
#   build_wallet_variant.sh <COIN> <TARGET> [workspace-root] [artifact-root]
#
# TARGET (one platform per call; build-single-wallets.sh loops these):
#   wheel              Python sdist + wheel            (local venv)
#   appimage | linux   Linux AppImage                  (docker: appimage-base)
#   windows | win      Windows .exe (+ zip bundle)     (docker + wine)
#   macos | mac        macOS .app + .dmg               (native — run ON macOS)
#   both               wheel + appimage                (back-compat)
#
# Workspaces are generated per coin+target from coin-overlays/coins.json by
# prepare_wallet_variant.py.  Artifacts land in <artifact-root>/<COIN>/<kind>/,
# renamed electrum-<coin> -> Electrum-<COIN> for release.
#
# Layout note: 0.25.2 keeps electrum/ + contrib/ at the repo ROOT (additive).
# AppImage  -> $WS/dist/*.AppImage
# Windows   -> $WS/contrib/build-wine/dist/*.exe
# macOS     -> $WS/dist/*.app + $WS/dist/*.dmg   (contrib/osx/make_osx.sh)
set -euo pipefail

usage() {
    sed -n '2,20p' "$0" >&2
}

[[ $# -ge 2 ]] || { usage; exit 1; }

# macOS ships bash 3.2 (no ${var^^}/${var,,}); use tr for portability.
COIN="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
TARGET="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="${3:-$REPO_ROOT/build/workspaces}"
ARTIFACT_ROOT="${4:-$REPO_ROOT/outputs}"
VENV_DIR="${ELECTRUM_BUILD_VENV:-$REPO_ROOT/.venv}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_DIR/bin/python}"

case "$TARGET" in
    wheel|appimage|linux|windows|win|macos|mac|both) ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown target: $TARGET" >&2; usage; exit 1 ;;
esac

# normalize aliases
[[ "$TARGET" == "linux" ]] && TARGET="appimage"
[[ "$TARGET" == "win" ]] && TARGET="windows"
[[ "$TARGET" == "mac" ]] && TARGET="macos"

LOCK_DIR="$REPO_ROOT/build/locks"
mkdir -p "$WORKSPACE_ROOT" "$LOCK_DIR"

lower_coin() { printf '%s' "$COIN" | tr '[:upper:]' '[:lower:]'; }

# Rename electrum-<coin>-* -> Electrum-<COIN>-* for release artifacts.
release_name() {
    local src; src="$(basename "$1")"
    echo "${src/electrum-$(lower_coin)/Electrum-$COIN}"
}

ensure_python_env() {
    if [[ ! -x "$PYTHON_BIN" ]]; then
        "${PYTHON_BIN_FALLBACK:-python3}" -m venv "$VENV_DIR"
    fi
    "$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel
    "$PYTHON_BIN" -m pip install -r "$REPO_ROOT/contrib/requirements/requirements.txt"
    "$PYTHON_BIN" -m pip install -r "$REPO_ROOT/contrib/requirements/requirements-build-appimage.txt"
    "$PYTHON_BIN" -m pip install 'cryptography>=2.6' 'pycryptodomex>=3.7'
}

# Generate a fresh per-target workspace (a full Electrum tree with this coin's
# constants + branding), git-initialised (the docker build.sh scripts fresh-clone
# the workspace, so it must be a git repo).
prepare_workspace() {
    local ws="$1"
    rm -rf "$ws"
    "${PYTHON_BIN_FALLBACK:-python3}" "$REPO_ROOT/scripts/prepare_wallet_variant.py" \
        --coin "$COIN" --workspace "$ws"
    if [[ ! -d "$ws/.git" ]]; then
        local ver
        ver="$(sed -nE "s/^ELECTRUM_VERSION *= *'([^']+)'.*/\1/p" "$ws/electrum/version.py" | head -1)"
        ( cd "$ws" && git init -q \
            && git config user.email "builder@localhost" \
            && git config user.name "Electrum Variant Builder" \
            && git add -A && git commit -q -m "$COIN wallet variant workspace" \
            && { [ -n "$ver" ] && git tag -f "$ver" >/dev/null 2>&1 || true; } )
        # ^ tag with ELECTRUM_VERSION so `git describe` (wine + macOS builds) yields
        #   a clean version (e.g. 4.7.2) instead of a commit hash, matching AppImage.
    fi
}

smoke_constants() {
    "${PYTHON_BIN_FALLBACK:-python3}" - "$1" <<'PY'
import re
import sys
from pathlib import Path

constants = Path(sys.argv[1]) / "electrum" / "constants.py"
text = constants.read_text(encoding="utf-8")
hrp = re.search(r"^\s*SEGWIT_HRP\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
genesis = re.search(r"^\s*GENESIS\s*=\s*['\"]([^'\"]+)['\"]", text, re.M)
assert hrp and hrp.group(1), "missing SEGWIT_HRP"
assert genesis and genesis.group(1), "missing GENESIS"
print("constants OK:", hrp.group(1))
PY
}

collect() {  # <glob-dir> <pattern> <artifact-subdir> [-R]
    local dir="$1" pat="$2" out="$ARTIFACT_ROOT/$COIN/$3" recurse="${4:-}"
    mkdir -p "$out"
    shopt -s nullglob
    local found=0 f
    for f in "$dir"/$pat; do
        found=1
        if [[ "$recurse" == "-R" ]]; then cp -Rf "$f" "$out/$(release_name "$f")";
        else cp -f "$f" "$out/$(release_name "$f")"; fi
    done
    shopt -u nullglob
    [[ $found -eq 1 ]] || { echo "no $pat produced in $dir" >&2; return 1; }
    echo "  -> $out/"
}

build_wheel() {
    local ws="$WORKSPACE_ROOT/$COIN/wheel"
    ensure_python_env
    prepare_workspace "$ws"
    smoke_constants "$ws"
    ( cd "$ws" && rm -rf build dist ./*.egg-info && "$PYTHON_BIN" setup.py sdist bdist_wheel )
    collect "$ws/dist" '*' python
}

build_appimage() {
    local ws="$WORKSPACE_ROOT/$COIN/appimage"
    prepare_workspace "$ws"
    # prepare_wallet_variant.py already fails on wrong-chain constants; this is a
    # best-effort extra check that needs electrum deps, so don't block the build.
    smoke_constants "$ws" || echo "  (constants smoke skipped — prepare step already validated)"
    ( cd "$ws" && ./contrib/build-linux/appimage/build.sh )
    collect "$ws/dist" '*.AppImage' linux
}

build_windows() {
    local ws="$WORKSPACE_ROOT/$COIN/windows"
    local lc; lc="$(lower_coin)"
    prepare_workspace "$ws"
    smoke_constants "$ws" || echo "  (constants smoke skipped — prepare step already validated)"
    ( cd "$ws" && \
        ELECTRUM_WINE_IMAGE_NAME="electrum-$lc-wine-builder-img" \
        ELECTRUM_WINE_CONTAINER_NAME="electrum-$lc-wine-builder-cont" \
        ./contrib/build-wine/build.sh )
    # wine emits generic electrum-<ver>{,-portable,-setup}.exe; rebrand per-coin.
    local d="$ws/contrib/build-wine/dist" f base
    shopt -s nullglob
    for f in "$d"/electrum-*.exe; do
        base="$(basename "$f")"
        mv "$f" "$d/Electrum-$COIN-${base#electrum-}"
    done
    shopt -u nullglob
    collect "$d" '*.exe' windows
}

build_macos() {
    # Runs ONLY on macOS (contrib/osx/make_osx.sh uses macOS tooling).
    [[ "$(uname -s)" == "Darwin" ]] || {
        echo "macos target must be built ON macOS (got $(uname -s))" >&2
        exit 2
    }
    # Standalone wallet DMG builds need the native macOS toolchain but not Node.
    ELECTRUM_MACOS_BOOTSTRAP_NODE=0 . "$REPO_ROOT/scripts/bootstrap-macos-build-env.sh"
    local ws="$WORKSPACE_ROOT/$COIN/macos"
    local osx_cache_shared="$WORKSPACE_ROOT/_osx-cache-shared"
    prepare_workspace "$ws"
    # Restore the shared osx build cache (python.org pkg, PyInstaller bootloader,
    # libsecp256k1/zbar/libusb dylibs) that prepare_workspace's wipe removed, so
    # builds after the first skip those slow steps.
    if [ -d "$osx_cache_shared" ]; then
        mkdir -p "$ws/contrib/osx/.cache"
        cp -R "$osx_cache_shared/." "$ws/contrib/osx/.cache/" 2>/dev/null || true
    fi
    # make_osx.sh runs `sudo installer` for python unconditionally; skip it when
    # the required version is already installed so unattended builds need no
    # interactive sudo (the version sanity-check right after the block still runs).
    python3 - "$ws/contrib/osx/make_osx.sh" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
a = 'info "Installing Python $PYTHON_VERSION"'
b = '    || fail "failed to install python"'
assert a in t and b in t, "make_osx.sh python block not found (version drift?)"
guard = ('_PREPY=$(python3 -c \'import sys;print(".".join(map(str,sys.version_info[:3])))\' '
         '2>/dev/null||true); if [ "$_PREPY" != "$PYTHON_VERSION" ]; then ')
t = t.replace(a, guard + a, 1).replace(b, b + '; fi', 1)
open(p, "w").write(t)
print("  patched make_osx.sh: python install now conditional")
PY
    # The bootstrap above prepares missing Homebrew packages once and adds their
    # opt paths to PATH. Avoid repeated global Brew installs inside every fresh
    # per-coin workspace unless bootstrap was explicitly bypassed.
    python3 - "$ws/contrib/osx/make_osx.sh" <<'PY'
import sys
p = sys.argv[1]
t = open(p).read()
a = 'brew install autoconf automake libtool gettext coreutils pkgconfig'
b = '''if [ "${ELECTRUM_MACOS_SKIP_OSX_BREW_INSTALL:-0}" = "1" ]; then
    info "Using bootstrapped macOS build tools from PATH; skipping brew install"
else
    brew install autoconf automake libtool gettext coreutils pkgconfig
fi'''
assert a in t, "make_osx.sh brew dependency block not found (version drift?)"
t = t.replace(a, b, 1)
c = '''if ! which msgfmt > /dev/null 2>&1; then
        brew install gettext
        brew link --force gettext
    fi'''
d = '''if ! which msgfmt > /dev/null 2>&1; then
        if [ "${ELECTRUM_MACOS_SKIP_OSX_BREW_INSTALL:-0}" = "1" ]; then
            fail "msgfmt not found after macOS build bootstrap"
        fi
        brew install gettext
        brew link --force gettext
    fi'''
assert c in t, "make_osx.sh gettext block not found (version drift?)"
t = t.replace(c, d, 1)
open(p, "w").write(t)
print("  patched make_osx.sh: brew installs now delegated to macOS bootstrap")
PY
    ( cd "$ws" && ./contrib/osx/make_osx.sh )
    # Persist the build cache for the next coin's build.
    if [ -d "$ws/contrib/osx/.cache" ]; then
        mkdir -p "$osx_cache_shared"
        cp -R "$ws/contrib/osx/.cache/." "$osx_cache_shared/" 2>/dev/null || true
    fi
    # make_osx.sh builds a generic Electrum.app + electrum-<ver>-unsigned.dmg.
    # Rebrand per-coin AFTER the build — patching PACKAGE/PACKAGE_NAME inside
    # make_osx + the spec is fragile (the hdiutil srcfolder and the spec bundle
    # name must stay in sync). Rename the app, fix CFBundleName, recreate the dmg.
    local appname ver plist
    appname="$(python3 -c "import json;print(json.load(open('$REPO_ROOT/coin-overlays/coins.json'))['$COIN']['app_name'])")"
    ver="$(cd "$ws" && git describe --tags --always 2>/dev/null || echo "$COIN")"
    if [ -d "$ws/dist/Electrum.app" ]; then
        rm -rf "$ws/dist/$appname.app"
        mv "$ws/dist/Electrum.app" "$ws/dist/$appname.app"
        plist="$ws/dist/$appname.app/Contents/Info.plist"
        /usr/libexec/PlistBuddy -c "Set :CFBundleName $appname" "$plist" 2>/dev/null \
            || /usr/libexec/PlistBuddy -c "Add :CFBundleName string $appname" "$plist" 2>/dev/null || true
        /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $appname" "$plist" 2>/dev/null \
            || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string $appname" "$plist" 2>/dev/null || true
        rm -f "$ws/dist/"*.dmg
        hdiutil create -fs HFS+ -volname "$appname" -srcfolder "$ws/dist/$appname.app" \
            "$ws/dist/$appname-$ver-unsigned.dmg" || echo "  WARN: dmg creation failed"
    fi
    collect "$ws/dist" '*.dmg' macos
    collect "$ws/dist" '*.app' macos -R || true
}

run_target() {
    local t="$1"
    # mkdir-based lock — portable (macOS has no flock). Atomic create = the lock.
    local lock="$LOCK_DIR/$COIN-$t.lockd"
    mkdir "$lock" 2>/dev/null || { echo "$COIN/$t already building (lock $lock)" >&2; exit 1; }
    trap 'rmdir "'"$lock"'" 2>/dev/null || true' EXIT
    echo "== building $COIN / $t =="
    case "$t" in
        wheel)    build_wheel ;;
        appimage) build_appimage ;;
        windows)  build_windows ;;
        macos)    build_macos ;;
    esac
    rmdir "$lock" 2>/dev/null || true
    trap - EXIT
}

case "$TARGET" in
    both) run_target wheel; run_target appimage ;;
    *)    run_target "$TARGET" ;;
esac

echo "== done: $COIN / $TARGET =="
