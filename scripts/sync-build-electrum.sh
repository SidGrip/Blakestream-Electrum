#!/usr/bin/env bash
# Sync the canonical build-electrum.sh + README "Electrium Wallet" subsection into
# every BlakeStream 25.2 coin repo. Single source of truth = this multicoin repo
# (contrib/coin-repo/). Run from anywhere.
#
#   sync-build-electrum.sh [--check]
#
#   --check   report drift only; make no changes.
#
# Env: COIN_REPOS_ROOT (path to the six coin daemon repos)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/contrib/coin-repo/build-electrum.sh"
TMPL="$REPO_ROOT/contrib/coin-repo/README-electrium.md.tmpl"
HELPER="$REPO_ROOT/scripts/_sync_readme_electrium.py"
COIN_REPOS_ROOT="${COIN_REPOS_ROOT:-}"

[ -f "$SRC" ] || { echo "missing canonical $SRC" >&2; exit 1; }
[ -f "$TMPL" ] || { echo "missing template $TMPL" >&2; exit 1; }

DRYRUN=0; [ "${1:-}" = "--check" ] && DRYRUN=1

repos=()
for d in "$COIN_REPOS_ROOT"/*-0.25.2; do
    [ -f "$d/electrum/coin.env" ] && repos+=("$d")
done
[ "${#repos[@]}" -gt 0 ] || { echo "no coin repos with electrum/coin.env under $COIN_REPOS_ROOT" >&2; exit 1; }

for d in "${repos[@]}"; do
    name="$(basename "$d")"
    # read coin.env in a subshell so its vars do not leak
    cc=""; cn=""
    eval "$(. "$d/electrum/coin.env"; printf 'cc=%q cn=%q' "$COIN_CODE" "$COIN_NAME")"
    echo "== $name ($cc / $cn) =="

    if [ "$DRYRUN" = "1" ]; then
        if cmp -s "$SRC" "$d/build-electrum.sh" 2>/dev/null; then echo "  build-electrum.sh: up-to-date"
        else echo "  build-electrum.sh: DIFFERS / missing"; fi
        grep -q "BEGIN electrium-build" "$d/README.md" 2>/dev/null && echo "  README.md: section present" || echo "  README.md: section MISSING"
        continue
    fi

    cp -f "$SRC" "$d/build-electrum.sh"
    chmod +x "$d/build-electrum.sh"
    python3 "$HELPER" "$TMPL" "$d/README.md" "$cc" "$cn"
    cmp -s "$SRC" "$d/build-electrum.sh" && echo "  build-electrum.sh: synced + verified" \
        || { echo "  ERROR: copy mismatch for $name" >&2; exit 1; }
done

echo "== sync complete: ${#repos[@]} coin repos =="
