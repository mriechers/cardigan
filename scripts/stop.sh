#!/bin/bash
# Stop The Metadata Neighborhood
#
# Stops the API server, worker process, and frontend dev server.
#
# Usage: ./scripts/stop.sh

echo "🏘️  Stopping The Metadata Neighborhood..."
echo ""

# Stop Cloudflare Tunnel
echo -n "Tunnel:    "
if pkill -f 'cloudflared tunnel' 2>/dev/null; then
    echo "✅ Stopped"
else
    echo "ℹ️  Was not running"
fi

# Stop frontend (Vite)
echo -n "Frontend:  "
if pkill -f 'vite' 2>/dev/null || pkill -f 'node.*vite' 2>/dev/null; then
    echo "✅ Stopped"
else
    echo "ℹ️  Was not running"
fi

# Stop API server
echo -n "API:       "
if pkill -f 'uvicorn api.main:app'; then
    echo "✅ Stopped"
else
    echo "ℹ️  Was not running"
fi

# Stop worker
echo -n "Worker:    "
if pkill -f 'run_worker.py'; then
    echo "✅ Stopped"
else
    echo "ℹ️  Was not running"
fi

# Stop watcher
echo -n "Watcher:   "
if pkill -f 'watch_transcripts.py'; then
    echo "✅ Stopped"
else
    echo "ℹ️  Was not running"
fi

# Wait for ports to actually be released (prevents restart race condition)
echo ""
echo -n "Waiting for ports to free up..."
for i in $(seq 1 20); do
    if ! lsof -i :8000 > /dev/null 2>&1 && ! lsof -i :3000 > /dev/null 2>&1; then
        echo " ✅"
        break
    fi
    if [ $i -eq 20 ]; then
        echo " ⚠️  Ports still in use (processes may need a moment)"
    fi
    sleep 0.5
done

echo "The Neighborhood is closed. 👋"
