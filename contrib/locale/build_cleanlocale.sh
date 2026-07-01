#!/bin/bash

set -e

CONTRIB_LOCALE="$(dirname "$(realpath "$0" 2> /dev/null || grealpath "$0")")"
CONTRIB="$CONTRIB_LOCALE"/..
PROJECT_ROOT="$CONTRIB"/..

cd "$PROJECT_ROOT"
git submodule update --init electrum/locale || true

LOCALE="$PROJECT_ROOT/electrum/locale/"
if [ ! -d "$LOCALE/locale" ]; then
    echo "missing locale source directory: $LOCALE/locale" >&2
    echo "run: git submodule update --init electrum/locale" >&2
    exit 1
fi

LOCALE_TOP="$(git -C "$LOCALE" rev-parse --show-toplevel 2>/dev/null || true)"
LOCALE_REAL="$(realpath "$LOCALE" 2> /dev/null || grealpath "$LOCALE")"
if [ "$LOCALE_TOP" = "$LOCALE_REAL" ]; then
    git -C "$LOCALE" clean -ffxd
    git -C "$LOCALE" reset --hard
else
    echo "locale files are vendored in this build workspace; skipping submodule clean/reset"
fi
"$CONTRIB_LOCALE/build_locale.sh" "$LOCALE/locale" "$LOCALE/locale"
