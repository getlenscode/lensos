#!/bin/sh
# Launch BrowserOS (Chromium-based) with the flags the container needs;
# fall back to chromium, then firefox-esr, on images without it.
if command -v browseros >/dev/null 2>&1; then
    # Pinned profile dir keeps the volume mount and the MCP config path stable.
    profile="${BROWSEROS_PROFILE_DIR:-$HOME/.browseros-profile}"
    cfg="$profile/.browseros/server_config.json"
    # Pre-seed BrowserOS's server config: fixed ports + accept non-loopback MCP
    # connections. BrowserOS may rewrite this file on startup, so the watcher
    # below reconciles whatever port it actually picked.
    mkdir -p "$profile/.browseros"
    if [ ! -f "$cfg" ]; then
        printf '%s\n' '{"flags":{"allow_remote_in_mcp":true},"ports":{"cdp":9011,"extension":9300,"server":9200}}' > "$cfg"
    fi
    # nginx proxies /mcp to 127.0.0.1:9200. BrowserOS writes the ports it
    # actually bound to .browseros/config.json (v0.50+) or server_config.json
    # (older) once up — wait for one, then forward 9200 to the real MCP port.
    # Never forward to 9200 itself: that's the port we listen on, and a
    # self-connecting socat would fork endlessly.
    (
        dir="$profile/.browseros"
        port=""
        for _ in $(seq 1 120); do
            port="$(jq -r '.ports.server // empty' "$dir/config.json" 2>/dev/null)"
            [ -n "$port" ] || port="$(jq -r '.ports.server // empty' "$dir/server_config.json" 2>/dev/null)"
            [ "$port" = "9200" ] && port=""
            [ -n "$port" ] && break
            sleep 1
        done
        while [ -n "$port" ]; do
            socat TCP-LISTEN:9200,bind=127.0.0.1,reuseaddr,fork "TCP:127.0.0.1:$port" 2>/dev/null
            sleep 2
        done
    ) &
    exec browseros --no-sandbox --disable-gpu --disable-dev-shm-usage \
        --user-data-dir="$profile" \
        --ozone-platform=wayland "$@"
elif command -v chromium >/dev/null 2>&1; then
    exec chromium --no-sandbox --disable-gpu --disable-dev-shm-usage \
        --ozone-platform=wayland "$@"
else
    exec firefox-esr "$@"
fi
