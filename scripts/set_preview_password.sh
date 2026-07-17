#!/usr/bin/env bash
# scripts/set_preview_password.sh — manage per-person passwords for the
# Cardigan remote-preview gate.
#
# The remote preview (Tailscale Funnel + hardened nginx vhost) is switched on
# solely by the PRESENCE of secrets/cardigan_web_htpasswd — absent = dormant,
# byte-identical to today. This helper manages the per-person "user:hash" lines
# in that file (bcrypt via htpasswd) without disturbing anyone else's line.
#
# Usage:
#   scripts/set_preview_password.sh <username>              # prompt for password (hidden)
#   scripts/set_preview_password.sh <username> <password>   # password as 2nd arg
#   scripts/set_preview_password.sh --revoke <username>     # remove that user's line
#   scripts/set_preview_password.sh -h | --help
#
# After any change: run `docker compose up -d web` to reload the gate, and
# share each password over a SECURE channel (Signal / 1Password) — never email.
#
# Exit codes:
#   0  success
#   1  generic failure (mismatch, empty password, hashing failed)
#   2  bad usage / missing dependency

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SECRETS_DIR="$REPO_ROOT/secrets"
HTPASSWD_FILE="$SECRETS_DIR/cardigan_web_htpasswd"

if [[ -t 1 ]]; then
  GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RED=$'\e[31m'; BOLD=$'\e[1m'; RESET=$'\e[0m'
else
  GREEN=''; YELLOW=''; RED=''; BOLD=''; RESET=''
fi
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
err()  { printf '  %s✗%s %s\n' "$RED" "$RESET" "$*" >&2; }

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

# Emit a single "user:hash" line on stdout. Prefer bcrypt (-B); if this
# htpasswd build lacks it, fall back to the default apr1 (MD5) hash.
# Warnings go to stderr so command substitution captures only the hash line.
generate_line() {
  local user="$1" pass="$2" out
  if out="$(htpasswd -nbB "$user" "$pass" 2>/dev/null)" && [[ -n "$out" ]]; then
    printf '%s\n' "$out"; return 0
  fi
  warn "bcrypt (-B) unsupported by this htpasswd; falling back to apr1 (MD5)."
  if out="$(htpasswd -nb "$user" "$pass" 2>/dev/null)" && [[ -n "$out" ]]; then
    printf '%s\n' "$out"; return 0
  fi
  return 1
}

# --- Parse args -------------------------------------------------------------
REVOKE=0
USERNAME=""
PASSWORD=""
PASSWORD_SET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --revoke)  REVOKE=1; shift; USERNAME="${1:-}"; [[ $# -gt 0 ]] && shift || true ;;
    -h|--help) usage; exit 0 ;;
    -*)        err "Unknown option: $1"; usage >&2; exit 2 ;;
    *)
      if [[ -z "$USERNAME" ]]; then
        USERNAME="$1"
      elif [[ $PASSWORD_SET -eq 0 ]]; then
        PASSWORD="$1"; PASSWORD_SET=1
      else
        err "Unexpected argument: $1"; exit 2
      fi
      shift ;;
  esac
done

if [[ -z "$USERNAME" ]]; then
  err "A username is required."; usage >&2; exit 2
fi
# Usernames land in an htpasswd "user:hash" line and are matched on the colon
# field — keep them to a safe, colon-free charset.
if [[ ! "$USERNAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  err "Invalid username '$USERNAME' — use letters, digits, '.', '_' or '-' only."
  exit 2
fi

# --- Revoke -----------------------------------------------------------------
if [[ $REVOKE -eq 1 ]]; then
  if [[ ! -f "$HTPASSWD_FILE" ]]; then
    warn "No htpasswd file at $HTPASSWD_FILE — nothing to revoke."
    exit 0
  fi
  if ! awk -F: -v u="$USERNAME" '$1==u{f=1} END{exit !f}' "$HTPASSWD_FILE"; then
    warn "No entry for '$USERNAME' in $HTPASSWD_FILE — nothing to revoke."
    exit 0
  fi
  tmp="$(mktemp "$SECRETS_DIR/.htpasswd.XXXXXX")"
  # shellcheck disable=SC2064
  trap "rm -f '$tmp'" EXIT
  awk -F: -v u="$USERNAME" '$1 != u' "$HTPASSWD_FILE" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$HTPASSWD_FILE"
  trap - EXIT
  ok "Revoked '$USERNAME'."

  # grep -c prints the count itself (0 on no match) and exits 1 when 0 —
  # `|| true` keeps set -e happy without appending a second "0".
  remaining="$(grep -c ':' "$HTPASSWD_FILE" 2>/dev/null || true)"
  echo
  echo "Next steps:"
  echo "  1. Reload the web gate:   ${BOLD}docker compose up -d web${RESET}"
  if [[ "$remaining" -eq 0 ]]; then
    echo "  2. No entries remain. To turn the preview fully OFF (dormant,"
    echo "     byte-identical to today), delete the file:"
    echo "       rm '$HTPASSWD_FILE' && docker compose up -d web"
  fi
  exit 0
fi

# --- Set / update -----------------------------------------------------------
if ! command -v htpasswd >/dev/null 2>&1; then
  err "htpasswd not found. It ships with macOS and the Apache httpd tools."
  exit 2
fi

if [[ $PASSWORD_SET -eq 0 ]]; then
  # Hidden input; prompts go to stderr so stdout stays clean.
  read -rs -p "Password for '$USERNAME': " PASSWORD; echo >&2
  read -rs -p "Confirm password: " confirm; echo >&2
  if [[ "$PASSWORD" != "$confirm" ]]; then
    err "Passwords did not match."; exit 1
  fi
  confirm=""
fi

if [[ -z "$PASSWORD" ]]; then
  err "Password must not be empty."; exit 1
fi

existed=0
if [[ -f "$HTPASSWD_FILE" ]] \
  && awk -F: -v u="$USERNAME" '$1==u{f=1} END{exit !f}' "$HTPASSWD_FILE"; then
  existed=1
fi

if ! line="$(generate_line "$USERNAME" "$PASSWORD")"; then
  err "htpasswd failed to hash the password."; exit 1
fi
PASSWORD=""  # done with the plaintext

mkdir -p "$SECRETS_DIR"
tmp="$(mktemp "$SECRETS_DIR/.htpasswd.XXXXXX")"
# shellcheck disable=SC2064
trap "rm -f '$tmp'" EXIT
# Preserve every other user's line verbatim; drop only this user's old line.
if [[ -f "$HTPASSWD_FILE" ]]; then
  awk -F: -v u="$USERNAME" '$1 != u' "$HTPASSWD_FILE" > "$tmp"
fi
printf '%s\n' "$line" >> "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$HTPASSWD_FILE"
trap - EXIT

if [[ $existed -eq 1 ]]; then
  ok "Updated entry for '$USERNAME' in $HTPASSWD_FILE"
else
  ok "Added entry for '$USERNAME' to $HTPASSWD_FILE"
fi

echo
echo "Next steps:"
echo "  1. Reload the web gate:   ${BOLD}docker compose up -d web${RESET}"
echo "  2. Share the URL + this password with '$USERNAME' over a ${BOLD}secure${RESET}"
echo "     channel (Signal or a 1Password link) — ${BOLD}never email${RESET}."
