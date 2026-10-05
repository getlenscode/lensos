import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import voice_commands as voice


class VoiceCommandsTest(unittest.TestCase):
    def test_common_intents(self):
        self.assertEqual(voice.parse_command("switch to workspace 3"),
                         {"action": "workspace", "number": 3})
        self.assertEqual(voice.parse_command("open terminal"), {"action": "terminal"})
        self.assertEqual(voice.parse_command("close terminal"), {"action": "close_terminal"})
        self.assertEqual(voice.parse_command("cd to ~/projects"),
                         {"action": "terminal", "path": "~/projects"})
        self.assertEqual(voice.parse_command("find files named report"),
                         {"action": "search_files", "query": "report"})
        self.assertEqual(voice.parse_command("split the open terminal vertically"),
                         {"action": "split", "direction": "vertical"})
        self.assertEqual(voice.parse_command("split horizontally"),
                         {"action": "split", "direction": "horizontal"})
        self.assertEqual(voice.parse_command("split"),
                         {"action": "split", "direction": "toggle"})

    def test_observation_intents(self):
        self.assertEqual(voice.parse_command("list windows"),
                         {"action": "list_windows"})
        self.assertEqual(voice.parse_command("read the top terminal"),
                         {"action": "read_terminal", "side": "top"})
        self.assertEqual(voice.parse_command("read browser"),
                         {"action": "read_browser", "side": None, "workspace": None})
        self.assertEqual(voice.parse_command("what's the url"),
                         {"action": "browser_url"})
        self.assertEqual(voice.parse_command("read file notes.txt"),
                         {"action": "read_file", "path": "notes.txt"})
        self.assertEqual(voice.parse_command("list files in Downloads"),
                         {"action": "list_dir", "path": "Downloads"})
        self.assertEqual(voice.parse_command("screenshot"), {"action": "screenshot"})
        self.assertEqual(voice.parse_command("look at the screen"),
                         {"action": "screenshot"})

    def test_focus_by_name(self):
        self.assertEqual(voice.parse_command("focus the browser"),
                         {"action": "focus_app", "name": "browser", "side": None})
        self.assertEqual(voice.parse_command("switch to nvim"),
                         {"action": "focus_app", "name": "nvim", "side": None})
        self.assertEqual(voice.parse_command("focus the right terminal"),
                         {"action": "focus_window", "kind": "terminal",
                          "side": "right", "workspace": None})

    def test_copy_intents(self):
        self.assertEqual(voice.parse_command("copy the terminal output"),
                         {"action": "copy_terminal", "side": None})
        self.assertEqual(voice.parse_command("copy that"),
                         {"action": "copy_terminal", "side": None})
        self.assertEqual(voice.parse_command("copy the error to clipboard"),
                         {"action": "copy_terminal", "side": None})
        self.assertEqual(voice.parse_command("copy file report.txt"),
                         {"action": "copy_file", "path": "report.txt"})
        self.assertEqual(voice.parse_command("copy the browser page"),
                         {"action": "copy_browser", "side": None})

    def test_tmux_intents(self):
        self.assertEqual(voice.parse_command("open tmux"),
                         {"action": "tmux_open", "session": "voiceos"})
        self.assertEqual(voice.parse_command("open tmux session called work"),
                         {"action": "tmux_open", "session": "work"})
        self.assertEqual(voice.parse_command("split tmux horizontally"),
                         {"action": "tmux_split", "direction": "-v"})
        self.assertEqual(voice.parse_command("run ls -la in tmux"),
                         {"action": "tmux_send", "text": "ls -la"})
        self.assertEqual(voice.parse_command("read tmux"),
                         {"action": "tmux_read"})
        self.assertEqual(voice.parse_command("list tmux"),
                         {"action": "tmux_list"})
        # the plain terminal rule still wins for non-tmux targets
        self.assertEqual(voice.parse_command("run ls in the terminal")["action"],
                         "run")

    def test_copy_file_stays_inside_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            (home / "notes.txt").write_text("hello")
            with patch.object(voice, "HOME", home), \
                    patch.object(voice.subprocess, "Popen") as popen:
                result = voice.execute_command(
                    {"action": "copy_file", "path": "notes.txt"})
                self.assertIn("Copied notes.txt", result["message"])
                popen.assert_called_once()
                self.assertEqual(popen.call_args[0][0], ["wl-copy"])
                popen.return_value.communicate.assert_called_once()
                self.assertEqual(
                    popen.return_value.communicate.call_args[0][0], b"hello")
            with patch.object(voice, "HOME", home), \
                    self.assertRaises(voice.CommandError):
                voice.execute_command(
                    {"action": "copy_file", "path": "../outside"})

    def test_pointer_intents(self):
        self.assertEqual(voice.parse_command("click at 100 200"),
                         {"action": "click", "x": 100, "y": 200,
                          "button": 1, "clicks": 1})
        self.assertEqual(voice.parse_command("right-click the left terminal"),
                         {"action": "click", "kind": "terminal", "side": "left",
                          "workspace": None, "button": 3, "clicks": 1})
        self.assertEqual(voice.parse_command("double click the window"),
                         {"action": "click", "kind": "window", "workspace": None,
                          "button": 1, "clicks": 2})
        self.assertEqual(voice.parse_command("scroll down 5 times in the browser"),
                         {"action": "scroll", "direction": "down", "count": 5,
                          "side": None, "kind": "browser"})
        self.assertEqual(voice.parse_command("scroll up"),
                         {"action": "scroll", "direction": "up", "count": 3,
                          "side": None, "kind": None})
        self.assertEqual(voice.parse_command("drag 10 20 to 300 400"),
                         {"action": "drag", "x1": 10, "y1": 20, "x2": 300, "y2": 400})
        self.assertEqual(voice.parse_command("drag the left window to the right"),
                         {"action": "drag_window", "kind": "window",
                          "side": "left", "direction": "right"})

    def test_screenshot_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(voice.CommandError):
                voice.execute_command({"action": "screenshot"})

    def test_rejects_unknown_and_unsafe_targets(self):
        # `run` intentionally accepts arbitrary shell text — the desktop
        # vocabulary allows anything the apps themselves allow.
        for text in ("workspace 99", "delete everything",
                     "open url file:///etc/passwd"):
            with self.subTest(text=text), self.assertRaises(voice.CommandError):
                voice.parse_command(text)

    def test_file_actions_stay_inside_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "notes.txt").write_text("test")
            with patch.object(voice, "HOME", home):
                with self.assertRaises(voice.CommandError):
                    voice.home_path("../outside")
                result = voice.execute_command({"action": "search_files", "query": "notes"})
                self.assertEqual(result["files"], [str(home / "notes.txt")])


if __name__ == "__main__":
    unittest.main()
