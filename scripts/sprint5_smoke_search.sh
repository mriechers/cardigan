#!/usr/bin/env bash
# Sprint 5 Staging Soak — Smoke Search Script
#
# Runs three test queries against /api/mmingest/search and records results
# to /tmp/sprint5-smoke-log.jsonl. Also pings legacy endpoints for regression
# detection.
#
# Intended to run every 30 minutes for the duration of the 24h soak.
#
# Setup:
#   1. Create a consumer key: python3 scripts/create_consumer_key.py \
#        --label sprint5-smoke --scopes mmingest:read
#   2. Set CONSUMER_KEY in your environment or crontab
#   3. Ensure Cardigan is running on CARDIGAN_URL (default http://localhost:8100)
#
# Schedule via cron (add to crontab with: crontab -e):
#   */30 * * * * CONSUMER_KEY=<key> bash /path/to/sprint5_smoke_search.sh
#
# OR run in a tmux loop:
#   export CONSUMER_KEY=<key>
#   while true; do bash scripts/sprint5_smoke_search.sh; sleep 1800; done
#
# Configuration (override via environment):
#   CONSUMER_KEY     API key with mmingest:read scope (REQUIRED)
#   CARDIGAN_URL     Base URL for Cardigan API (default: http://localhost:8100)
#   SMOKE_LOG        Output JSONL path (default: /tmp/sprint5-smoke-log.jsonl)
#
set -euo pipefail

CARDIGAN_URL="${CARDIGAN_URL:-http://localhost:8100}"
SMOKE_LOG="${SMOKE_LOG:-/tmp/sprint5-smoke-log.jsonl}"

if [ -z "${CONSUMER_KEY:-}" ]; then
    echo "ERROR: CONSUMER_KEY environment variable is required." >&2
    echo "  Create a key with: python3 scripts/create_consumer_key.py --label sprint5-smoke --scopes mmingest:read" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Helper: run one search query, record results
# ---------------------------------------------------------------------------

run_smoke_query() {
    local query="$1"
    local description="$2"
    local expect_hits="$3"   # "yes", "no", or "any"

    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

    local encoded_query
    # URL-encode the query string (basic percent-encoding for common chars)
    encoded_query="$(python3 -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))" "$query")"

    local url="${CARDIGAN_URL}/api/mmingest/search?q=${encoded_query}&limit=5"

    # Time the request
    local t0
    t0="$(date +%s%3N)"  # milliseconds since epoch (GNU date; macOS: use python fallback)

    # macOS date doesn't support %3N — use python for ms precision
    t0="$(python3 -c 'import time; print(int(time.time() * 1000))')"

    local http_response
    local http_status
    local http_body

    # curl: -s silent, -w for status code, -o to capture body
    local tmp_body
    tmp_body="$(mktemp /tmp/sprint5_smoke_body_XXXXXX.json)"
    # shellcheck disable=SC2064
    trap "rm -f '$tmp_body'" RETURN

    http_status="$(curl -s -o "$tmp_body" -w "%{http_code}" \
        -H "X-API-Key: $CONSUMER_KEY" \
        --max-time 10 \
        "$url" 2>/dev/null)" || http_status="000"

    local t1
    t1="$(python3 -c 'import time; print(int(time.time() * 1000))')"
    local latency_ms=$(( t1 - t0 ))

    http_body="$(cat "$tmp_body" 2>/dev/null || echo '{}')"

    # Extract hit count from JSON response
    local hits
    hits="$(python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    print(d.get('total', d.get('count', len(d.get('results', [])))))
except Exception:
    print(-1)
" "$http_body" 2>/dev/null)" || hits="-1"

    # Determine pass/fail for this query
    local outcome
    case "$expect_hits" in
        "yes")
            if [ "$http_status" = "200" ] && [ "${hits:-0}" -gt 0 ] 2>/dev/null; then
                outcome="pass"
            elif [ "$http_status" = "200" ] && [ "${hits:-0}" -eq 0 ] 2>/dev/null; then
                outcome="warn_no_hits"   # OK early in soak before indexing completes
            else
                outcome="fail"
            fi
            ;;
        "no")
            if [ "$http_status" = "200" ]; then
                outcome="pass"   # 200 with empty results is correct
            else
                outcome="fail"
            fi
            ;;
        *)
            outcome=$([ "$http_status" = "200" ] && echo "pass" || echo "fail")
            ;;
    esac

    local record
    record="$(python3 -c "
import json
print(json.dumps({
    'ts': '$ts',
    'event': 'smoke',
    'query': $(python3 -c "import json, sys; print(json.dumps(sys.argv[1]))" "$query"),
    'description': '$description',
    'status': int('$http_status') if '$http_status'.isdigit() else 0,
    'hits': int('$hits') if '$hits'.lstrip('-').isdigit() else -1,
    'latency_ms': $latency_ms,
    'outcome': '$outcome',
    'expect_hits': '$expect_hits',
}))")"

    echo "$record" >> "$SMOKE_LOG"
    echo "$record"

    return 0
}

# ---------------------------------------------------------------------------
# Helper: ping a legacy endpoint for regression detection
# ---------------------------------------------------------------------------

run_regression_check() {
    local endpoint="$1"
    local label="$2"

    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

    local t0
    t0="$(python3 -c 'import time; print(int(time.time() * 1000))')"

    local http_status
    http_status="$(curl -s -o /dev/null -w "%{http_code}" \
        --max-time 5 \
        "${CARDIGAN_URL}${endpoint}" 2>/dev/null)" || http_status="000"

    local t1
    t1="$(python3 -c 'import time; print(int(time.time() * 1000))')"
    local latency_ms=$(( t1 - t0 ))

    local outcome
    outcome=$([ "$http_status" = "200" ] && echo "pass" || echo "fail")

    local record
    record="$(python3 -c "
import json
print(json.dumps({
    'ts': '$ts',
    'event': 'regression_check',
    'endpoint': '$endpoint',
    'label': '$label',
    'status': int('$http_status') if '$http_status'.isdigit() else 0,
    'latency_ms': $latency_ms,
    'outcome': '$outcome',
}))")"

    echo "$record" >> "$SMOKE_LOG"
    echo "$record"

    return 0
}

# ---------------------------------------------------------------------------
# Run smoke queries
# ---------------------------------------------------------------------------

echo "Sprint 5 smoke test at $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "Target: $CARDIGAN_URL"
echo "Log: $SMOKE_LOG"
echo "---"

# Query 1: Common term — should have hits once /wisconsinlife/ sidecars are indexed
run_smoke_query "wisconsin" "common_term" "yes"

# Query 2: Known phrase from Wisconsin Life transcripts — phrase-match
# Using a phrase likely to appear in WLI captions: "Wisconsin Life" is
# the show name, commonly spoken in episode intros/outros.
# Wrapped in double quotes for FTS5 adjacent-phrase match.
run_smoke_query '"Wisconsin Life"' "wli_phrase_match" "yes"

# Query 3: Unlikely term — should return empty results, not a 404/500
# xyzzy is a well-known no-match sentinel in testing; the endpoint must
# return 200 with empty results, not an error.
run_smoke_query "xyzzy_no_match_expected_s5" "no_match_sentinel" "no"

# Regression checks: verify pre-existing endpoints still respond
run_regression_check "/api/system/health" "health_check"
run_regression_check "/api/jobs?limit=1" "jobs_endpoint"

echo "---"
echo "Smoke test complete."
