#!/bin/bash
# Ensure the SOCKS5 SSH tunnel to the Lithuanian proxy VPS is running.
# Used by cron_run.sh before each trading cycle and on server reboot.
#
# The tunnel routes Polymarket CLOB API requests through a non-restricted
# IP to bypass geo-blocking (UK/US/FR).
#
# Usage:
#   ./scripts/ensure_tunnel.sh          # Check and start if needed
#   ./scripts/ensure_tunnel.sh --kill   # Kill existing tunnel

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
KEY="$PROJECT_DIR/.ssh/proxy_key"
PROXY_HOST="root@76.13.79.61"
LOCAL_PORT=1080
PID_FILE="$PROJECT_DIR/.ssh/tunnel.pid"

kill_tunnel() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null
            echo "[tunnel] Killed tunnel PID $PID"
        fi
        rm -f "$PID_FILE"
    fi
    # Also kill any orphaned tunnel processes for this project
    pkill -f "ssh.*${KEY}.*-D.*${LOCAL_PORT}" 2>/dev/null
}

if [ "$1" = "--kill" ]; then
    kill_tunnel
    exit 0
fi

# Check if tunnel is already running and healthy
is_healthy() {
    if [ ! -f "$PID_FILE" ]; then
        return 1
    fi
    PID=$(cat "$PID_FILE")
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        return 1
    fi
    # Verify the port is actually listening
    if ! ss -tlnp 2>/dev/null | grep -q ":${LOCAL_PORT} " ; then
        return 1
    fi
    return 0
}

if is_healthy; then
    echo "[tunnel] Already running (PID $(cat "$PID_FILE"), port $LOCAL_PORT)"
    exit 0
fi

# Clean up any dead tunnel processes
kill_tunnel

echo "[tunnel] Starting SOCKS5 tunnel on 127.0.0.1:$LOCAL_PORT -> $PROXY_HOST ..."

# Ensure key permissions
chmod 600 "$KEY" 2>/dev/null

ssh \
    -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=60 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o ConnectTimeout=10 \
    -i "$KEY" \
    -D "127.0.0.1:$LOCAL_PORT" \
    -N -f \
    "$PROXY_HOST"

SSH_EXIT=$?
if [ $SSH_EXIT -ne 0 ]; then
    echo "[tunnel] ERROR: ssh exited with code $SSH_EXIT"
    exit 1
fi

# Find the PID of the tunnel we just started
sleep 1
TUNNEL_PID=$(pgrep -f "ssh.*${KEY}.*-D.*${LOCAL_PORT}" | head -1)
if [ -n "$TUNNEL_PID" ]; then
    echo "$TUNNEL_PID" > "$PID_FILE"
    echo "[tunnel] Started (PID $TUNNEL_PID, port $LOCAL_PORT)"
else
    echo "[tunnel] WARNING: tunnel started but PID not found"
fi
