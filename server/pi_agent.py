"""Run one scoped Pi session for a spoken desktop request."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from voice_commands import CommandError, layout_summary


PI_DIR = Path(__file__).resolve().parent / "pi-agent"
PI_BIN = PI_DIR / "node_modules" / ".bin" / "pi"
MODELS_JSON_SRC = PI_DIR / "models.json"
MAX_TEXT = 500
MAX_HISTORY = 6

_current_proc = None
_proc_lock = threading.Lock()


def cancel_current():
    """Kill the in-flight Pi subprocess, if any."""
    with _proc_lock:
        proc = _current_proc
    if proc is not None and proc.poll() is None:
        proc.kill()
        return True
    return False


def interpret_voice_request(text, history=None):
    text = " ".join(str(text).split())
    if not text or len(text) > MAX_TEXT:
        raise CommandError("Enter a command of at most 500 characters.")
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
        raise RuntimeError("OPENAI_API_KEY is required for the Pi voice agent (LiteLLM proxy).")
    if not PI_BIN.exists():
        raise RuntimeError("Pi is not installed. Rebuild the VoiceOS Docker image.")

    # Pi reads custom providers from ~/.pi/agent/models.json; keep it in sync
    # with the repo copy on every request.
    agent_dir = Path.home() / ".pi" / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MODELS_JSON_SRC, agent_dir / "models.json")

    context = []
    layout = layout_summary()
    if layout:
        context.append("Current windows: " + layout)
    for item in (history or [])[-MAX_HISTORY:]:
        context.append("Earlier request: " + item["text"] +
                       " → " + item.get("message", ""))
    prompt = "[" + "] [".join(context) + "] " + text if context else text
    command = [
        str(PI_BIN), "--mode", "json", "--no-session", "--no-builtin-tools",
        "--no-extensions", "--extension", str(PI_DIR / "desktop.ts"),
        "--tools", "desktop_action", "--no-skills", "--no-prompt-templates",
        "--no-context-files", "--system-prompt", str(PI_DIR / "SYSTEM.md"),
        "--provider", "litellm",
        "--model", os.environ.get("VOICEOS_PI_MODEL", "litellm/deepseek-v4.1-flash"),
        "--", prompt,
    ]
    global _current_proc
    with _proc_lock:
        _current_proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True,
                                         cwd=Path.home())
    try:
        stdout, stderr = _current_proc.communicate(timeout=90)
        returncode = _current_proc.returncode
    except subprocess.TimeoutExpired as error:
        _current_proc.kill()
        _current_proc.communicate()
        raise RuntimeError("Pi took too long to respond.") from error
    finally:
        with _proc_lock:
            _current_proc = None
    if returncode:
        if returncode < 0:
            raise RuntimeError("The request was cancelled.")
        raise RuntimeError("Pi failed: " + (stderr.strip()[-500:] or "unknown error"))

    actions = []
    final_text = ""
    model_error = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "tool_execution_end" and event.get("toolName") == "desktop_action":
            if event.get("isError"):
                content = event.get("result", {}).get("content", [])
                actions.append({"error": content[0].get("text", "Desktop action failed.") if content else "Desktop action failed."})
            else:
                actions.append(event.get("result", {}).get("details", {}))
        if event.get("type") == "message_end" and event.get("message", {}).get("role") == "assistant":
            message = event["message"]
            if message.get("stopReason") == "error":
                model_error = message.get("errorMessage", "Pi model request failed.")
            text_parts = [item.get("text", "") for item in message.get("content", []) if item.get("type") == "text"]
            if text_parts:
                final_text = "".join(text_parts).strip()
    if model_error and not actions:
        raise RuntimeError(model_error)
    files = [path for action in actions for path in action.get("files", [])]
    return {"message": final_text or (actions[-1].get("message", "Done.") if actions else "I couldn't identify an action."),
            "actions": actions, "files": files}
