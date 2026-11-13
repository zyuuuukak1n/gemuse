#!/usr/bin/env bash
set -euo pipefail

# Isolate browser outbound traffic from private RFC1918 / link-local / metadata ranges
apply_network_isolation() {
    if ! command -v iptables >/dev/null 2>&1; then
        echo "[entrypoint] WARN: iptables not found; skipping private network egress block" >&2
        return 0
    fi

    # Allow loopback and established connections, block new private-range egress
    if iptables -L OUTPUT >/dev/null 2>&1; then
        iptables -C OUTPUT -o lo -j ACCEPT 2>/dev/null || iptables -I OUTPUT 1 -o lo -j ACCEPT
        iptables -C OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
            iptables -I OUTPUT 2 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

        for cidr in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16; do
            iptables -C OUTPUT -d "$cidr" -j REJECT 2>/dev/null || iptables -A OUTPUT -d "$cidr" -j REJECT
        done
        echo "[entrypoint] Applied private network egress isolation via iptables"
    else
        echo "[entrypoint] WARN: NET_ADMIN capability unavailable; skipping iptables egress block" >&2
    fi
}

apply_network_isolation

# Ensure mounted volumes are writable by appuser (UID 1000)
mkdir -p /browser_profile /shared_files/uploads /shared_files/downloads
chown -R appuser:appuser /browser_profile /shared_files

export DISPLAY="${DISPLAY:-:99}"
SCREEN_GEOM="${SCREEN_WIDTH:-1280}x${SCREEN_HEIGHT:-800}x${SCREEN_DEPTH:-24}"
NOVNC_WEB_DIR="${NOVNC_WEB_DIR:-/usr/share/novnc}"

# Clean stale X lock if container restarts uncleanly
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

# Start Xvfb, x11vnc, noVNC/websockify, and FastAPI Playwright RPC server as non-root appuser
gosu appuser Xvfb "$DISPLAY" -screen 0 "$SCREEN_GEOM" -ac +extension RANDR &
XVFB_PID=$!

# Wait for X display socket
for _ in $(seq 1 30); do
    if [ -e /tmp/.X11-unix/X99 ]; then
        break
    fi
    sleep 0.2
done

X11VNC_ARGS=(-display "$DISPLAY" -forever -shared -rfbport 5900 -listen 127.0.0.1)
if [ -n "${NOVNC_PASSWORD:-}" ]; then
    PASSFILE="/tmp/.vncpass"
    gosu appuser x11vnc -storepasswd "$NOVNC_PASSWORD" "$PASSFILE" >/dev/null 2>&1
    X11VNC_ARGS+=(-rfbauth "$PASSFILE")
else
    X11VNC_ARGS+=(-nopw)
fi

gosu appuser x11vnc "${X11VNC_ARGS[@]}" >/tmp/x11vnc.log 2>&1 &
VNC_PID=$!

gosu appuser websockify --web="$NOVNC_WEB_DIR" 0.0.0.0:6080 127.0.0.1:5900 >/tmp/websockify.log 2>&1 &
WS_PID=$!

cleanup() {
    kill "$WS_PID" "$VNC_PID" "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

exec gosu appuser python -m uvicorn sandbox_server:app --host 0.0.0.0 --port 7000 --log-level info

