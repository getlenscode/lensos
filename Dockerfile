# Pinned to amd64: BrowserOS (the desktop's default browser) only ships
# Linux builds for x86_64. On Apple Silicon this runs under Rosetta.
FROM --platform=linux/amd64 debian:forky-slim

ENV NOVNC_PORT=6080 \
    PATH=/opt/noVNC/server/pi-agent/node_modules/.bin:$PATH \
    VNC_RESOLUTION=1280x800 \
    TRANSCRIBE_PORT=5000 \
    XDG_RUNTIME_DIR=/tmp/voiceos-runtime \
    LIBGL_ALWAYS_SOFTWARE=1 \
    DEBIAN_FRONTEND=noninteractive

# Layer 1: system packages (rarely changes)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        hyprland sway wayvnc kitty thunar firefox-esr chromium vim htop \
        waybar dunst swaybg neovim btop tmux rofi less \
        zsh eza ripgrep fd-find bat jq libnotify-bin papirus-icon-theme \
        fonts-jetbrains-mono fonts-font-awesome fonts-noto-cjk fontconfig locales \
        python3 nodejs npm nginx openssl xdg-utils dbus-user-session libcap2-bin wtype wl-clipboard \
        libgl1-mesa-dri xwayland ca-certificates curl \
        git make gcc g++ unzip tar grim socat \
    && curl -LsSf -o /tmp/browseros.deb \
         "https://github.com/browseros-ai/BrowserOS/releases/download/v0.50.5/BrowserOS_v0.50.5_amd64.deb" \
    && apt-get install -y /tmp/browseros.deb \
    && rm -f /tmp/browseros.deb \
    && setcap -r /usr/bin/Hyprland \
    && sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && ln -sf /usr/bin/batcat /usr/local/bin/bat \
    && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*

# Layer 1b: Discord — installed from the official .deb, not snap. snap
# needs snapd running under systemd as PID 1, and this container's PID 1
# is the entrypoint script, not systemd, so "snap install" has nothing to
# talk to. The .deb installs a standard /usr/share/applications entry, so
# rofi's app launcher (drun mode) picks it up with no extra wiring.
RUN apt-get update \
    && curl -LsSf -o /tmp/discord.deb \
         "https://discord.com/api/download?platform=linux&format=deb" \
    && apt-get install -y /tmp/discord.deb \
    && rm -f /tmp/discord.deb \
    # Electron aborts on the SUID sandbox check inside a container, so both
    # terminal and rofi launches go through flags (same as browser-launch.sh).
    && printf '%s\n' '#!/bin/sh' \
         'export ELECTRON_DISABLE_SANDBOX=1' \
         'exec /usr/bin/discord --no-sandbox --disable-gpu --disable-dev-shm-usage --ozone-platform=wayland "$@"' \
         > /usr/local/bin/discord \
    && chmod +x /usr/local/bin/discord \
    && sed -i 's|^Exec=/usr/bin/discord|Exec=/usr/local/bin/discord|' /usr/share/applications/discord.desktop \
    && rm -rf /var/lib/apt/lists/*

# Layer 2: pinned upstream builds not packaged for Debian forky/arm64
RUN curl -LsSf https://astral.sh/uv/0.9.5/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in amd64) luals=linux-x64 ;; arm64) luals=linux-arm64 ;; *) exit 1 ;; esac \
    && curl -LsSf -o /tmp/luals.tar.gz \
         "https://github.com/LuaLS/lua-language-server/releases/download/3.15.0/lua-language-server-3.15.0-${luals}.tar.gz" \
    && mkdir -p /opt/lua-language-server \
    && tar -xzf /tmp/luals.tar.gz -C /opt/lua-language-server \
    && ln -sf /opt/lua-language-server/bin/lua-language-server /usr/local/bin/ \
    && rm /tmp/luals.tar.gz

RUN useradd -m -s /usr/bin/zsh voiceos \
    && uv venv /opt/voiceos-venv

# Layer 3: dependency manifests only, so code edits don't re-run installs
COPY server/requirements.txt /opt/noVNC/server/requirements.txt
COPY server/pi-agent/package.json server/pi-agent/package-lock.json /opt/noVNC/server/pi-agent/
RUN uv pip install --no-cache --python /opt/voiceos-venv/bin/python -r /opt/noVNC/server/requirements.txt \
    && cd /opt/noVNC/server/pi-agent && npm ci --ignore-scripts --omit=dev \
    && npm install -g pyright typescript-language-server vscode-langservers-extracted

# Layer 4: JetBrainsMono Nerd Font (never changes)
RUN python3 -c "import io,os,urllib.request,zipfile; \
        data=urllib.request.urlopen('https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.zip').read(); \
        os.makedirs('/usr/local/share/fonts/JetBrainsMonoNerd', exist_ok=True); \
        zipfile.ZipFile(io.BytesIO(data)).extractall('/usr/local/share/fonts/JetBrainsMonoNerd')" \
    && fc-cache -f

# Layer 5: Oh My Zsh + external plugins (never changes)
RUN su voiceos -s /bin/sh -c ' \
      git clone --depth 1 https://github.com/ohmyzsh/ohmyzsh.git "$HOME/.oh-my-zsh" \
      && git clone --depth 1 https://github.com/zsh-users/zsh-autosuggestions \
           "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" \
      && git clone --depth 1 https://github.com/zsh-users/zsh-syntax-highlighting \
           "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting" \
      && git clone --depth 1 https://github.com/zsh-users/zsh-completions \
           "$HOME/.oh-my-zsh/custom/plugins/zsh-completions"'

# Layer 6: peakflava theme — re-runs only when theme files change
COPY theme/ /opt/noVNC/theme/
# /home/voiceos/.browseros-profile exists in the image so a named volume
# mounted there inherits voiceos ownership instead of root.
RUN mkdir -p /home/voiceos/.config /home/voiceos/Pictures/wallpapers /home/voiceos/.cache \
        /home/voiceos/.browseros-profile \
    && for d in kitty waybar dunst rofi hypr nvim btop tmux wlogout; do \
         cp -r "/opt/noVNC/theme/peakflava/$d" "/home/voiceos/.config/"; done \
    && cp /opt/noVNC/theme/peakflava/wallpapers/*.png \
          /opt/noVNC/theme/peakflava/wallpapers/*.jpg \
          /home/voiceos/Pictures/wallpapers/ \
    && cp /opt/noVNC/theme/peakflava/hypr/themes/default_colors.conf \
          /home/voiceos/.cache/hypr_colors.conf \
    # Vim gets a matching colorscheme from the theme bundle.
    && mkdir -p /home/voiceos/.vim/colors \
    && cp /opt/noVNC/theme/peakflava/vim/vimrc /home/voiceos/.vimrc \
    && cp /opt/noVNC/theme/peakflava/vim/colors/peakflava.vim /home/voiceos/.vim/colors/ \
    && cp /opt/noVNC/theme/peakflava/zsh/.zshrc /home/voiceos/.zshrc \
    && cp /opt/noVNC/theme/peakflava/zsh/custom-theme.zsh-theme \
          /home/voiceos/.oh-my-zsh/custom/themes/ \
    && chmod -R +x /home/voiceos/.config/hypr/scripts /home/voiceos/.config/waybar/scripts \
    && chown -R voiceos:voiceos /home/voiceos

# Layer 7: bundle lazy.nvim plugins incl. treesitter parsers and the
# telescope fzf build so first launch downloads nothing.
RUN su voiceos -s /bin/sh -c ' \
      nvim --headless "+Lazy! sync" +qa \
      && nvim --headless "+TSUpdateSync" +qa'

# Layer 8: application code (changes most often)
COPY . /opt/noVNC
# Sway runs the container-adapted waybar config on the same theme style.
RUN cp /opt/noVNC/docker/waybar/config.jsonc /home/voiceos/.config/waybar/config.jsonc \
    && cat /opt/noVNC/docker/waybar/extra.css >> /home/voiceos/.config/waybar/style.css \
    && chmod +x /opt/noVNC/docker/entrypoint.sh /opt/noVNC/docker/kitty-launch.sh \
        /opt/noVNC/docker/browser-launch.sh \
    && chown -R voiceos:voiceos /opt/noVNC /home/voiceos

USER voiceos
WORKDIR /home/voiceos
EXPOSE 6080
ENTRYPOINT ["/opt/noVNC/docker/entrypoint.sh"]
