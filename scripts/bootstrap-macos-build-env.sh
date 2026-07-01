#!/usr/bin/env bash
# Prepare macOS build tools with repo-local caches where practical.
#
# Source this file from build scripts. It intentionally keeps Node/npm/Electron
# build tooling under .build-tools/macos instead of requiring a global Node
# install. Native Apple and Homebrew build tools are still host-level tools; the
# script installs missing Brew packages only when needed.
set -euo pipefail

if [ "$(uname -s 2>/dev/null || printf unknown)" != "Darwin" ]; then
    return 0 2>/dev/null || exit 0
fi

_bootstrap_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="${REPO_ROOT:-$_bootstrap_dir}"
MACOS_TOOLS_DIR="${ELECTRUM_MACOS_TOOLS_DIR:-$REPO_ROOT/.build-tools/macos}"
MACOS_CACHE_DIR="${ELECTRUM_MACOS_CACHE_DIR:-$MACOS_TOOLS_DIR/cache}"
MACOS_NODE_VERSION="${ELECTRUM_MACOS_NODE_VERSION:-22.23.1}"
MACOS_ALLOW_BREW_INSTALL="${ELECTRUM_MACOS_ALLOW_BREW_INSTALL:-1}"
MACOS_BOOTSTRAP_NODE="${ELECTRUM_MACOS_BOOTSTRAP_NODE:-1}"
export ELECTRUM_MACOS_TOOLS_DIR="$MACOS_TOOLS_DIR"
export ELECTRUM_MACOS_CACHE_DIR="$MACOS_CACHE_DIR"
export ELECTRUM_MACOS_NODE_VERSION="$MACOS_NODE_VERSION"

mkdir -p "$MACOS_TOOLS_DIR" "$MACOS_CACHE_DIR"

macos_die() {
    echo "macOS build bootstrap: $*" >&2
    return 1 2>/dev/null || exit 1
}

prepend_path_if_dir() {
    [ -d "$1" ] || return 0
    case ":$PATH:" in
        *":$1:"*) ;;
        *) PATH="$1:$PATH" ;;
    esac
}

require_host_cmd() {
    command -v "$1" >/dev/null 2>&1 || macos_die "$1 is required for macOS builds"
}

require_host_cmd git
require_host_cmd curl
require_host_cmd tar
require_host_cmd shasum
require_host_cmd xcodebuild
require_host_cmd hdiutil

if ! command -v brew >/dev/null 2>&1; then
    macos_die "Homebrew is required for native macOS build tools. Install Homebrew once, then rerun the build."
fi

append_brew_paths() {
    local pkg prefix
    for pkg in "$@"; do
        prefix="$(brew --prefix "$pkg" 2>/dev/null || true)"
        [ -n "$prefix" ] || continue
        prepend_path_if_dir "$prefix/bin"
        prepend_path_if_dir "$prefix/sbin"
        prepend_path_if_dir "$prefix/libexec/gnubin"
    done
}

append_brew_paths autoconf automake libtool gettext coreutils pkgconf pkg-config pkgconfig

install_brew_pkg() {
    local pkg="$1"
    if [ "$MACOS_ALLOW_BREW_INSTALL" != "1" ]; then
        macos_die "missing $2. Install Homebrew package '$pkg' or rerun with ELECTRUM_MACOS_ALLOW_BREW_INSTALL=1"
    fi
    echo "== installing missing macOS build dependency: $pkg =="
    brew install "$pkg"
}

ensure_brew_cmd() {
    local cmd="$1"
    shift
    local pkg
    if command -v "$cmd" >/dev/null 2>&1; then
        return 0
    fi
    for pkg in "$@"; do
        if install_brew_pkg "$pkg" "$cmd"; then
            append_brew_paths "$pkg"
            command -v "$cmd" >/dev/null 2>&1 && return 0
        fi
    done
    macos_die "could not prepare required command: $cmd"
}

ensure_brew_cmd autoreconf autoconf
ensure_brew_cmd automake automake
ensure_brew_cmd glibtoolize libtool
ensure_brew_cmd msgfmt gettext
ensure_brew_cmd grealpath coreutils
ensure_brew_cmd pkg-config pkgconf pkg-config pkgconfig

ensure_local_node() {
    [ "$MACOS_BOOTSTRAP_NODE" = "1" ] || return 0

    local machine node_arch node_name node_dir tarball sums
    machine="$(uname -m)"
    case "$machine" in
        x86_64) node_arch="x64" ;;
        arm64) node_arch="arm64" ;;
        *) macos_die "unsupported macOS architecture for local Node: $machine" ;;
    esac

    node_name="node-v$MACOS_NODE_VERSION-darwin-$node_arch"
    node_dir="$MACOS_TOOLS_DIR/$node_name"
    tarball="$MACOS_CACHE_DIR/$node_name.tar.gz"
    sums="$MACOS_CACHE_DIR/SHASUMS256-node-$MACOS_NODE_VERSION.txt"

    if [ ! -x "$node_dir/bin/node" ]; then
        mkdir -p "$MACOS_CACHE_DIR"
        if [ ! -f "$tarball" ]; then
            curl -fL "https://nodejs.org/dist/v$MACOS_NODE_VERSION/$node_name.tar.gz" -o "$tarball"
        fi
        if [ ! -f "$sums" ]; then
            curl -fL "https://nodejs.org/dist/v$MACOS_NODE_VERSION/SHASUMS256.txt" -o "$sums"
        fi
        ( cd "$MACOS_CACHE_DIR" && grep " $node_name.tar.gz\$" "$(basename "$sums")" | shasum -a 256 -c - )
        rm -rf "$node_dir"
        tar -xzf "$tarball" -C "$MACOS_TOOLS_DIR"
    fi

    prepend_path_if_dir "$node_dir/bin"
    export npm_config_cache="${npm_config_cache:-$MACOS_TOOLS_DIR/npm-cache}"
    export ELECTRON_CACHE="${ELECTRON_CACHE:-$MACOS_TOOLS_DIR/electron-cache}"
    export ELECTRON_BUILDER_CACHE="${ELECTRON_BUILDER_CACHE:-$MACOS_TOOLS_DIR/electron-builder-cache}"
    mkdir -p "$npm_config_cache" "$ELECTRON_CACHE" "$ELECTRON_BUILDER_CACHE"
}

ensure_local_node

# The per-coin macOS workspace script is patched to honor this and avoid rerunning
# global Brew installs after this bootstrap has already prepared the tools.
export ELECTRUM_MACOS_SKIP_OSX_BREW_INSTALL=1
export PATH
