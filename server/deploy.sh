#!/usr/bin/env bash
# server/deploy.sh — friendly shell driver for the Blakestream ElectrumX server tier.
#
# Wraps the canonical Python implementation (server/deploy-electrumx.py) with:
#   1. State detection on the host (upstream coin daemons, existing ElectrumX).
#   2. A decision matrix that chooses build / pull / skip / abort based on state.
#   3. Optional Let's Encrypt deploy-hook installation for automated renewals.
#   4. `.env` autoload so the operator does not retype the same overrides each time.
#
# Everything else (daemon RPC discovery, compose generation, redacted summary,
# docker compose up) is delegated to deploy-electrumx.py. This script is the
# friendly entry point; deploy-electrumx.py is the workhorse.
#
# Why a separate shell driver alongside the Python script:
#   - `deploy-electrumx.py` was written before state detection was a requirement.
#     Bolting state detection into argparse + Python would muddy what is otherwise
#     a clean "discover and emit compose" tool.
#   - Shell handles the host-poking (pgrep, docker, systemctl, manifest inspect)
#     more idiomatically than Python, and shells out anyway.
#   - The decision matrix is operationally easier to audit when it's plain bash.

set -Eeuo pipefail
IFS=$'\n\t'

# --------------------------------------------------------------------------- #
# Paths + defaults
# --------------------------------------------------------------------------- #

# Resolve where this script lives (server/) and the repo root (its parent).
# Using BASH_SOURCE keeps the script portable: it works the same whether it's
# invoked from $PWD/server/deploy.sh, ../server/deploy.sh, or via an absolute
# path from a hook. No hardcoded /home/<user>/ assumptions.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Defaults can be overridden by .env, environment, or CLI flags (highest wins).
DEPLOY_DIR_DEFAULT="${SCRIPT_DIR}/deploy"
IMAGE_TAG_DEFAULT="sidgrip/electrumx-blakestream:25.2"
DAEMON_HOST_DEFAULT="127.0.0.1"
ELECTRUMX_UID_DEFAULT="0"
ELECTRUMX_GID_DEFAULT="0"
FEE_FALLBACK_DEFAULT="0.00002"
PORT_OFFSET_DEFAULT="0"

# --------------------------------------------------------------------------- #
# CLI flags + usage
# --------------------------------------------------------------------------- #

MODE=""             # "fresh" or "update"; required unless --help
DRY_RUN=0
BUILD_LOCAL=0
INSTALL_CERT_HOOK=1
OPEN_FIREWALL=0
ENV_FILE=""

usage() {
  cat <<'EOF'
Usage: server/deploy.sh <mode> [options]

Stand up or refresh the six per-coin ElectrumX services that back the
Blakestream wallets. Detects the host state first, prints a decision matrix
row, then either proceeds or aborts with explicit guidance.

Modes:
  --fresh         First-time install. Refuses to run if ElectrumX is already
                  present (either as a container or a native daemon) — use
                  --update instead.
  --update        Pull or rebuild the image, recreate the six services in
                  place. Idempotent when the image is already current.

Options:
  --build-local         Build the image locally via ../build-electrumx.sh
                        instead of pulling from a registry. Useful for forks
                        or offline hosts.
  --pull                Force `docker pull` even if the local digest already
                        matches the remote manifest (`--update` only).
  --dry-run             Print the actions and decisions; touch nothing.
  --no-cert-hook        Skip writing the Let's Encrypt deploy hook. Use when
                        the operator manages cert renewal another way (e.g.
                        terminating TLS at a load balancer).
  --open-firewall      Open generated public Electrum TCP/SSL ports in ufw.
  --env-file PATH       Source this .env. By default the script searches
                        $PWD/.env, ${DEPLOY_DIR}/.env, then this directory.
  --port-offset N       Add N to Electrum TCP/SSL/admin ports. Useful for
                        staging tests where production ports must stay free.
  -h, --help            Show this help.

Environment (also settable via .env):
  REPORT_HOST           Public hostname clients connect to (REPORT_SERVICES).
  DAEMON_HOST           Host that daemons listen on as seen from the container
                        when explicit DAEMON_URL_<COIN> values are used
                        (default: 127.0.0.1).
  DEPLOY_DIR            Where the compose, db/, and ssl/ live.
  IMAGE_TAG             Image to pull or build. Default:
                        sidgrip/electrumx-blakestream:25.2
  BUILD_LOCAL           1 = build instead of pull (same as --build-local).
  ELECTRUMX_DEFAULT_FEE_BTC_KVB
                        Fee fallback for chains whose daemons return -1 from
                        estimatefee. Default: 0.00002.
  ELECTRUMX_UID,
  ELECTRUMX_GID         Container user for copied TLS key ownership. The stock
                        25.2 image runs as root; set these only for a non-root
                        patched image.
  PORT_OFFSET           Same as --port-offset.
  OPEN_FIREWALL         1 = open generated public Electrum ports in ufw.
  CERT_DIR              Source Let's Encrypt cert dir. Default:
                        /etc/letsencrypt/live/${REPORT_HOST}
  DAEMON_URL_<COIN>     Explicit per-coin RPC URL. Leave blank to let
                        deploy-electrumx.py auto-discover.

Exit codes:
  0   Success or successful dry-run.
  1   Generic failure.
  2   Bad CLI usage.
  3   Decision matrix aborted the run (state conflict).
EOF
}

# Boolean flags are 0/1 ints (matches deploy-production.sh convention).
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh)         MODE="fresh" ;;
    --update)        MODE="update" ;;
    --build-local)   BUILD_LOCAL=1 ;;
    --pull)          FORCE_PULL=1 ;;
    --dry-run)       DRY_RUN=1 ;;
    --no-cert-hook)  INSTALL_CERT_HOOK=0 ;;
    --open-firewall) OPEN_FIREWALL=1 ;;
    --env-file)
      ENV_FILE="${2:?--env-file requires a path}"
      shift
      ;;
    --port-offset)
      PORT_OFFSET="${2:?--port-offset requires an integer}"
      shift
      ;;
    -h|--help)       usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ -z "${MODE}" ]]; then
  echo "Specify --fresh or --update (see --help)." >&2
  exit 2
fi

FORCE_PULL="${FORCE_PULL:-0}"

# --------------------------------------------------------------------------- #
# Logging helpers — `==>` banners match the explorer's deploy-production.sh
# --------------------------------------------------------------------------- #

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf '\n!! %s\n' "$*" >&2; }
die()  { printf '\nXX %s\n' "$*" >&2; exit "${2:-1}"; }

# A dry-run-aware exec wrapper. Prints the command in `+` form (matching the
# explorer's deploy script) and either runs it or prints a `[dry-run]` notice.
run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    "$@"
  else
    printf '   [dry-run] not executed\n'
  fi
}

# --------------------------------------------------------------------------- #
# .env autoload
# --------------------------------------------------------------------------- #

# Search order matches the README: explicit --env-file, then the operator's
# CWD, then the DEPLOY_DIR, then this script's directory. Loading is shell-set
# semantics (export every assignment) so .env variables become visible to both
# this script and any subprocess (including envsubst).
load_env_file() {
  local candidate
  for candidate in \
      "${ENV_FILE}" \
      "${PWD}/.env" \
      "${DEPLOY_DIR:-${DEPLOY_DIR_DEFAULT}}/.env" \
      "${SCRIPT_DIR}/.env"; do
    [[ -z "${candidate}" ]] && continue
    if [[ -f "${candidate}" ]]; then
      log "loading env from ${candidate}"
      # shellcheck disable=SC1090
      set -a; source "${candidate}"; set +a
      return 0
    fi
  done
  log "no .env found — using built-in defaults"
}
load_env_file

# Now layer in defaults for anything still unset. `${VAR:=default}` only
# assigns if VAR is empty/unset, so explicit env-file values win.
: "${DEPLOY_DIR:=${DEPLOY_DIR_DEFAULT}}"
: "${IMAGE_TAG:=${IMAGE_TAG_DEFAULT}}"
: "${DAEMON_HOST:=${DAEMON_HOST_DEFAULT}}"
: "${ELECTRUMX_UID:=${ELECTRUMX_UID_DEFAULT}}"
: "${ELECTRUMX_GID:=${ELECTRUMX_GID_DEFAULT}}"
: "${ELECTRUMX_DEFAULT_FEE_BTC_KVB:=${FEE_FALLBACK_DEFAULT}}"
: "${PORT_OFFSET:=${PORT_OFFSET_DEFAULT}}"
: "${OPEN_FIREWALL:=${OPEN_FIREWALL}}"
: "${REPORT_HOST:=}"
: "${CERT_DIR:=}"

# Resolve DEPLOY_DIR to an absolute path so all later relative resolutions
# (compose mounts, deploy-electrumx.py, the cert hook) agree on what it means.
DEPLOY_DIR="${DEPLOY_DIR%/}"
if [[ "${DEPLOY_DIR}" != /* ]]; then
  DEPLOY_DIR="${PWD}/${DEPLOY_DIR#./}"
fi

# CERT_DIR default depends on REPORT_HOST; resolve after env load.
if [[ -z "${CERT_DIR}" && -n "${REPORT_HOST}" ]]; then
  CERT_DIR="/etc/letsencrypt/live/${REPORT_HOST}"
fi

# Honor the env-form `BUILD_LOCAL=1` even when the CLI flag isn't passed.
if [[ "${BUILD_LOCAL:-0}" == "1" ]]; then
  BUILD_LOCAL=1
fi
if [[ "${OPEN_FIREWALL:-0}" == "1" ]]; then
  OPEN_FIREWALL=1
fi

# --------------------------------------------------------------------------- #
# State detection — three tiers
# --------------------------------------------------------------------------- #
#
# Tier 1: upstream coin daemons. ElectrumX is a thin protocol layer in front
#   of a full node. Without bitcoind-style daemons present (and txindex-synced)
#   there is nothing for ElectrumX to do, and discovery in deploy-electrumx.py
#   will fail anyway. We surface this gate early so the operator sees a clear
#   "set up daemons first" message instead of a wall of RPC errors.
#
# Tier 2: existing ElectrumX. Running containers under a recognisable name
#   pattern (blc / bbtc / elt / lit / pho / umo, or electrumx-<coin>) tell us
#   to either skip (already up to date) or warn (we'd clobber data on --fresh).
#   A native `electrumx_server` process is always an abort — auto-migration
#   risks DB corruption if the old service is killed mid-flush.
#
# Tier 3: image freshness. For container mode, compare the registry's digest
#   for ${IMAGE_TAG} against the local image's digest. If they match, no pull
#   is needed; --update can short-circuit to a no-op.

UPSTREAM_FOUND=0
UPSTREAM_DETAIL=""

is_container_pid() {
  local pid="$1"
  [[ -r "/proc/${pid}/cgroup" ]] || return 1
  grep -qaE '(docker|containerd|kubepods|libpod)' "/proc/${pid}/cgroup"
}

filter_host_processes() {
  local line pid
  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    pid="${line%% *}"
    [[ "${pid}" =~ ^[0-9]+$ ]] || continue
    is_container_pid "${pid}" && continue
    printf '%s\n' "${line}"
  done
}

# Names of the six coin daemons, native binary form. These match the actual
# Bitcoin Core fork binary names — `blakecoind`, `electrond`, etc. — not the
# friendly ticker names. The boundary `[/[:space:]]...($|[[:space:]])` ensures
# we match real binaries (/path/blakecoind, or `blakecoind` standalone) but
# NOT shell command lines that contain the string in quotes (e.g. a test
# command running this very script).
NATIVE_DAEMON_REGEX='[/[:space:]](blakecoind|blakebitcoind|electrond|lithiumd|photond|universalmoleculed)($|[[:space:]])'

detect_upstream_native() {
  local matches count
  if matches="$(pgrep -af "${NATIVE_DAEMON_REGEX}" 2>/dev/null)"; then
    # Exclude pgrep's own process (the binary form, in case pgrep is run from
    # a path with the daemon name in it) and our own deploy.sh process. The
    # boundary regex already excludes most self-matches; this is belt-and-braces.
    matches="$(printf '%s\n' "${matches}" | grep -vE '(pgrep|deploy\.sh|electrum-bootstrap)' || true)"
    matches="$(printf '%s\n' "${matches}" | filter_host_processes || true)"
    if [[ -n "${matches}" ]]; then
      UPSTREAM_FOUND=1
      count="$(printf '%s\n' "${matches}" | grep -c '^' || true)"
      UPSTREAM_DETAIL+="  native: ${count} process(es)"$'\n'
    fi
  fi
}

# Container daemons follow the bsx-<coin> or <coin> naming convention used in
# the Blakestream explorer's docker-compose. We don't enforce a specific name
# — any container whose image contains "blake" or "electron" or "lithium" etc.
# is a hint that upstream exists. deploy-electrumx.py will do the precise
# discovery.
detect_upstream_container() {
  local out
  if ! command -v docker >/dev/null 2>&1; then return; fi
  out="$(docker ps --format '{{.Names}} {{.Image}}' 2>/dev/null \
    | grep -iE '(blakecoin|blakebitcoin|electron|lithium|photon|universalmolecule)' \
    || true)"
  if [[ -n "${out}" ]]; then
    UPSTREAM_FOUND=1
    UPSTREAM_DETAIL+="  container: ${out//$'\n'/$'\n  container: '}"$'\n'
  fi
}

ELECTRUMX_NATIVE_RUNNING=0
ELECTRUMX_CONTAINERS=""

detect_electrumx_native() {
  local matches
  # Same boundary trick as upstream — only match real `electrumx_server`
  # binaries (/path/electrumx_server or standalone), never command lines that
  # happen to contain the string in quotes.
  if matches="$(pgrep -af '[/[:space:]]electrumx_server($|[[:space:]])' 2>/dev/null)"; then
    matches="$(printf '%s\n' "${matches}" | grep -vE '(pgrep|deploy\.sh|electrum-bootstrap)' || true)"
    matches="$(printf '%s\n' "${matches}" | filter_host_processes || true)"
    if [[ -n "${matches}" ]]; then
      ELECTRUMX_NATIVE_RUNNING=1
      UPSTREAM_DETAIL+="  electrumx (native): ${matches}"$'\n'
    fi
  fi
  # systemd-managed natives may not show in pgrep on some distros if the unit
  # is `Type=notify` and the worker hasn't forked yet; check units too.
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-units 'electrumx*' --no-legend --no-pager 2>/dev/null \
       | grep -q '\bactive\b'; then
      ELECTRUMX_NATIVE_RUNNING=1
    fi
  fi
}

detect_electrumx_containers() {
  if ! command -v docker >/dev/null 2>&1; then return; fi
  # Match both naming conventions: bare service names from server/docker-compose.yml
  # (blc, bbtc, ...) and the prefixed `electrumx-<coin>` form some operators use.
  # We filter on the image too, to avoid catching unrelated containers that
  # happen to share a name like `blc`.
  ELECTRUMX_CONTAINERS="$(docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' \
    2>/dev/null \
    | awk -F'\t' '$2 ~ /electrumx-blakestream/ { print }' \
    || true)"
}

# Image freshness — compares the registry manifest's image digest to the local
# image's digest. Returns 0 if up to date, 1 if drift detected, 2 if we can't
# determine (network, image not yet pulled, etc.).
image_is_current() {
  if ! command -v docker >/dev/null 2>&1; then return 2; fi
  local local_digest remote_digest
  local_digest="$(docker image inspect "${IMAGE_TAG}" --format '{{index .RepoDigests 0}}' 2>/dev/null \
    | sed 's|.*@||' || true)"
  if [[ -z "${local_digest}" ]]; then return 2; fi
  remote_digest="$(docker manifest inspect "${IMAGE_TAG}" 2>/dev/null \
    | grep -oE '"digest": "sha256:[a-f0-9]+"' | head -1 \
    | sed 's|.*: "||; s|"||' || true)"
  if [[ -z "${remote_digest}" ]]; then return 2; fi
  if [[ "${local_digest}" == "${remote_digest}" ]]; then return 0; fi
  return 1
}

log "state detection"
detect_upstream_native
detect_upstream_container
detect_electrumx_native
detect_electrumx_containers

if [[ "${UPSTREAM_FOUND}" -eq 1 ]]; then
  printf 'upstream coin daemons: PRESENT\n%s' "${UPSTREAM_DETAIL}"
else
  printf 'upstream coin daemons: MISSING\n'
fi

if [[ -n "${ELECTRUMX_CONTAINERS}" ]]; then
  printf 'electrumx containers running:\n%s\n' "${ELECTRUMX_CONTAINERS}"
fi
if [[ "${ELECTRUMX_NATIVE_RUNNING}" -eq 1 ]]; then
  printf 'electrumx native: RUNNING (will abort)\n'
fi

# --------------------------------------------------------------------------- #
# Decision matrix
# --------------------------------------------------------------------------- #
#
# Each row prints WHY we landed there so the operator can audit the decision
# without re-reading the script. Refusing-to-clobber is on purpose: the
# operator must explicitly --update to recreate running containers, and must
# manually stop a native daemon before we touch its host.

DECISION=""
ACTION=""

if [[ "${UPSTREAM_FOUND}" -ne 1 ]]; then
  DECISION="abort"
  cat <<EOF

Decision matrix → ABORT (no upstream)
  Reason: ElectrumX needs full-node daemons to talk to. Discovery found
          neither native processes nor running containers for any of
          BLC / BBTC / ELT / LIT / PHO / UMO.
  Fix:    Start the daemons first (each with server=1 + txindex=1).
          Native default datadirs are ~/.<coin>/ (blakecoin, blakebitcoin,
          electron, lithium, photon, universalmolecule).
          Then re-run this script.
EOF
  exit 3
fi

if [[ "${ELECTRUMX_NATIVE_RUNNING}" -eq 1 ]]; then
  DECISION="abort"
  cat <<EOF

Decision matrix → ABORT (native ElectrumX)
  Reason: A native electrumx_server process is already running on this host.
          Auto-migrating to containers risks DB corruption if we kill the
          old process mid-flush. Native + containerised ElectrumX must not
          run side-by-side on the same DB.
  Fix:    Stop the native service cleanly first (e.g. \`systemctl stop electrumx\`
          or \`kill -INT <pid>\`), wait until the process exits, then re-run.
EOF
  exit 3
fi

if [[ -n "${ELECTRUMX_CONTAINERS}" ]]; then
  case "${MODE}" in
    fresh)
      DECISION="abort"
      cat <<EOF

Decision matrix → ABORT (containers exist, --fresh)
  Reason: ElectrumX containers from a prior deploy are still on this host.
          --fresh would clobber the existing compose + db/ tree.
  Fix:    Use --update to refresh in place, or
          \`cd ${DEPLOY_DIR} && docker compose down\` then re-run --fresh
          (this will wipe the live LevelDBs — operator's call).
EOF
      exit 3
      ;;
    update)
      if image_is_current && [[ "${FORCE_PULL}" -ne 1 ]]; then
        DECISION="skip"
        ACTION="noop"
        log "Decision matrix → SKIP (image already current; pass --pull to force)"
      else
        DECISION="recreate"
        ACTION="pull-and-recreate"
        log "Decision matrix → RECREATE (image drift, will pull + recreate in place)"
      fi
      ;;
  esac
else
  # No ElectrumX containers and no native daemon — fresh-install path.
  DECISION="install"
  ACTION="install"
  if [[ "${MODE}" == "update" ]]; then
    log "no ElectrumX found; --update will install as if --fresh"
  else
    log "Decision matrix → INSTALL (no ElectrumX present; first-time deploy)"
  fi
fi

# --------------------------------------------------------------------------- #
# Decision execution
# --------------------------------------------------------------------------- #

# Ensure REPORT_HOST is set for any path that actually deploys.
if [[ "${ACTION}" != "noop" && -z "${REPORT_HOST}" ]]; then
  die "REPORT_HOST is required for install/recreate (set in .env or environment)" 2
fi

# Build vs pull. BUILD_LOCAL=1 is for operators with local source patches or
# air-gapped hosts. The build script lives one level up from server/ at the
# project root; we never assume an absolute path.
fetch_image() {
  if [[ "${BUILD_LOCAL}" -eq 1 ]]; then
    log "building image locally via build-electrumx.sh"
    if [[ ! -x "${REPO_ROOT}/build-electrumx.sh" ]]; then
      die "build-electrumx.sh not found at ${REPO_ROOT}/build-electrumx.sh" 1
    fi
    run "${REPO_ROOT}/build-electrumx.sh" "${IMAGE_TAG}" --smoke --no-push
  else
    log "pulling image ${IMAGE_TAG}"
    run docker pull "${IMAGE_TAG}"
  fi
}

# Copy initial Let's Encrypt material into the compose ssl/ mount. The renewal
# hook keeps these files fresh later, but first deploy needs them before compose
# generation so deploy-electrumx.py can enable SSL.
sync_certs() {
  if [[ -z "${CERT_DIR}" || ! -f "${CERT_DIR}/fullchain.pem" || ! -f "${CERT_DIR}/privkey.pem" ]]; then
    log "no complete cert lineage found; SSL will stay off for generated compose"
    return
  fi
  local ssl_dst="${DEPLOY_DIR}/ssl"
  log "copying TLS certs from ${CERT_DIR} to ${ssl_dst}"
  local sudo_cmd=()
  if [[ "$(id -u)" -ne 0 ]]; then
    sudo_cmd=(sudo)
  fi
  run mkdir -p "${ssl_dst}"
  run "${sudo_cmd[@]}" install -m 0644 "${CERT_DIR}/fullchain.pem" "${ssl_dst}/fullchain.pem"
  run "${sudo_cmd[@]}" install -m 0640 "${CERT_DIR}/privkey.pem" "${ssl_dst}/privkey.pem"
  run "${sudo_cmd[@]}" chown "${ELECTRUMX_UID}:${ELECTRUMX_GID}" \
      "${ssl_dst}/fullchain.pem" "${ssl_dst}/privkey.pem"
}

# Compose generation defers to the existing Python script. We pass through the
# operator's REPORT_HOST + DEPLOY_DIR + image, plus --start unless this is a
# dry-run. The Python script handles daemon discovery, redacted summary
# writing, and `docker compose up -d`.
generate_compose() {
  local py_args=(
    --image "${IMAGE_TAG}"
    --deploy-dir "${DEPLOY_DIR}"
    --report-host "${REPORT_HOST}"
    --fee-fallback "${ELECTRUMX_DEFAULT_FEE_BTC_KVB}"
    --port-offset "${PORT_OFFSET}"
  )
  if [[ -f "${DEPLOY_DIR}/ssl/fullchain.pem" && -f "${DEPLOY_DIR}/ssl/privkey.pem" ]] \
     || [[ -n "${CERT_DIR}" && -f "${CERT_DIR}/fullchain.pem" && -f "${CERT_DIR}/privkey.pem" ]]; then
    py_args+=( --ssl on )
  else
    py_args+=( --ssl auto )
  fi
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    py_args+=( --dry-run )
  else
    py_args+=( --start --wait-ready 1800 )
  fi
  log "delegating compose generation to deploy-electrumx.py"
  printf '+'
  printf ' %q' "${SCRIPT_DIR}/deploy-electrumx.py" "${py_args[@]}"
  printf '\n'
  "${SCRIPT_DIR}/deploy-electrumx.py" "${py_args[@]}"
}

wait_for_services() {
  [[ "${DRY_RUN}" -eq 1 ]] && return
  local py_args=(
    --image "${IMAGE_TAG}"
    --deploy-dir "${DEPLOY_DIR}"
    --report-host "${REPORT_HOST}"
    --fee-fallback "${ELECTRUMX_DEFAULT_FEE_BTC_KVB}"
    --port-offset "${PORT_OFFSET}"
    --wait-ready 1800
  )
  if [[ -f "${DEPLOY_DIR}/ssl/fullchain.pem" && -f "${DEPLOY_DIR}/ssl/privkey.pem" ]] \
     || [[ -n "${CERT_DIR}" && -f "${CERT_DIR}/fullchain.pem" && -f "${CERT_DIR}/privkey.pem" ]]; then
    py_args+=( --ssl on )
  else
    py_args+=( --ssl auto )
  fi
  log "waiting for Electrum services to answer server.version"
  printf '+'
  printf ' %q' "${SCRIPT_DIR}/deploy-electrumx.py" "${py_args[@]}"
  printf '\n'
  "${SCRIPT_DIR}/deploy-electrumx.py" "${py_args[@]}"
}

# Cert hook installation. Renders the template via envsubst — only the four
# variables we need are substituted, so $RENEWED_LINEAGE inside the template
# survives intact for certbot to fill in at runtime.
install_cert_hook() {
  if [[ "${INSTALL_CERT_HOOK}" -eq 0 ]]; then
    log "skipping cert hook (--no-cert-hook)"
    return
  fi
  if [[ ! -d /etc/letsencrypt ]]; then
    log "skipping cert hook (no /etc/letsencrypt on this host)"
    return
  fi
  local tmpl="${SCRIPT_DIR}/templates/electrumx-renewal-hook.sh.tmpl"
  local dst="/etc/letsencrypt/renewal-hooks/deploy/${REPORT_HOST}-electrumx.sh"
  if [[ ! -f "${tmpl}" ]]; then
    warn "cert hook template missing at ${tmpl}; skipping"
    return
  fi
  log "installing cert renewal hook at ${dst}"
  # We need root to write into /etc/letsencrypt/; gate behind dry-run for tests.
  local rendered
  rendered="$(REPORT_HOST="${REPORT_HOST}" DEPLOY_DIR="${DEPLOY_DIR}" \
              ELECTRUMX_UID="${ELECTRUMX_UID}" ELECTRUMX_GID="${ELECTRUMX_GID}" \
              envsubst '${REPORT_HOST} ${DEPLOY_DIR} ${ELECTRUMX_UID} ${ELECTRUMX_GID}' \
              < "${tmpl}")"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf '   [dry-run] would write %d bytes to %s\n' "${#rendered}" "${dst}"
    return
  fi
  local sudo=""
  if [[ "$(id -u)" -ne 0 ]]; then sudo="sudo"; fi
  printf '%s\n' "${rendered}" | ${sudo} tee "${dst}" >/dev/null
  ${sudo} chmod 0755 "${dst}"
}

open_firewall() {
  if [[ "${OPEN_FIREWALL}" -ne 1 ]]; then
    return
  fi
  if ! command -v ufw >/dev/null 2>&1; then
    warn "ufw is not installed; skipping firewall port open"
    return
  fi
  if [[ ! -f "${DEPLOY_DIR}/docker-compose.yml" ]]; then
    warn "cannot open firewall; missing ${DEPLOY_DIR}/docker-compose.yml"
    return
  fi

  local ports port
  ports="$(grep -Eo '(tcp|ssl)://0\.0\.0\.0:[0-9]+' "${DEPLOY_DIR}/docker-compose.yml" \
    | sed -E 's/.*:([0-9]+)$/\1/' \
    | sort -n -u)"
  if [[ -z "${ports}" ]]; then
    warn "no public Electrum TCP/SSL ports found in generated compose"
    return
  fi

  log "opening Electrum service ports in ufw"
  while read -r port; do
    [[ -n "${port}" ]] || continue
    run ufw allow "${port}/tcp"
  done <<<"${ports}"
}

# Refresh-existing path: pull (or build) then docker compose up -d --force-recreate.
recreate_in_place() {
  if [[ ! -f "${DEPLOY_DIR}/docker-compose.yml" ]]; then
    die "expected ${DEPLOY_DIR}/docker-compose.yml from a prior deploy; not found" 1
  fi
  fetch_image
  log "recreating services in place"
  run docker compose -f "${DEPLOY_DIR}/docker-compose.yml" up -d --force-recreate \
      blc bbtc elt lit pho umo
}

case "${ACTION}" in
  noop)
    if [[ -n "${REPORT_HOST}" ]]; then
      sync_certs
      install_cert_hook
    else
      log "skipping cert hook refresh (REPORT_HOST is not set)"
    fi
    log "nothing else to do"
    ;;
  install)
    fetch_image
    sync_certs
    generate_compose
    open_firewall
    install_cert_hook
    log "install complete"
    ;;
  pull-and-recreate)
    sync_certs
    recreate_in_place
    wait_for_services
    open_firewall
    install_cert_hook
    log "update complete"
    ;;
  *)
    die "internal: unhandled action '${ACTION}'" 1
    ;;
esac
