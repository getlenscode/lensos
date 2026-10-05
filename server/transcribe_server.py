"""
Small backend that proxies audio transcription requests to Groq's Whisper
API. Kept separate from the static noVNC files so the GROQ_API_KEY never
reaches the browser.

Run with:
    pip install -r server/requirements.txt
    cp .env.example .env  # then fill in GROQ_API_KEY
    python3 server/transcribe_server.py
"""

import os
import socket
import subprocess
import threading
import time
from collections import deque

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
import pi_agent
from pi_agent import interpret_voice_request
from voice_commands import (CommandError, desktop_lock, execute_command,
                            layout_summary, parse_command)

# Looks for a .env file in the current working directory, then walks up.
# In the Docker image this is /opt/noVNC/.env (mounted or copied in).
load_dotenv()

app = Flask(__name__)
_started_at = time.monotonic()

# Recent request/reply pairs, sent to Pi as context for follow-up requests
# ("now close it", "and switch back").
_history = deque(maxlen=8)
# Only one Pi session at a time: two sessions would fight over window focus.
_agent_lock = threading.Lock()


@app.post("/api/agent")
def agent_command():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    if not _agent_lock.acquire(blocking=False):
        return jsonify(error="Another request is still running. "
                             "Cancel it or wait for it to finish."), 409
    try:
        result = interpret_voice_request(text, history=list(_history))
        _history.append({"text": " ".join(str(text).split()),
                         "message": result.get("message", "")})
        return jsonify(**result)
    except CommandError as error:
        return jsonify(error=str(error)), 400
    except RuntimeError as error:
        return jsonify(error=str(error)), 503
    finally:
        _agent_lock.release()


@app.post("/api/cancel")
def cancel_agent():
    if pi_agent.cancel_current():
        return jsonify(message="Cancelled the running request.")
    return jsonify(error="No request is running."), 404


@app.post("/api/command")
def desktop_command():
    payload = request.get_json(silent=True) or {}
    try:
        with desktop_lock():
            command = parse_command(payload.get("text", ""))
            result = dict(action=command["action"], **execute_command(command))
        state = layout_summary()
        if state:
            result["windows"] = state
        return jsonify(**result)
    except CommandError as error:
        return jsonify(error=str(error)), 400
    except (OSError, subprocess.SubprocessError) as error:
        return jsonify(error="Desktop action failed: " + str(error)), 503


def _tcp_open(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


@app.get("/api/health")
def health():
    compositor = os.environ.get("VOICEOS_COMPOSITOR", "sway")
    try:
        if compositor == "sway":
            subprocess.run(["swaymsg", "-t", "get_version"], capture_output=True,
                           timeout=3, check=True)
            compositor_ok = True
        else:
            subprocess.run(["hyprctl", "version"], capture_output=True,
                           timeout=3, check=True)
            compositor_ok = True
    except (OSError, subprocess.SubprocessError):
        compositor_ok = False
    checks = {
        "compositor": compositor_ok,
        "wayvnc": _tcp_open(5901),
        "websockify": _tcp_open(6081),
        "litellm": _tcp_open(4000),
        "pi": pi_agent.PI_BIN.exists(),
        "transcription_key": bool(os.environ.get("GROQ_API_KEY")),
        "agent_key": bool(os.environ.get("OPENAI_API_KEY")
                          or os.environ.get("OPENROUTER_API_KEY")),
        "screenshots": os.environ.get("VOICEOS_SCREENSHOTS", "").lower()
                       in ("1", "true", "yes", "on"),
        "uptime_seconds": int(time.monotonic() - _started_at),
    }
    status = 200 if all(v for k, v in checks.items()
                        if k in ("compositor", "wayvnc", "websockify", "pi")) else 503
    return jsonify(ok=status == 200, checks=checks), status

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3-turbo"

MAX_BYTES = int(19.5 * 1024 * 1024)
ALLOWED_EXTENSIONS = {"flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "wav", "webm"}


def _extension(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _transcribe_bytes(data, filename):
    if len(data) > MAX_BYTES:
        return None, ("Audio file exceeds the 19.5MB limit", 413)

    ext = _extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        return None, (
            "Unsupported audio format: ." + ext +
            " (supported: " + ", ".join(sorted(ALLOWED_EXTENSIONS)) + ")",
            415,
        )

    if not GROQ_API_KEY:
        return None, ("GROQ_API_KEY is not configured on the server", 500)

    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": "Bearer " + GROQ_API_KEY},
        files={"file": (filename, data)},
        data={
            "model": MODEL,
            "temperature": "0",
            "response_format": "verbose_json",
        },
        timeout=120,
    )
    if resp.status_code != 200:
        return None, ("Transcription failed: " + resp.text, resp.status_code)

    return resp.json(), None


@app.post("/api/transcribe")
def transcribe_upload():
    if "file" not in request.files:
        return jsonify(error="No file provided"), 400

    f = request.files["file"]
    data = f.read()

    result, error = _transcribe_bytes(data, f.filename or "audio.webm")
    if error:
        message, status = error
        return jsonify(error=message), status

    return jsonify(text=result.get("text", ""), raw=result)


@app.post("/api/transcribe-url")
def transcribe_url():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify(error="No URL provided"), 400
    if not url.startswith(("http://", "https://")):
        return jsonify(error="URL must start with http:// or https://"), 400

    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()

            content_length = r.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_BYTES:
                return jsonify(error="Audio file exceeds the 19.5MB limit"), 413

            chunks = []
            total = 0
            for chunk in r.iter_content(chunk_size=1 << 16):
                total += len(chunk)
                if total > MAX_BYTES:
                    return jsonify(error="Audio file exceeds the 19.5MB limit"), 413
                chunks.append(chunk)
            data = b"".join(chunks)
    except requests.RequestException as e:
        return jsonify(error="Could not download audio: " + str(e)), 400

    filename = url.split("?", 1)[0].rsplit("/", 1)[-1] or "audio"
    if "." not in filename:
        filename += ".mp3"

    result, error = _transcribe_bytes(data, filename)
    if error:
        message, status = error
        return jsonify(error=message), status

    return jsonify(text=result.get("text", ""), raw=result)


if __name__ == "__main__":
    port = int(os.environ.get("TRANSCRIBE_PORT", 5000))
    app.run(host="127.0.0.1", port=port, threaded=True)
