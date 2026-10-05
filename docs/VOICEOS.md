# VoiceOS desktop

VoiceOS presents a Wayland desktop through noVNC. The bottom dock contains shortcuts for terminal, browser, files, workspaces, microphone, and the usual noVNC controls. The dock has glass styling and motion.

## Run

```sh
docker build -t voiceos .
docker run --rm -p 6080:6080 \
  -v voiceos-home:/home/voiceos \
  -e GROQ_API_KEY='your-groq-key' \
  -e OPENAI_API_KEY='your-openrouter-or-openai-key' \
  voiceos
```

Or with Compose (same volumes, plus a dedicated BrowserOS profile volume):

```sh
docker compose up -d --build
```

The named volume keeps the desktop user's home directory — files, editor state, shell history — across container restarts. Use a named volume rather than a bind mount so the image's bundled config (themes, dotfiles, Neovim plugins) seeds it correctly on first start. `browseros-profile` mounts at `/home/voiceos/.browseros-profile` and persists BrowserOS cookies, logins, and its `.browseros/server_config.json` independently of home.

Open `http://localhost:6080/vnc.html` and click Connect; no sign-in is required. Browser microphone access requires a secure context; `localhost` is treated as secure by current browsers. For a remote host, put HTTPS in front of port 6080 — the HTTP port is unauthenticated, and VNC plus the command API listen inside the container.

Set `VNC_RESOLUTION` to a value such as `1600x900` if needed. The default uses Sway's headless backend and software rendering, which works without a GPU. Hyprland is included as an optional `VOICEOS_COMPOSITOR=hyprland` mode for Linux hosts with a usable DRM device; observation, typing, and position-based commands are only implemented for Sway. That mode requires passing the host graphics devices into Docker and has not been validated in a headless Docker Desktop VM.

## BrowserOS MCP

BrowserOS autostarts on container boot, and its built-in MCP server is reachable at `http://localhost:6080/mcp` (Streamable HTTP) — nginx proxies it to the browser's loopback MCP port. Unauthenticated; treat it like the rest of port 6080. Example client config:

```json
{ "mcpServers": { "browseros": { "command": "npx", "args": ["-y", "mcp-remote", "http://localhost:6080/mcp"] } } }
```

## Commands

Tap the mic in the dock and speak — the orb listens, stops when you pause, transcribes, and runs the command automatically; no extra clicks needed. You can also type a command into the field and press Enter. The dock launchers and workspace buttons use the same command API. **Cancel** stops listening or the request currently being handled (`POST /api/cancel`); while one request runs, further agent requests are turned away rather than competing for window focus.

Whisper on Groq transcribes the audio, so `GROQ_API_KEY` is needed for the mic feature. [Pi](https://pi.dev/docs/latest) interprets the transcript and uses a single `desktop_action` tool to carry out up to ten requested actions in order. Its tool accepts only the VoiceOS commands below; Pi has no shell, file editing, or unrestricted desktop tool. Pi talks to an in-container LiteLLM proxy (`server/litellm_config.yaml`), which routes to `deepseek/deepseek-v4.1-flash` on OpenRouter — set `OPENAI_API_KEY` (or `OPENROUTER_API_KEY`, normalized in the entrypoint) for it. Set `VOICEOS_PI_MODEL` to choose another alias served by the proxy. Dock buttons run their explicit commands directly, without a model call.

Pi sees the current window layout with each request, a short history of recent requests for follow-ups ("now close it"), and a fresh window summary after every action so it can verify results before replying.

Examples:

- `workspace 2`, `next workspace`, `previous workspace`
- `open terminal`, `close window`, `close the terminal on the left`, `focus right`, `focus the browser`, `focus nvim`
- `split vertically`, `split the terminal horizontally` (sway only; opens a new terminal pane)
- `open browser`, `open browseros`, `open browser example.com`, `search web for Wayland` (browser = BrowserOS; `open firefox`/`open chromium` still work)
- `open files`, `open Downloads folder`, `search files for report`, `open file ~/report.txt`
- `cd to ~/projects` or `open terminal in ~/projects` opens a new terminal there
- `cd to /home in the top terminal` types the cd into that existing pane (sway only)
- `open <name>` tries an installed app, then a file or folder in the home directory
- `open ~/test.txt in vim`, `open vim`, `open htop` — console apps open inside a kitty window
- `run ls -la in the bottom terminal` types and executes a shell command in that pane
- `type hello{enter} in the left terminal`, `press ctrl+c in the top terminal` — arbitrary input; `type` supports `{enter}` `{esc}` `{tab}` `{ctrl+c}`-style placeholders

Observation commands (sway) let Pi verify state without spending model tokens on images:

- `list windows` — every window with workspace, title, geometry, focus
- `read the top terminal` — kitty scrollback text for that terminal
- `read the browser` / `read the page` — the browser page's text; `what's the url` for its URL
- `read file ~/notes.txt`, `list files in Downloads` — files inside the home directory
- `copy the terminal output`, `copy the error`, `copy that`, `copy the browser page`, `copy file report.txt` — text onto the Wayland clipboard
- `change wallpaper` / `next wallpaper` — cycles bundled wallpapers; `set wallpaper to deer` picks by name. The choice persists across restarts
- `open tmux`, `new tmux window called logs`, `split tmux horizontally`, `run <cmd> in tmux`, `read tmux`, `list tmux` — a persistent tmux session hosted in a terminal window

The focused window carries a bright cyan border so it's clear which window commands are targeting.

Pointer commands drive real input through the VNC channel:

- `click on the left terminal`, `click at 640 400`, `right-click`, `double-click`
- `scroll down 5 times in the browser`
- `drag 100 200 to 600 400`, `drag the left window to the right`

Screenshots are **disabled by default**. Set `VOICEOS_SCREENSHOTS=1` to let Pi run `screenshot` / `look at the screen`. Captures are downscaled to at most 1024px (`VOICEOS_SCREENSHOT_MAX`) and limited to three per request.

File and folder commands are limited to the desktop user's home directory. `run`/`type`/`press` drive real keyboard input into windows — including shell commands in terminals — so the agent can do anything the apps themselves allow.

## Health

`GET /api/health` reports whether the compositor, wayvnc, websockify, LiteLLM, and the Pi binary are up, which API keys are configured, and whether screenshots are enabled.
