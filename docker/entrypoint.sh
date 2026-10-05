#!/usr/bin/env bash
set -euo pipefail

# /tmp survives docker restart; wipe stale sockets (sway-ipc, kitty-*) so the
# readiness checks below don't latch onto dead ones from a previous run.
rm -rf "$XDG_RUNTIME_DIR"
mkdir -p "$XDG_RUNTIME_DIR" /tmp/voiceos-nginx /home/voiceos/.pi/agent
chmod 700 "$XDG_RUNTIME_DIR"
cp /opt/noVNC/server/pi-agent/models.json /home/voiceos/.pi/agent/models.json

# LiteLLM accepts the key under either name; export early so terminals see it too.
export OPENAI_API_KEY="${OPENAI_API_KEY:-${OPENROUTER_API_KEY:-}}"

# Session bus for waybar/dunst/notifications (no systemd user bus here).
export DBUS_SESSION_BUS_ADDRESS="$(dbus-daemon --session --fork --print-address=1 2>/dev/null | head -n1)"

resolution="${VNC_RESOLUTION:-1280x800}"
width="${resolution%x*}"
height="${resolution#*x}"

compositor="${VOICEOS_COMPOSITOR:-sway}"
if [[ "$compositor" == hyprland ]]; then
    # Requires a DRM render device, e.g. --device /dev/dri on Linux.
    Hyprland --config /opt/noVNC/docker/hyprland.conf > /tmp/voiceos-compositor.log 2>&1 &
elif [[ "$compositor" == sway ]]; then
    export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=1
    sway --unsupported-gpu --config /opt/noVNC/docker/sway.conf \
        > /tmp/voiceos-compositor.log 2>&1 &
else
    echo "VOICEOS_COMPOSITOR must be sway or hyprland" >&2
    exit 1
fi

desktop_socket=""
for i in $(seq 1 80); do
    for candidate in "$XDG_RUNTIME_DIR"/wayland-*; do
        if [[ -S "$candidate" ]]; then
            desktop_socket="$candidate"
            break
        fi
    done
    [[ -n "$desktop_socket" ]] && break
    sleep 0.25
done
[[ -n "$desktop_socket" ]] || { cat /tmp/voiceos-compositor.log; exit 1; }

export WAYLAND_DISPLAY="${desktop_socket##*/}"
export VOICEOS_COMPOSITOR="$compositor"
# Xwayland's display so X11 clients (e.g. rofi) spawned by the API work too.
export DISPLAY="${DISPLAY:-:0}"
if [[ "$compositor" == hyprland ]]; then
    export HYPRLAND_INSTANCE_SIGNATURE="$(find "$XDG_RUNTIME_DIR/hypr" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | head -n 1)"
else
    export SWAYSOCK="$(find "$XDG_RUNTIME_DIR" -maxdepth 1 -type s -name 'sway-ipc*' -print -quit)"
    swaymsg output HEADLESS-1 resolution "${width}x${height}" >/dev/null
fi
# The theme's awww wallpaper daemon isn't packaged; swaybg covers it.
# The "change wallpaper" command rewrites this marker file, so the
# choice survives container restarts via the home volume.
wallpaper="$(cat "$HOME/.config/voiceos-wallpaper" 2>/dev/null || true)"
wallpaper="${wallpaper:-$HOME/Pictures/wallpapers/aesthetic_deer.png}"
swaybg -i "$wallpaper" -m fill > /tmp/voiceos-swaybg.log 2>&1 &
if [[ "$compositor" == sway ]]; then
    waybar > /tmp/voiceos-waybar.log 2>&1 &
    dunst > /tmp/voiceos-dunst.log 2>&1 &
fi
wayvnc 127.0.0.1 5901 > /tmp/voiceos-wayvnc.log 2>&1 &
/opt/voiceos-venv/bin/websockify 127.0.0.1:6081 127.0.0.1:5901 \
    > /tmp/voiceos-websockify.log 2>&1 &

# BrowserOS autostarts so its built-in MCP server (nginx /mcp → 127.0.0.1:9200)
# is reachable without pressing $mod+b.
/opt/noVNC/docker/browser-launch.sh > /tmp/voiceos-browseros.log 2>&1 &

# LiteLLM proxy gives Pi an OpenAI-compatible endpoint routed to the
# configured provider/model.
/opt/voiceos-venv/bin/litellm --config /opt/noVNC/server/litellm_config.yaml \
    --host 127.0.0.1 --port 4000 > /tmp/voiceos-litellm.log 2>&1 &
for _ in $(seq 1 60); do
    (echo > /dev/tcp/127.0.0.1/4000) 2>/dev/null && break
    sleep 0.25
done

/opt/voiceos-venv/bin/python /opt/noVNC/server/transcribe_server.py > /tmp/voiceos-api.log 2>&1 &

exec nginx -c /opt/noVNC/docker/nginx.conf -g 'daemon off;'
