#!/bin/sh
# Launch kitty with a unique remote-control socket so the command API can
# read scrollback from any terminal window (see read_terminal in
# server/voice_commands.py).
exec kitty --listen-on "unix:$XDG_RUNTIME_DIR/kitty-$$-$RANDOM.sock" "$@"
