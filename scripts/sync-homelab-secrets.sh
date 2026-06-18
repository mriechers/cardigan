#!/usr/bin/env bash
# scripts/sync-homelab-secrets.sh — push keychain secrets to the cardigan
# homelab LXC.
#
# Pulls each `developer.cardigan.<KEY>` entry from the macOS Keychain, drops
# it as a Docker secret file in /root/cardigan/secrets/ on the target LXC,
# applies 0o444 mode so the appuser inside the container can read it, and
# (optionally) restarts the api+worker so the new values take effect.
#
# Why a dedicated cardigan namespace (vs. developer.workspace.*):
# Separable rotation/revocation per deployment. The cardigan source code's
# keychain fallback in api/services/secrets.py reads developer.workspace.*
# for local Mac dev. The homelab LXC reads /run/secrets/* (set by this
# script), so the two paths don't collide.
#
# Usage:
#   ./scripts/sync-homelab-secrets.sh                # full sync + restart
#   ./scripts/sync-homelab-secrets.sh --no-restart   # just push files
#   ./scripts/sync-homelab-secrets.sh --host <ip>    # override target host
#   ./scripts/sync-homelab-secrets.sh --dry-run      # show plan only
#
# Exit codes:
#   0  success
#   1  generic failure
#   2  missing required secret in keychain

set -euo pipefail

HOST="${CARDIGAN_HOMELAB_HOST:-192.168.1.42}"
RESTART=1
DRY_RUN=0

SECRETS=(
  OPENROUTER_API_KEY
  AIRTABLE_API_KEY
  LANGFUSE_PUBLIC_KEY
  LANGFUSE_SECRET_KEY
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)       HOST="$2"; shift 2 ;;
    --no-restart) RESTART=0; shift ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RED=$'\e[31m'; RESET=$'\e[0m'
ok()   { echo "  ${GREEN}✓${RESET} $*"; }
warn() { echo "  ${YELLOW}!${RESET} $*"; }
err()  { echo "  ${RED}✗${RESET} $*" >&2; }

echo "[sync-homelab-secrets] host=$HOST restart=$RESTART dry_run=$DRY_RUN"
echo

# Pre-flight: confirm every required secret exists in keychain
echo "[1/3] Validating keychain entries"
missing=0
for key in "${SECRETS[@]}"; do
  if security find-generic-password -s "developer.cardigan.$key" >/dev/null 2>&1; then
    ok "developer.cardigan.$key"
  else
    err "developer.cardigan.$key not in keychain"
    missing=$((missing+1))
  fi
done
[[ $missing -gt 0 ]] && { err "$missing missing entries — add via 'security add-generic-password -a \"\$USER\" -s developer.cardigan.<KEY> -w -U'"; exit 2; }

# Stage secrets in a tmpdir, then scp atomically
echo
echo "[2/3] Pulling values from keychain + scp to $HOST"
TMP=$(mktemp -d)
# shellcheck disable=SC2064
trap "rm -rf '$TMP'" EXIT

for key in "${SECRETS[@]}"; do
  # tr -d '\n' strips the trailing newline `security -w` appends; .strip() in
  # secrets.py would handle it anyway but cleaner not to rely on that.
  security find-generic-password -s "developer.cardigan.$key" -w | tr -d '\n' > "$TMP/${key,,}"
done

if [[ $DRY_RUN -eq 1 ]]; then
  warn "Dry-run: would scp ${#SECRETS[@]} files to root@$HOST:/root/cardigan/secrets/"
  ls -la "$TMP/"
  exit 0
fi

ssh "root@$HOST" 'mkdir -p /root/cardigan/secrets && chmod 700 /root/cardigan/secrets'
scp -q "$TMP"/* "root@$HOST:/root/cardigan/secrets/"
# 0o444: appuser (uid 1000 inside the container) needs to read these. Docker
# Compose file-based secrets preserve host permissions; 0o400 root:root means
# only host root can read = silently broken inside the container.
ssh "root@$HOST" 'chmod 0444 /root/cardigan/secrets/*'
ok "${#SECRETS[@]} secrets pushed to $HOST"

# Restart api + worker so the secrets bootstrap re-runs
echo
if [[ $RESTART -eq 0 ]]; then
  warn "Skipping restart (--no-restart). Run manually:"
  echo "  ssh root@$HOST 'cd /root/cardigan && docker compose restart api worker'"
  exit 0
fi
echo "[3/3] Restarting api + worker"
ssh "root@$HOST" 'cd /root/cardigan && docker compose restart api worker' >/dev/null
sleep 3
status=$(ssh "root@$HOST" "docker inspect --format '{{.State.Health.Status}}' cardigan-api-1 2>/dev/null || echo unknown")
[[ "$status" == "healthy" ]] && ok "api healthy" || warn "api status: $status (may still be starting)"

echo
echo "${GREEN}DONE${RESET} Secrets synced. Verify with:"
echo "  curl -s https://cardigan.riechers.co/api/system/health | python3 -m json.tool"
