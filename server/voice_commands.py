"""Small, explicit voice command vocabulary for the remote desktop."""

import base64
import contextlib
import fcntl
import glob
import io
import os
import json
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

try:
    import vnc_input
except ImportError:  # imported as server.voice_commands (tests, repo root)
    from server import vnc_input


HOME = Path.home().resolve()
MAX_RESULTS = 30
MAX_TEXT_CHARS = 6000
DESKTOP_LOCK_PATH = "/tmp/voiceos-desktop.lock"


class CommandError(ValueError):
    pass


@contextlib.contextmanager
def desktop_lock():
    """Serialize all desktop actions so concurrent requests can't fight
    over window focus or interleave synthetic input."""
    lock_file = open(DESKTOP_LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def parse_command(text):
    value = " ".join(str(text).strip().split())
    lower = value.lower().rstrip(".!?")
    if not value or len(value) > 500:
        raise CommandError("Enter a command of at most 500 characters.")

    match = re.fullmatch(r"(?:switch to |go to |show |focus(?: on)? )?workspace (\d{1,2})", lower)
    if match:
        number = int(match.group(1))
        if 1 <= number <= 10:
            return {"action": "workspace", "number": number}
    if lower in ("next workspace", "switch to next workspace"):
        return {"action": "workspace_relative", "direction": "+1"}
    if lower in ("previous workspace", "switch to previous workspace"):
        return {"action": "workspace_relative", "direction": "-1"}
    if lower in ("open terminal", "new terminal", "launch terminal"):
        return {"action": "terminal"}
    if lower in ("close terminal", "close the terminal"):
        return {"action": "close_terminal"}
    if lower in ("close window", "close this window"):
        return {"action": "close"}
    match = re.fullmatch(
        r"close (?:the )?(window|terminal|browser|files?|pane) (?:on|at) the (" + _POS + ")"
        r"(?: (?:on|in|at) (?:the )?workspace (\d{1,2}))?", lower)
    if match:
        return {"action": "close_directional", "kind": match.group(1),
                "side": _norm_side(match.group(2)), "workspace": match.group(3)}
    match = re.fullmatch(
        r"close (?:the )?(" + _POS + ") (window|terminal|browser|files?|pane)"
        r"(?: (?:on|in|at) (?:the )?workspace (\d{1,2}))?", lower)
    if match:
        return {"action": "close_directional", "kind": match.group(2),
                "side": _norm_side(match.group(1)), "workspace": match.group(3)}
    match = re.fullmatch(
        r"close (?:the )?(window|terminal|browser|files?|pane) "
        r"(?:on|in|at) (?:the )?workspace (\d{1,2})", lower)
    if match:
        return {"action": "close_directional", "kind": match.group(1), "workspace": match.group(2)}
    match = re.fullmatch(
        r"(?:focus|focus on|switch to) (?:the )?(" + _POS + ") "
        r"(window|terminal|browser|files?|pane)(?: (?:on|in|at) (?:the )?workspace (\d{1,2}))?", lower)
    if match:
        return {"action": "focus_window", "kind": match.group(2),
                "side": _norm_side(match.group(1)), "workspace": match.group(3)}
    match = re.fullmatch(
        r"(?:focus|focus on|switch to) (?:the )?(window|terminal|browser|files?|pane) "
        r"(?:on|in|at) (?:the )?workspace (\d{1,2})", lower)
    if match:
        return {"action": "focus_window", "kind": match.group(1), "workspace": match.group(2)}
    match = re.fullmatch(r"(?:focus|focus on|switch to) (?:the )?(left|right|up|down|top|bottom)(?: window| pane)?", lower)
    if match:
        direction = {"top": "up", "bottom": "down"}.get(match.group(1), match.group(1))
        return {"action": "focus", "direction": direction}
    # Focus a named app or window: "focus the browser", "switch to nvim",
    # "focus the left editor".
    match = re.fullmatch(
        r"(?:focus|focus on|switch to|bring up|raise) (?:the |my )?(.+)", lower)
    if match:
        target = match.group(1).strip()
        side = None
        pm = re.fullmatch(r"(" + _POS + r") (.+)", target)
        if pm:
            side = _norm_side(pm.group(1))
            target = pm.group(2)
        target = re.sub(r"\s*(window|app|pane)s?$", "", target).strip()
        return {"action": "focus_app", "name": target, "side": side}
    match = re.fullmatch(
        r"split (?:the )?(" + _POS + ") "
        r"(window|terminal|pane)(?: into a| into| in a)? (vertical|horizontal)(?:ly)?(?: split)?", lower)
    if match:
        return {"action": "split", "direction": match.group(3),
                "kind": match.group(2), "side": _norm_side(match.group(1))}
    match = re.fullmatch(
        r"split(?: the)?(?: current| open| active| this)?(?: window| terminal| pane)? "
        r"(vertical|horizontal)(?:ly)?", lower)
    if match:
        return {"action": "split", "direction": match.group(1)}
    if lower in ("split", "split toggle", "toggle split", "toggle split direction"):
        return {"action": "split", "direction": "toggle"}
    if lower in ("open browser", "launch browser", "new browser"):
        return {"action": "browser", "url": "about:home"}
    if lower in ("open files", "open file manager", "show files"):
        return {"action": "files"}
    match = re.match(r"^(?:open browser (?:at |to )?|open (?:website |url ))(.+)$", value, re.I)
    if match:
        url = match.group(1).strip()
        if not urlparse(url).scheme:
            url = "https://" + url
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise CommandError("Only HTTP and HTTPS websites can be opened.")
        return {"action": "browser", "url": url}
    match = re.match(r"^(?:search (?:the )?web for |google )(.+)$", value, re.I)
    if match:
        return {"action": "browser", "url": "https://www.google.com/search?q=" + quote_plus(match.group(1))}
    match = re.match(r"^(?:search files for |find files? (?:named |called |for )?)(.+)$", value, re.I)
    if match:
        return {"action": "search_files", "query": match.group(1).strip()}
    match = re.fullmatch(
        r"(?:cd|change directory)(?: to)? (.+?) in (?:the |a |an )?"
        r"(" + _POS + ") "
        r"(terminal|window|pane)", lower)
    if match:
        return {"action": "terminal_cd", "path": match.group(1).strip(),
                "kind": match.group(3), "side": _norm_side(match.group(2))}
    match = re.match(r"^(?:change directory to |cd to |cd |open terminal (?:in|at) )(.+)$", value, re.I)
    if match:
        return {"action": "terminal", "path": match.group(1).strip()}
    match = re.match(r"^open file (.+)$", value, re.I)
    if match:
        return {"action": "open_file", "path": match.group(1).strip()}
    # tmux: a "voiceos" session hosted in a kitty window so it stays visible.
    match = re.fullmatch(
        r"(?:open|start|launch|attach to)(?: a)? tmux(?: session)?"
        r"(?: (?:called|named) (\S+))?", lower)
    if match:
        return {"action": "tmux_open", "session": match.group(1) or "voiceos"}
    match = re.fullmatch(
        r"(?:new|open) tmux (?:window|win)(?: (?:called|named) (.+))?", lower)
    if match:
        return {"action": "tmux_window", "name": match.group(1)}
    match = re.fullmatch(
        r"split tmux(?: (vertically|horizontally|vertical|horizontal))?", lower)
    if match:
        return {"action": "tmux_split",
                "direction": {"vertically": "-h", "vertical": "-h",
                              "horizontally": "-v", "horizontal": "-v"}.get(
                                  match.group(1), "-h")}
    match = re.fullmatch(
        r"(?:run|exec(?:ute)?) (.+?) in tmux", value, re.I)
    if match:
        return {"action": "tmux_send", "text": match.group(1).strip()}
    match = re.fullmatch(
        r"tmux (?:type|send|write) (.+)", value, re.I)
    if match:
        return {"action": "tmux_send", "text": match.group(1).strip()}
    match = re.fullmatch(
        r"(?:read|show|get) (?:the )?tmux(?: session| pane| window)?"
        r"(?:'s)?(?: (?:output|contents?|scrollback))?", lower)
    if match:
        return {"action": "tmux_read"}
    match = re.fullmatch(
        r"(?:list|show) tmux(?: (?:sessions?|windows?|panes))?s?", lower)
    if match:
        return {"action": "tmux_list"}
    match = re.fullmatch(
        r"(?:run|execute) (.+?) in (?:the |a |an )?(?:(" + _POS + r") )?"
        r"(terminal|window|pane|browser)(?: (?:on|in|at) (?:the )?workspace (\d{1,2}))?", value, re.I)
    if match:
        return {"action": "run", "command": match.group(1),
                "side": _norm_side(match.group(2)) if match.group(2) else None,
                "kind": match.group(3), "workspace": match.group(4)}
    match = re.fullmatch(r"(?:run|execute) (.+)", value, re.I)
    if match:
        return {"action": "run", "command": match.group(1)}
    match = re.fullmatch(
        r"(?:type|send|input) (.+?) in (?:the |a |an )?(?:(" + _POS + r") )?"
        r"(terminal|window|pane|browser)(?: (?:on|in|at) (?:the )?workspace (\d{1,2}))?", value, re.I)
    if match:
        return {"action": "type", "text": match.group(1),
                "side": _norm_side(match.group(2)) if match.group(2) else None,
                "kind": match.group(3), "workspace": match.group(4)}
    match = re.fullmatch(r"(?:type|send|input) (.+)", value, re.I)
    if match:
        return {"action": "type", "text": match.group(1)}
    match = re.fullmatch(
        r"(?:press|hit) (?:the )?(.+?) in (?:the |a |an )?(?:(" + _POS + r") )?"
        r"(terminal|window|pane|browser)(?: (?:on|in|at) (?:the )?workspace (\d{1,2}))?", value, re.I)
    if match:
        return {"action": "press", "keys": match.group(1),
                "side": _norm_side(match.group(2)) if match.group(2) else None,
                "kind": match.group(3), "workspace": match.group(4)}
    match = re.fullmatch(r"(?:press|hit) (?:the )?(.+)", value, re.I)
    if match:
        return {"action": "press", "keys": match.group(1)}
    if lower in ("list windows", "show windows", "list open windows",
                 "show open windows", "list all windows", "what windows are open",
                 "which windows are open", "what's open", "what is open"):
        return {"action": "list_windows"}
    match = re.fullmatch(
        r"(?:read|show|cat) (?:the )?(?:(" + _POS + r") )?"
        r"(terminal|pane|window)(?:'s)?(?: output| contents?| scrollback)?", lower)
    if match:
        return {"action": "read_terminal",
                "side": _norm_side(match.group(1)) if match.group(1) else None}
    match = re.fullmatch(
        r"(?:read|get|show|what(?:'s| is) on) (?:the )?(?:(" + _POS + r") )?"
        r"(browser|web ?page|page|website)(?: (?:on|in|at) (?:the )?workspace (\d{1,2}))?", lower)
    if match:
        return {"action": "read_browser",
                "side": _norm_side(match.group(1)) if match.group(1) else None,
                "workspace": match.group(3)}
    match = re.fullmatch(
        r"(?:get|read|show|what(?:'s| is)) (?:the )?(?:browser |page |current )?url", lower)
    if match:
        return {"action": "browser_url"}
    # Copy terminal scrollback, browser text, or a file to the clipboard:
    # "copy the terminal output", "copy the error", "copy that",
    # "copy file report.txt", "copy the browser page"
    match = re.fullmatch(
        r"copy (?:the |a |an |this |that |last )?(.+?)(?: to (?:the )?clipboard)?", lower)
    if match:
        target = match.group(1).strip()
        side = None
        pm = re.fullmatch(r"(" + _POS + r") (.+)", target)
        if pm:
            side = _norm_side(pm.group(1))
            target = pm.group(2)
        target = re.sub(r"\s*(output|contents?|text|scrollback|logs?|errors?)\s*$",
                        "", target).strip()
        if target.startswith("file "):
            return {"action": "copy_file", "path": target[5:].strip()}
        if target in ("browser", "page", "webpage", "web page", "browser page",
                      "browser window", "url", "link"):
            return {"action": "copy_browser", "side": side}
        if (target.startswith(("~", "/", "."))
                or re.search(r"\.[a-z0-9]{1,5}$", target)):
            return {"action": "copy_file", "path": target}
        # "copy", "copy that", "copy the error", "copy terminal" etc.
        return {"action": "copy_terminal", "side": side}
    # Wallpaper: "change wallpaper", "next wallpaper", "random wallpaper",
    # "set wallpaper to chainsaw", "set wallpaper to ~/Pictures/x.png"
    match = re.fullmatch(
        r"(?:change|next|new|random|shuffle|set|use|switch)(?: the)? wallpaper"
        r"(?: to (.+))?", lower)
    if match:
        return {"action": "wallpaper", "name": match.group(1)}
    match = re.fullmatch(r"wallpaper (.+)", lower)
    if match:
        return {"action": "wallpaper", "name": match.group(1)}
    match = re.fullmatch(r"(?:read|show|cat) file (.+)", value, re.I)
    if match:
        return {"action": "read_file", "path": match.group(1).strip()}
    match = re.fullmatch(r"(?:list|show|ls)(?: the)? files(?: (?:in|of|for) (.+))?", value, re.I)
    if match:
        return {"action": "list_dir", "path": (match.group(1) or "~").strip()}
    match = re.fullmatch(r"(?:list|show|ls) (.+?) (folder|directory|dir)", value, re.I)
    if match:
        return {"action": "list_dir", "path": match.group(1).strip()}
    if re.fullmatch(
            r"(?:take (?:a |an )?)?screen ?shot|"
            r"(?:look|peek) at (?:the |my |this )?screen|"
            r"what(?:'s| is) on (?:the |my |this )?screen|"
            r"show (?:me )?(?:the |my )?screen", lower):
        return {"action": "screenshot"}
    match = re.fullmatch(
        r"(?:(right|middle|double)[ -])?click(?: at| on)? (\d{1,4})[ ,x]+(\d{1,4})", lower)
    if match:
        return {"action": "click", "x": int(match.group(2)), "y": int(match.group(3)),
                **_click_button(match.group(1))}
    match = re.fullmatch(
        r"(?:(right|middle|double)[ -])?click(?: on)? (?:the )?(" + _POS + r") "
        r"(window|terminal|browser|files?|pane)(?: (?:on|in|at) (?:the )?workspace (\d{1,2}))?", lower)
    if match:
        return {"action": "click", "kind": match.group(3),
                "side": _norm_side(match.group(2)), "workspace": match.group(4),
                **_click_button(match.group(1))}
    match = re.fullmatch(
        r"(?:(right|middle|double)[ -])?click(?: on)? (?:the )?"
        r"(window|terminal|browser|files?|pane)(?: (?:on|in|at) (?:the )?workspace (\d{1,2}))?", lower)
    if match:
        return {"action": "click", "kind": match.group(2), "workspace": match.group(3),
                **_click_button(match.group(1))}
    match = re.fullmatch(r"(?:(right|middle|double)[ -])?click", lower)
    if match:
        return {"action": "click", **_click_button(match.group(1))}
    match = re.fullmatch(
        r"scroll (up|down|left|right)(?: (\d{1,2})(?: times| clicks| notches)?)?"
        r"(?: (?:in|on|at) (?:the )?(?:(" + _POS + r") )?"
        r"(window|terminal|browser|files?|pane))?", lower)
    if match:
        return {"action": "scroll", "direction": match.group(1),
                "count": int(match.group(2) or 3),
                "side": _norm_side(match.group(3)) if match.group(3) else None,
                "kind": match.group(4)}
    match = re.fullmatch(
        r"drag (?:from )?(\d{1,4})[ ,]+(\d{1,4}) to (\d{1,4})[ ,]+(\d{1,4})", lower)
    if match:
        return {"action": "drag", "x1": int(match.group(1)), "y1": int(match.group(2)),
                "x2": int(match.group(3)), "y2": int(match.group(4))}
    match = re.fullmatch(
        r"drag (?:the )?(" + _POS + r") (window|terminal|browser|files?|pane) "
        r"to (?:the )?(left|right|top|bottom)", lower)
    if match:
        return {"action": "drag_window", "kind": match.group(2),
                "side": _norm_side(match.group(1)), "direction": match.group(3)}
    match = re.fullmatch(
        r"drag (?:the )?(window|terminal|browser|files?|pane) "
        r"to (?:the )?(left|right|top|bottom)", lower)
    if match:
        return {"action": "drag_window", "kind": match.group(1), "direction": match.group(2)}
    match = re.fullmatch(r"open (.+?) in (?:the |a |an )?(kitty|terminal|vim|nvim|vi|emacs|nano|htop|"
                         r"top|less|man|firefox|chromium|chrome|browser|thunar)", value, re.I)
    if match:
        return {"action": "open_in", "target": match.group(1).strip(),
                "app": match.group(2).lower()}
    match = re.fullmatch(r"(?:open|launch|start) (?:the )?(.+?)( folder| directory)?", value, re.I)
    if match:
        return {"action": "open", "target": match.group(1).strip().rstrip("/"),
                "is_folder": bool(match.group(2))}
    raise CommandError("Command not recognized. Try “workspace 2”, “open terminal”, “search files for notes”, or “open browser”.")


_POS = (r"leftmost|rightmost|topmost|bottommost|upper|lower|"
        r"(?:top|bottom|upper|lower)[ -](?:left|right)|"
        r"left|right|top|bottom")


def _norm_side(raw):
    words = raw.replace("-", " ").split()
    return " ".join({"leftmost": "left", "rightmost": "right", "topmost": "top",
                     "bottommost": "bottom", "upper": "top", "lower": "bottom"}.get(w, w)
                    for w in words)


def _click_button(raw):
    if raw == "right":
        return {"button": 3, "clicks": 1}
    if raw == "middle":
        return {"button": 2, "clicks": 1}
    if raw == "double":
        return {"button": 1, "clicks": 2}
    return {"button": 1, "clicks": 1}


# Every kitty launched through VoiceOS gets its own remote-control socket
# (see docker/kitty-launch.sh); the launcher falls back to plain kitty when
# the script isn't installed.
KITTY_LAUNCHER = "/opt/noVNC/docker/kitty-launch.sh"
KITTY_CMD = KITTY_LAUNCHER if os.path.exists(KITTY_LAUNCHER) else "kitty"


def _ppid(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None


def _kitty_window(kitty_pid):
    """Find the kitty window whose shell runs under the compositor window's
    process. Returns (socket_path, kitty_window_id) or None."""
    if kitty_pid is None:
        return None
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp/voiceos-runtime"
    for sock in sorted(glob.glob(os.path.join(runtime, "kitty-*.sock"))):
        try:
            listing = json.loads(_run("kitty", "@", "--to", "unix:" + sock, "ls"))
        except (CommandError, json.JSONDecodeError):
            continue
        for os_window in listing:
            for tab in os_window.get("tabs", []):
                for win in tab.get("windows", []):
                    child = win.get("pid")
                    if child and _ppid(child) == kitty_pid:
                        return sock, win["id"]
    return None


def _kitty_text(sock, window_id):
    try:
        return _run("kitty", "@", "--to", "unix:" + sock, "get-text",
                    "--extent", "all", "--match", "id:" + str(window_id))
    except CommandError:
        # Older kitty without the "all" extent: fall back to the visible screen.
        return _run("kitty", "@", "--to", "unix:" + sock, "get-text",
                    "--match", "id:" + str(window_id))


def _wl_paste_text(tries=15):
    for _ in range(tries):
        try:
            out = subprocess.run(["wl-paste", "-n"], capture_output=True,
                                 text=True, timeout=3)
            if out.stdout:
                return out.stdout
        except subprocess.SubprocessError:
            pass
        time.sleep(0.1)
    raise CommandError("Nothing landed on the clipboard after copying.")


def _hypr_windows():
    try:
        clients = json.loads(_run("hyprctl", "-j", "clients"))
    except CommandError:
        return []
    try:
        active = json.loads(_run("hyprctl", "-j", "activewindow"))
    except (CommandError, json.JSONDecodeError):
        active = {}
    return [{"workspace": str(c.get("workspace", {}).get("name") or c.get("workspace", {}).get("id")),
             "app_id": c.get("class"), "title": c.get("title"),
             "x": c.get("at", [0, 0])[0], "y": c.get("at", [0, 0])[1],
             "width": c.get("size", [0, 0])[0], "height": c.get("size", [0, 0])[1],
             "focused": c.get("address") == active.get("address")}
            for c in clients if not c.get("hidden")]


_HYPR_KIND = {"terminal": ("kitty",), "browser": ("firefox", "chromium"),
              "files": ("thunar",), "file": ("thunar",)}


def _hypr_target_point(command, vnc):
    windows = _hypr_windows()
    kind = command.get("kind")
    if not windows:
        raise CommandError("No matching window is open.")
    if kind in (None, "window", "pane"):
        candidates = windows
    else:
        needles = _HYPR_KIND.get(kind, (kind,))
        candidates = [w for w in windows if w["app_id"]
                      and any(n in w["app_id"].lower() for n in needles)]
    if not candidates:
        raise CommandError("No matching window is open.")
    side = command.get("side")
    if side:
        component = {"left": lambda w: w["x"], "right": lambda w: -w["x"] - w["width"],
                     "top": lambda w: w["y"], "bottom": lambda w: -w["y"] - w["height"]}
        target = min(candidates,
                     key=lambda w: sum(component[s](w) for s in str(side).split()))
    else:
        target = next((w for w in candidates if w["focused"]), candidates[0])
    return target["x"] + target["width"] / 2, target["y"] + target["height"] / 2


def _target_point(command, vnc):
    """Resolve a click/scroll target to output coordinates."""
    if command.get("x") is not None:
        return command["x"], command["y"]
    if os.environ.get("VOICEOS_COMPOSITOR", "sway") == "hyprland":
        return _hypr_target_point(command, vnc)
    if command.get("kind") or command.get("side") or command.get("workspace"):
        target = _pick_window(command.get("kind") or "window", command.get("side"),
                              command.get("workspace"))
        if target is None:
            raise CommandError("No matching window is open.")
    else:
        target = _focused_con(json.loads(_run("swaymsg", "-t", "get_tree")))
        if target is None:
            raise CommandError("No window is focused.")
    rect = target["rect"]
    return rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2


def _spoken_path(value):
    value = re.sub(r"\bforward slash\b", "/", str(value))
    value = re.sub(r"\bslash\b", "/", value)
    value = re.sub(r"\btilde\b", "~", value)
    return re.sub(r" ?([/~]) ?", r"\1", value).strip()


def home_path(raw):
    path = (HOME / _spoken_path(raw).replace("~", str(HOME), 1)).resolve()
    if not path.is_relative_to(HOME):
        raise CommandError("Files must be inside the desktop user's home directory.")
    return path


def dir_path(raw):
    value = _spoken_path(str(raw).strip())
    if value.startswith("~"):
        path = Path(value.replace("~", str(HOME), 1))
    else:
        path = Path(value)
    if not path.is_absolute():
        path = HOME / path
    return path.resolve()


def _run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=8, check=False)
    if result.returncode:
        raise CommandError(result.stderr.strip() or result.stdout.strip() or "Desktop command failed.")
    return result.stdout


def _spawn(*args):
    # Fully detach stdio so a spawned app can't corrupt JSON tool output
    # or keep the caller's pipes open.
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _sway_terminal(node):
    if node.get("app_id") == "kitty":
        return node.get("id")
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        found = _sway_terminal(child)
        if found is not None:
            return found
    return None


def _sway_windows(node, out):
    if node.get("app_id"):
        out.append(node)
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        _sway_windows(child, out)


KIND_APP_IDS = {
    "terminal": {"kitty"},
    "browser": {"browseros", "BrowserOS", "firefox", "firefox-esr",
                "chromium", "Chromium", "chromium-browser"},  # BrowserOS reports app_id "chromium-browser"
    "files": {"thunar"}, "file": {"thunar"},
}

# BrowserOS is the default browser; the launcher script falls back to
# chromium, then firefox-esr, on platforms without a BrowserOS build.
BROWSER_LAUNCHER = "/opt/noVNC/docker/browser-launch.sh"
_BROWSER_CMD = [BROWSER_LAUNCHER] if os.path.exists(BROWSER_LAUNCHER) else [
    "chromium", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--ozone-platform=wayland"]

OPEN_ALIASES = {
    "browser": _BROWSER_CMD, "browseros": ["browseros", "--no-sandbox",
        "--disable-gpu", "--disable-dev-shm-usage", "--ozone-platform=wayland"],
    "firefox": ["firefox-esr"],
    "files": ["thunar"], "file manager": ["thunar"], "filemanager": ["thunar"],
    "terminal": ["kitty"],
    "chromium": ["chromium", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                 "--ozone-platform=wayland"],
    "chrome": ["chromium", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
               "--ozone-platform=wayland"],
}

# Apps that need a TTY: launched inside a new kitty window.
CONSOLE_APPS = {"terminal", "kitty", "vim", "vi", "nvim", "emacs", "nano", "htop",
                "top", "less", "more", "man", "tig", "ranger", "python", "python3",
                "node", "bash", "sh"}

KEY_NAMES = {"enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
             "tab": "Tab", "backspace": "BackSpace", "bs": "BackSpace", "space": "space",
             "delete": "Delete", "del": "Delete", "up": "Up", "down": "Down",
             "left": "Left", "right": "Right", "home": "Home", "end": "End",
             "pageup": "Page_Up", "pgup": "Page_Up", "pagedown": "Page_Down",
             "pgdn": "Page_Down", "insert": "Insert", "ins": "Insert"}
MOD_NAMES = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
             "super": "super", "meta": "super", "cmd": "super", "win": "super",
             "mod": "super"}


def _wtype_keys(spec):
    parts = re.split(r"[+\-]", spec.lower())
    mods = [MOD_NAMES[p.strip()] for p in parts[:-1] if p.strip() in MOD_NAMES]
    key = parts[-1].strip()
    args = ["-s", "80"]  # space events out; bursts get dropped on this compositor
    for mod in mods:
        args += ["-M", mod]
    if key:
        if re.fullmatch(r"f\d{1,2}", key):
            keyname = key.upper()
        elif len(key) == 1:
            keyname = key
        else:
            keyname = KEY_NAMES.get(key, key.capitalize())
        args += ["-P", keyname, "-p", keyname]
    for mod in reversed(mods):
        args += ["-m", mod]
    return args


def _wtype_text(payload):
    args = []
    pos = 0
    for m in re.finditer(r"\{([^{}]+)\}", payload):
        if m.start() > pos:
            args.append(payload[pos:m.start()])
        args += _wtype_keys(m.group(1))
        pos = m.end()
    if pos < len(payload):
        args.append(payload[pos:])
    return args or [payload]


def _copy(text):
    # wl-copy forks into the background to serve the clipboard; capture pipes
    # would block until a paste happens, so detach stdio and feed stdin.
    proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    proc.stdin.write(text.encode())
    proc.stdin.close()
    # Wait until the clipboard actually holds our text before pasting.
    for _ in range(20):
        try:
            out = subprocess.run(["wl-paste", "-n"], capture_output=True,
                                 text=True, timeout=3)
            if out.stdout == text:
                return
        except subprocess.SubprocessError:
            pass
        time.sleep(0.05)
    raise CommandError("Clipboard did not update.")


def _inject_text(payload, app_id):
    # Type text with wtype; fall back to clipboard paste only for characters
    # wtype cannot synthesize. {key} placeholders always go through wtype.
    for part in re.split(r"(\{[^{}]+\})", payload):
        if not part:
            continue
        if part.startswith("{") and part.endswith("}"):
            _run("wtype", *_wtype_keys(part[1:-1]))
            continue
        result = subprocess.run(["wtype", "-s", "25", part], capture_output=True,
                                text=True, timeout=60)
        if result.returncode == 0:
            time.sleep(0.15)  # let the app settle between key groups
            continue
        _copy(part)
        if app_id == "kitty":
            _run("wtype", "-s", "80", "-M", "ctrl", "-M", "shift", "-k", "v",
                 "-m", "shift", "-m", "ctrl")
        else:
            _run("wtype", "-s", "80", "-M", "ctrl", "-k", "v", "-m", "ctrl")


def _window_running(app, path):
    """If a console app already has this path open, return its window."""
    try:
        found = subprocess.run(["pgrep", "-f", re.escape(str(app) + " " + str(path))],
                               capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    pids = found.stdout.split()
    if not pids:
        return None
    parents = set()
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as f:
                parents.add(int(f.read().rsplit(")", 1)[1].split()[1]))
        except (OSError, IndexError, ValueError):
            continue
    if not parents:
        return None
    windows = []
    for ws in _workspace_nodes(json.loads(_run("swaymsg", "-t", "get_tree"))):
        _sway_windows(ws, windows)
    for w in windows:
        if w.get("pid") in parents:
            return w
    return None


def _focused_con(tree):
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.get("focused") and node.get("app_id"):
            return node
        stack += node.get("nodes", []) + node.get("floating_nodes", [])
    return None


def _sway_focus_name(name):
    """Focus a sway window by app_id or title regex. Returns True on match."""
    esc = re.escape(name)
    for crit in (f'app_id="(?i)^{esc}$"', f'app_id="(?i){esc}"',
                 f'title="(?i)^{esc}$"', f'title="(?i){esc}"'):
        try:
            out = _run("swaymsg", f"[{crit}]", "focus")
        except CommandError:
            continue
        try:
            res = json.loads(out)
        except ValueError:
            return True
        if isinstance(res, list) and res and res[0].get("success"):
            return True
    return False


def _hypr_focus_name(name):
    """Focus a Hyprland window by class or title regex (best-effort)."""
    esc = re.escape(name)
    for sel in (f"class:^(?i){esc}$", f"class:^(?i).*{esc}.*",
                f"title:^(?i).*{esc}.*"):
        try:
            if _run("hyprctl", "dispatch", "focuswindow",
                    sel).strip().startswith("ok"):
                return True
        except CommandError:
            continue
    return False


def _terminal_scrollback(side):
    target = _pick_window("terminal", side)
    if target is None:
        raise CommandError("No matching terminal is open.")
    found = _kitty_window(target.get("pid"))
    if found is None:
        raise CommandError("That terminal's output isn't readable "
                           "(it wasn't started through VoiceOS).")
    sock, win_id = found
    return _kitty_text(sock, win_id).rstrip("\n")


def _tmux(*args):
    return _run("tmux", *args)


def _tmux_has(session):
    return subprocess.run(["tmux", "has-session", "-t", session],
                          capture_output=True).returncode == 0


def _tmux_ensure(session):
    """Guarantee the tmux session exists; create it inside a kitty window
    so it shows up on the desktop."""
    if _tmux_has(session):
        return
    _spawn(KITTY_CMD, "--directory", str(HOME), "-e",
           "tmux", "new-session", "-s", session)
    for _ in range(25):
        if _tmux_has(session):
            return
        time.sleep(0.2)
    raise CommandError("tmux session did not come up.")


def _clipboard_set(text):
    try:
        proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
        proc.communicate(text.encode(), timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CommandError("wl-copy failed: " + str(error))


def _focus_and_wait(con_id, tries=15):
    for _ in range(tries):
        # Re-issue focus each round: a window mapping right now can steal it back.
        _run("swaymsg", f"[con_id={con_id}] focus")
        focused = _focused_con(json.loads(_run("swaymsg", "-t", "get_tree")))
        if focused and focused.get("id") == con_id:
            return True
        time.sleep(0.1)
    return False


def _workspace_nodes(tree):
    for output in tree.get("nodes", []):
        for ws in output.get("nodes", []):
            if ws.get("type") == "workspace":
                yield ws


def _pick_window(kind, side=None, workspace=None):
    tree = json.loads(_run("swaymsg", "-t", "get_tree"))
    windows = []
    for ws in _workspace_nodes(tree):
        if workspace is not None and ws.get("name") != str(workspace):
            continue
        _sway_windows(ws, windows)
    allowed = KIND_APP_IDS.get(kind)
    if allowed is not None:
        windows = [w for w in windows if w.get("app_id") in allowed]
    if not windows:
        return None
    if not side:
        focused = next((w for w in windows if w.get("focused")), None)
        if focused is not None:
            return focused
        return min(windows, key=lambda w: (w["rect"]["y"], w["rect"]["x"]))
    component = {"left": lambda r: r["x"], "right": lambda r: -r["x"] - r["width"],
                 "top": lambda r: r["y"], "bottom": lambda r: -r["y"] - r["height"]}
    def position(w):
        return sum(component[s](w["rect"]) for s in str(side).split())
    return min(windows, key=position)


def layout_summary():
    """Per-workspace description of open windows for the Pi agent's context."""
    if os.environ.get("VOICEOS_COMPOSITOR", "sway") != "sway":
        return ""
    try:
        tree = json.loads(_run("swaymsg", "-t", "get_tree"))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, CommandError):
        return ""
    sections = []
    for ws in _workspace_nodes(tree):
        windows = []
        _sway_windows(ws, windows)
        if not windows:
            continue
        name = ws.get("name") or "?"
        label = "scratchpad" if name.startswith("__i3") else f"workspace {name}"
        centers_x = [w["rect"]["x"] + w["rect"]["width"] / 2 for w in windows]
        centers_y = [w["rect"]["y"] + w["rect"]["height"] / 2 for w in windows]
        mid_x = (min(centers_x) + max(centers_x)) / 2
        mid_y = (min(centers_y) + max(centers_y)) / 2
        parts = []
        for w in sorted(windows, key=lambda n: (n["rect"]["y"], n["rect"]["x"])):
            rect = w["rect"]
            cx = rect["x"] + rect["width"] / 2
            cy = rect["y"] + rect["height"] / 2
            if len(windows) == 1:
                where = "only window"
            else:
                v = "" if abs(cy - mid_y) < 1 else ("top" if cy <= mid_y else "bottom")
                h = "" if abs(cx - mid_x) < 1 else ("left" if cx <= mid_x else "right")
                where = "-".join(p for p in (v, h) if p) or "middle"
            entry = w.get("app_id") or "window"
            entry += " " + where
            if w.get("focused"):
                entry += " (focused)"
            parts.append(entry)
        if any(w.get("focused") for w in windows):
            label += " (current)"
        sections.append(label + ": " + "; ".join(parts))
    return "; ".join(sections) if sections else "no windows open"


def _describe(command):
    text = f"the {command['kind']}"
    if command.get("side"):
        text += f" on the {command['side']}"
    if command.get("workspace"):
        text += f" on workspace {command['workspace']}"
    return text


def execute_command(command):
    action = command["action"]
    compositor = os.environ.get("VOICEOS_COMPOSITOR", "sway")
    if action == "workspace":
        if compositor == "hyprland":
            _run("hyprctl", "dispatch", "workspace", str(command["number"]))
        else:
            _run("swaymsg", f"workspace number {command['number']}")
        return {"message": f"Switched to workspace {command['number']}."}
    if action == "workspace_relative":
        if compositor == "hyprland":
            _run("hyprctl", "dispatch", "workspace", "e" + command["direction"])
        else:
            direction = "next" if command["direction"] == "+1" else "prev"
            _run("swaymsg", "workspace " + direction)
        return {"message": "Switched workspace."}
    if action == "close":
        if compositor == "hyprland":
            _run("hyprctl", "dispatch", "killactive")
        else:
            _run("swaymsg", "kill")
        return {"message": "Closed the active window."}
    if action == "close_directional":
        if compositor == "hyprland":
            raise CommandError("Closing windows by position is not supported on Hyprland.")
        target = _pick_window(command["kind"], command.get("side"), command.get("workspace"))
        if target is None:
            raise CommandError("No matching window is open.")
        _run("swaymsg", f"[con_id={target['id']}] kill")
        return {"message": "Closed " + _describe(command) + "."}
    if action == "focus_window":
        if compositor == "hyprland":
            raise CommandError("Focusing windows by position is not supported on Hyprland.")
        target = _pick_window(command["kind"], command.get("side"), command.get("workspace"))
        if target is None:
            raise CommandError("No matching window is open.")
        _focus_and_wait(target["id"])
        return {"message": "Focused " + _describe(command) + "."}
    if action == "terminal_cd":
        if compositor == "hyprland":
            raise CommandError("Typing into windows is not supported on Hyprland.")
        target = _pick_window(command["kind"], command["side"])
        if target is None:
            raise CommandError("No matching terminal is open.")
        path = dir_path(command["path"])
        if not path.is_dir():
            raise CommandError("Directory does not exist: " + str(path))
        if not _focus_and_wait(target["id"]):
            raise CommandError("Could not focus the target terminal.")
        _inject_text("cd " + shlex.quote(str(path)), target.get("app_id"))
        _run("wtype", "-s", "80", "-P", "Return", "-p", "Return")
        return {"message": f"Ran cd {path} in the {command['side']} {command['kind']}."}
    if action == "focus":
        if compositor == "hyprland":
            _run("hyprctl", "dispatch", "movefocus",
                 {"left": "l", "right": "r", "up": "u", "down": "d"}[command["direction"]])
        else:
            _run("swaymsg", "focus", command["direction"])
        return {"message": f"Focused the window to the {command['direction']}."}
    if action == "focus_app":
        name = command["name"]
        # Prefer a known app kind so "the browser" matches any browser id.
        kind = next((k for k, ids in KIND_APP_IDS.items()
                     if name == k or name in ids), None)
        if compositor == "sway":
            if kind is not None or not name:
                target = _pick_window(kind, command.get("side"),
                                      command.get("workspace"))
                if target is not None:
                    _run("swaymsg", f"[con_id={target['id']}] focus")
                    return {"message": f"Focused {target['name']}."}
            if name and _sway_focus_name(name):
                return {"message": f"Focused {name}."}
        elif compositor == "hyprland":
            if _hypr_focus_name(name):
                return {"message": f"Focused {name}."}
        raise CommandError(f"No window matching '{name or 'that'}' is open.")
    if action == "run":
        if compositor == "hyprland":
            raise CommandError("Typing into windows is not supported on Hyprland.")
        kind = command.get("kind") or "terminal"
        target = _pick_window(kind, command.get("side"), command.get("workspace"))
        if target is None and kind == "terminal":
            _spawn(KITTY_CMD, "--directory", str(HOME))
            for _ in range(25):
                time.sleep(0.2)
                target = _pick_window(kind)
                if target:
                    break
        if target is None:
            raise CommandError("No matching window is open.")
        if not _focus_and_wait(target["id"]):
            raise CommandError("Could not focus the target window.")
        _inject_text(command["command"], target.get("app_id"))
        _run("wtype", "-s", "80", "-P", "Return", "-p", "Return")
        return {"message": "Ran: " + command["command"]}
    if action in ("type", "press"):
        if compositor == "hyprland":
            raise CommandError("Typing into windows is not supported on Hyprland.")
        if command.get("kind") or command.get("side") or command.get("workspace"):
            target = _pick_window(command.get("kind"), command.get("side"),
                                  command.get("workspace"))
            if target is None:
                raise CommandError("No matching window is open.")
            if not _focus_and_wait(target["id"]):
                raise CommandError("Could not focus the target window.")
        else:
            target = _focused_con(json.loads(_run("swaymsg", "-t", "get_tree")))
            if target is None:
                raise CommandError("No window is focused.")
        if action == "type":
            _inject_text(command["text"], target.get("app_id"))
            return {"message": "Typed the text."}
        args = []
        for token in command["keys"].split():
            args += _wtype_keys(token)
        _run("wtype", *args)
        return {"message": "Pressed " + command["keys"] + "."}
    if action == "open_in":
        target = command["target"]
        app = command["app"]
        path = dir_path(target)
        exists = path.exists()
        if app in ("terminal", "kitty"):
            cwd = str(path if path.is_dir() else path.parent) if exists else str(HOME)
            _spawn(KITTY_CMD, "--directory", cwd)
            return {"message": "Opened a terminal in " + cwd + "."}
        if not exists:
            raise CommandError("Nothing found at " + str(path))
        if app in CONSOLE_APPS:
            existing = _window_running(app, path)
            if existing is not None:
                _focus_and_wait(existing["id"])
                return {"message": f"{app} is already open on {path}; focused it."}
            _spawn(KITTY_CMD, "--directory", str(path.parent if path.is_file() else path),
                   app, str(path))
        else:
            argv = OPEN_ALIASES.get(app, [app]) + [str(path)]
            _spawn(*argv)
        return {"message": f"Opened {path} in {app}."}
    if action == "open":
        target = command["target"]
        argv = OPEN_ALIASES.get(target.lower())
        if argv is None and shutil.which(target):
            argv = [target]
        if argv:
            if argv[0] in CONSOLE_APPS:
                argv = [KITTY_CMD, "--directory", str(HOME)] + argv
            _spawn(*argv)
            return {"message": "Opened " + target + "."}
        path = home_path(target)
        if path.is_dir():
            _spawn("thunar", str(path))
            return {"message": "Opened folder " + str(path) + "."}
        if path.is_file():
            _spawn("xdg-open", str(path))
            return {"message": "Opened " + str(path) + "."}
        if command.get("is_folder"):
            path.mkdir(parents=True, exist_ok=True)
            _spawn("thunar", str(path))
            return {"message": "Created and opened folder " + str(path) + "."}
        raise CommandError(
            "Can't open '" + target + "': not an installed app or a file/folder in the home directory.")
    if action == "split":
        if compositor == "hyprland":
            raise CommandError("Window splitting is not supported on Hyprland.")
        if command.get("side"):
            target = _pick_window(command.get("kind"), command["side"])
            if target is None:
                raise CommandError("No matching window is open.")
            _focus_and_wait(target["id"])
        # "Vertical" means a vertical divider (panes side by side), which is
        # sway's splith; "horizontal" stacks panes top/bottom, sway's splitv.
        direction = {"vertical": "h", "horizontal": "v"}.get(command["direction"], "toggle")
        _run("swaymsg", "split", direction)
        _spawn(KITTY_CMD, "--directory", str(HOME))
        if command.get("side"):
            return {"message": f"Split the {command['side']} {command.get('kind', 'window')} "
                               f"{command['direction']}ly and opened a terminal."}
        return {"message": f"Opened a new terminal in a {command['direction']} split."}
    if action == "close_terminal":
        if compositor == "hyprland":
            clients = json.loads(_run("hyprctl", "-j", "clients"))
            terminal = next((client for client in clients if client.get("class", "").lower() == "kitty"), None)
            if terminal is None:
                raise CommandError("No terminal window is open.")
            _run("hyprctl", "dispatch", "focuswindow", "address:" + terminal["address"])
            _run("hyprctl", "dispatch", "killactive")
        else:
            terminal_id = _sway_terminal(json.loads(_run("swaymsg", "-t", "get_tree")))
            if terminal_id is None:
                raise CommandError("No terminal window is open.")
            _run("swaymsg", f"[con_id={terminal_id}] kill")
        return {"message": "Closed a terminal."}
    if action == "terminal":
        path = dir_path(command.get("path", "~"))
        if not path.is_dir():
            raise CommandError("Directory does not exist: " + str(path))
        _spawn(KITTY_CMD, "--directory", str(path))
        return {"message": "Opened a terminal in " + str(path)}
    if action == "browser":
        _spawn(*_BROWSER_CMD, "--new-window", command["url"])
        return {"message": "Opened browser."}
    if action == "files":
        _spawn("thunar", str(HOME))
        return {"message": "Opened files."}
    if action == "open_file":
        path = home_path(command["path"])
        if not path.is_file():
            raise CommandError("File does not exist: " + str(path))
        _spawn("xdg-open", str(path))
        return {"message": "Opened " + str(path)}
    if action == "search_files":
        query = command["query"].casefold()
        if len(query) < 2:
            raise CommandError("Search for at least two characters.")
        matches = []
        for root, dirs, files in os.walk(HOME):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            for name in files:
                if query in name.casefold():
                    matches.append(str(Path(root) / name))
                    if len(matches) >= MAX_RESULTS:
                        return {"message": "Showing the first 30 matching files.", "files": matches}
        return {"message": f"Found {len(matches)} matching files.", "files": matches}
    if action == "list_windows":
        if compositor == "hyprland":
            windows = _hypr_windows()
        else:
            tree = json.loads(_run("swaymsg", "-t", "get_tree"))
            windows = []
            for ws in _workspace_nodes(tree):
                found = []
                _sway_windows(ws, found)
                for w in found:
                    windows.append({"workspace": ws.get("name"),
                                    "app_id": w.get("app_id"),
                                    "title": w.get("name"),
                                    "x": w["rect"]["x"], "y": w["rect"]["y"],
                                    "width": w["rect"]["width"],
                                    "height": w["rect"]["height"],
                                    "focused": bool(w.get("focused"))})
        return {"message": f"{len(windows)} window(s) open.", "windows": windows}
    if action == "read_terminal":
        if compositor != "sway":
            raise CommandError("Reading terminal output is only supported on Sway.")
        text = _terminal_scrollback(command.get("side"))
        return {"message": "Read the terminal output.",
                "text": text[-MAX_TEXT_CHARS:],
                "truncated": len(text) > MAX_TEXT_CHARS}
    if action == "copy_terminal":
        if compositor != "sway":
            raise CommandError("Reading terminal output is only supported on Sway.")
        text = _terminal_scrollback(command.get("side"))
        _clipboard_set(text)
        return {"message": f"Copied the terminal output ({len(text)} chars) "
                           "to the clipboard."}
    if action == "copy_browser":
        # The browser read already lands on the clipboard via Ctrl+A/C.
        result = execute_command({"action": "read_browser",
                                  "side": command.get("side")})
        return {"message": "Copied the browser page to the clipboard.",
                "chars": len(result.get("text", ""))}
    if action == "copy_file":
        path = home_path(command["path"])
        if not path.is_file():
            raise CommandError("File does not exist: " + str(path))
        try:
            text = path.read_text(errors="replace")
        except OSError as error:
            raise CommandError(str(error))
        _clipboard_set(text)
        return {"message": f"Copied {path.name} ({len(text)} chars) "
                           "to the clipboard."}
    if action == "read_browser":
        if compositor != "sway":
            raise CommandError("Reading browser text is only supported on Sway.")
        target = _pick_window("browser", command.get("side"), command.get("workspace"))
        if target is None:
            raise CommandError("No browser window is open.")
        if not _focus_and_wait(target["id"]):
            raise CommandError("Could not focus the browser window.")
        _run("wtype", "-s", "80", "-M", "ctrl", "-k", "a", "-m", "ctrl")
        _run("wtype", "-s", "80", "-M", "ctrl", "-k", "c", "-m", "ctrl")
        text = _wl_paste_text()
        _run("wtype", *_wtype_keys("escape"))
        return {"message": "Read the browser page (via clipboard).",
                "title": target.get("name"),
                "text": text[:MAX_TEXT_CHARS],
                "truncated": len(text) > MAX_TEXT_CHARS}
    if action == "browser_url":
        if compositor != "sway":
            raise CommandError("Reading the browser URL is only supported on Sway.")
        target = _pick_window("browser", command.get("side"))
        if target is None:
            raise CommandError("No browser window is open.")
        if not _focus_and_wait(target["id"]):
            raise CommandError("Could not focus the browser window.")
        _run("wtype", "-s", "80", "-M", "ctrl", "-k", "l", "-m", "ctrl")
        _run("wtype", "-s", "80", "-M", "ctrl", "-k", "c", "-m", "ctrl")
        url = _wl_paste_text().strip()
        _run("wtype", *_wtype_keys("escape"))
        return {"message": "The browser is at " + url, "url": url}
    if action == "read_file":
        path = home_path(command["path"])
        if not path.is_file():
            raise CommandError("File does not exist: " + str(path))
        try:
            text = path.read_text(errors="replace")
        except OSError as error:
            raise CommandError(str(error))
        return {"message": "Read " + str(path) + ".", "path": str(path),
                "text": text[:MAX_TEXT_CHARS],
                "truncated": len(text) > MAX_TEXT_CHARS}
    if action == "list_dir":
        path = home_path(command.get("path") or "~")
        if not path.is_dir():
            raise CommandError("Directory does not exist: " + str(path))
        entries = sorted(child.name + ("/" if child.is_dir() else "")
                         for child in path.iterdir())
        return {"message": f"{len(entries)} entries in {path}.",
                "path": str(path), "entries": entries[:200],
                "truncated": len(entries) > 200}
    if action == "tmux_open":
        session = command["session"]
        if _tmux_has(session):
            _spawn(KITTY_CMD, "--directory", str(HOME), "-e",
                   "tmux", "attach", "-t", session)
            return {"message": f"Attached a terminal to tmux session '{session}'."}
        _tmux_ensure(session)
        return {"message": f"Started tmux session '{session}' in a terminal."}
    if action == "tmux_window":
        _tmux_ensure("voiceos")
        argv = ["new-window", "-t", "voiceos:"]
        if command.get("name"):
            argv += ["-n", command["name"]]
        _tmux(*argv)
        return {"message": "Opened a new tmux window"
                           + (f" '{command['name']}'." if command.get("name") else ".")}
    if action == "tmux_split":
        _tmux_ensure("voiceos")
        _tmux("split-window", command["direction"], "-t", "voiceos:")
        return {"message": "Split the tmux pane "
                           + ("side by side." if command["direction"] == "-h"
                              else "top and bottom.")}
    if action == "tmux_send":
        _tmux_ensure("voiceos")
        _tmux("send-keys", "-l", "-t", "voiceos:", command["text"])
        _tmux("send-keys", "-t", "voiceos:", "Enter")
        return {"message": f"Ran '{command['text']}' in tmux."}
    if action == "tmux_read":
        if not _tmux_has("voiceos"):
            raise CommandError("No tmux session is running; say "
                               "'open tmux' to start one.")
        text = _tmux("capture-pane", "-p", "-t", "voiceos:",
                     "-S", "-200").rstrip("\n")
        return {"message": "Read the tmux session output.",
                "text": text[-MAX_TEXT_CHARS:],
                "truncated": len(text) > MAX_TEXT_CHARS}
    if action == "tmux_list":
        try:
            sessions = _tmux("list-sessions", "-F",
                             "#{session_name}: #{session_windows} window(s)")
        except CommandError:
            raise CommandError("No tmux sessions are running.")
        windows = _tmux("list-windows", "-a", "-F",
                        "#{session_name}:#{window_index} #{window_name}"
                        "#{?window_active, (active),}")
        return {"message": "tmux sessions listed.", "sessions": sessions,
                "windows": windows}
    if action == "wallpaper":
        wall_dir = HOME / "Pictures" / "wallpapers"
        exts = (".png", ".jpg", ".jpeg", ".webp")
        images = sorted(p for p in wall_dir.iterdir()
                        if p.suffix.lower() in exts) if wall_dir.is_dir() else []
        marker = HOME / ".config" / "voiceos-wallpaper"
        name = (command.get("name") or "").strip()
        path = None
        if name:
            # substring match against bundled wallpapers first,
            # ignoring case and separators ("aesthetic deer" →
            # aesthetic_deer.png)
            norm = lambda s: re.sub(r"[\s_\-.]+", "", s)  # noqa: E731
            hits = [p for p in images if norm(name) in norm(p.stem)]
            if hits:
                path = hits[0]
            else:
                path = home_path(name)
                if not path.is_file() or path.suffix.lower() not in exts:
                    raise CommandError(f"No wallpaper image matching '{name}'.")
        else:
            if not images:
                raise CommandError("No wallpapers in ~/Pictures/wallpapers.")
            try:
                current = marker.read_text().strip()
            except OSError:
                current = ""
            idx = next((i for i, p in enumerate(images)
                        if str(p) == current), -1)
            path = images[(idx + 1) % len(images)]
        subprocess.run(["pkill", "-x", "swaybg"], capture_output=True)
        _spawn("swaybg", "-i", str(path), "-m", "fill")
        try:
            marker.write_text(str(path))
        except OSError:
            pass
        return {"message": f"Wallpaper set to {path.name}.",
                "wallpaper": str(path)}
    if action == "screenshot":
        if os.environ.get("VOICEOS_SCREENSHOTS", "").lower() not in ("1", "true", "yes", "on"):
            raise CommandError("Screen capture is disabled. "
                               "Set VOICEOS_SCREENSHOTS=1 to enable it.")
        tmp = f"/tmp/voiceos-shot-{int(time.time() * 1000)}.png"
        _run("grim", tmp)
        try:
            from PIL import Image
            with Image.open(tmp) as source:
                img = source.convert("RGB")
            max_side = int(os.environ.get("VOICEOS_SCREENSHOT_MAX", "1024"))
            img.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=65)
            data = base64.b64encode(buf.getvalue()).decode()
            return {"message": "Captured the screen.", "image": data,
                    "mime": "image/jpeg", "width": img.width, "height": img.height}
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if action == "click":
        with vnc_input.VNC() as vnc:
            x, y = _target_point(command, vnc)
            vnc.click(x, y, command.get("button", 1), command.get("clicks", 1))
        name = {1: "Clicked", 2: "Middle-clicked", 3: "Right-clicked"}[command.get("button", 1)]
        if command.get("clicks", 1) == 2:
            name = "Double-clicked"
        return {"message": f"{name} at {int(x)}, {int(y)}.", "x": int(x), "y": int(y)}
    if action == "scroll":
        buttons = {"up": 4, "down": 5, "left": 6, "right": 7}
        count = min(command.get("count", 3), 20)
        with vnc_input.VNC() as vnc:
            x, y = _target_point(command, vnc)
            vnc.scroll(x, y, buttons[command["direction"]], count)
        return {"message": f"Scrolled {command['direction']} {count} times "
                           f"at {int(x)}, {int(y)}."}
    if action == "drag":
        with vnc_input.VNC() as vnc:
            vnc.drag(command["x1"], command["y1"], command["x2"], command["y2"])
        return {"message": f"Dragged from {command['x1']},{command['y1']} "
                           f"to {command['x2']},{command['y2']}."}
    if action == "drag_window":
        with vnc_input.VNC() as vnc:
            x1, y1 = _target_point(command, vnc)
            x2, y2 = {"left": (vnc.width * 0.22, vnc.height / 2),
                      "right": (vnc.width * 0.78, vnc.height / 2),
                      "top": (vnc.width / 2, vnc.height * 0.18),
                      "bottom": (vnc.width / 2, vnc.height * 0.82)}[command["direction"]]
            vnc.drag(x1, y1, x2, y2, super_held=True)
        return {"message": f"Dragged the window to the {command['direction']}."}
    raise CommandError("Unsupported action.")
