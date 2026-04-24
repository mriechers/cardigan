#!/bin/bash
# Check status of The Metadata Neighborhood
#
# Usage: ./scripts/status.sh

echo "🏘️  The Metadata Neighborhood - Status"
echo "======================================"
echo ""

# Check metadata.neighborhood alias
echo "Host Alias:"
echo -n "  metadata.neighborhood:    "
if grep -q "metadata.neighborhood" /etc/hosts 2>/dev/null; then
    echo "✅ Configured in /etc/hosts"
else
    echo "❌ Not configured (run ./scripts/setup-local-domain.sh)"
fi

echo ""
echo "Backend Services:"

# Check API
echo -n "  API Server (port 8000):   "
if lsof -i :8000 > /dev/null 2>&1; then
    echo "✅ Running"
else
    echo "❌ Not running"
fi

# Check worker
echo -n "  Worker:                   "
if pgrep -f 'run_worker.py' > /dev/null 2>&1; then
    echo "✅ Running (PID $(pgrep -f 'run_worker.py'))"
else
    echo "❌ Not running"
fi

# Check watcher
echo -n "  Watcher:                  "
if pgrep -f 'watch_transcripts.py' > /dev/null 2>&1; then
    echo "✅ Running (PID $(pgrep -f 'watch_transcripts.py'))"
else
    echo "❌ Not running"
fi

echo ""
echo "Frontend:"

# Check frontend
echo -n "  Vite Dev Server (3000):   "
if lsof -i :3000 > /dev/null 2>&1; then
    echo "✅ Running"
else
    echo "❌ Not running"
fi

echo ""
echo "Tunnel:"

# Check cloudflared process
echo -n "  cloudflared:              "
if pgrep -f 'cloudflared tunnel' > /dev/null 2>&1; then
    echo "✅ Running (PID $(pgrep -f 'cloudflared tunnel'))"
else
    echo "ℹ️  Not running"
fi

# Check tunnel endpoint reachability
echo -n "  cardigan.bymarkriechers.com: "
HTTP_CODE=$(curl -s --connect-timeout 5 -o /dev/null -w "%{http_code}" https://cardigan.bymarkriechers.com 2>/dev/null)
if echo "$HTTP_CODE" | grep -qE "^(200|301|302|303|403)$"; then
    echo "✅ Reachable ($HTTP_CODE)"
else
    echo "❌ Not reachable ($HTTP_CODE)"
fi

# Check health endpoint and connectivity
echo ""
echo "Connectivity:"

# Test localhost API
echo -n "  localhost:8100:           "
if curl -s --connect-timeout 2 http://localhost:8100/api/system/health > /dev/null 2>&1; then
    echo "✅ Responding"
else
    echo "❌ Not responding"
fi

# Test metadata.neighborhood API
echo -n "  metadata.neighborhood:8100: "
if curl -s --connect-timeout 2 http://metadata.neighborhood:8100/api/system/health > /dev/null 2>&1; then
    echo "✅ Responding"
else
    echo "❌ Not responding"
fi

# Test localhost frontend
echo -n "  localhost:3100:           "
if curl -s --connect-timeout 2 http://localhost:3100 > /dev/null 2>&1; then
    echo "✅ Responding"
else
    echo "❌ Not responding"
fi

# Test metadata.neighborhood frontend
echo -n "  metadata.neighborhood:3100: "
if curl -s --connect-timeout 2 http://metadata.neighborhood:3100 > /dev/null 2>&1; then
    echo "✅ Responding"
else
    echo "❌ Not responding"
fi

# Queue stats
echo ""
echo "Queue Status:"
if curl -s http://localhost:8100/api/queue/stats > /dev/null 2>&1; then
    QUEUE=$(curl -s http://localhost:8100/api/queue/stats 2>/dev/null)
    PENDING=$(echo "$QUEUE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pending',0))" 2>/dev/null || echo "?")
    IN_PROGRESS=$(echo "$QUEUE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('in_progress',0))" 2>/dev/null || echo "?")
    COMPLETED=$(echo "$QUEUE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('completed',0))" 2>/dev/null || echo "?")
    echo "  Pending:     $PENDING"
    echo "  In Progress: $IN_PROGRESS"
    echo "  Completed:   $COMPLETED"
else
    echo "  ❌ API not responding"
fi

echo ""
echo "URLs:"
echo "  Dashboard: http://metadata.neighborhood:3000"
echo "  API:       http://metadata.neighborhood:8000"
echo "  API Docs:  http://metadata.neighborhood:8000/docs"
echo "  Tunnel:    https://cardigan.bymarkriechers.com (if enabled)"
