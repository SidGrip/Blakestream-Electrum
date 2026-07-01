#!/usr/bin/env bash
# server/enable-blockfilters.sh — one-shot fleet rollout to enable BIP 157/158
# compact block filters (blockfilterindex=1 + peerblockfilters=1) across the
# Blakestream daemon fleet, so the daemons can serve SPV / Neutrino wallets.
#
# Design shape (why this exists as a separate script instead of a
# deploy-daemons.sh --update):
#
#   deploy-daemons.sh --update rewrites the whole daemon .conf from a template,
#   which would erase any per-host settings not present in the template (e.g.
#   coinstatsindex=1 on production, docker-bridge rpcallowip ranges on the
#   ElectrumX hosts). This script does the opposite: it locates the existing
#   .conf, backs it up, idempotently appends the two new lines, and restarts
#   the affected container. Nothing else on the host is touched.
#
# Rollout topology:
#
#   For each --hosts entry, the script kicks off a per-host worker as a
#   background job. Each worker runs the six coins SEQUENTIALLY on its host,
#   ordered smallest → largest (BLC, LIT, PHO, BBTC, UMO, ELT) so that any
#   early breakage is caught fast. Only one coin at a time on a given host
#   is offline while its blockfilter index builds; the peer ElectrumX host
#   keeps serving that coin to wallet clients.
#
# Idempotent — a config that already has blockfilterindex=1 is left alone
# and the daemon is not restarted. Safe to re-run to catch stragglers.
#
# All paths and hosts are supplied at run time. No LAN IPs or private paths
# are baked in — this file is safe to push to a public repo.

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --------------------------------------------------------------------------- #
# Coin metadata — must match deploy-daemons.sh so both tools agree on what
# a "coin" is. Copied here (not sourced) so this script can run on hosts
# that don't have the full server/ tree available.
# --------------------------------------------------------------------------- #

# Order matters for the rollout: smallest chain first so a broken coin fails
# fast. Sizes as of the pre-rollout fleet audit (rough per-coin blocks/):
#   BLC ~1.5G, LIT ~2.7G, PHO ~3.2G, BBTC ~3.4G, UMO ~5.4G, ELT ~9G
COIN_ORDER=(blc lit pho bbtc umo elt)

declare -A DATADIR_NAME=(
  [blc]=".blakecoin"
  [bbtc]=".blakebitcoin"
  [elt]=".electron"
  [lit]=".lithium"
  [pho]=".photon"
  [umo]=".universalmolecule"
)
declare -A CONF_NAME=(
  [blc]="blakecoin.conf"
  [bbtc]="blakebitcoin.conf"
  [elt]="electron.conf"
  [lit]="lithium.conf"
  [pho]="photon.conf"
  [umo]="universalmolecule.conf"
)
declare -A CLI_BIN=(
  [blc]="blakecoin-cli"
  [bbtc]="blakebitcoin-cli"
  [elt]="electron-cli"
  [lit]="lithium-cli"
  [pho]="photon-cli"
  [umo]="universalmolecule-cli"
)
declare -A IMAGE_MATCH=(
  [blc]="blakecoin"
  [bbtc]="blakebitcoin"
  [elt]="electron"
  [lit]="lithium"
  [pho]="photon"
  [umo]="universalmolecule"
)

# --------------------------------------------------------------------------- #
# CLI + defaults
# --------------------------------------------------------------------------- #

HOSTS_CSV="${BLAKESTREAM_DAEMON_HOSTS:-}"
DAEMON_DATA_ROOT_DEFAULT="/root"
DRY_RUN=0
DISABLE=0
STOP_TIMEOUT=600
POLL_TIMEOUT=14400   # 4 h per coin (worst-case ELT)
POLL_INTERVAL=30
LOG_DIR="${SCRIPT_DIR}/logs"

usage() {
  cat <<'EOF'
Usage: server/enable-blockfilters.sh --hosts HOST1[,HOST2,...] [options]

Roll out blockfilterindex=1 + peerblockfilters=1 to the six Blakestream coin
daemon containers on each host. Per-host runs in parallel; per-coin runs
sequentially within each host, smallest → largest.

Required:
  --hosts USER@HOST[,USER@HOST,...]
                        Comma-separated ssh targets. Env: BLAKESTREAM_DAEMON_HOSTS

Options:
  --data-root PATH      Parent directory of the daemon datadirs on each host.
                        Default: /root  (so datadirs are /root/.blakecoin etc.)
  --dry-run             Print planned actions per host and exit 0. Touches nothing.
  --disable             Reverse mode: strip the two lines and restart. Symmetric
                        rollback path.
  --stop-timeout SEC    docker stop -t value used for clean shutdown. Default: 600.
  --poll-timeout SEC    Max time to wait for basicblockfilter.synced=true per
                        coin. On timeout the coin is logged and the host worker
                        moves on. Default: 14400 (4 h).
  --poll-interval SEC   Seconds between getindexinfo polls. Default: 30.
  --log-dir PATH        Where per-host logs land. Default: <script>/logs/
  -h, --help            Show this help.

Exit codes:
  0   All hosts succeeded (or dry-run OK).
  1   At least one host reported a hard failure. Per-host logs have detail.
  2   Bad CLI usage.

Examples:
  server/enable-blockfilters.sh \
    --hosts root@host-a.example.com,root@host-b.example.com,root@host-c.example.com \
    --dry-run

  BLAKESTREAM_DAEMON_HOSTS=root@a,root@b,root@c \
    server/enable-blockfilters.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hosts)         HOSTS_CSV="${2:?--hosts requires a comma-separated list}"; shift ;;
    --data-root)     DAEMON_DATA_ROOT="${2:?--data-root requires a path}"; shift ;;
    --dry-run)       DRY_RUN=1 ;;
    --disable)       DISABLE=1 ;;
    --stop-timeout)  STOP_TIMEOUT="${2:?--stop-timeout requires seconds}"; shift ;;
    --poll-timeout)  POLL_TIMEOUT="${2:?--poll-timeout requires seconds}"; shift ;;
    --poll-interval) POLL_INTERVAL="${2:?--poll-interval requires seconds}"; shift ;;
    --log-dir)       LOG_DIR="${2:?--log-dir requires a path}"; shift ;;
    -h|--help)       usage; exit 0 ;;
    *)               echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

DAEMON_DATA_ROOT="${DAEMON_DATA_ROOT:-${DAEMON_DATA_ROOT_DEFAULT}}"

if [[ -z "${HOSTS_CSV}" ]]; then
  echo "Specify --hosts (or set BLAKESTREAM_DAEMON_HOSTS)." >&2
  usage >&2
  exit 2
fi

# Split HOSTS_CSV into an array; trim whitespace around each entry.
IFS=',' read -r -a HOSTS <<< "${HOSTS_CSV}"
for i in "${!HOSTS[@]}"; do
  HOSTS[$i]="$(echo "${HOSTS[$i]}" | xargs)"
done

mkdir -p "${LOG_DIR}"

# --------------------------------------------------------------------------- #
# Logging + helpers
# --------------------------------------------------------------------------- #

# hlog: log line tagged with the host it belongs to. Timestamped so a
# `tail -F` of multiple per-host logs interleaves readably.
hlog() {
  local host="$1"; shift
  local ts; ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '[%s] [%s] %s\n' "${ts}" "${host}" "$*"
}

# glog: main (orchestrator) log line, no host tag.
glog() {
  local ts; ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '[%s] %s\n' "${ts}" "$*"
}

# ssh_run <host> <shell-command-string>
# Runs the command on the remote host via ssh. Uses BatchMode so we fail
# hard on missing keys instead of hanging on a password prompt.
ssh_run() {
  local host="$1"; shift
  local cmd="$1"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "${host}" "${cmd}"
}

# --------------------------------------------------------------------------- #
# Per-host worker
# --------------------------------------------------------------------------- #

# process_host <host> — runs the six coins in COIN_ORDER on the given host.
# Writes progress to a per-host log file. Never touches other hosts.
process_host() {
  local host="$1"
  local log="${LOG_DIR}/enable-blockfilters-$(echo "${host}" | tr '@' '_' | tr '/' '_')-$(date -u '+%Y%m%dT%H%M%SZ').log"

  # From here on, everything we print goes to the per-host log. The main
  # orchestrator log summarises start/finish/failure only.
  exec >> "${log}" 2>&1

  hlog "${host}" "worker starting; data root=${DAEMON_DATA_ROOT}, dry-run=${DRY_RUN}, disable=${DISABLE}"

  # Sanity check: can we reach the host at all?
  if ! ssh_run "${host}" "true"; then
    hlog "${host}" "ERROR: ssh unreachable; aborting host"
    return 1
  fi

  # Docker check.
  if ! ssh_run "${host}" "command -v docker >/dev/null 2>&1"; then
    hlog "${host}" "ERROR: docker not found on remote; aborting host"
    return 1
  fi

  local coin failures=0
  for coin in "${COIN_ORDER[@]}"; do
    if process_coin "${host}" "${coin}"; then
      hlog "${host}" "${coin}: OK"
    else
      hlog "${host}" "${coin}: FAILED"
      failures=$((failures + 1))
    fi
  done

  hlog "${host}" "worker done; failures=${failures}"
  return "${failures}"
}

# process_coin <host> <coin> — the per-coin state machine.
#
#   1. Discover the coin's container name on the host (matches the image or
#      known naming patterns).
#   2. Idempotency check: is blockfilterindex already set in the .conf?
#   3. Backup the existing .conf.
#   4. Append the two settings (or strip them if --disable).
#   5. docker stop -t <STOP_TIMEOUT> the container (clean shutdown).
#   6. docker start the container.
#   7. Poll `<coin>-cli getindexinfo` until basicblockfilter.synced=true
#      OR --disable path skips the poll (nothing to build).
process_coin() {
  local host="$1" coin="$2"
  local datadir="${DAEMON_DATA_ROOT%/}/${DATADIR_NAME[${coin}]}"
  local conf="${datadir}/${CONF_NAME[${coin}]}"
  local cli="${CLI_BIN[${coin}]}"

  hlog "${host}" "${coin}: begin (conf=${conf})"

  # Step 1: find the container. We match on the NAME column, allowing the
  # three naming conventions the fleet uses in practice:
  #   - bare ticker      (e.g. "blc", "elt")           — EU + JP + Explorer
  #   - bsx-<ticker>     (e.g. "bsx-bbtc")             — Explorer for some
  #   - electrum-daemon-<ticker>                       — deploy-daemons.sh default
  # We deliberately do NOT match on the image column because throwaway test
  # containers (e.g. "elt-bootstrap-test" running the same image) would then
  # get picked up. Anchoring the pattern to the full Name field rejects
  # anything with a stray suffix like -test / -bootstrap / -sentinel.
  local name_pat
  name_pat="^(${coin}|bsx-${coin}|electrum-daemon-${coin})$"
  local container
  container="$(ssh_run "${host}" "docker ps --format '{{.Names}}' 2>/dev/null | grep -E '${name_pat}' | head -1")"
  if [[ -z "${container}" ]]; then
    hlog "${host}" "${coin}: no running container matches name ~ /${name_pat}/; skipping"
    return 1
  fi
  hlog "${host}" "${coin}: container=${container}"

  # Step 2: idempotency check.
  # For enable mode: if the config already has blockfilterindex, no-op.
  # For disable mode: if it does NOT have it, no-op.
  #
  # `grep -c` outputs the count AND exits 1 when count is 0, so a naive
  # `... || echo 0` fallback fires simultaneously and we get "0\n0". Suppress
  # the exit by piping through wc -l on the local shell, which gives us a
  # single integer we can compare cleanly.
  local has_setting
  has_setting="$(ssh_run "${host}" "grep -c '^blockfilterindex' '${conf}' 2>/dev/null; true" | head -1)"
  has_setting="${has_setting:-0}"
  if [[ "${DISABLE}" -eq 0 ]] && [[ "${has_setting}" -gt 0 ]]; then
    hlog "${host}" "${coin}: config already has blockfilterindex; verifying index status only"
    verify_index_synced "${host}" "${container}" "${cli}" "${coin}"
    return $?
  fi
  if [[ "${DISABLE}" -eq 1 ]] && [[ "${has_setting}" -eq 0 ]]; then
    hlog "${host}" "${coin}: config has no blockfilterindex line; nothing to disable"
    return 0
  fi

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    local action
    if [[ "${DISABLE}" -eq 1 ]]; then action="strip"; else action="append"; fi
    hlog "${host}" "${coin}: [dry-run] would backup ${conf}, ${action} blockfilterindex+peerblockfilters, docker stop -t ${STOP_TIMEOUT} ${container}, docker start ${container}"
    return 0
  fi

  # Step 3: backup the .conf.
  local ts; ts="$(date -u '+%Y%m%dT%H%M%SZ')"
  local bak="${conf}.bak-${ts}"
  if ! ssh_run "${host}" "cp -a '${conf}' '${bak}'"; then
    hlog "${host}" "${coin}: ERROR backing up ${conf} → ${bak}"
    return 1
  fi
  hlog "${host}" "${coin}: backed up conf → ${bak}"

  # Step 4: mutate the .conf. For enable, append the two lines idempotently.
  # For disable, delete any lines starting with blockfilterindex= or
  # peerblockfilters= (sed -i in place).
  if [[ "${DISABLE}" -eq 0 ]]; then
    if ! ssh_run "${host}" "printf '\n# BIP 157/158 compact filters (added by enable-blockfilters.sh)\nblockfilterindex=1\npeerblockfilters=1\n' >> '${conf}'"; then
      hlog "${host}" "${coin}: ERROR appending settings to ${conf}"
      return 1
    fi
    hlog "${host}" "${coin}: appended blockfilterindex + peerblockfilters"
  else
    if ! ssh_run "${host}" "sed -i '/^blockfilterindex=/d; /^peerblockfilters=/d; /^# BIP 157\\/158/d' '${conf}'"; then
      hlog "${host}" "${coin}: ERROR stripping settings from ${conf}"
      return 1
    fi
    hlog "${host}" "${coin}: stripped blockfilterindex + peerblockfilters"
  fi

  # Step 5 + 6: clean shutdown + start.
  hlog "${host}" "${coin}: docker stop -t ${STOP_TIMEOUT} ${container} (this respects the container's --stop-timeout too)"
  if ! ssh_run "${host}" "docker stop -t ${STOP_TIMEOUT} '${container}'"; then
    hlog "${host}" "${coin}: ERROR stopping container"
    return 1
  fi
  hlog "${host}" "${coin}: docker start ${container}"
  if ! ssh_run "${host}" "docker start '${container}'"; then
    hlog "${host}" "${coin}: ERROR starting container"
    return 1
  fi

  # Step 7: for enable mode, wait until the index is caught up.
  if [[ "${DISABLE}" -eq 0 ]]; then
    verify_index_synced "${host}" "${container}" "${cli}" "${coin}"
    return $?
  fi
  return 0
}

# verify_index_synced <host> <container> <cli> <coin>
# Polls the running daemon's `getindexinfo` until basicblockfilter.synced is
# true or POLL_TIMEOUT elapses. Also tolerates the RPC being briefly
# unavailable while the daemon is coming up.
verify_index_synced() {
  local host="$1" container="$2" cli="$3" coin="$4"
  local deadline=$(( SECONDS + POLL_TIMEOUT ))
  local last_height=0

  hlog "${host}" "${coin}: waiting for basicblockfilter.synced=true (timeout ${POLL_TIMEOUT}s)"
  while [[ "${SECONDS}" -lt "${deadline}" ]]; do
    local info synced height
    # Redirect stderr — during a fresh index build, getindexinfo may return
    # "index not ready" etc. We want to poll through those, not treat them
    # as fatal.
    info="$(ssh_run "${host}" "docker exec '${container}' ${cli} getindexinfo 2>/dev/null || true")"
    if [[ -z "${info}" ]]; then
      sleep "${POLL_INTERVAL}"
      continue
    fi
    # Parse via jq if available on the remote; otherwise fall back to grep.
    # We only need the two fields for the basic filter — grep is fine.
    synced="$(printf '%s' "${info}" | grep -A2 '"basic block filter index"' | grep 'synced' | grep -o 'true\|false' | head -1 || true)"
    height="$(printf '%s' "${info}" | grep -A2 '"basic block filter index"' | grep 'best_block_height' | grep -oE '[0-9]+' | head -1 || echo 0)"

    if [[ "${synced}" == "true" ]]; then
      hlog "${host}" "${coin}: index synced at height ${height} — OK"
      return 0
    fi
    if [[ "${height}" != "${last_height}" ]]; then
      hlog "${host}" "${coin}: index building — height=${height}"
      last_height="${height}"
    fi
    sleep "${POLL_INTERVAL}"
  done

  hlog "${host}" "${coin}: TIMEOUT after ${POLL_TIMEOUT}s waiting for index sync"
  return 1
}

# --------------------------------------------------------------------------- #
# Orchestrator: kick off one worker per host, wait for all.
# --------------------------------------------------------------------------- #

glog "orchestrator: hosts=${HOSTS[*]}, dry-run=${DRY_RUN}, disable=${DISABLE}, log-dir=${LOG_DIR}"

declare -a WORKER_PIDS=()
declare -A WORKER_HOST=()

for host in "${HOSTS[@]}"; do
  glog "spawning worker for ${host}"
  process_host "${host}" &
  pid=$!
  WORKER_PIDS+=("${pid}")
  WORKER_HOST[${pid}]="${host}"
done

overall=0
for pid in "${WORKER_PIDS[@]}"; do
  host="${WORKER_HOST[${pid}]}"
  if wait "${pid}"; then
    glog "worker for ${host} finished cleanly"
  else
    rc=$?
    glog "worker for ${host} exited non-zero (rc=${rc}); see per-host log in ${LOG_DIR}"
    overall=1
  fi
done

if [[ "${overall}" -eq 0 ]]; then
  glog "all hosts completed successfully"
else
  glog "one or more hosts had failures; per-host logs are in ${LOG_DIR}"
fi
exit "${overall}"
